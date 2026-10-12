set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 .yolo-study-20261009/venv/bin/python -u - <<'PY'
import os,json,time,cv2,hashlib,zipfile
from pathlib import Path
root=Path('.cat-training-expanded-20261009');out=Path('.species-zoom-shadow-20261012');out.mkdir(exist_ok=True)
os.environ['YOLO_CONFIG_DIR']=str(out.resolve()/'config');os.environ['YOLO_AUTOINSTALL']='False'
import torch
from ultralytics import YOLO
from ultralytics.utils import SETTINGS
SETTINGS.update({'sync':False});torch.set_num_threads(1);cv2.setNumThreads(1)
weights=root/'runs/expanded-v1/weights/best.pt';model=YOLO(str(weights))
rows=[r for r in json.loads((root/'reviewed-manifest.json').read_text()) if r['split']=='test']
backgrounds=list(Path('.cat-training-20261009/dataset/images').rglob('*-background.jpg'))
def iou(a,b):
 x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]));v=x*y
 return v/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-v+1e-9)
def adjust(im):
 lab=cv2.cvtColor(im,cv2.COLOR_BGR2LAB);l,a,b=cv2.split(lab)
 l=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)).apply(l)
 return cv2.cvtColor(cv2.merge((l,a,b)),cv2.COLOR_LAB2BGR)
report={'weights_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'scope':'Previously inspected development examples; no new heldout accuracy claim. Context crops are supplied, not autonomous discovery.','variants':{}}
for name,size,enhance in [('original320',320,False),('closer640',640,False),('shadow320',320,True),('closer_shadow640',640,True)]:
 records=[];start=time.monotonic()
 for r in rows:
  im=cv2.imread(r['image']);ax,ay,_,_=r['source_region'];x,y,bx,by=r['box'];gt=[x-ax,y-ay,bx-ax,by-ay]
  if enhance:im=adjust(im)
  pred=model.predict(im,imgsz=size,conf=.30,iou=.45,device='cpu',verbose=False)[0]
  cats=[{'box':[float(v) for v in b.xyxy[0]],'score':float(b.conf[0])} for b in pred.boxes if model.names[int(b.cls[0])]=='cat']
  matched=r['kind']=='Cat' and any(iou(p['box'],gt)>=.5 for p in cats)
  records.append({'clip':r['clip'],'detection_id':r['detection_id'],'kind':r['kind'],'matched':matched,'cats':cats})
  cv2.rectangle(im,(int(gt[0]),int(gt[1])),(int(gt[2]),int(gt[3])),(0,255,0),2)
  for p in cats:
   a,b,c,d=map(int,p['box']);cv2.rectangle(im,(a,b),(c,d),(0,0,255),2)
  cv2.imwrite(str(out/f'{name}-{r["detection_id"]}.jpg'),im)
 negatives=[]
 for p in backgrounds:
  im=cv2.imread(str(p));im=adjust(im) if enhance else im
  pred=model.predict(im,imgsz=size,conf=.30,iou=.45,device='cpu',verbose=False,classes=[15])[0]
  negatives.append({'image':str(p),'cat_boxes':len(pred.boxes)})
 summary={'cats_found':sum(r['matched'] for r in records),'cat_targets':sum(r['kind']=='Cat' for r in records),'noncat_false_images':sum(r['kind']!='Cat' and bool(r['cats']) for r in records),'noncat_images':sum(r['kind']!='Cat' for r in records),'background_false_images':sum(bool(r['cat_boxes']) for r in negatives),'background_images':len(negatives),'elapsed_seconds':time.monotonic()-start}
 report['variants'][name]={'summary':summary,'records':records,'backgrounds':negatives};(out/'results.json').write_text(json.dumps(report,indent=2));print(name,json.dumps(summary),flush=True)
with zipfile.ZipFile(out/'review.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in out.iterdir():
  if p.suffix in ('.json','.jpg'):z.write(p,p.name)
print('COMPLETE',flush=True)
PY
systemctl is-active blink-dvr.service blink-identity.service
