"""Household Cat and opt-in Dog review suggestions; only human assignments name a Cat."""
import json
import os
import subprocess
from pathlib import Path
import identity_candidates
from identity_store import connect, require_clip
from identity_analysis import normalized
from identity_candidates import rank_candidates


def _legacy_suggest(conn,item,catalog_id):
    if item['subject_type']!='Cat' or item.get('confirmed') or item.get('identity_id') is not None:
        return None
    model=item.get('embedding_model')
    if not model or not model.startswith('dinov2-small-cls-v1:') or (item.get('quality_score') or 0)<.2:
        return None
    try:
        vector=normalized(json.loads(item['embedding']))
    except (ValueError,TypeError):
        return None
    if len(vector)!=384:return None
    ranks=rank_candidates(conn,'Cat',model,vector,catalog_id)
    if len(ranks)<2:return None
    top,runner=ranks[:2]
    if (top['score']<.60
            or top['score']-runner['score']<.20 or top['nearest']<.55):return None
    profile=conn.execute("SELECT identity_id,name FROM identities WHERE active=1 AND subject_type='Cat' AND identity_id=?",(top['identity_id'],)).fetchone()
    if profile is None:return None
    support=set()
    for row in conn.execute("""SELECT d.catalog_id,s.embedding FROM clip_detections d
            JOIN identity_samples s USING(detection_id)
            WHERE d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
            AND d.confirmed=1 AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions) AND d.subject_type='Cat' AND d.identity_id=s.identity_id
            AND s.identity_id=? AND d.catalog_id<>? AND s.model_version=? AND s.quality_score>=.2""",
            (profile['identity_id'],catalog_id,model)):
        try:sample=normalized(json.loads(row['embedding']))
        except (ValueError,TypeError):continue
        if len(sample)==384 and sum(a*b for a,b in zip(vector,sample))>=.55:support.add(row['catalog_id'])
    if len(support)<2:return None
    for row in conn.execute("""SELECT e.embedding FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
            AND d.confirmed=1 AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions) AND d.subject_type='Cat' AND d.identity_id IS NULL
            AND d.catalog_id<>? AND e.embedding_model=? AND e.quality_score>=.2 AND e.embedding IS NOT NULL""",(catalog_id,model)):
        try:sample=normalized(json.loads(row['embedding']))
        except (ValueError,TypeError):continue
        if len(sample)==384:
            score=sum(a*b for a,b in zip(vector,sample))
            if score>=.65 and score>=top['nearest']-.03:return None
    return dict(detection_id=item['detection_id'],suggested_identity_id=profile['identity_id'],suggested_name=profile['name'])


def suggest(conn,item,catalog_id):
    if item.get('subject_type')=='Dog':
        from dog_review_evidence import recommend
        return recommend(conn,item,catalog_id)
    from cat_review_evidence import recommend
    return recommend(conn,item,catalog_id)


def _dog_refresh(conn,item,cid):
    from dog_review_revision import revision as identity_revision
    from dog_review_cache import signature,store
    conn.commit()  # Never hold a writer while doing numerical matching.
    did=item['detection_id'];epoch=identity_revision(conn,cid);token=signature(conn,did)
    row=conn.execute("""SELECT d.detection_id,d.catalog_id,d.subject_type,d.confirmed,d.identity_id,e.embedding,e.embedding_model,e.quality_score
        FROM clip_detections d JOIN identity_evidence e USING(detection_id)
        WHERE d.detection_id=? AND d.catalog_id=? AND d.subject_type='Dog' AND d.confirmed=0""",(did,cid)).fetchone()
    if row is None:return None
    current=dict(row);current['views']=[dict(v) for v in conn.execute('SELECT seconds,crop_jpeg,embedding,embedding_model,quality_score FROM identity_track_views WHERE detection_id=? ORDER BY seconds',(did,))]
    if signature(conn,did)!=token or identity_revision(conn,cid)!=epoch:return None
    result=suggest(conn,current,cid)
    conn.execute('BEGIN IMMEDIATE')
    ok=store(conn,did,result,expected_revision=epoch,expected_signature=token)
    conn.commit()
    return result if ok else None


