set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 .venv-ai/bin/python - <<'PY'
import json,cv2,numpy as np,hashlib,datetime,collections,zipfile
from pathlib import Path
cv2.setNumThreads(1);root=Path('.cat-training-expanded-20261009');rows=json.loads((root/'recovery-manifest.json').read_text())
old={r['clip'] for r in json.loads(Path('.cat-training-20261009/split-manifest.json').read_text())};old|={24102,23392,23281}
# Whole camera sessions separated by gaps of at least two hours; previous pilot clips force their session to development.
sessions=[]
for camera in sorted({r['camera'] for r in rows}):
 current=[];last=None
 for r in sorted([r for r in rows if r['camera']==camera],key=lambda r:r['captured_at']):
  t=datetime.datetime.fromisoformat(r['captured_at'])
  if last is not None and (t-last).total_seconds()>7200:sessions.append(current);current=[]
  current.append(r);last=t
 if current:sessions.append(current)
for session in sessions:
 key=session[0]['camera']+':'+session[0]['captured_at'];number=int(hashlib.sha256(('expanded-v1:'+key).encode()).hexdigest()[:8],16)%10
 split='train' if any(r['clip'] in old for r in session) else ('test' if number<2 else 'val' if number<4 else 'train')
 for r in session:r.update(split=split,session=key)
tiles=[]
for r in rows:
 if not r['recoverable']:continue
 did=r['detection_id'];im=cv2.imread(str(root/f'frame-{did}.jpg'));h,w=im.shape[:2];x,y,bx,by=r['box']
 size=min(h,w,max(256,bx-x+48,by-y+48));ax=max(0,min(w-size,(x+bx-size)//2));ay=max(0,min(h-size,(y+by-size)//2));crop=im[ay:ay+size,ax:ax+size]
 image_dir=root/'dataset'/'images'/r['split'];label_dir=root/'dataset'/'labels'/r['split'];image_dir.mkdir(parents=True,exist_ok=True);label_dir.mkdir(parents=True,exist_ok=True)
 name=str(did);cv2.imwrite(str(image_dir/(name+'.jpg')),crop,[cv2.IMWRITE_JPEG_QUALITY,95]);cls={'Cat':15,'Dog':16,'Person':0}[r['kind']]
 (label_dir/(name+'.txt')).write_text(f'{cls} {(x+bx-2*ax)/2/size:.8f} {(y+by-2*ay)/2/size:.8f} {(bx-x)/size:.8f} {(by-y)/size:.8f}\n')
 r.update(image=str(image_dir/(name+'.jpg')),source_region=[ax,ay,ax+size,ay+size]);tiles.append(r)
(root/'split-manifest.json').write_text(json.dumps(tiles,indent=2));print('splits',dict(collections.Counter((r['split'],r['kind']) for r in tiles)))
print('sessions',[(s[0]['session'],s[0]['split'],len(s)) for s in sessions])
with zipfile.ZipFile(root/'dataset-review.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in root.glob('review-*.jpg'):z.write(p,p.name)
 z.write(root/'split-manifest.json','split-manifest.json')
PY
