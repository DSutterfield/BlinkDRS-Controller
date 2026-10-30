"""Optional, derived review proposals; background scoring and cache-only reads.

This module never assigns an identity/Type or writes reference samples. A
reference epoch and exact target signature fence publication after scoring.
"""
import json
from name_first_review_config import enabled
from dog_review_cache import signature

POLICY = 'name-first-two-separated-v1'
SOFT_CAT_POLICY = 'name-first-soft-cat-preservation-v2'


def policy():
    from cat_name_preservation_config import enabled as soft_cat_enabled
    return SOFT_CAT_POLICY if soft_cat_enabled() else POLICY


def ensure(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS name_first_epoch(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL);
        INSERT OR IGNORE INTO name_first_epoch VALUES(1,0);
        CREATE TABLE IF NOT EXISTS name_first_cache(
            detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
            proposal TEXT NOT NULL, epoch INTEGER NOT NULL, signature TEXT NOT NULL, policy TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS name_first_requests(
            detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
            requested_at REAL NOT NULL);
    ''')
    # New unconfirmed recordings do not invalidate the reference gallery.
    for table in ('identity_samples','subject_type_samples','identities','evaluation_holdout_clips','subject_analysis_exclusions'):
        for event in ('INSERT','UPDATE','DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS nf_ref_{table}_{event.lower()}
                AFTER {event} ON {table} BEGIN UPDATE name_first_epoch SET revision=revision+1 WHERE id=1; END''')
    for event in ('INSERT','UPDATE','DELETE'):
        condition = 'OLD.confirmed=1 OR NEW.confirmed=1' if event=='UPDATE' else ('OLD.confirmed=1' if event=='DELETE' else 'NEW.confirmed=1')
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS nf_confirm_{event.lower()} AFTER {event} ON clip_detections
            WHEN {condition} BEGIN UPDATE name_first_epoch SET revision=revision+1 WHERE id=1; END''')
    # Confirmed Unknown evidence is also part of the name veto gallery.
    for table in ('identity_evidence',):
        for event in ('INSERT','UPDATE','DELETE'):
            ref='OLD' if event=='DELETE' else 'NEW'
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS nf_ref_{table}_{event.lower()} AFTER {event} ON {table}
                WHEN EXISTS(SELECT 1 FROM clip_detections WHERE detection_id={ref}.detection_id AND confirmed=1)
                BEGIN UPDATE name_first_epoch SET revision=revision+1 WHERE id=1; END''')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        for event in ('INSERT','UPDATE','DELETE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS nf_notify_{event.lower()} AFTER {event} ON name_first_cache
                BEGIN UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END''')


def epoch(conn):
    return conn.execute('SELECT revision FROM name_first_epoch WHERE id=1').fetchone()[0]


def target(conn,did):
    row=conn.execute('''SELECT d.*,e.embedding,e.embedding_model,e.quality_score,e.crop_jpeg
        FROM clip_detections d JOIN identity_evidence e USING(detection_id)
        WHERE d.detection_id=? AND d.confirmed=0 AND d.subject_type IN ('Unknown','Cat','Dog')
        AND NOT EXISTS(SELECT 1 FROM subject_analysis_exclusions s WHERE s.detection_id=d.detection_id)
        AND NOT EXISTS(SELECT 1 FROM clip_unknown_motion n WHERE n.catalog_id=d.catalog_id)''',(did,)).fetchone()
    if row is None:return None
    item=dict(row)
    item['views']=[dict(v) for v in conn.execute('''SELECT seconds,crop_jpeg,embedding,embedding_model,quality_score
        FROM identity_track_views WHERE detection_id=? ORDER BY seconds''',(did,))]
    return item


def fresh(conn,did):
    hit=conn.execute('SELECT * FROM name_first_cache WHERE detection_id=?',(did,)).fetchone()
    return bool(hit and hit['policy']==policy() and hit['epoch']==epoch(conn) and hit['signature']==signature(conn,did))


def store(conn,did,proposal,expected_epoch,expected_signature,expected_policy=None):
    item=target(conn,did)
    if item is None or epoch(conn)!=expected_epoch or signature(conn,did)!=expected_signature:return False
    if expected_policy is not None and policy()!=expected_policy:return False
    if proposal:
        profile=conn.execute("SELECT name,subject_type FROM identities WHERE identity_id=? AND active=1",(proposal['suggested_identity_id'],)).fetchone()
        if not profile or profile['subject_type'] not in ('Cat','Dog') or item['subject_type'] not in ('Unknown',profile['subject_type']):return False
        proposal=dict(proposal,suggested_name=profile['name'],suggested_subject_type=profile['subject_type'],review_required=True,confirmed=False,detection_id=did)
    conn.execute('''INSERT INTO name_first_cache VALUES(?,?,?,?,?) ON CONFLICT(detection_id) DO UPDATE SET
        proposal=excluded.proposal,epoch=excluded.epoch,signature=excluded.signature,policy=excluded.policy''',
        (did,json.dumps(proposal or {}),expected_epoch,expected_signature,policy()))
    if not proposal and item['subject_type']=='Cat' and policy()==SOFT_CAT_POLICY:
        # A fully scored withdrawal must not expose an older derived Cat hint.
        # Stored identities/Types and confirmations remain untouched.
        conn.execute('DELETE FROM cat_review_cache WHERE detection_id=?',(did,))
    conn.execute('DELETE FROM name_first_requests WHERE detection_id=?',(did,))
    return True


def attach(conn,rows):
    if not enabled():return
    for row in rows:
        did=row['detection_id']
        if row.get('confirmed') or row.get('analysis_excluded') or row['subject_type'] not in ('Unknown','Cat','Dog'):continue
        if not fresh(conn,did) or target(conn,did) is None:continue
        proposal=json.loads(conn.execute('SELECT proposal FROM name_first_cache WHERE detection_id=?',(did,)).fetchone()[0])
        if proposal:row.update(proposal,identity_id=None,name=None)


def request_review(conn,cid):
    if not enabled():return
    for row in conn.execute("SELECT detection_id FROM clip_detections WHERE catalog_id=? AND confirmed=0 AND subject_type IN ('Unknown','Cat','Dog') ORDER BY detection_id LIMIT 100",(cid,)).fetchall():
        did=row[0]
        if target(conn,did) is not None and not fresh(conn,did):
            conn.execute("INSERT INTO name_first_requests VALUES(?,CAST(strftime('%s','now') AS REAL)) ON CONFLICT(detection_id) DO NOTHING",(did,))


def references(conn,model,cid):
    from identity_analysis import normalized
    from subject_type_learning import diverse_cat_references
    from subject_type_check import prepare_references
    out=[]
    for kind in ('Person','Cat','Dog'):
        rows=conn.execute('''SELECT t.*,d.catalog_id FROM subject_type_samples t JOIN clip_detections d USING(detection_id)
            WHERE d.catalog_id NOT IN(SELECT catalog_id FROM evaluation_excluded_clips)
            AND d.catalog_id<>? AND d.confirmed=1 AND d.subject_type=t.subject_type
            AND d.detection_id NOT IN(SELECT detection_id FROM subject_analysis_exclusions)
            AND t.subject_type=? AND t.model_version=? AND t.embedding IS NOT NULL AND t.quality_score>=.2
            ORDER BY t.detection_id DESC LIMIT ?''',(cid,kind,model,2048 if kind=='Cat' else 128))
        usable=[]
        for row in rows:
            try:
                item=dict(row);item['vector']=normalized(json.loads(item['embedding']))
                if len(item['vector'])==384:usable.append(item)
            except (ValueError,TypeError):continue
        out.extend(diverse_cat_references(usable) if kind=='Cat' else usable)
    return prepare_references(out)


def score(conn,item):
    if item.get('confirmed') or item.get('analysis_excluded') or item.get('subject_type') not in ('Unknown','Cat','Dog'):return None
    from cat_name_preservation_config import enabled as soft_cat_enabled
    from name_first_evidence import build,prepare
    core=references(conn,item.get('embedding_model'),item['catalog_id'])
    if soft_cat_enabled() and item.get('subject_type') in ('Cat','Unknown'):
        from cat_name_preservation import recommend
        return recommend(prepare(conn,item,item['catalog_id'],core),item)
    return build(conn,item,item['catalog_id'],core)(item)


def backfill(db_path,limit=1):
    if not enabled():return []
    from identity_store import connect
    completed=[]
    with connect(db_path) as conn:
        conn.execute("DELETE FROM name_first_requests WHERE requested_at<CAST(strftime('%s','now') AS REAL)-1800")
        rows=conn.execute('''SELECT d.detection_id FROM clip_detections d JOIN clips c ON c.id=d.catalog_id
            JOIN identity_evidence e USING(detection_id) LEFT JOIN name_first_requests q USING(detection_id)
            WHERE d.confirmed=0 AND d.subject_type IN ('Unknown','Cat','Dog') AND c.local_present=1
            AND e.embedding IS NOT NULL AND (q.detection_id IS NOT NULL OR d.catalog_id IN
                (SELECT id FROM clips ORDER BY captured_at DESC LIMIT 100))
            ORDER BY q.requested_at IS NULL,q.requested_at DESC,d.detection_id DESC LIMIT 200''').fetchall()
        conn.commit()
        for row in rows:
            did=row[0]
            # Read snapshot only during numerical work. Never reserve a writer.
            conn.execute('BEGIN')
            try:
                item=target(conn,did)
                if item is None or fresh(conn,did):continue
                revision=epoch(conn);token=signature(conn,did);version=policy()
                proposal=score(conn,item)
            finally:conn.rollback()
            conn.execute('BEGIN IMMEDIATE')
            if store(conn,did,proposal,revision,token,expected_policy=version):completed.append(did)
            conn.commit()
            if len(completed)>=max(1,min(limit,2)):break
    return completed
