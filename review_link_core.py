"""Frozen review-link policy; no identity names, labels or learning writes."""
import math
class Helpers:
 @staticmethod
 def area(b):return max(0,b[2]-b[0])*max(0,b[3]-b[1])
 @staticmethod
 def iou(a,b):
  inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]));u=Helpers.area(a)+Helpers.area(b)-inter;return inter/u if u else 0.
 @staticmethod
 def cosine(a,b):
  if a is None or b is None or len(a)!=384 or len(b)!=384:return -1.
  n=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b));return sum(x*y for x,y in zip(a,b))/n if n and math.isfinite(n) else -1.
m=Helpers()
POLICY=dict(core_fraction=.70)
def evaluate(items,frames,fps,cv_module,checkpoint=None):
 global np,cv2
 import numpy as np
 cv2=cv_module
 return propose(items,frames,fps)

def center(b):return np.array([(b[0]+b[2])/2,(b[1]+b[3])/2],float)

def diag(b):return math.hypot(b[2]-b[0],b[3]-b[1])

def translate(b,d):return [float(b[0]+d[0]),float(b[1]+d[1]),float(b[2]+d[0]),float(b[3]+d[1])]

def seed(frame,box):
 mask=np.zeros_like(frame);x,y,r,b=box;pad=(1-POLICY['core_fraction'])/2;w=r-x;h=b-y;x,y,r,b=map(int,[x+w*pad,y+h*pad,r-w*pad,b-h*pad]);mask[max(0,y):max(0,b),max(0,x):max(0,r)]=255
 return cv2.goodFeaturesToTrack(frame,maxCorners=100,qualityLevel=.03,minDistance=4,mask=mask)

def advance(frames,lo,hi,box):
 pts=seed(frames[lo],box)
 if pts is None or len(pts)<12:return None,dict(reason='too_few_core_points',initial=0 if pts is None else len(pts))
 initial=len(pts);origin=pts.copy();step=1 if hi>=lo else -1;history=[(lo,np.array([0.,0.]))];last=lo;reason='observed_endpoint'
 for k in range(lo,hi,step):
  nxt,st,_=cv2.calcOpticalFlowPyrLK(frames[k],frames[k+step],pts,None,winSize=(21,21),maxLevel=3)
  if nxt is None:reason='point_loss_pause';break
  back,rev,_=cv2.calcOpticalFlowPyrLK(frames[k+step],frames[k],nxt,None,winSize=(21,21),maxLevel=3)
  if back is None:reason='point_loss_pause';break
  good=(st.ravel()==1)&(rev.ravel()==1)&(np.linalg.norm(back-pts,axis=2).ravel()<=1.)
  if int(good.sum())<max(12,math.ceil(initial*.65)):reason='point_loss_pause';break
  nxt=nxt[good].reshape(-1,1,2);orig=origin[good].reshape(-1,1,2);disp=np.median(nxt-orig,axis=0).ravel();pts=nxt;origin=orig;last=k+step;history.append((last,disp))
 displacement=history[-1][1];return dict(frame=last,box=translate(box,displacement),history=history),dict(reason=reason,initial=initial,remaining=len(pts),last_frame=last,requested_frame=hi)

def velocity(frames,fps,at,box,side):
 delta=max(2,round(.25*fps));other=max(0,at-delta) if side=='before' else min(len(frames)-1,at+delta)
 if other==at:return None,dict(reason='no_velocity_window')
 state,why=advance(frames,at,other,box)
 if state is None or state['frame']!=other:return None,why
 v=(center(state['box'])-center(box))/((other-at)/fps)
 return v,why

