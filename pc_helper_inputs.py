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

def reference_context(conn,cid):
    # Mirror the candidate pools read by subject_type_learning.load_references.
    # Individual names/samples and unrelated confirmed images are not PC inputs.
    eligible="""d.confirmed=1 AND d.subject_type=t.subject_type
        AND d.catalog_id<>? AND t.embedding IS NOT NULL AND t.quality_score>=.2
        AND d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
        AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions)"""
    versions=[r[0] for r in conn.execute('SELECT DISTINCT t.model_version FROM subject_type_samples t JOIN clip_detections d USING(detection_id) WHERE '+eligible+' AND t.model_version IS NOT NULL ORDER BY t.model_version',(cid,))]
    queries=[('holdouts','SELECT catalog_id FROM evaluation_excluded_clips ORDER BY catalog_id',()),
             ('holdout-history','SELECT catalog_id FROM evaluation_holdout_clips ORDER BY catalog_id',()),
             # These IDs/hashes also veto cached reviewed-image variants.
             ('excluded-crops','SELECT detection_id,crop_sha256 FROM subject_analysis_exclusions ORDER BY detection_id',())]
    for model in versions:
        for kind in ('Person','Cat','Dog','Vehicle'):
            queries.append(('type:'+model+':'+kind,
                '''SELECT t.detection_id,t.subject_type,t.embedding,t.model_version,t.quality_score,
                    t.crop_hash,d.catalog_id,e.crop_jpeg FROM subject_type_samples t
                    JOIN clip_detections d USING(detection_id) LEFT JOIN identity_evidence e USING(detection_id)
                    WHERE '''+eligible+''' AND t.model_version=? AND t.subject_type=?
                    ORDER BY t.detection_id DESC LIMIT ?''',(cid,model,kind,2048 if kind=='Cat' else 128)))
    return digest(conn,queries)

def context_parts(conn,cid):
    own=clip_context(conn,cid);refs=reference_context(conn,cid)
    return dict(clip_context_sha256=own,reference_context_sha256=refs,
                context_sha256=hashlib.sha256(('pc-inference-v2:'+own+':'+refs).encode()).hexdigest())

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
