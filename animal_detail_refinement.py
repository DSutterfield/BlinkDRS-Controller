"""Refine small animal searches while preserving the installed detection path."""
from PIL import Image
from animal_detail_models import DetailedAnimalSceneModels,DetailAnimalDetector
from animal_scene_search import containment
from identity_worker import overlap

class RefinedAnimalSceneModels(DetailedAnimalSceneModels):
 def __init__(self,*args,**kwargs):
  super().__init__(*args,**kwargs);self.raw_cache={};self.previous_strong=[]
 def raw_detail_detections(self,frame,region=None):
  """Low-score animal locations are search proposals, never final Types."""
  self.checkpoint();key=tuple(region) if region is not None else None
  if key in self.raw_cache:return self.raw_cache[key]
  if self.detail is None:self.detail=DetailAnimalDetector(self.models,self.model_path)
  h,w=frame.shape[:2];a,b,c,e=region or [0,0,w,h]
  image=Image.fromarray(self.models.cv2.cvtColor(frame[b:e,a:c],self.models.cv2.COLOR_BGR2RGB))
  with self.models.torch.inference_mode():result=self.detail.detector([self.detail.transform(image)])[0]
  found=[]
  for label,score,box in zip(result['labels'],result['scores'],result['boxes']):
   label=int(label);score=float(score)
   if label not in (17,18) or score<.10:continue
   x,y,z,t=box.tolist();found.append(dict(subject_type='Cat' if label==17 else 'Dog',confidence=score,box=[x+a,y+b,z+a,t+b],detector_model=self.detail.version))
  self.raw_cache[key]=found;return found
 def detail_detections(self,frame,region=None):
  # Preserve the installed proposal floor and order for its original pass.
  return [d for d in self.raw_detail_detections(frame,region) if d['confidence']>=.30]
 def detect(self,frame):
  idx=self.index;previous=list(self.recent);previous_strong=getattr(self,'previous_strong',[]) if idx else [];self.raw_cache={}
  kept=super().detect(frame);h,w=frame.shape[:2]
  full=self.raw_detail_detections(frame);extras=list(full)
  seeds=[]
  # Prior positions guide a smaller search; current raw proposals can also
  # guide refinement. Every accepted crop still needs fresh strong Type evidence.
  candidates=[d for age,d in previous if idx-age<=2]+sorted(full,key=lambda d:d['confidence'],reverse=True)
  for d in candidates:
   a,b,c,e=d['box']
   if min(c-a,e-b)<16 or max(c-a,e-b)>128:continue
   if any(overlap(d['box'],x['box'])>.35 for x in seeds):continue
   seeds.append(d)
   if len(seeds)>=2:break
  regions=[]
  for d in seeds:
   a,b,c,e=d['box'];size=256;x=max(0,min(max(0,w-size),int((a+c-size)/2)));y=max(0,min(max(0,h-size),int((b+e-size)/2)));region=[x,y,min(w,x+size),min(h,y+size)]
   if region not in regions:regions.append(region)
  for region in regions:extras+=self.raw_detail_detections(frame,region)
  # Include weaker locations from the original 384-pixel focused searches.
  extras=[d for detections in self.raw_cache.values() for d in detections]
  added=[]
  for d in sorted(extras,key=lambda d:d['confidence'],reverse=True):
   if any(x['subject_type'] in ('Cat','Dog') and (overlap(d['box'],x['box'])>.35 or containment(d['box'],x['box'])>.7) for x in kept):continue
   frame_type,strong=self.type_evidence(frame,d,kept)
   if strong not in ('Cat','Dog'):
    if frame_type not in ('Cat','Dog'):continue
    from animal_type_temporal import nearby_small_boxes
    from subject_type_check import rank_types
    near=[p for age,p in previous_strong if idx-age==1 and nearby_small_boxes(d['box'],p['box'])]
    if len(near)!=1 or near[0]['subject_type']!=frame_type:continue
    a,b,c,e=map(int,d['box']);a,b,c,e=max(0,a),max(0,b),min(w,c),min(h,e)
    v,m,q=self.models.animal_type_embedding(frame[b:e,a:c]);ranks=rank_types(getattr(self.models,'type_references',[]),v,m)
    if q<.2 or len(ranks)<3:continue
    top=ranks[0];support=top['independent_similarities']
    # Use the existing multi-view support floor, with conservative admission.
    if top['subject_type']!=frame_type or top['score']<.65 or top['score']-ranks[1]['score']<.25 or len(support)<2 or support[1]<.50:continue
    kept.append(dict(d,subject_type=frame_type,detail_temporal_candidate=True));continue
   item=dict(d,subject_type=strong,detail_refinement_confirmed=True);kept.append(item);added.append((idx,item))
  history=added+self.recent;self.recent=[]
  for age,d in history:
   if idx-age>2 or max(d['box'][2]-d['box'][0],d['box'][3]-d['box'][1])>128:continue
   if any(overlap(d['box'],x['box'])>.35 or containment(d['box'],x['box'])>.7 for _,x in self.recent):continue
   self.recent.append((age,d))
   if len(self.recent)>=2:break
  self.previous_strong=[]
  for d in kept:
   if d['subject_type'] not in ('Cat','Dog'):continue
   a,b,c,e=d['box']
   if not 16<=min(c-a,e-b)<48 or max(c-a,e-b)>128:continue
   _,strong=self.type_evidence(frame,d,kept)
   if strong==d['subject_type']:self.previous_strong.append((idx,d))
  return kept
