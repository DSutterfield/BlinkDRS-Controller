set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 .venv-ai/bin/python -u - <<'PY'
import sqlite3,json,cv2,numpy as np,subprocess,hashlib,zipfile
from pathlib import Path
cv2.setNumThreads(1)
root=Path('.cat-training-expanded-20261009');root.mkdir(exist_ok=True)
c=sqlite3.connect('file:/home/dan/BlinkDVR/blink_catalog.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=c.execute("select d.detection_id,d.catalog_id,d.subject_type,d.first_seen_seconds,e.crop_jpeg,c.video_path,c.device_name_snapshot,c.captured_at from clip_detections d join identity_evidence e using(detection_id) join clips c on c.id=d.catalog_id where d.confirmed=1 and d.subject_type in ('Cat','Dog','Person') and e.detection_key like 'manual:%' and c.local_present=1 order by d.catalog_id,d.detection_id").fetchall()
manifest=[]
for r in rows:
 did=r['detection_id'];out=root/f'frame-{did}.jpg'
 p=subprocess.run(['ffmpeg','-v','error','-threads','1','-noautorotate','-ss',str(r['first_seen_seconds']),'-i',str(Path('/home/dan/BlinkDVR')/r['video_path']),'-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1'],capture_output=True,timeout=40)
 if p.returncode:continue
 im=cv2.imdecode(np.frombuffer(p.stdout,np.uint8),1);crop=cv2.imdecode(np.frombuffer(r['crop_jpeg'],np.uint8),1)
 if im is None or crop is None:continue
 h,w=crop.shape[:2]
 if h>im.shape[0] or w>im.shape[1]:continue
 m=cv2.matchTemplate(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),cv2.TM_CCOEFF_NORMED)
 _,score,_,(x,y)=cv2.minMaxLoc(m);m[max(0,y-h//2):y+h//2+1,max(0,x-w//2):x+w//2+1]=-1;runner=float(m.max())
 row=dict(clip=r['catalog_id'],detection_id=did,kind=r['subject_type'],camera=r['device_name_snapshot'],captured_at=r['captured_at'],seconds=r['first_seen_seconds'],box=[x,y,x+w,y+h],match_score=score,runnerup=runner,recoverable=score>=.90 and score-runner>=.05,frame_sha256=hashlib.sha256(p.stdout).hexdigest())
 cv2.imwrite(str(out),im,[cv2.IMWRITE_JPEG_QUALITY,95]);manifest.append(row)
 (root/'recovery-manifest.json').write_text(json.dumps(manifest,indent=2))
 print(did,row['clip'],row['kind'],round(score,4),row['recoverable'],flush=True)
review=[]
for r in manifest:
 if not r['recoverable']:continue
 im=cv2.imread(str(root/f"frame-{r['detection_id']}.jpg"));x,y,bx,by=r['box'];size=min(min(im.shape[:2]),max(256,bx-x+64,by-y+64));ax=max(0,min(im.shape[1]-size,(x+bx-size)//2));ay=max(0,min(im.shape[0]-size,(y+by-size)//2));im=im[ay:ay+size,ax:ax+size].copy();cv2.rectangle(im,(x-ax,y-ay),(bx-ax,by-ay),(0,255,0),2);im=cv2.resize(im,(320,320));cv2.putText(im,f"{r['detection_id']} {r['clip']} {r['kind']}",(4,20),cv2.FONT_HERSHEY_SIMPLEX,.45,(0,255,255),1);review.append(im)
for start in range(0,len(review),20):
 canvas=np.zeros((4*320,5*320,3),np.uint8)
 for j,im in enumerate(review[start:start+20]):canvas[j//5*320:(j//5+1)*320,j%5*320:(j%5+1)*320]=im
 cv2.imwrite(str(root/f'review-{start//20}.jpg'),canvas)
with zipfile.ZipFile(root/'review.zip','w',zipfile.ZIP_DEFLATED) as z:
 for f in root.glob('review-*.jpg'):z.write(f,f.name)
 z.write(root/'recovery-manifest.json','recovery-manifest.json')
print('COMPLETE',len(manifest),'accepted',sum(r['recoverable'] for r in manifest),flush=True)
PY