def _review_in_process(db_path,catalog_id,dog_budget=2):
    from dog_review_config import enabled as dog_pilot
    with connect(db_path) as conn:
        require_clip(conn,catalog_id)
        if conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?',(catalog_id,)).fetchone():return []
        rows=conn.execute("""SELECT d.detection_id,d.catalog_id,d.subject_type,d.confirmed,d.identity_id,
            e.embedding,e.embedding_model,e.quality_score FROM clip_detections d
            JOIN identity_evidence e USING(detection_id) WHERE d.catalog_id=? AND d.confirmed=0
            AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions)
            AND ((d.subject_type='Cat' AND d.identity_id IS NULL) OR (d.subject_type='Dog' AND ?))
            ORDER BY d.detection_id LIMIT 100""",(catalog_id,int(dog_pilot()))).fetchall()
        output=[];cat_budget=12
        from cat_review_cache import store
        from dog_review_cache import attach as attach_dog
        for row in rows:
            item=dict(row)
            item['views']=[dict(v) for v in conn.execute('SELECT seconds,crop_jpeg,embedding,embedding_model,quality_score FROM identity_track_views WHERE detection_id=? ORDER BY seconds',(item['detection_id'],))]
            if item['subject_type']=='Dog':
                cached=[dict(row)];attach_dog(conn,cached)
                from dog_review_revision import revision as identity_revision
                hit=conn.execute('SELECT gallery_revision FROM dog_review_cache WHERE detection_id=?',(item['detection_id'],)).fetchone()
                if hit and hit[0]==identity_revision(conn,catalog_id):
                    if cached[0].get('suggested_identity_id'):
                        output.append(dict({k:cached[0][k] for k in ['detection_id','suggested_identity_id','suggested_name','suggestion_tier','review_required']},confirmed=False))
                    else:output.append(dict(detection_id=item['detection_id'],suggested_identity_id=None,suggested_name=None,suggestion_tier=None,review_required=True,confirmed=False))
                    continue
                if dog_budget<=0:
                    output.append(dict(detection_id=item['detection_id'],suggested_identity_id=None,suggested_name=None,suggestion_tier=None,review_required=True,confirmed=False));continue
                dog_budget-=1;result=_dog_refresh(conn,item,catalog_id)
            else:
                if cat_budget<=0:continue
                cat_budget-=1;result=suggest(conn,item,catalog_id);store(conn,item['detection_id'],result);conn.commit()
            if result:output.append(result)
            elif item['subject_type']=='Dog' and conn.execute("SELECT 1 FROM clip_detections WHERE detection_id=? AND subject_type='Dog' AND confirmed=0",(item['detection_id'],)).fetchone():
                output.append(dict(detection_id=item['detection_id'],suggested_identity_id=None,suggested_name=None,suggestion_tier=None,review_required=True,confirmed=False))
        return output


def backfill_requested(db_path,limit=1):
    name_done=[]
    from name_first_review_config import enabled as name_first_enabled
    if name_first_enabled():
        from name_first_review import backfill
        name_done=backfill(db_path,limit)
    from dog_review_config import enabled
    if not enabled():return name_done
    with connect(db_path) as conn:
        conn.execute("DELETE FROM dog_review_requests WHERE requested_at<CAST(strftime('%s','now') AS REAL)-1800 OR NOT EXISTS(SELECT 1 FROM clip_detections d JOIN clips c ON c.id=d.catalog_id WHERE d.detection_id=dog_review_requests.detection_id AND d.subject_type='Dog' AND d.confirmed=0 AND c.local_present=1 AND d.detection_id NOT IN(SELECT detection_id FROM subject_analysis_exclusions) AND NOT EXISTS(SELECT 1 FROM clip_unknown_motion n WHERE n.catalog_id=d.catalog_id))")
        rows=conn.execute("""SELECT d.detection_id,d.catalog_id FROM dog_review_requests q
            JOIN clip_detections d USING(detection_id) JOIN identity_evidence e USING(detection_id)
            WHERE e.embedding IS NOT NULL ORDER BY q.requested_at DESC,d.detection_id LIMIT ?""",(max(0,min(limit,2)),)).fetchall()
        conn.commit()
        attempted=[]
        for row in rows:
            _dog_refresh(conn,dict(row),row['catalog_id']);attempted.append(row['detection_id'])
        return name_done+attempted


