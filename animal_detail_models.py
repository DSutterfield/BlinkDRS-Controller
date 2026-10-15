"""Additional animal-only detection; original Person and identity paths are preserved."""
from pathlib import Path
from PIL import Image
from animal_scene_search import AnimalSceneModels,containment
from identity_worker import overlap

MODEL_FILE='fasterrcnn_mobilenet_v3_large_fpn-fb6a3cc7.pth'
MODEL_SHA256='fb6a3cc702b1df54c18a44b26708cd083614211062d0c36d2ca7bf9270df3533'

class DetailAnimalDetector:
 def __init__(self,models,path):
  from identity_models import file_hash
  from torchvision.models.detection import fasterrcnn_mobilenet_v3_large_fpn,FasterRCNN_MobileNet_V3_Large_FPN_Weights
  assert file_hash(path)==MODEL_SHA256,'Animal detail model fingerprint mismatch'
  self.models=models;self.version='fasterrcnn-mobilenet-v3-large-fpn:'+MODEL_SHA256
  self.detector=fasterrcnn_mobilenet_v3_large_fpn(weights=None,weights_backbone=None,min_size=640,max_size=1024).eval()
  self.detector.load_state_dict(models.torch.load(path,map_location='cpu',weights_only=True))
  self.transform=FasterRCNN_MobileNet_V3_Large_FPN_Weights.COCO_V1.transforms()
 def detect(self,frame,region=None):
  h,w=frame.shape[:2];a,b,c,e=region or [0,0,w,h]
  image=Image.fromarray(self.models.cv2.cvtColor(frame[b:e,a:c],self.models.cv2.COLOR_BGR2RGB))
  with self.models.torch.inference_mode():result=self.detector([self.transform(image)])[0]
  items=[]
  for label,score,box in zip(result['labels'],result['scores'],result['boxes']):
   label=int(label);score=float(score)
   # This detector never supplies Person or Vehicle proposals.
   if label not in (17,18) or score<.30:continue
   x,y,z,t=box.tolist();items.append(dict(subject_type='Cat' if label==17 else 'Dog',confidence=score,box=[x+a,y+b,z+a,t+b],detector_model=self.version))
  return items

class DetailedAnimalSceneModels(AnimalSceneModels):
 def __init__(self,models,checkpoint=None,detail=None,model_path=None):
  super().__init__(models,checkpoint);self.detail=detail;self.model_path=model_path or Path(__file__).resolve().parent/'ai-models'/MODEL_FILE;self.recent=[]
 def detail_detections(self,frame,region=None):
  self.checkpoint()
  if self.detail is None:self.detail=DetailAnimalDetector(self.models,self.model_path)
  return self.detail.detect(frame,region)
 def type_evidence(self,frame,d,detections,use_context=False):
  from subject_type_check import in_person_region
  h,w=frame.shape[:2];a,b,c,e=map(int,d['box']);box=[max(0,a),max(0,b),min(w,c),min(h,e)];a,b,c,e=box
  if min(c-a,e-b)<16:return None,None
  # Strong same-frame Person coverage cannot generate a supplemental animal.
  if in_person_region(dict(d,box=box),detections):return None,None
  v,m,q=self.models.animal_type_embedding(frame[b:e,a:c])
  if use_context:v,m,q,context=self.models.choose_animal_type_features(frame,box,d['subject_type'],v,m,q)
  frame_type=self.models.resolve_animal_type(d['subject_type'],v,m)
  strong=self.models.resolve_track_type(dict(subject_type=d['subject_type'],type_embedding=v,type_embedding_model=m,type_quality_score=q,type_views=[]))
  return frame_type,strong
 def detect(self,frame):
  idx=self.index;base=super().detect(frame);h,w=frame.shape[:2];proposals=[]
  if idx%2==0:proposals+=self.detail_detections(frame)
  seeds=[]
  for age,d in self.recent:
   if idx-age<=2 and not any(overlap(d['box'],e['box'])>.35 for e in seeds):seeds.append(d)
   if len(seeds)>=2:break
  regions=[]
  for d in seeds:
   a,b,c,e=d['box'];size=min(512,max(384,int(max(c-a,e-b)*4)));x=max(0,min(max(0,w-size),int((a+c-size)/2)));y=max(0,min(max(0,h-size),int((b+e-size)/2)));region=[x,y,min(w,x+size),min(h,y+size)]
   if not any(overlap(region,r)>.7 for r in regions):regions.append(region)
  for region in regions:proposals+=self.detail_detections(frame,region)
  kept=list(base);history=[]
  # Existing proposals keep priority, so the new model cannot replace a
  # previously successful crop with a less useful overlapping crop.
  for d in sorted(proposals,key=lambda d:d['confidence'],reverse=True):
   if any(e['subject_type'] in ('Cat','Dog') and (overlap(d['box'],e['box'])>.35 or containment(d['box'],e['box'])>.7) for e in kept):continue
   frame_type,strong=self.type_evidence(frame,d,base)
   if frame_type in ('Cat','Dog') and max(d['box'][2]-d['box'][0],d['box'][3]-d['box'][1])<=128:history.append((idx,d))
   # The original single-view strong Type gate is unchanged: .75 ranking,
   # .30 margin, and support from two distinct confirmed Clips/images.
   if strong in ('Cat','Dog'):kept.append(dict(d,subject_type=strong,detail_type_confirmed=True))
  for d in base:
   if d['subject_type'] not in ('Cat','Dog') or max(d['box'][2]-d['box'][0],d['box'][3]-d['box'][1])>128:continue
   frame_type,strong=self.type_evidence(frame,d,base,use_context=True)
   if strong in ('Cat','Dog'):history.append((idx,d))
  history+=self.recent;unique=[]
  for age,d in history:
   if idx-age>2 or any(overlap(d['box'],e['box'])>.35 or containment(d['box'],e['box'])>.7 for _,e in unique):continue
   unique.append((age,d))
   if len(unique)>=2:break
  self.recent=unique
  return kept
