"""Durable evaluation membership and answer labels, excluded from learning."""
from pathlib import Path


def ensure_holdouts(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS evaluation_holdout_clips (
        blink_media_id TEXT PRIMARY KEY, catalog_id INTEGER NOT NULL,
        dataset_id TEXT NOT NULL, device_id INTEGER NOT NULL, camera TEXT NOT NULL,
        captured_at TEXT NOT NULL, video_sha256 TEXT NOT NULL, copy_path TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE VIEW IF NOT EXISTS evaluation_excluded_clips AS
        SELECT DISTINCT c.id AS catalog_id FROM clips c JOIN evaluation_holdout_clips h
        ON CAST(c.blink_media_id AS TEXT)=h.blink_media_id OR
        (c.device_id=h.device_id AND ABS(julianday(c.captured_at)-julianday(h.captured_at))*86400<=300.001);
    CREATE TABLE IF NOT EXISTS evaluation_holdout_labels (
        blink_media_id TEXT NOT NULL, detection_id INTEGER NOT NULL, subject_type TEXT NOT NULL,
        identity_id INTEGER, name TEXT, confirmed_at TEXT, crop_jpeg BLOB,
        first_seen_seconds REAL,last_seen_seconds REAL,
        active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(blink_media_id,detection_id));
    ''')
    # No FK to clips: membership, recordings and test answers survive retention.
    for table in ('identity_samples','subject_type_samples'):
        for event in ('INSERT','UPDATE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS holdout_block_{table}_{event.lower()}
                BEFORE {event} ON {table} WHEN NEW.detection_id IN
                (SELECT detection_id FROM clip_detections WHERE catalog_id IN (SELECT catalog_id FROM evaluation_excluded_clips))
                BEGIN SELECT RAISE(IGNORE); END''')
    capture='''INSERT INTO evaluation_holdout_labels
        (blink_media_id,detection_id,subject_type,identity_id,name,confirmed_at,crop_jpeg,first_seen_seconds,last_seen_seconds,active)
        SELECT h.blink_media_id,d.detection_id,d.subject_type,d.identity_id,i.name,d.confirmed_at,e.crop_jpeg,d.first_seen_seconds,d.last_seen_seconds,1
        FROM clip_detections d JOIN clips c ON c.id=d.catalog_id
        JOIN evaluation_holdout_clips h ON h.blink_media_id=CAST(c.blink_media_id AS TEXT)
        LEFT JOIN identities i USING(identity_id) LEFT JOIN identity_evidence e USING(detection_id)
        WHERE d.detection_id=NEW.detection_id AND d.confirmed=1
        ON CONFLICT(blink_media_id,detection_id) DO UPDATE SET subject_type=excluded.subject_type,
        identity_id=excluded.identity_id,name=excluded.name,confirmed_at=excluded.confirmed_at,
        crop_jpeg=excluded.crop_jpeg,first_seen_seconds=excluded.first_seen_seconds,last_seen_seconds=excluded.last_seen_seconds,active=1;'''
    for table,event in (('clip_detections','INSERT'),('clip_detections','UPDATE'),('identity_evidence','INSERT'),('identity_evidence','UPDATE OF crop_jpeg')):
        suffix='insert' if event=='INSERT' else 'update'
        conn.execute(f'CREATE TRIGGER IF NOT EXISTS holdout_answer_{table}_{suffix} AFTER {event} ON {table} BEGIN {capture} END')
    conn.executescript('''
    CREATE TRIGGER IF NOT EXISTS holdout_answer_unconfirm AFTER UPDATE ON clip_detections
        WHEN NEW.confirmed=0 BEGIN
        UPDATE evaluation_holdout_labels SET active=0 WHERE detection_id=NEW.detection_id AND blink_media_id IN
        (SELECT CAST(blink_media_id AS TEXT) FROM clips WHERE id=NEW.catalog_id); END;
    CREATE TRIGGER IF NOT EXISTS holdout_answer_remove AFTER DELETE ON clip_detections
        WHEN EXISTS(SELECT 1 FROM clips WHERE id=OLD.catalog_id AND local_present=1) BEGIN
        UPDATE evaluation_holdout_labels SET active=0 WHERE detection_id=OLD.detection_id AND blink_media_id IN
        (SELECT CAST(blink_media_id AS TEXT) FROM clips WHERE id=OLD.catalog_id); END;
    CREATE TRIGGER IF NOT EXISTS holdout_answer_motion AFTER INSERT ON clip_unknown_motion BEGIN
        INSERT INTO evaluation_holdout_labels(blink_media_id,detection_id,subject_type,confirmed_at,active)
        SELECT h.blink_media_id,0,'Motion',NEW.confirmed_at,1 FROM clips c JOIN evaluation_holdout_clips h
        ON h.blink_media_id=CAST(c.blink_media_id AS TEXT) WHERE c.id=NEW.catalog_id
        ON CONFLICT(blink_media_id,detection_id) DO UPDATE SET active=1,confirmed_at=excluded.confirmed_at;
        END;
    ''')


def excluded_ids(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='evaluation_holdout_clips'").fetchone():return set()
    return {r[0] for r in conn.execute('SELECT catalog_id FROM evaluation_excluded_clips')}


def blocked(conn,catalog_id):
    return catalog_id in excluded_ids(conn)


def list_holdouts(db_path):
    from identity_store import connect
    with connect(db_path) as conn:
        rows=[dict(r) for r in conn.execute('''SELECT h.*,
            (SELECT COUNT(*) FROM evaluation_holdout_labels a WHERE a.blink_media_id=h.blink_media_id AND a.active=1) reviewed_answers
            FROM evaluation_holdout_clips h ORDER BY h.camera,h.catalog_id''')]
    for r in rows:
        r['copy_available']=Path(r['copy_path']).is_file()
        r['answer_status']='partial_review' if r['reviewed_answers'] else 'unreviewed'
        r['video_url']='/api/v1/evaluation/holdouts/'+r['blink_media_id']+'/video'
    return rows


def video_path(db_path,blink_media_id):
    from identity_store import connect
    with connect(db_path) as conn:
        row=conn.execute('SELECT copy_path FROM evaluation_holdout_clips WHERE blink_media_id=?',(blink_media_id,)).fetchone()
    if row is None:raise LookupError('Evaluation clip not found')
    path=Path(row[0]).resolve();root=Path(db_path).resolve().parent/'bai-evaluation'
    if not path.is_relative_to(root) or path.suffix.lower()!='.mp4':raise ValueError('Invalid evaluation recording path')
    if not path.is_file():raise LookupError('Evaluation recording is unavailable')
    return path
