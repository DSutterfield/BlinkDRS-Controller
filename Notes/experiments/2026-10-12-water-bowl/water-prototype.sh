set -e
cd /home/dan/BlinkDRS-Controller
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 15 .venv-ai/bin/python -u - <<'PY'
import os,json,sqlite3,datetime,hashlib,cv2,numpy as np,sys,zipfile,subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
root=Path('.water-combined-20261012');sys.path.insert(0,str(root.resolve()))
from water_sequence import link_sequences
from identity_models import LocalModels
from subject_type_check import resolve_animal_type,rank_types
from identity_analysis import normalized
model=LocalModels('ai-models');tz=ZoneInfo('America/Chicago')
manifest=json.loads(Path('.cat-training-expanded-20261009/reviewed-manifest.json').read_text())
samples=[r for r in manifest if r['split']=='test' and r['camera']=='Water Bowl']
evaluation_dates={datetime.datetime.fromisoformat(r['captured_at']).astimezone(tz).date() for r in samples}
evaluation_clips={r['clip'] for r in samples}
c=sqlite3.connect('file:/home/dan/BlinkDVR/blink_catalog.db?mode=ro',uri=True);c.row_factory=sqlite3.Row
references=[]
for kind in ('Person','Cat','Dog'):
 for row in c.execute('''select t.*,d.catalog_id,c.captured_at from subject_type_samples t join clip_detections d using(detection_id) join clips c on c.id=d.catalog_id where d.confirmed=1 and d.subject_type=t.subject_type and t.subject_type=? and t.model_version=? and t.embedding is not null and t.quality_score>=.2 order by t.detection_id desc''',(kind,model.pet_version)):
  if row['catalog_id'] in evaluation_clips or datetime.datetime.fromisoformat(row['captured_at']).astimezone(tz).date() in evaluation_dates:continue
  item=dict(row);item['vector']=normalized(json.loads(item['embedding']));references.append(item)
  if sum(r['subject_type']==kind for r in references)>=128:break
pred=json.loads((root/'input-predictions.json').read_text())['variants'];a={r['detection_id']:r for r in pred['original320']['records']};b={r['detection_id']:r for r in pred['shadow320']['records']}
def iou(a,b):
 v=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]));return v/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-v+1e-9)
records=[];observations=[]
for r in samples:
 did=r['detection_id'];im=cv2.imread(r['image']);frame=cv2.imread(str(Path('.cat-training-expanded-20261009')/f'frame-{did}.jpg'));h,w=frame.shape[:2]
 pairs=[(x,y) for x in a[did]['cats'] for y in b[did]['cats'] if iou(x['box'],y['box'])>=.35]
 proposals=[]
 for pair in pairs:
  for p in pair:
   if not any(p['box']==q['box'] for q in proposals):proposals.append(p)
 details=[];ax,ay,_,_=r['source_region'];gt=[r['box'][0]-ax,r['box'][1]-ay,r['box'][2]-ax,r['box'][3]-ay]
 for n,p in enumerate(proposals):
  x,y,bx,by=map(int,p['box']);x=max(0,x);y=max(0,y);bx=min(im.shape[1],bx);by=min(im.shape[0],by)
  if bx<=x or by<=y:continue
  crop=im[y:by,x:bx];vector,version,quality=model.type_embedding(crop)
  resolved=resolve_animal_type(references,'Cat',vector,version);ranks=rank_types(references,vector,version)
  detail={'box':p['box'],'quality':quality,'species':resolved,'ranks':ranks,'target_match':r['kind']=='Cat' and iou(p['box'],gt)>=.5};details.append(detail)
  # Alternative boundaries stay inside one observation, never multiple animals/votes.
 accepted={d['species'] for d in details if d['species'] in ('Cat','Dog')}
 species=next(iter(accepted)) if len(accepted)==1 and not any(d['species'] is None for d in details) else 'Unknown'
 record={'clip':r['clip'],'detection_id':did,'ground_truth':r['kind'],'candidate_found':any(d['target_match'] for d in details),'species':species,'alternatives':details};records.append(record)
 if details:
  # Geometry/appearance representative selected by source crop quality, never ground truth.
  chosen=max(details,key=lambda d:d['quality']);x,y,bx,by=map(int,chosen['box']);crop=im[max(0,y):min(im.shape[0],by),max(0,x):min(im.shape[1],bx)]
  vector,version,quality=model.type_embedding(crop)
  observations.append(dict(id=did,camera='Water Bowl',clip=r['clip'],time=datetime.datetime.fromisoformat(r['captured_at']).timestamp()+r['seconds'],quality=quality,model=version,hash=hashlib.sha256(crop.tobytes()).hexdigest(),species=species,box=[(x+ax)/w,(y+ay)/h,(bx+ax)/w,(by+ay)/h],vector=vector))
 print('verified',did,r['clip'],r['kind'],species,len(details),flush=True)
