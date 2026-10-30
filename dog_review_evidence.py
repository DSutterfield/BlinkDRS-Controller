"""Dog review suggestions from existing evidence only; never assigned identities."""
import hashlib,io,json,math
from PIL import Image,UnidentifiedImageError
from identity_analysis import normalized
from identity_candidates import rank_candidates
from dog_identity import _unknown_veto
from dog_review_policy import choose


def recommend(conn,item,catalog_id,ranks=None):
    if item.get('subject_type')!='Dog' or item.get('confirmed') or item.get('analysis_excluded'):return None
    did=item.get('detection_id',0)
    if conn.execute('SELECT 1 FROM subject_analysis_exclusions WHERE detection_id=?',(did,)).fetchone():return None
    if conn.execute('SELECT 1 FROM clip_unknown_motion WHERE catalog_id=?',(catalog_id,)).fetchone():return None
    model=item.get('embedding_model')
    if not isinstance(model,str) or not model.startswith('dinov2-small-cls-v1:'):return None
    def vector(v):
        if v.get('embedding_model')!=model:return []
        q=v.get('quality_score')
        if not isinstance(q,(int,float)) or not math.isfinite(q) or q<.2:return []
        try:u=normalized(json.loads(v['embedding']) if isinstance(v.get('embedding'),str) else v.get('embedding'))
        except (ValueError,TypeError,KeyError):return []
        return u if len(u)==384 else []
    if not vector(item):return None
    samples={}
    for r in conn.execute("""SELECT s.identity_id,s.embedding,d.catalog_id FROM identity_samples s
        JOIN identities i USING(identity_id) JOIN clip_detections d USING(detection_id)
        WHERE i.active=1 AND i.subject_type='Dog' AND d.subject_type='Dog' AND d.confirmed=1
        AND d.identity_id=s.identity_id AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions)
        AND d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips)
        AND d.catalog_id<>? AND s.model_version=? AND s.quality_score>=.2""",(catalog_id,model)):
        try:u=normalized(json.loads(r['embedding']))
        except (ValueError,TypeError):continue
        if len(u)==384:samples.setdefault(r['identity_id'],[]).append((r['catalog_id'],u))
    def evidence(v,rank=None,canonical=False):
        u=vector(v)
        if not u:return None
        rank=rank if rank is not None else rank_candidates(conn,'Dog',model,u,catalog_id)
        if len(rank)<2:return None
        top=rank[0];sources={i:{cid for cid,z in refs if sum(a*b for a,b in zip(u,z))>=.60} for i,refs in samples.items()}
        veto=_unknown_veto(conn,model,u,catalog_id,top['nearest'])
        qualified=top['identity_id'] if not veto and top['score']>=.8 and top['score']-rank[1]['score']>=(.4 if canonical else .3) and top['nearest']>=.65 and len(sources.get(top['identity_id'],set()))>=(3 if canonical else 2) else None
        return dict(ranks=rank,sources=sources,quality=v['quality_score'],veto=veto,qualified=qualified)
    anchor=evidence(item,ranks,True)
    if anchor is None:return None
    selected=[];seen=set()
    valid=[v for v in item.get('views',[])[:4] if isinstance(v.get('seconds'),(int,float)) and math.isfinite(v['seconds']) and v['seconds']>=0]
    for v in sorted(valid,key=lambda x:x['seconds']):
        if not v.get('crop_jpeg') or any(abs(v['seconds']-t)<.5 for t,_,_ in selected):continue
        h=hashlib.sha256(v['crop_jpeg']).digest()
        if h in seen:continue
        try:
            with Image.open(io.BytesIO(v['crop_jpeg'])) as im:p=list(im.convert('L').resize((9,8)).getdata())
        except (OSError,ValueError,UnidentifiedImageError):continue
        dh=sum(int(p[y*9+x]>p[y*9+x+1])<<(63-y*8-x) for y in range(8) for x in range(8))
        if any((dh^other).bit_count()<4 for _,other,_ in selected):continue
        e=evidence(v)
        if e:selected.append((v['seconds'],dh,e));seen.add(h)
    result=choose(anchor,[e for _,_,e in selected])
    if result is None:return None
    iid,tier=result
    profile=conn.execute("SELECT name FROM identities WHERE identity_id=? AND active=1 AND subject_type='Dog'",(iid,)).fetchone()
    if profile is None:return None
    return dict(detection_id=did,suggested_identity_id=iid,suggested_name=profile[0],suggestion_tier=tier,review_required=True,confirmed=False)
