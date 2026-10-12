"""Frozen frame-level Water Bowl experiment. Read-only catalog, isolated outputs."""
import os,json,time,hashlib,sqlite3,datetime,sys,zipfile
from pathlib import Path
from zoneinfo import ZoneInfo
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['YOLO_AUTOINSTALL']='False'
root=Path('/home/dan/BlinkDRS-Controller/.water-combined-20261012');out=root/'reserve-evaluation';out.mkdir(exist_ok=True)
os.environ['YOLO_CONFIG_DIR']=str(out/'config')
sys.path.insert(0,str(root));sys.path.insert(0,str(root.parent))
import cv2,numpy as np
from identity_models import LocalModels
from subject_type_check import resolve_animal_type,rank_types,in_person_region
from identity_analysis import normalized
from water_sequence import link_sequences,cosine
models=LocalModels(root.parent/'ai-models')
from ultralytics import YOLO
from ultralytics.utils import SETTINGS
SETTINGS.update({'sync':False});yolo=YOLO(str(root.parent/'.cat-training-expanded-20261009/runs/expanded-v1/weights/best.pt'))
samples=json.loads((root/'reserved-review.json').read_text());human=json.loads((root/'human-review.json').read_text())
tz=ZoneInfo('America/Chicago');excluded_dates={datetime.datetime.fromisoformat(r['captured_at']).astimezone(tz).date() for r in samples};excluded_clips={r['clip'] for r in samples}
c=sqlite3.connect('file:/home/dan/BlinkDVR/blink_catalog.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
refs=[]
for kind in ('Person','Cat','Dog'):
 for row in c.execute('''select t.*,d.catalog_id,c.captured_at from subject_type_samples t join clip_detections d using(detection_id) join clips c on c.id=d.catalog_id where d.confirmed=1 and d.subject_type=t.subject_type and t.subject_type=? and t.model_version=? and t.embedding is not null and t.quality_score>=.2 order by t.detection_id desc''',(kind,models.pet_version)):
  if row['catalog_id'] in excluded_clips or datetime.datetime.fromisoformat(row['captured_at']).astimezone(tz).date() in excluded_dates:continue
  item=dict(row);item['vector']=normalized(json.loads(item['embedding']));refs.append(item)
  if sum(x['subject_type']==kind for x in refs)>=128:break

def iou(a,b):
 v=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]));return v/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-v+1e-9)
def adjust(im):
 l,a,b=cv2.split(cv2.cvtColor(im,cv2.COLOR_BGR2LAB));l=cv2.createCLAHE(clipLimit=2.,tileGridSize=(8,8)).apply(l)
 return cv2.cvtColor(cv2.merge((l,a,b)),cv2.COLOR_LAB2BGR)
def verify(im,box,claimed,persons):
 h,w=im.shape[:2];x,y,bx,by=map(int,box);x=max(0,x);y=max(0,y);bx=min(w,bx);by=min(h,by)
 if bx<=x or by<=y:return dict(box=box,species='Unknown',quality=0.,vector=None,model=None,hash=None)
 crop=im[y:by,x:bx];vector,version,quality=models.type_embedding(crop)
 resolved=resolve_animal_type(refs,claimed,vector,version,person_region=in_person_region({'box':box},persons))
 return dict(box=box,species=resolved,quality=quality,vector=vector,model=version,hash=hashlib.sha256(crop.tobytes()).hexdigest(),ranks=rank_types(refs,vector,version))
def public(r):return {k:v for k,v in r.items() if k!='vector'}