report={'scope':'Development contextual-crop verification; not full-video discovery or independent accuracy. No named identity propagation.','reference_sources':{k:len({r['catalog_id'] for r in references if r['subject_type']==k}) for k in ('Person','Cat','Dog')},'excluded_reference_dates_chicago':[str(d) for d in sorted(evaluation_dates)],'records':records,'sequence_links':link_sequences(observations),'feature_model':model.pet_version}
report['summary']={'cat_targets':sum(r['ground_truth']=='Cat' for r in records),'matched_cat_candidates':sum(r['candidate_found'] for r in records),'matched_and_cat_supported':sum(r['candidate_found'] and r['species']=='Cat' for r in records),'noncat_images':sum(r['ground_truth']!='Cat' for r in records),'noncat_images_with_cat_support':sum(r['ground_truth']!='Cat' and r['species']=='Cat' for r in records),'possible_sequence_links':len(report['sequence_links'])}
(root/'verification-results.json').write_text(json.dumps(report,indent=2));print('SUMMARY',json.dumps(report['summary']),flush=True)
# Reserve recent sequences for human ground truth BEFORE model predictions.
clips={r['id']:r for r in json.loads((root/'inventory.json').read_text())};ordered=sorted(clips.values(),key=lambda r:datetime.datetime.fromisoformat(r['captured_at']).timestamp())
selected=[]
for label,predicate in [('daytime',lambda t:9<=t.hour<17),('nighttime',lambda t:t.hour>=21 or t.hour<5)]:
 pairs=[]
 for x,y in zip(ordered,ordered[1:]):
  tx=datetime.datetime.fromisoformat(x['captured_at']);ty=datetime.datetime.fromisoformat(y['captured_at'])
  if predicate(tx.astimezone(tz)) and predicate(ty.astimezone(tz)) and 0<(ty-tx).total_seconds()<=60:pairs.append((x,y))
 if pairs:
  for r in pairs[-1]:selected.append(dict(r,period_by_clock=label))
tiles=[];review=[]
for r in selected:
 path=Path('/home/dan/BlinkDVR')/r['video_path'];cap=cv2.VideoCapture(str(path));fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
 if fps<=0 or count<=0:cap.release();continue
 for fraction in (.1,.5,.9):
  index=int((count-1)*fraction);cap.set(cv2.CAP_PROP_POS_FRAMES,index);ok,im=cap.read()
  if not ok:continue
  name=f'reserve-{r["id"]}-{int(fraction*100)}.jpg';cv2.imwrite(str(root/name),im,[cv2.IMWRITE_JPEG_QUALITY,95]);seconds=index/fps
  thumb=cv2.resize(im,(640,360));cv2.rectangle(thumb,(0,0),(640,28),(0,0,0),-1)
  text=f'{r["id"]}  {seconds:.1f}s  {r["period_by_clock"]}';cv2.putText(thumb,text,(8,20),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1);tiles.append(thumb)
  review.append({'clip':r['id'],'captured_at':r['captured_at'],'period_by_clock':r['period_by_clock'],'seconds':seconds,'image':name,'labels':None})
 cap.release()
for start in range(0,len(tiles),6):
 canvas=np.zeros((3*360,2*640,3),np.uint8)
 for i,tile in enumerate(tiles[start:start+6]):canvas[(i//2)*360:(i//2+1)*360,(i%2)*640:(i%2+1)*640]=tile
 cv2.imwrite(str(root/f'review-sheet-{start//6}.jpg'),canvas)
(root/'reserved-review.json').write_text(json.dumps(review,indent=2))
with zipfile.ZipFile(root/'prototype-review.zip','w',zipfile.ZIP_DEFLATED) as z:
 for p in root.iterdir():
  if p.suffix in ('.jpg','.json','.py') and p.name!='inventory.json':z.write(p,p.name)
print('COMPLETE reserved frames',len(review),flush=True)
PY
systemctl is-active blink-dvr.service blink-identity.service
