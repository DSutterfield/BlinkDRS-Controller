set -e
cd /home/dan/BlinkDRS-Controller
cat > .cat-training-expanded-20261009/train.py <<'PY'
import os,json,time,hashlib,collections
from pathlib import Path
root=Path(__file__).resolve().parent
os.environ['YOLO_CONFIG_DIR']=str(root/'config');(root/'config').mkdir(exist_ok=True);os.environ['YOLO_AUTOINSTALL']='False'
import torch,cv2,yaml
from ultralytics import YOLO
from ultralytics.utils import SETTINGS
SETTINGS.update({'sync':False});torch.set_num_threads(1);cv2.setNumThreads(1)
rows=json.loads((root/'split-manifest.json').read_text())
# Visual audit: wrong type or additional unlabeled subjects in the contextual tile.
excluded={1491,1456,219,222,375,376,1506,1507,1508,1509,348,1344,1338,1339,1350,1347,1351,1352,1353,1354,1379,1393,1326,1327,1328,1322,1323,1324,1240,1241,1243,2362,2351,2356,2357,2496,2491,2492,2466,2459,2437}
kept=[]
for r in rows:
 if r['detection_id'] in excluded:
  for folder,ext in [('images','jpg'),('labels','txt')]:
   p=root/'dataset'/folder/r['split']/(str(r['detection_id'])+'.'+ext)
   if p.exists():p.rename(p.with_suffix(p.suffix+'.excluded'))
 else:kept.append(r)
for a in ('train','val','test'):
 for b in ('train','val','test'):
  if a!=b:assert not ({r['session'] for r in kept if r['split']==a}&{r['session'] for r in kept if r['split']==b})
hashes=[hashlib.sha256((root.parent/r['image']).read_bytes()).hexdigest() for r in kept];assert len(hashes)==len(set(hashes))
(root/'reviewed-manifest.json').write_text(json.dumps(kept,indent=2))
(root/'annotation-audit.json').write_text(json.dumps(dict(excluded=sorted(excluded),reason='Wrong subject type or additional visible unlabeled subjects; original database confirmations preserved.',counts={s:dict(collections.Counter(r['kind'] for r in kept if r['split']==s)) for s in ('train','val','test')},limits='Human crop boxes may include margins or truncate tails. Existing pilot sessions now development only. Fresh heldout sessions are daylight; independent nighttime generalization remains unmeasured.'),indent=2))
model=YOLO(str(root.parent/'.cat-training-20261009/yolo11n.pt'))
data=dict(path=str(root/'dataset'),train='images/train',val='images/val',test='images/test',names=model.names)
(root/'dataset/data.yaml').write_text(yaml.safe_dump(data))
# Preserve the pretrained 80-class head, including cat/dog/person; do not replace it with a random single-class head.
start=time.monotonic()
def progress(t):
 (root/'progress.json').write_text(json.dumps(dict(epoch=t.epoch+1,elapsed_seconds=time.monotonic()-start,metrics={k:float(v) for k,v in t.metrics.items()}),indent=2))
model.add_callback('on_fit_epoch_end',progress)
model.train(data=str(root/'dataset/data.yaml'),epochs=16,imgsz=320,batch=4,device='cpu',workers=0,freeze=23,optimizer='AdamW',lr0=.0005,lrf=.2,warmup_epochs=1,patience=16,seed=20261009,deterministic=True,amp=False,cache=False,plots=False,save=True,project=str(root/'runs'),name='expanded-v1',exist_ok=False,mosaic=0,scale=.15,translate=.1,fliplr=.5,flipud=0,verbose=False)
(root/'complete.json').write_text(json.dumps(dict(elapsed_seconds=time.monotonic()-start,best=str(root/'runs/expanded-v1/weights/best.pt')),indent=2))
PY
cd .cat-training-expanded-20261009
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 ../.yolo-study-20261009/venv/bin/python -u train.py > training.log 2>&1
cat complete.json