def backfill_pending(db_path,limit=2):
    requested=backfill_requested(db_path,min(limit,2))
    limit=max(0,min(limit,2)-len(requested))
    if not limit:return
    from dog_review_config import enabled as dog_pilot
    from dog_review_revision import revision as identity_revision
    with connect(db_path) as conn:
        conn.commit()
        epoch=identity_revision(conn)
        rows=conn.execute("""SELECT d.detection_id,d.catalog_id,d.subject_type,d.confirmed,d.identity_id,
            e.embedding,e.embedding_model,e.quality_score FROM clip_detections d
            JOIN identity_evidence e USING(detection_id) LEFT JOIN cat_review_cache r USING(detection_id)
            LEFT JOIN dog_review_cache g USING(detection_id)
            LEFT JOIN dog_review_source_revisions v ON v.catalog_id=d.catalog_id
            WHERE d.confirmed=0 AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions)
            AND ((d.subject_type='Cat' AND d.identity_id IS NULL AND r.detection_id IS NULL)
              OR (d.subject_type='Dog' AND ? AND (g.detection_id IS NULL OR g.gallery_revision<>(?-COALESCE(v.revision,0)))))
            AND e.embedding IS NOT NULL AND d.catalog_id IN (SELECT id FROM clips ORDER BY captured_at DESC LIMIT 500)
            ORDER BY d.detection_id DESC LIMIT ?""",(int(dog_pilot()),epoch,limit)).fetchall()
        for row in rows:
            item=dict(row);cid=item['catalog_id'];item['views']=[dict(v) for v in conn.execute(
                'SELECT seconds,crop_jpeg,embedding,embedding_model,quality_score FROM identity_track_views WHERE detection_id=? ORDER BY seconds',(item['detection_id'],))]
            if item['subject_type']=='Dog':_dog_refresh(conn,item,cid)
            else:
                from cat_review_cache import store
                store(conn,item['detection_id'],suggest(conn,item,cid));conn.commit()


def review_suggestions(db_path,catalog_id):
    with connect(db_path) as conn:
        require_clip(conn,catalog_id)
        from dog_review_config import enabled as dog_pilot
        from name_first_review_config import enabled as name_first_enabled
        from cat_name_preservation_config import enabled as soft_cat_enabled
        if dog_pilot() or name_first_enabled():
            # Review is cache-only while the pilot is enabled. Background work
            # and publication own scoring, including mixed Cat/Dog clips.
            from identity_store import clip_identities
            from dog_review_cache import request_review
            request_review(conn,catalog_id)
            if name_first_enabled():
                from name_first_review import request_review as request_names
                request_names(conn,catalog_id)
            output=[]
            for row in clip_identities(conn,catalog_id):
                if row['confirmed'] or row.get('analysis_excluded'):continue
                if row.get('suggested_subject_type') or (name_first_enabled() and (row['subject_type']=='Unknown' or (soft_cat_enabled() and row['subject_type']=='Cat'))):
                    output.append(dict(detection_id=row['detection_id'],suggested_identity_id=row.get('suggested_identity_id'),suggested_name=row.get('suggested_name'),suggested_subject_type=row.get('suggested_subject_type'),suggestion_tier=row.get('suggestion_tier'),review_required=True,confirmed=False))
                elif row['subject_type']=='Dog':
                    output.append(dict(detection_id=row['detection_id'],suggested_identity_id=row.get('suggested_identity_id'),suggested_name=row.get('suggested_name'),suggestion_tier=row.get('suggestion_tier'),review_required=True,confirmed=False,refresh_pending=row.get('refresh_pending',False)))
                elif row['subject_type']=='Cat' and row.get('suggested_identity_id'):
                    output.append(dict(detection_id=row['detection_id'],suggested_identity_id=row['suggested_identity_id'],suggested_name=row['suggested_name'],suggestion_tier=row['suggestion_tier'],review_required=True,confirmed=False))
            return output
    try:
        import numpy
    except ModuleNotFoundError as exc:
        if exc.name!='numpy':raise
        root=Path(identity_candidates.__file__).resolve().parent
        python=root/'.venv-ai/bin/python'
        if not python.is_file():raise RuntimeError('Local AI matching environment is unavailable')
        env=dict(os.environ,OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
        env['PYTHONPATH']=str(Path(__file__).resolve().parent)+os.pathsep+str(root)
        result=subprocess.run([str(python),str(Path(__file__).resolve()),'--db',str(db_path),
                               '--catalog-id',str(catalog_id),'--cached-dogs'],capture_output=True,text=True,
                              check=True,timeout=12,env=env)
        return json.loads(result.stdout)
    return _review_in_process(db_path,catalog_id,dog_budget=0)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--catalog-id',type=int,required=True)
    parser.add_argument('--cached-dogs',action='store_true')
    args=parser.parse_args()
    print(json.dumps(_review_in_process(args.db,args.catalog_id,dog_budget=0 if args.cached_dogs else 2)))
