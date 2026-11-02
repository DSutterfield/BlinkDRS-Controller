"""Transactional analysis queue and model-specific, human-confirmed reference gallery."""
import hashlib
import json
import math
import threading
import sqlite3
import logging
from functools import wraps
from datetime import datetime, timezone, timedelta
from identity_store import connect, require_clip


def is_database_busy(exc):
    # Extended SQLITE_BUSY codes include BUSY_SNAPSHOT; do not hide other errors.
    code = getattr(exc, 'sqlite_errorcode', None)
    return isinstance(exc, sqlite3.OperationalError) and code is not None and (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def defer_when_busy(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            if not is_database_busy(exc):
                raise
            logging.warning('%s deferred: catalog is busy', function.__name__)
            return None
    return call


def ensure_analysis(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS analysis_transient_retries (
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        attempts INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS identity_analysis_jobs (
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK(state IN ('queued','running','done','failed')),
        message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS pc_helper_leases (
        token TEXT PRIMARY KEY, catalog_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
        owner TEXT NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL,
        manifest TEXT NOT NULL, result_sha256 TEXT, accepted_at REAL);
    CREATE UNIQUE INDEX IF NOT EXISTS pc_helper_active_clip ON pc_helper_leases(catalog_id) WHERE status='leased';
    CREATE INDEX IF NOT EXISTS pc_helper_expiry ON pc_helper_leases(status,expires);
    CREATE TRIGGER IF NOT EXISTS pc_helper_fresh_retry AFTER UPDATE OF state ON identity_analysis_jobs
        WHEN NEW.state='queued' AND OLD.state IN ('done','failed') BEGIN
        UPDATE pc_helper_leases SET status='retired' WHERE catalog_id=NEW.catalog_id
            AND status IN ('expired','released','canceled'); END;
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
    CREATE TABLE IF NOT EXISTS identity_track_views (
        view_id INTEGER PRIMARY KEY,
        detection_id INTEGER NOT NULL REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        seconds REAL NOT NULL, crop_jpeg BLOB NOT NULL,
        embedding TEXT NOT NULL, embedding_model TEXT NOT NULL, quality_score REAL NOT NULL,
        UNIQUE(detection_id,seconds));
    CREATE TABLE IF NOT EXISTS identity_candidates (
        detection_id INTEGER NOT NULL REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        identity_id INTEGER NOT NULL REFERENCES identities(identity_id) ON DELETE CASCADE,
        rank INTEGER NOT NULL, score REAL NOT NULL, method TEXT NOT NULL,
        PRIMARY KEY(detection_id,identity_id));
    """)

    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        for event in ('INSERT','UPDATE','DELETE'):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS analysis_notify_{event.lower()}
                AFTER {event} ON identity_analysis_jobs BEGIN
                UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")


def queue_clip(db_path, catalog_id):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.execute('BEGIN IMMEDIATE')
        require_clip(conn, catalog_id)
        # Queued timestamps are the worker's existing FIFO ordering key.
        # Reserve an earlier slot atomically; keep an active job untouched.
        first = conn.execute("""SELECT MIN(j.updated_at) FROM identity_analysis_jobs j
            JOIN clips c ON c.id=j.catalog_id
            WHERE j.state='queued' AND c.local_present=1 AND j.catalog_id<>?""", (catalog_id,)).fetchone()[0]
        slot = datetime.now(timezone.utc).replace(tzinfo=None)
        if first is not None:
            slot = min(slot, datetime.fromisoformat(first)-timedelta(seconds=1))
        conn.execute("""INSERT INTO identity_analysis_jobs(catalog_id,state,updated_at) VALUES(?,'queued',?)
            ON CONFLICT(catalog_id) DO UPDATE SET state='queued',message='',updated_at=excluded.updated_at
            WHERE identity_analysis_jobs.state IN ('queued','done','failed')""", (catalog_id,slot.isoformat(sep=' ')))
        conn.execute("DELETE FROM analysis_transient_retries WHERE catalog_id=? AND EXISTS(SELECT 1 FROM identity_analysis_jobs WHERE catalog_id=? AND state='queued')",(catalog_id,catalog_id))
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


@defer_when_busy
def set_worker_status(db_path, state, message):
    with connect(db_path, timeout=.25) as conn:
        ensure_analysis(conn)
        conn.execute("""INSERT INTO identity_worker_status(id,state,message) VALUES(1,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state,message=excluded.message,
            updated_at=CURRENT_TIMESTAMP""", (state, message[:1000]))


class WorkerHeartbeat:
    """Keep a live worker available during long inference and model loading."""
    def __init__(self, db_path, interval=30):
        self.db_path = db_path
        self.interval = interval
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import logging
        while not self.stop.wait(self.interval):
            try:
                with connect(self.db_path) as conn:
                    conn.execute("""UPDATE identity_worker_status SET updated_at=CURRENT_TIMESTAMP
                        WHERE id=1 AND state IN ('loading','ready','analyzing','paused')""")
            except Exception:
                logging.exception('Could not refresh local AI worker heartbeat')

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()


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


@defer_when_busy
def claim_job(db_path):
    with connect(db_path) as conn:
        ensure_analysis(conn)
        conn.execute('BEGIN IMMEDIATE')
        # Local workers recover remote leases even if the PC never reconnects.
        from pc_helper_queue import expire
        from time import time as lease_now
        expire(conn, lease_now())
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
        JOIN identities i USING(identity_id) LEFT JOIN clip_detections d USING(detection_id)
        WHERE (s.detection_id IS NULL OR d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips))
        AND i.active=1 AND i.subject_type=? AND s.model_version=?""", (subject_type, model))
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


def prepare_detections(conn, catalog_id, observations, fingerprint=None):
    prepared = []
    if conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,)).fetchone():
        return prepared
    from review_link_pilot import enabled as link_pilot, scoring_item
    review_pilot = link_pilot()
    review_observations = observations
    if review_pilot:
        from review_link_pilot import publication_sources
        review_observations = publication_sources(conn,catalog_id,fingerprint,observations)
    for item in observations:
        if item['subject_type'] == 'Unknown':
            iid, similarity = None, None
        elif item['subject_type'] == 'Dog':
            from dog_review_config import enabled as dog_pilot
            if dog_pilot():iid,similarity=None,None
            else:
                from identity_candidates import dog_recommendation
                iid, similarity = dog_recommendation(conn, item, catalog_id), None
        else:
            iid, similarity = match_identity(conn, item['subject_type'], item.get('embedding_model'), item.get('embedding'),
                                             .90 if item['subject_type'] != 'Person' else .50, .05)
        from pc_helper_revisions import identity_revision
        from dog_review_revision import revision as dog_revision
        dog_epoch=dog_revision(conn,catalog_id) if item['subject_type']=='Dog' else None
        ranks = []
        if item['subject_type'] in ('Cat', 'Dog'):
            from identity_candidates import rank_candidates
            ranks = rank_candidates(conn, item['subject_type'], item.get('embedding_model'), item.get('embedding'), catalog_id)
        item = dict(item)
        review_source = next((t for t in review_observations if t['key']==item['key']),item)
        review_item = scoring_item(review_source,review_observations) if review_pilot else item
        if item['subject_type'] == 'Cat':
            from cat_review_evidence import recommend
            item['_cat_review'] = recommend(conn,review_item,catalog_id,ranks)
        elif item['subject_type']=='Dog':
            from dog_review_config import enabled as dog_pilot
            if dog_pilot():
                from dog_review_evidence import recommend
                item['_dog_review']=recommend(conn,review_item,catalog_id,ranks)
                item['_dog_review_revision']=dog_epoch
        prepared.append((item, iid, similarity, ranks))
    return prepared


def publish_prepared(conn, catalog_id, fingerprint, observations, prepared):
    """Caller owns one write transaction; never commits or opens a connection."""
    require_clip(conn, catalog_id)
    conn.execute('DELETE FROM clip_detections WHERE catalog_id=? AND confirmed=0 AND detection_id IN (SELECT detection_id FROM identity_evidence WHERE catalog_id=?)', (catalog_id,catalog_id))
    if conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?', (catalog_id,)).fetchone():
        conn.execute("UPDATE identity_analysis_jobs SET state='done',message='Kept confirmed Unknown Motion / No Visible Subject.',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?", (catalog_id,))
        return
    for item, iid, similarity, ranks in prepared:
        key = hashlib.sha256(f"{fingerprint}:{item['key']}".encode()).hexdigest()
        if conn.execute('SELECT 1 FROM identity_rejections WHERE catalog_id=? AND detection_key=?', (catalog_id,key)).fetchone():
            continue
        existing = conn.execute('SELECT d.detection_id,d.confirmed FROM identity_evidence e JOIN clip_detections d USING(detection_id) WHERE e.catalog_id=? AND e.detection_key=?', (catalog_id,key)).fetchone()
        if existing and existing['confirmed']:
            continue
        from dog_review_config import enabled as dog_pilot
        if item['subject_type']=='Dog' and dog_pilot():iid=None
        # A profile may be deleted/deactivated while ranking runs.
        valid_ids = {r[0] for r in conn.execute('SELECT identity_id FROM identities WHERE active=1 AND subject_type=?', (item['subject_type'],))}
        if iid not in valid_ids:
            iid = None
        ranks = [rank for rank in ranks if rank['identity_id'] in valid_ids]
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
        if item['subject_type'] in ('Cat','Dog'):
            from identity_candidates import store_candidates
            store_candidates(conn,did,item['subject_type'],item.get('embedding_model'),item.get('embedding'),catalog_id,ranks=ranks)
        if item['subject_type'] == 'Cat':
            from cat_review_cache import store
            store(conn,did,item.get('_cat_review'))
        for view in item.get('views', [])[:4]:
            conn.execute('''INSERT OR IGNORE INTO identity_track_views
                (detection_id,seconds,crop_jpeg,embedding,embedding_model,quality_score)
                VALUES(?,?,?,?,?,?)''', (did,view['seconds'],view['crop_jpeg'],
                json.dumps(normalized(view['embedding'])),view['embedding_model'],view['quality_score']))
        if item['subject_type']=='Dog' and dog_pilot() and item.get('_dog_review_revision') is not None:
            from dog_review_cache import store as store_dog
            store_dog(conn,did,item.get('_dog_review'),expected_revision=item['_dog_review_revision'])
    conn.execute("UPDATE identity_analysis_jobs SET state='done',message=?,updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?",
                 (f"Analyzed sampled frames; {len(observations)} subject appearances. Sampling can miss brief activity.",catalog_id))


def publish_detections(db_path, catalog_id, fingerprint, observations):
    """Preserve confirmed labels and removed evidence on every retry."""
    import time
    for attempt in range(3):
        try:
            with connect(db_path, timeout=30) as conn:
                require_clip(conn, catalog_id)
                prepared = prepare_detections(conn, catalog_id, observations, fingerprint=fingerprint)
            with connect(db_path, timeout=30) as conn:
                conn.execute('BEGIN IMMEDIATE')
                publish_prepared(conn, catalog_id, fingerprint, observations, prepared)
            return
        except sqlite3.OperationalError as exc:
            if not is_database_busy(exc) or attempt==2:raise
            time.sleep(.25*(attempt+1))


def learn_confirmed(conn, detection_id):
    from subject_exclusions import blocked
    if blocked(conn,detection_id):return
    row = conn.execute("""SELECT d.identity_id,d.subject_type AS assigned_type,e.* FROM clip_detections d
        JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=? AND d.confirmed=1""", (detection_id,)).fetchone()
    from evaluation_holdout import blocked
    if row and not blocked(conn,row['catalog_id']) and row['identity_id'] is not None and row['assigned_type'] == row['subject_type'] and row['embedding'] and row['quality_score'] >= .2:
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
            WHERE d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips) AND d.confirmed=1 AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions) AND
            (d.subject_type<>e.subject_type OR e.embedding_model='manual-pending'
            OR (d.subject_type='Cat' AND e.embedding_model IS NULL AND COALESCE(e.quality_score,0)=0)) LIMIT 4""")]


def rebuild_reference(db_path, correction, vector, model, quality):
    with connect(db_path) as conn:
        from subject_exclusions import blocked
        if blocked(conn,correction['detection_id']):return
        row = conn.execute('SELECT identity_id,subject_type,confirmed FROM clip_detections WHERE detection_id=?',
                           (correction['detection_id'],)).fetchone()
        if not row or not row['confirmed'] or row['identity_id'] != correction['identity_id'] or row['subject_type'] != correction['subject_type']:
            return  # Another human edit superseded this inference.
        conn.execute('UPDATE identity_evidence SET subject_type=?,embedding=?,embedding_model=?,quality_score=?,similarity=NULL WHERE detection_id=?',
                     (row['subject_type'],json.dumps(normalized(vector)) if vector is not None else None,model,quality,correction['detection_id']))
        conn.execute('DELETE FROM identity_samples WHERE detection_id=?',(correction['detection_id'],))
        learn_confirmed(conn,correction['detection_id'])


def record_job_error(db_path,catalog_id,exc):
    """Transient SQLite contention gets bounded retries; damaged inputs still fail."""
    from pc_helper_queue import transaction
    with transaction(db_path) as c:
        row=c.execute('SELECT state FROM identity_analysis_jobs WHERE catalog_id=?',(catalog_id,)).fetchone()
        if row is None or row[0]!='running':return
        if is_database_busy(exc):
            c.execute('INSERT INTO analysis_transient_retries VALUES(?,1) ON CONFLICT(catalog_id) DO UPDATE SET attempts=attempts+1',(catalog_id,))
            n=c.execute('SELECT attempts FROM analysis_transient_retries WHERE catalog_id=?',(catalog_id,)).fetchone()[0]
            state='queued' if n<=3 else 'failed'
            message=f'Temporary database contention; retry {n}/3' if n<=3 else 'Database remained busy after three automatic retries'
        else:state,message='failed',str(exc)
        c.execute('UPDATE identity_analysis_jobs SET state=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?',(state,message[:1000],catalog_id))
