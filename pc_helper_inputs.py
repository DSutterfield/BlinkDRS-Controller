"""Separate PC inference inputs from the Pi's later identity-name scoring."""
import hashlib

def digest(conn,queries):
    h=hashlib.sha256()
    for label,sql,args in queries:
        h.update(label.encode())
        for row in conn.execute(sql,args):h.update(repr(tuple(row)).encode())
    return h.hexdigest()

def clip_context(conn,cid):
    return digest(conn,[
        ('clip','SELECT id,video_path,local_present,device_id,captured_at FROM clips WHERE id=?',(cid,)),
        ('confirmed','SELECT * FROM clip_detections WHERE catalog_id=? AND confirmed=1 ORDER BY detection_id',(cid,)),
        ('motion','SELECT * FROM clip_unknown_motion WHERE catalog_id=?',(cid,)),
        ('removed','SELECT * FROM identity_rejections WHERE catalog_id=? ORDER BY detection_key',(cid,)),
        ('confirmed-images','SELECT e.detection_id,e.crop_jpeg FROM identity_evidence e JOIN clip_detections d USING(detection_id) WHERE d.catalog_id=? AND d.confirmed=1 ORDER BY e.detection_id',(cid,)),
        ('confirmed-views','SELECT v.* FROM identity_track_views v JOIN clip_detections d USING(detection_id) WHERE d.catalog_id=? AND d.confirmed=1 ORDER BY v.view_id',(cid,))])

# Conservative candidate pool: the same capped inputs the existing Type loader
# can select. No inference/ranking/threshold changes are made here.
ELIGIBLE="""d.confirmed=1 AND d.subject_type=t.subject_type AND d.catalog_id<>?
 AND t.embedding IS NOT NULL AND t.quality_score>=.2
 AND d.catalog_id NOT IN(SELECT catalog_id FROM evaluation_excluded_clips)
 AND d.detection_id NOT IN(SELECT detection_id FROM subject_analysis_exclusions)"""
REFERENCE_ROW="""SELECT t.detection_id,t.subject_type,t.embedding,t.model_version,
 t.quality_score,t.crop_hash,d.catalog_id,e.crop_jpeg,
 EXISTS(SELECT 1 FROM subject_analysis_exclusions x WHERE x.crop_sha256=t.crop_hash)
 FROM subject_type_samples t JOIN clip_detections d USING(detection_id)
 LEFT JOIN identity_evidence e USING(detection_id) WHERE """+ELIGIBLE


def _row_hash(row):
    return hashlib.sha256(repr(tuple(row)).encode()).hexdigest()


def _policy(conn):
    # Durable definitions survive retention. Dynamic neighbor membership does not.
    return digest(conn,[('protected-policy',
        'SELECT blink_media_id,catalog_id,dataset_id,device_id,captured_at FROM evaluation_holdout_clips ORDER BY blink_media_id',())])


def _review_asset():
    import json
    from pathlib import Path
    path=Path(__file__).resolve().parent/'ai-models/cat-reviewed-examples-v1.json'
    if not path.is_file():return None,[]
    raw=path.read_bytes()
    return hashlib.sha256(raw).hexdigest(),json.loads(raw)['examples'][:16]


def _allowed_reviewed(conn,record):
    did=record.get('detection_id');cid=record['catalog_id'];crop=record['crop_hash']
    return (record['subject_type']=='Cat' and not conn.execute(
        'SELECT 1 FROM evaluation_excluded_clips WHERE catalog_id=? UNION ALL SELECT 1 FROM evaluation_holdout_clips WHERE catalog_id=?',(cid,cid)).fetchone()
        and not conn.execute('SELECT 1 FROM subject_analysis_exclusions WHERE detection_id=? OR crop_sha256=?',(did,crop)).fetchone())


def reference_guard(conn,cid):
    asset,records=_review_asset();rows=[]
    versions=[r[0] for r in conn.execute('SELECT DISTINCT t.model_version FROM subject_type_samples t JOIN clip_detections d USING(detection_id) WHERE '+ELIGIBLE+' AND t.model_version IS NOT NULL ORDER BY t.model_version',(cid,))]
    for model in versions:
        for kind in ('Person','Cat','Dog','Vehicle'):
            for row in conn.execute(REFERENCE_ROW+' AND t.model_version=? AND t.subject_type=? ORDER BY t.detection_id DESC LIMIT ?',(cid,model,kind,2048 if kind=='Cat' else 128)):
                rows.append([row[0],_row_hash(row)])
    reviewed=[dict(detection_id=r.get('detection_id'),catalog_id=r['catalog_id'],crop_hash=r['crop_hash'],subject_type='Cat') for r in records if _allowed_reviewed(conn,r)]
    return dict(version=1,policy=_policy(conn),rows=rows,review_asset=asset,reviewed=reviewed)


def validate_reference_guard(conn,cid,guard):
    """New references cannot replace a running job's pinned inputs.

    Revalidate EVERY pinned candidate, even if new rows displace it from today's
    capped pool. A changed/retracted/newly excluded input still invalidates work.
    """
    if guard.get('version')!=1 or _policy(conn)!=guard['policy']:return False
    asset,_=_review_asset()
    if asset!=guard['review_asset']:return False
    expected=dict(guard['rows']);found={};ids=list(expected)
    for offset in range(0,len(ids),800):
        batch=ids[offset:offset+800]
        for row in conn.execute(REFERENCE_ROW+' AND t.detection_id IN ('+','.join('?'*len(batch))+')',(cid,*batch)):
            found[row[0]]=_row_hash(row)
    return found==expected and all(_allowed_reviewed(conn,r) for r in guard['reviewed'])


def guard_context(guard):
    import json
    return hashlib.sha256(json.dumps(guard,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def reference_context(conn,cid):
    return guard_context(reference_guard(conn,cid))


def context_parts(conn,cid):
    own=clip_context(conn,cid);refs=reference_context(conn,cid)
    return dict(clip_context_sha256=own,reference_context_sha256=refs,
                context_sha256=hashlib.sha256(('pc-inference-v3:'+own+':'+refs).encode()).hexdigest())

def context(conn,cid):return context_parts(conn,cid)['context_sha256']

def publication_context(conn):
    # A fresh fence for short Pi-side identity scoring, not four-minute PC work.
    return digest(conn,[
        ('profiles','SELECT identity_id,name,subject_type,active FROM identities ORDER BY identity_id',()),
        ('identity-samples','SELECT s.*,d.catalog_id,d.confirmed,d.identity_id,d.subject_type FROM identity_samples s LEFT JOIN clip_detections d USING(detection_id) ORDER BY s.sample_id',()),
        ('unknowns',"""SELECT d.detection_id,d.catalog_id,d.subject_type,e.embedding,e.embedding_model,e.quality_score
            FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.confirmed=1 AND d.identity_id IS NULL AND d.subject_type IN ('Cat','Dog')
            AND e.embedding IS NOT NULL AND e.quality_score>=.2
            AND d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
            AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions)
            ORDER BY d.detection_id""",())])
