"""Whole-scene animal search; preserve original Person and Vehicle proposals."""
from ssd_views import iter_ssd_views
from identity_worker import overlap

def containment(a,b):
 inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
 area=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
 return inter/area if area>0 else 0

class AnimalSceneModels:
 def __init__(self,models,checkpoint=None):
  self.models=models;self.index=0;self.checkpoint=checkpoint or (lambda:None)
 def __getattr__(self,name):return getattr(self.models,name)
 def grid(self,im,key,size,stride):
  h,w=im.shape[:2];items=[]
  xs=sorted(set(range(0,max(1,w-size+1),stride))|{max(0,w-size)})
  ys=sorted(set(range(0,max(1,h-size+1),stride))|{max(0,h-size)})
  views=((im[y:y+size,x:x+size],x,y) for y in ys for x in xs)
  for tile,x,y,r in iter_ssd_views(self.models,views,self.checkpoint):
   for label,score,box in zip(r['labels'],r['scores'],r['boxes']):
    label=int(label);score=float(score)
    if label not in (17,18) or score<.30:continue
    b=box.tolist();items.append(dict(subject_type='Cat' if label==17 else 'Dog',confidence=score,box=[b[0]+x,b[1]+y,b[2]+x,b[3]+y]))
  kept=[]
  for d in sorted(items,key=lambda d:d['confidence'],reverse=True):
   if not any(e['subject_type']==d['subject_type'] and overlap(e['box'],d['box'])>.35 for e in kept):kept.append(d)
  return kept
 def detect(self,im):
  checked=getattr(type(self.models),'detect_with_checkpoint',None)
  base=checked(self.models,im,self.checkpoint) if checked else self.models.detect(im)
  idx=self.index;self.index+=1
  proposals=self.grid(im,None,384,256)
  if idx%4==0:proposals+=self.grid(im,None,192,128)
  candidates=[d for d in base+proposals if d['subject_type'] in ('Cat','Dog') and d['confidence']>=.55]
  kept=[]
  for d in sorted(candidates,key=lambda d:d['confidence'],reverse=True):
   if not any(overlap(e['box'],d['box'])>.35 or containment(e['box'],d['box'])>.7 for e in kept):kept.append(d)
  # Low-score locations are proposals only: two strong confirmed-reference
  # context checks are required before they enter animal analysis.
  from subject_type_check import in_person_region
  weak=[]
  for d in sorted(base+proposals,key=lambda d:d['confidence'],reverse=True):
   if d['subject_type'] not in ('Cat','Dog') or not .30<=d['confidence']<.55:continue
   if any(overlap(e['box'],d['box'])>.35 or containment(e['box'],d['box'])>.7 for e in kept+weak):continue
   weak.append(d)
   if len(weak)>=12:break
  h,w=im.shape[:2] if weak else (0,0)
  for d in weak:
   self.checkpoint();a,b,c,e=map(int,d['box']);box=[max(0,a),max(0,b),min(w,c),min(h,e)]
   if min(box[2]-box[0],box[3]-box[1])<16:continue
   if self.models.confirm_weak_animal_proposal(im,box,d['subject_type'],person_region=in_person_region(d,base)):
    kept.append(dict(d,weak_type_confirmed=True))
  return [d for d in base if d['subject_type'] not in ('Cat','Dog')]+kept
