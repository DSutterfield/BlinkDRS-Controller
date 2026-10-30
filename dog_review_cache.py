"""Derived dog pilot hints. No assignments, confirmations or reference learning."""
import json,sqlite3,hashlib
from dog_review_revision import revision as identity_revision


def ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dog_review_cache (
        detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        proposal TEXT NOT NULL,gallery_revision INTEGER NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    from dog_review_revision import ensure as ensure_revision
    ensure_revision(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS dog_review_requests(detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,requested_at REAL NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS dog_requests_priority ON dog_review_requests(requested_at DESC,detection_id)')
    for table in ('identity_evidence','identity_track_views','subject_analysis_exclusions'):
        for event in ('INSERT','UPDATE','DELETE'):
            ref='OLD' if event=='DELETE' else 'NEW'
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS dog_hint_{table}_{event.lower()} AFTER {event} ON {table}
                BEGIN DELETE FROM dog_review_cache WHERE detection_id={ref}.detection_id; END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS dog_hint_label_change AFTER UPDATE OF subject_type,identity_id,confirmed,catalog_id ON clip_detections
        BEGIN DELETE FROM dog_review_cache WHERE detection_id=NEW.detection_id; END""")
    for event in ('INSERT','UPDATE','DELETE'):
        ref='OLD' if event=='DELETE' else 'NEW'
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS dog_hint_no_visible_{event.lower()} AFTER {event} ON clip_unknown_motion
            BEGIN DELETE FROM dog_review_cache WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id={ref}.catalog_id); END""")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        for event,condition in [('INSERT',"NEW.proposal<>'{}'"),('UPDATE','NEW.proposal<>OLD.proposal OR NEW.gallery_revision<>OLD.gallery_revision'),('DELETE',"OLD.proposal<>'{}'")]:
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS dog_hint_notify_{event.lower()} AFTER {event} ON dog_review_cache
                WHEN {condition} BEGIN UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END""")


def signature(conn,did):
    row=conn.execute("SELECT d.catalog_id,d.subject_type,d.identity_id,d.confirmed,e.embedding,e.embedding_model,e.quality_score,e.crop_jpeg FROM clip_detections d LEFT JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=?",(did,)).fetchone()
    views=list(conn.execute('SELECT seconds,embedding,embedding_model,quality_score,crop_jpeg FROM identity_track_views WHERE detection_id=? ORDER BY seconds',(did,)))
    return hashlib.sha256(repr((tuple(row) if row else None,[tuple(v) for v in views])).encode()).hexdigest()


def store(conn,did,proposal,expected_revision=None,expected_signature=None):
    row=conn.execute("""SELECT d.catalog_id FROM clip_detections d WHERE d.detection_id=? AND d.subject_type='Dog' AND d.confirmed=0
        AND NOT EXISTS(SELECT 1 FROM subject_analysis_exclusions s WHERE s.detection_id=d.detection_id)
        AND NOT EXISTS(SELECT 1 FROM clip_unknown_motion n WHERE n.catalog_id=d.catalog_id)""",(did,)).fetchone()
    if row is None:return False
    rev=identity_revision(conn,row['catalog_id'])
    if (expected_revision is not None and rev!=expected_revision) or (expected_signature is not None and signature(conn,did)!=expected_signature):return False
    hint={}
    if proposal:
        iid=proposal.get('suggested_identity_id');tier=proposal.get('suggestion_tier')
        profile=conn.execute("SELECT name FROM identities WHERE identity_id=? AND active=1 AND subject_type='Dog'",(iid,)).fetchone()
        if profile and tier in ('possible','probable'):
            hint=dict(detection_id=did,suggested_identity_id=iid,suggested_name=profile[0],suggestion_tier=tier,review_required=True,confirmed=False)
    conn.execute("""INSERT INTO dog_review_cache(detection_id,proposal,gallery_revision) VALUES(?,?,?)
        ON CONFLICT(detection_id) DO UPDATE SET proposal=excluded.proposal,gallery_revision=excluded.gallery_revision,updated_at=CURRENT_TIMESTAMP
        WHERE dog_review_cache.proposal<>excluded.proposal OR dog_review_cache.gallery_revision<>excluded.gallery_revision""",(did,json.dumps(hint),rev))
    conn.execute('DELETE FROM dog_review_requests WHERE detection_id=?',(did,))
    return True


def attach(conn,rows):
    dogs=[x for x in rows if x['subject_type']=='Dog' and not x['confirmed']]
    if not dogs:return
    # Legacy unconfirmed model names remain stored but are never presented as assignments.
    for row in dogs:row.update(identity_id=None,name=None,review_required=True,refresh_pending=False)
    try:cached={r[0]:(json.loads(r[1]),r[2]) for r in conn.execute('SELECT detection_id,proposal,gallery_revision FROM dog_review_cache WHERE detection_id IN ('+','.join('?' for _ in dogs)+')',[r['detection_id'] for r in dogs])}
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):raise
        return
    active={r[0]:r[1] for r in conn.execute("SELECT identity_id,name FROM identities WHERE active=1 AND subject_type='Dog'")}
    excluded={r[0] for r in conn.execute('SELECT detection_id FROM subject_analysis_exclusions')}
    queued={r[0] for r in conn.execute('SELECT detection_id FROM dog_review_requests')}
    for row in dogs:
        row['refresh_pending']=row['detection_id'] in queued and row['detection_id'] not in excluded
        hint,epoch=cached.get(row['detection_id'],({},-1));iid=hint.get('suggested_identity_id')
        if epoch!=identity_revision(conn,row['catalog_id']) or iid not in active or row['detection_id'] in excluded:continue
        row.update(suggested_identity_id=iid,suggested_name=active[iid],suggestion_tier=hint['suggestion_tier'],review_required=True)


def request_review(conn,cid):
    """Queue only missing/stale Dog hints; never perform scoring on the API thread."""
    rev=identity_revision(conn,cid)
    conn.execute("""INSERT INTO dog_review_requests(detection_id,requested_at)
        SELECT d.detection_id,CAST(strftime('%s','now') AS REAL) FROM clip_detections d
        JOIN identity_evidence e USING(detection_id) LEFT JOIN dog_review_cache g USING(detection_id)
        WHERE d.catalog_id=? AND d.subject_type='Dog' AND d.confirmed=0 AND e.embedding IS NOT NULL
        AND d.detection_id NOT IN(SELECT detection_id FROM subject_analysis_exclusions)
        AND NOT EXISTS(SELECT 1 FROM clip_unknown_motion n WHERE n.catalog_id=d.catalog_id)
        AND (g.detection_id IS NULL OR g.gallery_revision<>?)
        ORDER BY d.detection_id LIMIT 100
        ON CONFLICT(detection_id) DO UPDATE SET requested_at=excluded.requested_at""",(cid,rev))
