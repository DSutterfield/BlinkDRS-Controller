"""Derived review hints. Never assigned identities, confirmations or training rows."""
import json,sqlite3

def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS cat_review_cache (
        detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        proposal TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        conn.execute("""CREATE TRIGGER IF NOT EXISTS cat_review_notify_insert AFTER INSERT ON cat_review_cache
            WHEN NEW.proposal<>'{}' BEGIN UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS cat_review_notify_update AFTER UPDATE ON cat_review_cache
            WHEN NEW.proposal<>OLD.proposal BEGIN UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")


def store(conn,did,proposal):
    ensure(conn)
    conn.execute('''INSERT INTO cat_review_cache(detection_id,proposal) VALUES(?,?)
        ON CONFLICT(detection_id) DO UPDATE SET proposal=excluded.proposal,updated_at=CURRENT_TIMESTAMP
        WHERE cat_review_cache.proposal<>excluded.proposal''',
        (did,json.dumps(proposal or {})))

def attach(conn,rows):
    if not rows:return
    try:
        cached={r[0]:json.loads(r[1]) for r in conn.execute('SELECT detection_id,proposal FROM cat_review_cache WHERE detection_id IN ('+','.join('?' for _ in rows)+')',[r['detection_id'] for r in rows])}
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):raise
        return
    active={r[0]:r[1] for r in conn.execute("SELECT identity_id,name FROM identities WHERE active=1 AND subject_type='Cat'")}
    for row in rows:
        proposal=cached.get(row['detection_id'],{})
        if row['subject_type']=='Cat' and not row['confirmed'] and row.get('identity_id') is None and proposal.get('suggested_identity_id') in active:
            row.update({k:proposal[k] for k in ['suggested_identity_id','suggested_name','suggestion_tier']})
            row['suggested_name']=active[proposal['suggested_identity_id']]