records=[];tracks={}
for sample in samples:
 im=cv2.imread(str(root/sample['image']));h,w=im.shape[:2];started=time.monotonic()
 baseline=models.detect(im);persons=[d for d in baseline if d['subject_type']=='Person'];base=[]
 for d in baseline:
  if d['subject_type'] in ('Cat','Dog'):base.append(dict(verify(im,d['box'],d['subject_type'],persons),detector_type=d['subject_type'],confidence=d['confidence']))
 xs=sorted(set(list(range(0,max(1,w-384),256))+[max(0,w-384)]));ys=sorted(set(list(range(0,max(1,h-384),256))+[max(0,h-384)]));proposals={k:[] for k in ('original','adjusted')}
 for oy in ys:
  for ox in xs:
   tile=im[oy:oy+384,ox:ox+384]
   for name,view in [('original',tile),('adjusted',adjust(tile))]:
    pred=yolo.predict(view,imgsz=320,conf=.30,iou=.45,device='cpu',verbose=False,classes=[15])[0]
    for b in pred.boxes:
     x,y,bx,by=map(float,b.xyxy[0]);proposals[name].append(dict(box=[x+ox,y+oy,bx+ox,by+oy],score=float(b.conf[0]),view=name))
 supported=[p for name,other in [('original','adjusted'),('adjusted','original')] for p in proposals[name] if any(iou(p['box'],q['box'])>=.35 for q in proposals[other])]
 groups=[]
 for p in sorted(supported,key=lambda p:p['score'],reverse=True):
  group=next((g for g in groups if iou(g[0]['box'],p['box'])>=.35),None)
  if group is None:groups.append([p])
  elif len(group)<4 and not any(p['box']==q['box'] for q in group):group.append(p)
 combined=[];frame_used=set()
 for gi,group in enumerate(groups):
  details=[verify(im,p['box'],'Cat',persons) for p in group];accepted={d['species'] for d in details if d['species'] in ('Cat','Dog')}
  species=next(iter(accepted)) if len(accepted)==1 and not any(d['species'] is None for d in details) else 'Unknown'
  # Retain all original-pixel alternatives. Pick a feature representative by species evidence/quality only.
  chosen=max(details,key=lambda d:(d['species']==species and species in ('Cat','Dog'),d['quality']))
  combined.append({'species':species,'alternatives':[public(d) for d in details]})
  if chosen['vector'] is not None:
   cliptracks=tracks.setdefault(sample['clip'],[]);possible=[]
   for ti,t in enumerate(cliptracks):
    if ti in frame_used or t['model']!=chosen['model']:continue
    if t['species']!='Unknown' and species!='Unknown' and t['species']!=species:continue
    sim=cosine(t['vector'],chosen['vector'])
    if sim is not None and sim>=.90 and iou(t['pixel_box'],chosen['box'])>=.10:possible.append((sim,ti))
   possible.sort(reverse=True)
   ti=possible[0][1] if possible and (len(possible)==1 or possible[0][0]-possible[1][0]>=.05) else len(cliptracks)
   obs={'id':f'{sample["clip"]}:{ti}','camera':'Water Bowl','clip':sample['clip'],'time':datetime.datetime.fromisoformat(sample['captured_at']).timestamp()+sample['seconds'],'quality':chosen['quality'],'model':chosen['model'],'hash':chosen['hash'],'species':species,'box':[chosen['box'][0]/w,chosen['box'][1]/h,chosen['box'][2]/w,chosen['box'][3]/h],'pixel_box':chosen['box'],'vector':chosen['vector']}
   if ti==len(cliptracks):cliptracks.append(obs)
   else:cliptracks[ti]=obs
   frame_used.add(ti)
 overlay=im.copy()
 for d in base:
  x,y,bx,by=map(int,d['box']);cv2.rectangle(overlay,(x,y),(bx,by),(255,150,0),2);cv2.putText(overlay,'base '+str(d['species']),(x,max(18,y-5)),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,150,0),1)
 for g in combined:
  for d in g['alternatives']:
   x,y,bx,by=map(int,d['box']);color=(0,255,0) if g['species']=='Cat' else (0,165,255)
   cv2.rectangle(overlay,(x,y),(bx,by),color,2);cv2.putText(overlay,'combined '+g['species'],(x,min(h-5,by+17)),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
 name='overlay-'+sample['image'];cv2.imwrite(str(out/name),overlay)
 record=dict(sample,baseline=[public(d) for d in base],combined=combined,raw_original_cat_proposals=len(proposals['original']),raw_adjusted_cat_proposals=len(proposals['adjusted']),elapsed_seconds=time.monotonic()-started,overlay=name)
 records.append(record);(out/'frames.json').write_text(json.dumps(records,indent=2));print(sample['image'],'base',[(d['detector_type'],d['species']) for d in base],'combined',[g['species'] for g in combined],round(record['elapsed_seconds'],1),flush=True)

links=link_sequences([r for rows in tracks.values() for r in rows])
report={'scope':'Frozen sampled-frame comparison; clip-level human Cat labels only. Frame target boxes/visibility not yet human annotated, so no IoU accuracy or exhaustive video recall claim.','excluded_reference_dates_chicago':[str(d) for d in excluded_dates],'sequence_links':links,'tracks':[{k:v for k,v in r.items() if k!='vector'} for rows in tracks.values() for r in rows],'frames':records,'feature_model':models.pet_version,'yolo_sha256':hashlib.sha256((root.parent/'.cat-training-expanded-20261009/runs/expanded-v1/weights/best.pt').read_bytes()).hexdigest(),'baseline_detector':models.detector_version,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(out/'results.json').write_text(json.dumps(report,indent=2))
for start in range(0,len(records),6):
 canvas=np.zeros((1080,1280,3),np.uint8)
 for i,r in enumerate(records[start:start+6]):
  tile=cv2.resize(cv2.imread(str(out/r['overlay'])),(640,360));cv2.rectangle(tile,(0,0),(640,26),(0,0,0),-1);cv2.putText(tile,f'{r["clip"]} {r["seconds"]:.1f}s',(8,19),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1);canvas[i//2*360:(i//2+1)*360,i%2*640:(i%2+1)*640]=tile
 cv2.imwrite(str(out/f'comparison-sheet-{start//6}.jpg'),canvas)
with zipfile.ZipFile(out/'reserve-evaluation.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in out.iterdir():
  if p.suffix in ('.json','.jpg'):z.write(p,p.name)
print('COMPLETE sequence links',len(links),flush=True)
