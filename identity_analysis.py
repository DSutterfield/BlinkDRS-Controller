"""Transactional analysis queue and model-specific, human-confirmed reference gallery."""
import hashlib
import json
import math
from datetime import datetime, timezone
from identity_store import connect, require_clip


def ensure_analysis(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS identity_analysis_jobs (
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
        message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS identity_evidence (
        detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        catalog_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
        detection_key TEXT NOT NULL, subject_type TEXT NOT NULL,
        crop_jpeg BLOB NOT NULL, embedding TEXT, embedding_model TEXT,
        quality_score REAL, similarity REAL,
        UNIQUE(catalog_id,detection_key));
    CREATE TABLE IF NOT EXISTS subject_type_samples (
        detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        subject_type TEXT NOT NULL, embedding TEXT, model_version TEXT NOT NULL,
        quality_score REAL NOT NULL, crop_hash TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS identity_rejections (
        catalog_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
        detection_key TEXT NOT NULL, PRIMARY KEY(catalog_id,detection_key));
    CREATE TABLE IF NOT EXISTS identity_worker_status (
        id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, message TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)

    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        for event in ('INSERT','UPDATE','DELETE'):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS analysis_notify_{event.lower()}
                AFTER {event} ON identity_analysis_jobs BEGIN
                UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")


def queue_clip(db_path, catalog_id):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        require_clip(conn, catalog_id)
        conn.execute("""INSERT INTO identity_analysis_jobs(catalog_id,state) VALUES(?,'queued')
            ON CONFLICT(catalog_id) DO UPDATE SET state='queued',message='',updated_at=CURRENT_TIMESTAMP
            WHERE identity_analysis_jobs.state IN ('done','failed')""", (catalog_id,))
        return dict(conn.execute('SELECT * FROM identity_analysis_jobs WHERE catalog_id=?', (catalog_id,)).fetchone())


def job_status(db_path, catalog_id):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        require_clip(conn, catalog_id)
        row = conn.execute('SELECT * FROM identity_analysis_jobs WHERE catalog_id=?', (catalog_id,)).fetchone()
        worker = conn.execute('SELECT * FROM identity_worker_status WHERE id=1').fetchone()
        available = worker is not None and worker['state'] in ('loading','ready','analyzing','paused') and (datetime.now(timezone.utc) - datetime.fromisoformat(worker['updated_at']).replace(tzinfo=timezone.utc)).total_seconds() < 900
        return {'worker_available': available, 'state': row['state'] if row else 'not_analyzed',
                'message': row['message'] if row else '',
                'worker': dict(worker) if worker else None}


def set_worker_status(db_path, state, message):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.execute("""INSERT INTO identity_worker_status(id,state,message) VALUES(1,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,message=excluded.message,
            updated_at=CURRENT_TIMESTAMP""", (state, message[:1000]))


def enable_new_clip_queue(db_path):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS identity_queue_new_clip AFTER INSERT ON clips
        WHEN NEW.local_present=1 BEGIN
            INSERT OR IGNORE INTO identity_analysis_jobs(catalog_id,state) VALUES(NEW.id,'queued'); END;
        CREATE TRIGGER IF NOT EXISTS identity_queue_restored_clip AFTER UPDATE OF local_present ON clips
        WHEN NEW.local_present=1 AND OLD.local_present=0 BEGIN
            INSERT OR IGNORE INTO identity_analysis_jobs(catalog_id,state) VALUES(NEW.id,'queued'); END;
        """)


def claim_job(db_path):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("""SELECT j.catalog_id,c.video_path FROM identity_analysis_jobs j
            JOIN clips c ON c.id=j.catalog_id WHERE j.state='queued' AND c.local_present=1
            ORDER BY j.updated_at,j.catalog_id LIMIT 1""").fetchone()
        if row:
            conn.execute("UPDATE identity_analysis_jobs SET state='running',message='',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?", (row['catalog_id'],))
        return dict(row) if row else None


def finish_job(db_path, catalog_id, state, message=''):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.execute('UPDATE identity_analysis_jobs SET state=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?',
                     (state, message[:1000], catalog_id))


def normalized(vector):
    if not vector or len(vector) > 4096 or not all(math.isfinite(v) for v in vector):
        raise ValueError('Invalid model embedding')
    length = math.sqrt(sum(v*v for v in vector))
    if length < 1e-8:
        raise ValueError('Empty model embedding')
    return [float(v / length) for v in vector]


def match_identity(conn, subject_type, model, vector, threshold, margin):
    """Cosine similarity is a ranking score, never a calibrated probability."""
    if vector is None:
        return None, None
    vector = normalized(vector)
    scores = {}
    rows = conn.execute("""SELECT s.identity_id,s.embedding FROM identity_samples s
        JOIN identities i USING(identity_id) WHERE i.active=1 AND i.subject_type=? AND s.model_version=?""", (subject_type, model))
    for row in rows:
        try:
            sample = normalized(json.loads(row['embedding']))
        except (ValueError, TypeError):
            continue
        if len(sample) == len(vector):
            score = sum(a*b for a,b in zip(sample, vector))
            scores[row['identity_id']] = max(scores.get(row['identity_id'], -1), score)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, None
    best_id, best = ranked[0]
    if best < threshold or (len(ranked) > 1 and best - ranked[1][1] < margin):
        return None, best
    return best_id, best


def publish_detections(db_path, catalog_id, fingerprint, observations):
    """A retry never alters confirmed labels and never recreates explicitly removed evidence."""
    with connect(db_path) as conn:
        ensure_analysis(conn)
        require_clip(conn, catalog_id)
        conn.execute('DELETE FROM clip_detections WHERE catalog_id=? AND confirmed=0 AND detection_id IN (SELECT detection_id FROM identity_evidence WHERE catalog_id=?)', (catalog_id,catalog_id))
        if conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,)).fetchone():
            conn.execute("UPDATE identity_analysis_jobs SET state='done',message='Kept confirmed Unknown Motion / No Visible Subject.',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?", (catalog_id,))
            return
        for item in observations:
            key = hashlib.sha256(f"{fingerprint}:{item['key']}".encode()).hexdigest()
            if conn.execute('SELECT 1 FROM identity_rejections WHERE catalog_id=? AND detection_key=?', (catalog_id,key)).fetchone():
                continue
            existing = conn.execute('SELECT d.detection_id,d.confirmed FROM identity_evidence e JOIN clip_detections d USING(detection_id) WHERE e.catalog_id=? AND e.detection_key=?', (catalog_id,key)).fetchone()
            if existing and existing['confirmed']:
                continue
            iid, similarity = match_identity(conn, item['subject_type'], item.get('embedding_model'), item.get('embedding'),
                                             .90 if item['subject_type'] != 'Person' else .50, .05)
            if existing:
                did = existing['detection_id']
                conn.execute('UPDATE clip_detections SET identity_id=? WHERE detection_id=? AND confirmed=0', (iid,did))
            else:
                cursor = conn.execute("""INSERT INTO clip_detections(catalog_id,identity_id,subject_type,confidence,
                    first_seen_seconds,last_seen_seconds,model_version) VALUES(?,?,?,?,?,?,?)""",
                    (catalog_id,iid,item['subject_type'],item['confidence'],item['first_seen_seconds'],item['last_seen_seconds'],item['detector_model']))
                did = cursor.lastrowid
            conn.execute("""INSERT INTO identity_evidence VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(detection_id) DO UPDATE SET similarity=excluded.similarity""",
                (did,catalog_id,key,item['subject_type'],item['crop_jpeg'],
                 json.dumps(normalized(item['embedding'])) if item.get('embedding') is not None else None,
                 item.get('embedding_model'),item.get('quality_score'),similarity,))
        conn.execute("UPDATE identity_analysis_jobs SET state='done',message=?,updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?",
                     (f"Analyzed sampled frames; {len(observations)} subject appearances. Sampling can miss brief activity.",catalog_id))


def learn_confirmed(conn, detection_id):
    row = conn.execute("""SELECT d.identity_id,d.subject_type AS assigned_type,e.* FROM clip_detections d
        JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=? AND d.confirmed=1""", (detection_id,)).fetchone()
    if row and row['identity_id'] is not None and row['assigned_type'] == row['subject_type'] and row['embedding'] and row['quality_score'] >= .2:
        conn.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) VALUES(?,?,?,?,?)',
                     (row['identity_id'],detection_id,row['embedding'],row['embedding_model'],row['quality_score']))


def crop_image(db_path, catalog_id, detection_id):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        require_clip(conn, catalog_id)
        row = conn.execute('SELECT crop_jpeg FROM identity_evidence WHERE catalog_id=? AND detection_id=?', (catalog_id,detection_id)).fetchone()
        if not row:
            raise LookupError('No subject image is available for this label.')
        return row[0]


def pending_type_corrections(db_path):
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute("""SELECT d.detection_id,d.identity_id,d.subject_type,e.crop_jpeg
            FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.confirmed=1 AND
            (d.subject_type<>e.subject_type OR e.embedding_model='manual-pending') LIMIT 4""")]


def rebuild_reference(db_path, correction, vector, model, quality):
    with connect(db_path) as conn:
        row = conn.execute('SELECT identity_id,subject_type,confirmed FROM clip_detections WHERE detection_id=?',
                           (correction['detection_id'],)).fetchone()
        if not row or not row['confirmed'] or row['identity_id'] != correction['identity_id'] or row['subject_type'] != correction['subject_type']:
            return  # Another human edit superseded this inference.
        conn.execute('UPDATE identity_evidence SET subject_type=?,embedding=?,embedding_model=?,quality_score=?,similarity=NULL WHERE detection_id=?',
                     (row['subject_type'],json.dumps(normalized(vector)) if vector is not None else None,model,quality,correction['detection_id']))
        conn.execute('DELETE FROM identity_samples WHERE detection_id=?',(correction['detection_id'],))
        learn_confirmed(conn,correction['detection_id'])
