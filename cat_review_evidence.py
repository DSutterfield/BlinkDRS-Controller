"""Read-only feature/rank aggregation for review hints, with separated views."""
import json,hashlib,io
from identity_analysis import normalized
from identity_candidates import rank_candidates
from cat_review_policy import choose

def recommend(conn,item,cid,ranks=None):
    if item.get('subject_type')!='Cat' or item.get('confirmed') or item.get('identity_id') is not None:return None
    model=item.get('embedding_model')
    if not model or not model.startswith('dinov2-small-cls-v1:'):return None
    def vector(v):
        try:return normalized(json.loads(v['embedding']) if isinstance(v['embedding'],str) else v['embedding'])
        except (KeyError,ValueError,TypeError):return []
    if len(vector(item))!=384 or (item.get('quality_score') or 0)<.2:return None
    samples={};unknown=[]
    for r in conn.execute('''SELECT s.identity_id,s.embedding,d.catalog_id FROM identity_samples s
        JOIN identities i USING(identity_id) LEFT JOIN clip_detections d USING(detection_id)
        WHERE i.active=1 AND i.subject_type='Cat' AND s.model_version=? AND s.quality_score>=.2
        AND (s.detection_id IS NULL OR (d.confirmed=1 AND d.identity_id=s.identity_id
        AND d.catalog_id<>? AND d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)))''',(model,cid)):
        try:u=normalized(json.loads(r['embedding']))
        except (ValueError,TypeError):continue
        if len(u)==384:samples.setdefault(r['identity_id'],[]).append((r['catalog_id'],u))
    for r in conn.execute('''SELECT e.embedding FROM clip_detections d JOIN identity_evidence e USING(detection_id)
        WHERE d.confirmed=1 AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions) AND d.subject_type='Cat' AND d.identity_id IS NULL AND d.catalog_id<>?
        AND d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
        AND e.embedding_model=? AND e.quality_score>=.2 AND e.embedding IS NOT NULL''',(cid,model)):
        try:u=normalized(json.loads(r[0]))
        except (ValueError,TypeError):continue
        if len(u)==384:unknown.append(u)
    def evidence(v,rank=None):
        u=vector(v)
        if len(u)!=384 or (v.get('quality_score') or 0)<.2 or v.get('embedding_model')!=model:return None
        rank=rank if rank is not None else rank_candidates(conn,'Cat',model,u,cid)
        if len(rank)<2:return None
        top=rank[0];sources={i:{source for source,sample in refs if source is not None and sum(a*b for a,b in zip(u,sample))>=.55} for i,refs in samples.items()}
        veto=any((s:=sum(a*b for a,b in zip(u,sample)))>=.65 and s>=top['nearest']-.03 for sample in unknown)
        qualified=top['identity_id'] if not veto and top['score']>=.60 and top['score']-rank[1]['score']>=.20 and top['nearest']>=.55 and len(sources.get(top['identity_id'],set()))>=2 else None
        return dict(ranks=rank,sources=sources,quality=v['quality_score'],veto=veto,qualified=qualified)
    canonical=evidence(item,ranks)
    if canonical is None:return None
    chosen=[];hashes=set()
    for v in sorted(item.get('views',[]),key=lambda v:v['seconds']):
        if not v.get('crop_jpeg') or any(abs(v['seconds']-t)<.5 for t,_,_ in chosen):continue
        digest=hashlib.sha256(v['crop_jpeg']).digest()
        if digest in hashes:continue
        from PIL import Image
        pixels=list(Image.open(io.BytesIO(v['crop_jpeg'])).convert('L').resize((9,8)).getdata());dh=sum(int(pixels[y*9+x]>pixels[y*9+x+1])<<(63-y*8-x) for y in range(8) for x in range(8))
        if any((dh^other).bit_count()<4 for _,other,_ in chosen):continue
        e=evidence(v)
        if e:chosen.append((v['seconds'],dh,e));hashes.add(digest)
    selected=choose(canonical,[e for _,_,e in chosen])
    if selected is None:return None
    iid,tier=selected;profile=conn.execute("SELECT name FROM identities WHERE identity_id=? AND active=1 AND subject_type='Cat'",(iid,)).fetchone()
    if profile is None:return None
    return dict(detection_id=item.get('detection_id',0),suggested_identity_id=iid,suggested_name=profile[0],suggestion_tier=tier,review_required=True)
