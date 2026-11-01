"""Durable confirmed-clip evidence before video expiry; never assigns or teaches.

The independent SQLite archive contains checksummed, versioned JSON snapshots.
Hidden catalog stubs keep existing matching joins and source diversity intact.
The archive is committed and read back BEFORE any original can be removed.
Callers hold a catalog BEGIN IMMEDIATE transaction throughout capture/removal.
"""
import base64
import hashlib
import json
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

TABLES = ('clip_detections', 'identity_evidence', 'identity_track_views',
          'identity_samples', 'subject_type_samples', 'identity_candidates',
          'identity_rejections', 'subject_analysis_exclusions', 'clip_unknown_motion')

def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS retained_learning_clips(
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        snapshot_sha256 TEXT NOT NULL, archive_path TEXT NOT NULL,
        retained_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def has_learning(conn, cid):
    available = tables(conn)
    for table, clause in (
        ('clip_detections', 'catalog_id=? AND confirmed=1'),
        ('clip_unknown_motion', 'catalog_id=?'),
        ('evaluation_holdout_clips', 'catalog_id=?'),
    ):
        if table in available and conn.execute('SELECT 1 FROM '+table+' WHERE '+clause+' LIMIT 1', (cid,)).fetchone():
            return True
    for table in ('identity_samples', 'subject_type_samples', 'subject_analysis_exclusions'):
        if table in available and 'clip_detections' in available and conn.execute(
            'SELECT 1 FROM '+table+' WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id=?) LIMIT 1', (cid,)
        ).fetchone():
            return True
    return False

def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur]

def _encode(value):
    if isinstance(value, bytes):
        return {'__blob_base64__': base64.b64encode(value).decode('ascii')}
    raise TypeError(type(value).__name__)

def preserve(conn, root, cid, video=None):
    """Archive all sibling evidence for a confirmed clip, preserving exclusions.

    Returns False for an unreviewed clip. Snapshot failure raises before deletion.
    Existing images/vectors are copied byte-for-byte; no new features are made.
    """
    ensure(conn)
    if not has_learning(conn, cid):
        return False
    payload = {'format': 'blinkdrs-confirmed-learning-v1', 'tables': {}}
    payload['clip'] = _rows(conn, 'SELECT * FROM clips WHERE id=?', (cid,))[0]
    payload['video_sha256'] = None
    if video is not None and Path(video).is_file():
        with Path(video).open('rb') as f:
            payload['video_sha256'] = hashlib.file_digest(f, 'sha256').hexdigest()
    available = tables(conn)
    for table in TABLES:
        if table not in available:
            continue
        columns = {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
        if 'catalog_id' in columns:
            sql = 'SELECT * FROM '+table+' WHERE catalog_id=?'
        else:
            sql = 'SELECT * FROM '+table+' WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id=?)'
        payload['tables'][table] = _rows(conn, sql, (cid,))
    ids = {r['identity_id'] for r in payload['tables'].get('clip_detections', []) if r.get('identity_id') is not None}
    payload['identities'] = _rows(conn, 'SELECT * FROM identities WHERE identity_id IN ('+','.join('?'*len(ids))+')', tuple(sorted(ids))) if ids else []
    # Versioned copies preserve the label as it was at expiry without silently
    # overwriting later human corrections. The live catalog remains authoritative.
    raw = json.dumps(payload, default=_encode, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(raw).hexdigest()
    folder = Path(root)/'learning'; folder.mkdir(exist_ok=True)
    path = folder/'confirmed-learning.sqlite'
    with closing(sqlite3.connect(path, timeout=30)) as dest:
        dest.execute('PRAGMA synchronous=FULL')
        dest.execute('''CREATE TABLE IF NOT EXISTS clip_snapshots(
            snapshot_sha256 TEXT PRIMARY KEY, catalog_id INTEGER NOT NULL,
            blink_media_id TEXT, payload BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        with dest:
            dest.execute('INSERT OR IGNORE INTO clip_snapshots(snapshot_sha256,catalog_id,blink_media_id,payload) VALUES(?,?,?,?)',
                         (digest, cid, payload['clip'].get('blink_media_id'), zlib.compress(raw)))
        stored = dest.execute('SELECT payload FROM clip_snapshots WHERE snapshot_sha256=?', (digest,)).fetchone()
        if stored is None or zlib.decompress(stored[0]) != raw:
            raise OSError('Learning archive verification failed; recording retained')
    path.chmod(0o600)
    conn.execute('''INSERT INTO retained_learning_clips(catalog_id,snapshot_sha256,archive_path) VALUES(?,?,?)
        ON CONFLICT(catalog_id) DO UPDATE SET snapshot_sha256=excluded.snapshot_sha256,
        archive_path=excluded.archive_path,retained_at=CURRENT_TIMESTAMP''', (cid, digest, str(path)))
    return True

def verified_stub(conn, cid):
    """Never cascade a previously retained clip, even after archive I/O failure."""
    if 'retained_learning_clips' not in tables(conn):
        return False
    row = conn.execute('SELECT snapshot_sha256,archive_path FROM retained_learning_clips WHERE catalog_id=?', (cid,)).fetchone()
    if row is None:
        return False
    path = Path(row[1])
    with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro', uri=True)) as dest:
        blob = dest.execute('SELECT payload FROM clip_snapshots WHERE snapshot_sha256=?', (row[0],)).fetchone()
        if blob is None or hashlib.sha256(zlib.decompress(blob[0])).hexdigest() != row[0]:
            raise OSError('Learning archive missing or corrupt; cleanup stopped')
    return True
