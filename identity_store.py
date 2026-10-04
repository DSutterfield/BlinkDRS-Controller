"""Local identity metadata, independent of recording source and Blink Cloud."""
import sqlite3
from contextlib import contextmanager


def ensure_identities(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS identities (
        identity_id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE,
        subject_type TEXT NOT NULL CHECK(subject_type IN ('Person','Cat','Dog')),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(name, subject_type));
    CREATE TABLE IF NOT EXISTS clip_detections (
        detection_id INTEGER PRIMARY KEY,
        catalog_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
        identity_id INTEGER REFERENCES identities(identity_id),
        subject_type TEXT NOT NULL CHECK(subject_type IN ('Person','Cat','Dog')),
        confidence REAL CHECK(confidence BETWEEN 0 AND 1),
        first_seen_seconds REAL CHECK(first_seen_seconds >= 0),
        last_seen_seconds REAL CHECK(last_seen_seconds >= first_seen_seconds),
        confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)),
        confirmed_at TEXT, model_version TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS detections_clip ON clip_detections(catalog_id);
    CREATE TABLE IF NOT EXISTS clip_unknown_motion (
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS identity_samples (
        sample_id INTEGER PRIMARY KEY,
        identity_id INTEGER NOT NULL REFERENCES identities(identity_id),
        detection_id INTEGER REFERENCES clip_detections(detection_id) ON DELETE SET NULL,
        embedding BLOB, model_version TEXT, quality_score REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    from identity_analysis import ensure_analysis
    ensure_analysis(conn)
    # Notify existing Windows clients after a committed label change.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS identities_notify_{event.lower()}
                AFTER {event} ON clip_detections BEGIN
                UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS unknown_motion_notify_{event.lower()}
                AFTER {event} ON clip_unknown_motion BEGIN
                UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")


@contextmanager
def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        ensure_identities(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def list_identities(db_path):
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM identities WHERE active=1 ORDER BY name, subject_type')]


def create_identity(db_path, name, subject_type):
    name = name.strip()
    if not name or len(name) > 100 or subject_type not in ('Person', 'Cat', 'Dog'):
        raise ValueError('Enter a name (1–100 characters) and Person, Cat, or Dog.')
    with connect(db_path) as conn:
        conn.execute('INSERT OR IGNORE INTO identities(name,subject_type) VALUES(?,?)',
                     (name, subject_type))
        return dict(conn.execute('SELECT * FROM identities WHERE name=? AND subject_type=?',
                                 (name, subject_type)).fetchone())


def delete_identity(db_path, identity_id):
    """Forget a profile and references while retaining clip subjects as Unknown."""
    with connect(db_path) as conn:
        if not conn.execute('SELECT 1 FROM identities WHERE identity_id=?',
                            (identity_id,)).fetchone():
            raise LookupError('Identity profile not found.')
        conn.execute('DELETE FROM identity_samples WHERE identity_id=?', (identity_id,))
        conn.execute('UPDATE identity_evidence SET similarity=NULL WHERE detection_id IN '
                     '(SELECT detection_id FROM clip_detections WHERE identity_id=?)', (identity_id,))
        conn.execute('UPDATE clip_detections SET identity_id=NULL, confirmed=1, '
                     'confirmed_at=CURRENT_TIMESTAMP WHERE identity_id=?', (identity_id,))
        conn.execute('DELETE FROM identities WHERE identity_id=?', (identity_id,))
        return {'deleted': True, 'identity_id': identity_id}


def clip_identities(conn, catalog_id):
    rows = [dict(row) for row in conn.execute("""SELECT d.*, i.name, e.similarity,
        CASE WHEN e.crop_jpeg IS NOT NULL THEN 1 ELSE 0 END AS crop_available
        FROM clip_detections d LEFT JOIN identities i USING(identity_id)
        LEFT JOIN identity_evidence e USING(detection_id)
        WHERE d.catalog_id=? ORDER BY d.detection_id""", (catalog_id,))]
    for row in rows:
        row["confirmed"] = bool(row["confirmed"])
        row["crop_available"] = bool(row["crop_available"])
    if not rows:
        marked = conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,)).fetchone()
        analyzed = conn.execute("SELECT 1 FROM identity_analysis_jobs WHERE catalog_id=? AND state='done'", (catalog_id,)).fetchone()
        if marked or analyzed:
            rows.append({'detection_id': 0, 'identity_id': None,
                         'name': 'Unknown Motion / No Visible Subject', 'subject_type': 'Motion',
                         'confirmed': bool(marked), 'crop_available': False,
                         'first_seen_seconds': None, 'last_seen_seconds': None, 'similarity': None})
    return rows


def mark_unknown_motion(db_path, catalog_id):
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        if conn.execute('SELECT 1 FROM clip_detections WHERE catalog_id=?', (catalog_id,)).fetchone():
            raise ValueError('Remove the subject labels before marking no visible subject.')
        conn.execute('INSERT OR IGNORE INTO clip_unknown_motion(catalog_id) VALUES(?)', (catalog_id,))
        return clip_identities(conn, catalog_id)


def get_clip_identities(db_path, catalog_id):
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        return clip_identities(conn, catalog_id)


def require_clip(conn, catalog_id):
    if not conn.execute('SELECT 1 FROM clips WHERE id=? AND local_present=1',
                        (catalog_id,)).fetchone():
        raise LookupError('Locally available clip not found.')


def assign_identity(db_path, catalog_id, identity_id, subject_type, detection_id=None):
    """Explicit human assignment/correction. No fabricated confidence or training sample."""
    if subject_type not in ('Person', 'Cat', 'Dog'):
        raise ValueError('Invalid subject type.')
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        if identity_id is not None:
            identity = conn.execute('SELECT * FROM identities WHERE identity_id=? AND active=1',
                                    (identity_id,)).fetchone()
            if identity is None or identity['subject_type'] != subject_type:
                raise ValueError('Choose an active identity of the same subject type.')
        conn.execute('DELETE FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,))
        if detection_id is None:
            # Repeated Add/Confirm does not create duplicate whole-clip manual labels.
            existing = conn.execute("""SELECT detection_id FROM clip_detections WHERE
                catalog_id=? AND identity_id IS ? AND subject_type=? AND confirmed=1
                AND first_seen_seconds IS NULL""", (catalog_id, identity_id, subject_type)).fetchone()
            if existing is None:
                conn.execute("""INSERT INTO clip_detections
                    (catalog_id,identity_id,subject_type,confirmed,confirmed_at,model_version)
                    VALUES(?,?,?,1,CURRENT_TIMESTAMP,'manual')""", (catalog_id, identity_id, subject_type))
        else:
            result = conn.execute("""UPDATE clip_detections SET identity_id=?, subject_type=?,
                confirmed=1, confirmed_at=CURRENT_TIMESTAMP WHERE catalog_id=? AND detection_id=?""",
                (identity_id, subject_type, catalog_id, detection_id))
            if result.rowcount != 1:
                raise LookupError('Detection not found for this clip.')
            # A corrected label invalidates old reference samples for this detection.
            conn.execute('DELETE FROM identity_samples WHERE detection_id=?', (detection_id,))
            from identity_analysis import learn_confirmed
            learn_confirmed(conn, detection_id)
            # A linked subject replaces the redundant whole-clip label for that name.
            if identity_id is not None and conn.execute(
                    'SELECT 1 FROM identity_evidence WHERE detection_id=? AND crop_jpeg IS NOT NULL',
                    (detection_id,)).fetchone():
                redundant = "catalog_id=? AND identity_id=? AND subject_type=? AND first_seen_seconds IS NULL AND detection_id<>?"
                args = (catalog_id, identity_id, subject_type, detection_id)
                conn.execute('DELETE FROM identity_samples WHERE detection_id IN '
                             '(SELECT detection_id FROM clip_detections WHERE ' + redundant + ')', args)
                conn.execute('DELETE FROM clip_detections WHERE ' + redundant, args)
        return clip_identities(conn, catalog_id)


def remove_detection(db_path, catalog_id, detection_id):
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        if detection_id == 0:
            conn.execute('DELETE FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,))
            conn.execute("DELETE FROM identity_analysis_jobs WHERE catalog_id=? AND state='done'", (catalog_id,))
            return clip_identities(conn, catalog_id)
        conn.execute('INSERT OR IGNORE INTO identity_rejections SELECT catalog_id,detection_key FROM identity_evidence WHERE catalog_id=? AND detection_id=?', (catalog_id,detection_id))
        conn.execute('DELETE FROM identity_samples WHERE detection_id IN '
                     '(SELECT detection_id FROM clip_detections WHERE catalog_id=? AND detection_id=?)',
                     (catalog_id, detection_id))
        if conn.execute('DELETE FROM clip_detections WHERE catalog_id=? AND detection_id=?',
                        (catalog_id, detection_id)).rowcount != 1:
            raise LookupError('Detection not found for this clip.')
        return clip_identities(conn, catalog_id)
