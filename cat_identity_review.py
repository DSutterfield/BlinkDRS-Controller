"""Read-only household Cat review suggestions; only human assignments name a Cat."""
import json
import os
import subprocess
from pathlib import Path
import identity_candidates
from identity_store import connect, require_clip
from identity_analysis import normalized
from identity_candidates import rank_candidates


def suggest(conn,item,catalog_id):
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
            AND d.confirmed=1 AND d.subject_type='Cat' AND d.identity_id=s.identity_id
            AND s.identity_id=? AND d.catalog_id<>? AND s.model_version=? AND s.quality_score>=.2""",
            (profile['identity_id'],catalog_id,model)):
        try:sample=normalized(json.loads(row['embedding']))
        except (ValueError,TypeError):continue
        if len(sample)==384 and sum(a*b for a,b in zip(vector,sample))>=.55:support.add(row['catalog_id'])
    if len(support)<2:return None
    for row in conn.execute("""SELECT e.embedding FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
            AND d.confirmed=1 AND d.subject_type='Cat' AND d.identity_id IS NULL
            AND d.catalog_id<>? AND e.embedding_model=? AND e.quality_score>=.2 AND e.embedding IS NOT NULL""",(catalog_id,model)):
        try:sample=normalized(json.loads(row['embedding']))
        except (ValueError,TypeError):continue
        if len(sample)==384:
            score=sum(a*b for a,b in zip(vector,sample))
            if score>=.65 and score>=top['nearest']-.03:return None
    return dict(detection_id=item['detection_id'],suggested_identity_id=profile['identity_id'],suggested_name=profile['name'])


def _review_in_process(db_path,catalog_id):
    with connect(db_path) as conn:
        require_clip(conn,catalog_id)
        rows=conn.execute("""SELECT d.detection_id,d.subject_type,d.confirmed,d.identity_id,
            e.embedding,e.embedding_model,e.quality_score FROM clip_detections d
            JOIN identity_evidence e USING(detection_id) WHERE d.catalog_id=?
            AND d.subject_type='Cat' AND d.confirmed=0 AND d.identity_id IS NULL
            ORDER BY d.detection_id LIMIT 12""",(catalog_id,)).fetchall()
        return [result for row in rows if (result:=suggest(conn,dict(row),catalog_id)) is not None]


def review_suggestions(db_path,catalog_id):
    with connect(db_path) as conn:
        require_clip(conn,catalog_id)
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
                               '--catalog-id',str(catalog_id)],capture_output=True,text=True,
                              check=True,timeout=12,env=env)
        return json.loads(result.stdout)
    return _review_in_process(db_path,catalog_id)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--catalog-id',type=int,required=True)
    args=parser.parse_args()
    print(json.dumps(_review_in_process(args.db,args.catalog_id)))