def propose(items,frames,fps):
 assert all(not {'name','identity_id','confirmed','truth'}&set(x) for x in items)
 observations={};diagnostics={}
 # Only potential edge endpoints need motion work. All other subjects remain
 # in items for competitor/overlap checks; association semantics are unchanged.
 endpoints={k for i,a in enumerate(items) for j,b in enumerate(items) if i!=j and a['kind'] in ('Cat','Dog') and a['kind']==b['kind'] and 0<b['start']-a['end']<=3.000001 for k in (i,j)}
 for i,a in enumerate(items):
  if i not in endpoints:continue
  if not a.get('views'):continue
  views=a['views'];start=min(views,key=lambda x:abs(x['seconds']-a['start']));end=min(views,key=lambda x:abs(x['seconds']-a['end']));idx=min(len(frames)-1,max(0,round(end['seconds']*fps)));target=min(len(frames)-1,max(idx,round(a['end']*fps)))
  state,why=advance(frames,idx,target,end['box']);diagnostics[a['id']]=why
  if state is not None:
   actual=state['frame']/fps;v,vwhy=velocity(frames,fps,state['frame'],state['box'],'before');firstidx=min(len(frames)-1,max(0,round(start['seconds']*fps)));rv,rwhy=velocity(frames,fps,firstidx,start['box'],'after')
   observations[i]=dict(end=dict(seconds=actual,box=state['box'],feature=end),start=start,velocity=v,reverse_velocity=rv,velocity_evidence=vwhy,reverse_velocity_evidence=rwhy)
 probes=[];edges=[];forbidden=set();by_id={a['id']:i for i,a in enumerate(items)}
 for i,a in enumerate(items):
  for j,b in enumerate(items):
   gap=b['start']-a['end']
   if i==j or a['kind'] not in ('Cat','Dog') or a['kind']!=b['kind'] or not 0<gap<=3.000001:continue
   row=dict(a=a['id'],b=b['id'],catalog_gap=gap);why=None
   if i not in observations or j not in observations:why='missing_observed_endpoint'
   else:
    x,y=observations[i],observations[j];elapsed=y['start']['seconds']-x['end']['seconds'];row['observed_gap']=elapsed
    if not 0<elapsed<=3.000001:why='pause_expired_or_stale_anchor'
    elif x['velocity'] is None or y['reverse_velocity'] is None:why='uncertain_velocity'
    else:
     lb,rb=x['end']['box'],y['start']['box'];ratio=m.area(lb)/m.area(rb);scale=(diag(lb)+diag(rb))/2;pred=center(lb)+x['velocity']*elapsed;back=center(rb)-y['reverse_velocity']*elapsed;err=float(np.linalg.norm(pred-center(rb))/scale);rev=float(np.linalg.norm(back-center(lb))/scale);row.update(forward_error_diagonals=err,reverse_error_diagonals=rev,area_ratio=ratio,velocity=x['velocity'].tolist(),reverse_velocity=y['reverse_velocity'].tolist())
     if not .5<=ratio<=2:why='size'
     elif max(err,rev)>.60:why='predicted_location'
     else:
      l,r=x['end']['feature'],y['start']
      pool=[]
      for av in a['views']:
       for bv in b['views']:
        if av['model']==bv['model'] and av['model'].startswith('dinov2-small-cls-v1:') and min(av['quality'],bv['quality'])>=.2:pool.append((m.cosine(av['vector'],bv['vector']),av,bv))
      if pool:sim,l,r=max(pool,key=lambda z:z[0])
      else:sim=-1.
      row.update(cosine=sim,appearance_pairs=len(pool),matched_view_seconds=[l['seconds'],r['seconds']])
      if l['model']!=r['model'] or not l['model'].startswith('dinov2-small-cls-v1:'):why='feature_model'
      elif min(l['quality'],r['quality'])<.2:why='feature_quality'
      elif sim<.65:why='appearance'
      else:
       corridor=[min(lb[0],rb[0],pred[0]-(lb[2]-lb[0])/2),min(lb[1],rb[1],pred[1]-(lb[3]-lb[1])/2),max(lb[2],rb[2],pred[0]+(lb[2]-lb[0])/2),max(lb[3],rb[3],pred[1]+(lb[3]-lb[1])/2)];comp=[]
       for k,c in enumerate(items):
        if k in(i,j) or c['end']<x['end']['seconds'] or c['start']>y['start']['seconds']:continue
        relevant=[v for v in c.get('views',[]) if x['end']['seconds']-.5<=v['seconds']<=y['start']['seconds']+.5]
        if not relevant or any(m.iou(corridor,v['box'])>.01 for v in relevant):comp.append(c['id'])
       row['competitors']=comp
       if comp:
        why='competing_subject';forbidden.add(tuple(sorted((i,j))))
        for other in comp:
         k=by_id[other];forbidden.add(tuple(sorted((i,k))));forbidden.add(tuple(sorted((j,k))))
   row['reason']=why or 'eligible';probes.append(row)
   if not why:edges.append((i,j,row))
 parent=list(range(len(items)))
 def find(i):
  while parent[i]!=i:i=parent[i]
  return i
 links=[]
 for i,j,row in edges:
  outgoing=[z for z in edges if z[0]==i];incoming=[z for z in edges if z[1]==j]
  alternatives=[z[2]['cosine'] for z in outgoing+incoming if z is not (i,j,row) and not(z[0]==i and z[1]==j)]
  # Reject component union only when it would join an explicitly incompatible
  # pair, preserving independent earlier edges. Constraints survive transitive unions.
  left_members=[k for k in range(len(items)) if find(k)==find(i)]
  right_members=[k for k in range(len(items)) if find(k)==find(j)]
  violated=[(a,b) for a in left_members for b in right_members if tuple(sorted((a,b))) in forbidden]
  if violated:
   row['reason']='cannot_link_constraint';row['blocked_component_pairs']=[[items[a]['id'],items[b]['id']] for a,b in violated];continue
  if alternatives and row['cosine']-max(alternatives)<.08:row['reason']='nonunique_reacquisition';continue
  l,r=find(i),find(j)
  if l==r:continue
  if any(items[x]['start']<=items[y]['end'] and items[y]['start']<=items[x]['end'] for x in range(len(items)) for y in range(len(items)) if find(x)==l and find(y)==r):row['reason']='component_overlap';continue
  parent[r]=l;links.append([items[i]['id'],items[j]['id']])
 groups={}
 for i in range(len(items)):groups.setdefault(find(i),[]).append(i)
 return list(groups.values()),links,probes,diagnostics
