set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 .yolo-study-20261009/venv/bin/python -u - <<'PY'
import os,json,time,hashlib,cv2,numpy as np,zipfile
from pathlib import Path
root=Path('.cat-training-expanded-20261009');os.environ['YOLO_CONFIG_DIR']=str(root/'config');os.environ['YOLO_AUTOINSTALL']='False'
import torch
from ultralytics import YOLO
from ultralytics.utils import SETTINGS
SETTINGS.update({'sync':False});torch.set_num_threads(1);cv2.setNumThreads(1)
out=root/'evaluation';out.mkdir(exist_ok=True)
rows=json.loads((root/'reviewed-manifest.json').read_text());test=[r for r in rows if r['split']=='test']
assert len(test)==22
def iou(a,b):
 x=max(0,min(a[2],b[2])-max(a[0],b[0]));y=max(0,min(a[3],b[3])-max(a[1],b[1]));v=x*y;return v/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-v+1e-9)
results={}
for name,path in [('baseline',Path('.cat-training-20261009/yolo11n.pt')),('expanded',root/'runs/expanded-v1/weights/best.pt')]:
 model=YOLO(str(path));records=[];tp=fp=fn=0;reject_false=0
 for r in test:
  im=cv2.imread(r['image']);ax,ay,_,_=r['source_region'];x,y,bx,by=r['box'];gt=[x-ax,y-ay,bx-ax,by-ay]
  pred=model.predict(im,imgsz=320,conf=.30,iou=.45,device='cpu',verbose=False)[0];cats=[];allpred=[]
  for b in pred.boxes:
   p=dict(box=[float(v) for v in b.xyxy[0]],score=float(b.conf[0]),kind=model.names[int(b.cls[0])]);allpred.append(p)
   if p['kind']=='cat':cats.append(p)
  match=next((p for p in sorted(cats,key=lambda p:p['score'],reverse=True) if r['kind']=='Cat' and iou(p['box'],gt)>=.5),None)
  tp+=int(match is not None);fn+=int(r['kind']=='Cat' and match is None);fp+=len(cats)-int(match is not None);reject_false+=int(r['kind']!='Cat' and bool(cats))
  record=dict(detection_id=r['detection_id'],clip=r['clip'],kind=r['kind'],session=r['session'],gt=gt,matched=match is not None,predictions=allpred);records.append(record)
  cv2.rectangle(im,(gt[0],gt[1]),(gt[2],gt[3]),(0,255,0),2)
  for p in cats:
   px,py,qx,qy=map(int,p['box']);cv2.rectangle(im,(px,py),(qx,qy),(0,0,255),2);cv2.putText(im,f"cat {p['score']:.2f}",(px,max(15,py)),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,0,255),1)
  cv2.imwrite(str(out/f"{name}-{r['detection_id']}.jpg"),im)
 results[name]=dict(tp=tp,fp=fp,fn=fn,noncat_images_with_cat_prediction=reject_false,records=records,weights_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
 print(name,'fresh test',tp,fp,fn,'noncat false',reject_false,flush=True)
 # Previously inspected empty-background tiles: diagnostic only, not an independent test.
 negatives=[]
 for image in Path('.cat-training-20261009/dataset/images').rglob('*-background.jpg'):
  p=model.predict(str(image),imgsz=320,conf=.30,iou=.45,device='cpu',verbose=False,classes=[15])[0]
  negatives.append(dict(image=str(image),cat_predictions=len(p.boxes)))
 results[name]['development_backgrounds']=negatives
 # Fixed grid diagnostic on original difficult clip; neither threshold nor checkpoint tuned on it.
 im=cv2.imread('.detector-study-20261009/input-24102.png');h,w=im.shape[:2];detections=[]
 xs=sorted(set(list(range(0,max(1,w-384),256))+[w-384]));ys=sorted(set(list(range(0,max(1,h-384),256))+[h-384]))
 for ay in ys:
  for ax in xs:
   p=model.predict(im[ay:ay+384,ax:ax+384],imgsz=320,conf=.30,iou=.45,device='cpu',verbose=False,classes=[15])[0]
   for b in p.boxes:
    x,y,bx,by=map(float,b.xyxy[0]);detections.append(dict(box=[x+ax,y+ay,bx+ax,by+ay],score=float(b.conf[0])))
 keep=[]
 for d in sorted(detections,key=lambda d:d['score'],reverse=True):
  if all(iou(d['box'],k['box'])<.35 for k in keep):keep.append(d)
 results[name]['diagnostic24102']=keep
 overlay=im.copy()
 for d in keep:
  x,y,bx,by=map(int,d['box']);cv2.rectangle(overlay,(x,y),(bx,by),(0,0,255),2);cv2.putText(overlay,f"cat {d['score']:.2f}",(x,max(20,y)),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,0,255),2)
 cv2.imwrite(str(out/f'{name}-24102.jpg'),overlay)
 whole=[]
 for r in [r for r in test if r['kind']=='Cat']:
  frame=cv2.imread(str(root/f"frame-{r['detection_id']}.jpg"));fh,fw=frame.shape[:2];predictions=[];started=time.monotonic()
  xx=sorted(set(list(range(0,max(1,fw-384),256))+[fw-384]));yy=sorted(set(list(range(0,max(1,fh-384),256))+[fh-384]))
  for oy in yy:
   for ox in xx:
    p=model.predict(frame[oy:oy+384,ox:ox+384],imgsz=320,conf=.30,iou=.45,device='cpu',verbose=False,classes=[15])[0]
    for b in p.boxes:
     x,y,bx,by=map(float,b.xyxy[0]);predictions.append(dict(box=[x+ox,y+oy,bx+ox,by+oy],score=float(b.conf[0])))
  matched=any(iou(d['box'],r['box'])>=.5 for d in predictions)
  x,y,bx,by=r['box'];cv2.rectangle(frame,(x,y),(bx,by),(0,255,0),2)
  for d in predictions:
   x,y,bx,by=map(int,d['box']);cv2.rectangle(frame,(x,y),(bx,by),(0,0,255),2)
  cv2.imwrite(str(out/f"{name}-whole-{r['detection_id']}.jpg"),frame)
  whole.append(dict(clip=r['clip'],detection_id=r['detection_id'],matched=matched,predictions=predictions,elapsed_seconds=time.monotonic()-started))
 results[name]['whole_frame_target_recall']=whole
 print(name,'whole frame targets found',sum(r['matched'] for r in whole),'of',len(whole),flush=True)
 print(name,'background false images',sum(bool(n['cat_predictions']) for n in negatives),'of',len(negatives),'24102',keep,flush=True)
(out/'results.json').write_text(json.dumps(dict(protocol=dict(confidence=.30,match_iou=.5,imgsz=320,selection='best validation checkpoint only; fixed threshold; no heldout tuning',fresh_test_cat_images=8,fresh_test_noncat_images=14,scope='Daylight contextual crops; not full-video recall. Nighttime training examples are not independent nighttime validation.'),models=results),indent=2))
with zipfile.ZipFile(root/'expanded-study.zip','w',zipfile.ZIP_DEFLATED) as z:
 z.write('.cat-training-20261009/yolo11n.pt','pretrained/yolo11n.pt')
 for p in root.rglob('*'):
  if p.is_file() and 'config' not in p.parts and p.name!='expanded-study.zip' and p.suffix not in ('.cache','.excluded'):
   z.write(p,str(p.relative_to(root)))
print('EVALUATION COMPLETE',flush=True)
PY
sha256sum .cat-training-expanded-20261009/expanded-study.zip
systemctl is-active blink-dvr.service blink-identity.service
