"""Frozen two-view name-first scorer. Existing DINO features only, no learning.

Returns a separate review proposal; never changes a detection or accepted Type.
Numerical policy ported from name-first-test/run.py, 2026-10-30.
"""
import collections,hashlib,io,json,math
from dataclasses import dataclass
import numpy as np
from PIL import Image
import identity_candidates as ranker
from identity_analysis import normalized
from cat_review_policy import choose as catchoose
from dog_review_policy import choose as dogchoose
from subject_type_check import rank_types

def vec(v):
    try:return normalized(json.loads(v) if isinstance(v,str) else v)
    except (ValueError,TypeError):return []

@dataclass(frozen=True)
class PreparedReview:
    """One immutable gallery snapshot shared by guarded review-only policies."""
    evaluate: object
    evidence: object
    separated: object
    hint: object
    type_ranks: object
    pooled_match: object
    profiles: object

    def __call__(self,item):
        return self.evaluate(item)


def prepare(c,item,cid,core):
    return build(c,item,cid,core,prepared=True)


def build(c,item,cid,core,*,prepared=False):
    model=item.get('embedding_model')
    if not isinstance(model,str) or not model.startswith('dinov2-small-cls-v1:') or len(vec(item.get('embedding')))!=384:
        return None if prepared else lambda item: None
    heads={};golden=[]
    for kind in ['Dog','Cat']:

     u=vec(item['embedding']);head=ranker.rank_candidates(c,kind,model,u,cid,prepared_head=True)
     if not head:return None if prepared else lambda item: None
     names,xm,ym,coef,rows=head;supportrefs={};unknown=[]
     for r in c.execute('''select s.identity_id,s.embedding,d.catalog_id from identity_samples s join identities i using(identity_id) left join clip_detections d using(detection_id) where i.active=1 and i.subject_type=? and s.model_version=? and s.quality_score>=.2 and (s.detection_id is null or (d.confirmed=1 and d.identity_id=s.identity_id and d.catalog_id<>? and d.catalog_id not in(select catalog_id from evaluation_excluded_clips)))''',(kind,model,cid)):
      v=vec(r['embedding'])
      if len(v)==384:supportrefs.setdefault(r['identity_id'],[]).append((r['catalog_id'],v))
     for r in c.execute('''select e.embedding from clip_detections d join identity_evidence e using(detection_id) where d.confirmed=1 and d.subject_type=? and d.identity_id is null and d.detection_id not in(select detection_id from subject_analysis_exclusions) and d.catalog_id not in(select catalog_id from evaluation_excluded_clips) and d.catalog_id<>? and e.embedding_model=? and e.quality_score>=.2 and e.embedding is not null''',(kind,cid,model)):
      v=vec(r[0])
      if len(v)==384:unknown.append(v)
     heads[kind]=dict(names=names,xm=xm,ym=ym,coef=coef,rows=rows,mat=np.array([v for r,v in rows]),ids=np.array([r['identity_id'] for r,v in rows]),samples={iid:(np.array([v for cid,v in rr]),[cid for cid,v in rr]) for iid,rr in supportrefs.items()},unknown=np.array(unknown) if unknown else np.empty((0,384)))
    profiles={r[0]:dict(name=r[1],kind=r[2]) for r in c.execute("select identity_id,name,subject_type from identities where active=1 and subject_type in('Cat','Dog')")};order=sorted(heads,key=lambda k:(len(heads[k]['names']),k));poolmat=np.concatenate([heads[k]['mat'] for k in order]);poolids=np.concatenate([heads[k]['ids'] for k in order]);poolnames=sorted(set(int(x) for x in poolids));cache={};typecache={}
    def fast_rank(conn,kind,m,u,cid):
     assert m==model;h=heads[kind];q=np.array(vec(u));values=(q-h['xm'])@h['coef']+h['ym'];near=h['mat']@q;out=[]
     for iid,value in zip(h['names'],values):
      rr=[r for r,v in h['rows'] if r['identity_id']==iid];out.append(dict(identity_id=iid,name=rr[0]['name'],score=float(value),nearest=float(np.max(near[h['ids']==iid])),reference_clips=len({r['catalog_id'] for r in rr if r['catalog_id'] is not None})))
     return sorted(out,key=lambda x:x['score'],reverse=True)
    def evidence(v,kind,canonical=False):
     u=vec(v.get('embedding'))
     if len(u)!=384 or v.get('embedding_model')!=model or (v.get('quality_score') or 0)<.2:return None
     key=(kind,canonical,tuple(u),v['quality_score'])
     if key in cache:return cache[key]
     h=heads[kind];q=np.array(u);rr=fast_rank(c,kind,model,u,None);top=rr[0];threshold=.60 if kind=='Dog' else .55;sources={iid:{cid for cid,dot in zip(ids,mat@q) if cid is not None and dot>=threshold} for iid,(mat,ids) in h['samples'].items()};veto=bool(h['unknown'].size and np.any((h['unknown']@q>=.65)&(h['unknown']@q>=top['nearest']-.03)))
     if kind=='Dog':qualified=top['identity_id'] if not veto and top['score']>=.8 and top['score']-rr[1]['score']>=(.4 if canonical else .3) and top['nearest']>=.65 and len(sources.get(top['identity_id'],set()))>=(3 if canonical else 2) else None
     else:qualified=top['identity_id'] if not veto and top['score']>=.6 and top['score']-rr[1]['score']>=.2 and top['nearest']>=.55 and len(sources.get(top['identity_id'],set()))>=2 else None
     out=dict(ranks=rr,sources=sources,quality=v['quality_score'],veto=veto,qualified=qualified);cache[key]=out;return out

    def separated(item):
     out=[];seen=set()
     for v in sorted(item.get('views',[])[:4],key=lambda v:v['seconds']):
      if not v.get('crop_jpeg') or v.get('embedding_model')!=model or len(vec(v.get('embedding')))!=384 or (v.get('quality_score') or 0)<.2:continue
      t=v['seconds'];digest=hashlib.sha256(v['crop_jpeg']).digest()
      if not isinstance(t,(float,int)) or not np.isfinite(t) or t<0 or digest in seen or any(abs(t-q[0])<.5 for q in out):continue
      try:
       with Image.open(io.BytesIO(v['crop_jpeg'])) as im:pix=list(im.convert('L').resize((9,8)).getdata())
      except (OSError,ValueError):continue
      dh=sum(int(pix[y*9+x]>pix[y*9+x+1])<<(63-y*8-x) for y in range(8) for x in range(8))
      if any((dh^q[1]).bit_count()<4 for q in out):continue
      out.append((t,dh,v));seen.add(digest)
     return [q[2] for q in out]
    def hint(item,kind):
     a=evidence(item,kind,True)
     if not a:return None
     ev=[evidence(v,kind,False) for v in separated(item)];ev=[v for v in ev if v];r=(dogchoose if kind=='Dog' else catchoose)(a,ev)
     return dict(identity_id=r[0],name=profiles[r[0]]['name'],kind=kind,tier=r[1],review_required=True) if r else None
    def type_ranks(v):
     u=vec(v.get('embedding'));key=tuple(u)
     if key not in typecache:typecache[key]=rank_types(core,u,model) if len(u)==384 else []
     return typecache[key]
    def evaluate(item):
     typ=item.get('subject_type','Unknown');hints={k:hint(item,k) for k in order};base=hints.get(typ);baseline=dict(type=typ,name=base['name'] if base else None,tier=base['tier'] if base else None,review_required=True);vv=separated(item);votes=[];conflicts=set()
     for v in vv:
      ev={k:evidence(v,k) for k in order};q=np.array(vec(v['embedding']));near=poolmat@q;ranked=sorted([(float(np.max(near[poolids==iid])),iid) for iid in poolnames],reverse=True);best,iid=ranked[0];margin=best-ranked[1][0];kind=profiles[iid]['kind'];e=ev[kind]
      if e and e['qualified']==iid and margin>=.03:
       conflicts.add(iid)
       if hints[kind] and hints[kind]['identity_id']==iid:votes.append(dict(seconds=v['seconds'],identity_id=iid,name=profiles[iid]['name'],kind=kind,cosine=best,cross_name_margin=margin))
     counts=collections.Counter(v['identity_id'] for v in votes);eligible=[iid for iid,n in counts.items() if n>=2];reason='no two separated strongly supported cross-list name votes';candidate=None
     if len(conflicts)>1:reason='strong cross-view name conflict'
     elif len(eligible)==1:
      iid=eligible[0];kind=profiles[iid]['kind'];veto=False;why=[]
      for v in [item]+vv:
       rr=type_ranks(v)
       if len(rr)<3:continue
       top=rr[0];s=top['independent_similarities'];two=len(s)>=2 and s[1]>=.5;m=top['score']-rr[1]['score'];bad=two and ((top['subject_type']=='Person' and top['score']>=.8 and m>=.3) or (top['subject_type'] in ['Cat','Dog'] and top['subject_type']!=kind and top['score']>=.75 and m>=.3))
       if bad:veto=True;why.append(top['subject_type'])
      if typ not in ['Unknown',kind]:reason='accepted Type preserved'
      elif veto:reason='strong Type conflict: '+','.join(sorted(set(why)))
      else:candidate=dict(type=kind,name=profiles[iid]['name'],tier='probable',review_required=True);reason='name-first accepted with cross-list and Type conflict checks'
     after=candidate or dict(baseline);assert typ=='Unknown' or after['type']==typ
     return dict(suggested_identity_id=eligible[0],suggested_name=candidate['name'],suggested_subject_type=candidate['type'],suggestion_tier='probable',review_required=True,confirmed=False) if candidate else None
    def pooled_match(v):
     q=np.array(vec(v.get('embedding')))
     if len(q)!=384 or v.get('embedding_model')!=model:return None
     near=poolmat@q;rank=sorted([(float(np.max(near[poolids==iid])),iid) for iid in poolnames],reverse=True)
     best,iid=rank[0];kind=profiles[iid]['kind'];e=evidence(v,kind)
     if e and e['qualified']==iid and best-rank[1][0]>=.03:
      return dict(identity_id=iid,kind=kind,evidence=e,margin=best-rank[1][0])
     return None
    if prepared:return PreparedReview(evaluate,evidence,separated,hint,type_ranks,pooled_match,profiles)
    return evaluate
