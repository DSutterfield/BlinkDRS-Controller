"""Household-specific candidate ranking. Cats remain possibilities, never confirmations."""
import hashlib,json,math
_CACHE={}
def rank_candidates(conn,kind,model,vector,exclude_clip=None):
 if kind not in ('Cat','Dog') or not model or not model.startswith('dinov2-small-cls-v1:') or vector is None or len(vector)!=384:return []
 import numpy as np
 from identity_analysis import normalized
 query=np.array(normalized(vector));raw=list(conn.execute('''SELECT s.sample_id,s.identity_id,s.embedding,d.catalog_id,i.name
  FROM identity_samples s JOIN identities i USING(identity_id) LEFT JOIN clip_detections d USING(detection_id)
  WHERE i.active=1 AND i.subject_type=? AND s.model_version=? AND s.quality_score>=.2
  AND (s.detection_id IS NULL OR d.confirmed=1) ORDER BY s.sample_id LIMIT2000'''.replace('LIMIT2000','LIMIT 2000'),(kind,model)))
 rows=[]
 for row in raw:
  if exclude_clip is not None and row['catalog_id']==exclude_clip:continue
  try:v=normalized(json.loads(row['embedding']))
  except (TypeError,ValueError):continue
  if len(v)==384:rows.append((row,np.array(v)))
 names=sorted({r['identity_id'] for r,v in rows})
 if len(names)<2 or len(rows)<6:return []
 key=hashlib.sha256(repr([(r['sample_id'],r['identity_id'],r['catalog_id'],r['embedding'],r['name']) for r,v in rows]).encode()).hexdigest()
 cachekey=(kind,model,key)
 if cachekey in _CACHE:head=_CACHE[cachekey]
 else:
  from collections import Counter
  counts=Counter((r['identity_id'],r['catalog_id'] if r['catalog_id'] is not None else 'photo:'+str(r['sample_id'])) for r,v in rows)
  sources={n:len({source for identity,source in counts if identity==n}) for n in names};w=np.array([1/(sources[r['identity_id']]*counts[(r['identity_id'],r['catalog_id'] if r['catalog_id'] is not None else 'photo:'+str(r['sample_id']))]) for r,v in rows]);w*=len(w)/w.sum()
  x=np.array([v for r,v in rows]);y=np.array([[float(r['identity_id']==n) for n in names] for r,v in rows]);xm=np.average(x,axis=0,weights=w);ym=np.average(y,axis=0,weights=w);a=(x-xm)*np.sqrt(w[:,None]);b=(y-ym)*np.sqrt(w[:,None]);coef=a.T@np.linalg.solve(a@a.T+np.eye(len(a)),b)
  head=(names,xm,ym,coef,rows)
  if len(_CACHE)>=16:_CACHE.clear()
  _CACHE[cachekey]=head
 names,xm,ym,coef,rows=head;values=(query-xm)@coef+ym;results=[]
 for n,value in zip(names,values):
  own=[(r,v) for r,v in rows if r['identity_id']==n];results.append(dict(identity_id=n,name=own[0][0]['name'],score=float(value),reference_clips=len({r['catalog_id'] for r,v in own if r['catalog_id'] is not None}),nearest=float(max(query@v for r,v in own))))
 return sorted(results,key=lambda r:r['score'],reverse=True)
def store_candidates(conn,did,kind,model,vector,catalog_id):
 ranks=rank_candidates(conn,kind,model,vector,catalog_id)
 conn.execute('DELETE FROM identity_candidates WHERE detection_id=?',(did,))
 for index,r in enumerate(ranks[:3]):conn.execute('INSERT INTO identity_candidates(detection_id,identity_id,rank,score,method) VALUES(?,?,?,?,?)',(did,r['identity_id'],index+1,r['score'],'household-ridge-v1-possible'))
 return ranks
def dog_recommendation(conn,item,catalog_id):
 # An experimental high-score single view cannot label an individual automatically.
 if item['subject_type']!='Dog':return None
 views=item.get('views',[]);supported=[]
 for view in views:
  ranks=rank_candidates(conn,'Dog',view['embedding_model'],view['embedding'],catalog_id)
  if len(ranks)>=2 and ranks[0]['score']>=.8 and ranks[0]['score']-ranks[1]['score']>=.3 and ranks[0]['reference_clips']>=2 and ranks[0]['nearest']>=.75:supported.append((ranks[0]['identity_id'],view['embedding']))
 if len(supported)>=2 and len({r[0] for r in supported})==1:
  from identity_analysis import normalized
  vectors=[normalized(r[1]) for r in supported]
  if any(len(a)==len(b) and sum(x*y for x,y in zip(a,b))<.98 for i,a in enumerate(vectors) for b in vectors[i+1:]):return supported[0][0]
 return None

