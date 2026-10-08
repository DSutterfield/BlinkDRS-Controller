"""Searchable sightings and explicitly reviewed batch labels; no inferred confirmations."""
import hashlib,json,math
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo
from identity_store import connect,require_clip

def window(start,end,tz='America/Chicago'):
 try:
  a=date.fromisoformat(start);b=date.fromisoformat(end);zone=ZoneInfo(tz)
 except (ValueError,KeyError) as exc:raise ValueError('Choose valid dates and timezone.') from exc
 if b<a or (b-a).days>31:raise ValueError('Choose a date range of at most32 days.')
 return datetime.combine(a,time.min,zone).astimezone(timezone.utc).isoformat(),datetime.combine(b+timedelta(days=1),time.min,zone).astimezone(timezone.utc).isoformat()

def token(row):
 keys=['detection_id','catalog_id','subject_type','identity_id','confirmed','embedding_model','embedding','crop_jpeg']
 return hashlib.sha256(repr(tuple(row[k] for k in keys)).encode()).hexdigest()

def vector(value):
 try:v=json.loads(value);n=math.sqrt(sum(float(x)**2 for x in v))
 except (TypeError,ValueError,OverflowError):return None
 if not 1<=len(v)<=4096 or not math.isfinite(n) or n<1e-8:return None
 return [float(x)/n for x in v] if all(math.isfinite(float(x)) for x in v) else None

def scope(start,end,camera):
 a,b=window(start,end);where='c.local_present=1 AND datetime(c.captured_at)>=datetime(?) AND datetime(c.captured_at)<datetime(?)';args=[a,b]
 if camera:where+=' AND COALESCE(CAST(c.device_id AS TEXT),c.device_name_snapshot,\'Unknown camera\')=?';args.append(camera)
 return where,args

def sightings(db,start,end,camera=None,identity_id=None,status='all',limit=100,offset=0):
 if status not in ['all','confirmed','suggested','unknown','review','possible']:raise ValueError('Invalid sighting status.')
 where,args=scope(start,end,camera)
 with connect(db) as conn:
  cameras=[dict(r) for r in conn.execute("SELECT DISTINCT COALESCE(CAST(device_id AS TEXT),device_name_snapshot,'Unknown camera') AS camera,COALESCE(device_name_snapshot,'Unknown camera') AS name FROM clips WHERE local_present=1 ORDER BY name")]
  counts=[dict(r) for r in conn.execute(f'''SELECT i.identity_id,i.name,i.subject_type,
   COUNT(DISTINCT CASE WHEN d.confirmed=1 THEN c.id END) AS confirmed_clips,
   COUNT(DISTINCT CASE WHEN d.confirmed=0 AND NOT EXISTS(SELECT1 FROM clip_detections x WHERE x.catalog_id=c.id AND x.identity_id=d.identity_id AND x.confirmed=1) THEN c.id END) AS suggested_clips,
   MIN(c.captured_at) AS first_seen,MAX(c.captured_at) AS last_seen
   FROM clips c JOIN clip_detections d ON d.catalog_id=c.id JOIN identities i USING(identity_id) WHERE {where} GROUP BY i.identity_id ORDER BY i.name'''.replace('SELECT1','SELECT 1'),args)]
  coverage=dict(conn.execute(f'''SELECT COUNT(*) AS total_clips,
   SUM(CASE WHEN j.state='done' THEN1 ELSE0 END) AS analyzed_clips,
   SUM(CASE WHEN EXISTS(SELECT1 FROM clip_detections d WHERE d.catalog_id=c.id AND d.confirmed=0) THEN1 ELSE0 END) AS clips_needing_review,
   SUM(CASE WHEN j.catalog_id IS NULL THEN1 ELSE0 END) AS not_queued_clips
   FROM clips c LEFT JOIN identity_analysis_jobs j ON j.catalog_id=c.id WHERE {where}'''.replace('SELECT1','SELECT 1').replace('THEN1','THEN 1').replace('ELSE0','ELSE 0'),args).fetchone())
  clauses=[where];params=list(args)
  if identity_id is not None:clauses.append('(d.identity_id=? OR (d.confirmed=0 AND EXISTS(SELECT 1 FROM identity_candidates p JOIN identities pi ON pi.identity_id=p.identity_id WHERE p.detection_id=d.detection_id AND p.identity_id=? AND pi.active=1 AND pi.subject_type=d.subject_type)))');params.extend([identity_id,identity_id])
  clauses+=({'confirmed':['d.confirmed=1'],'suggested':['d.confirmed=0','d.identity_id IS NOT NULL'],'unknown':['d.identity_id IS NULL'],'review':['d.confirmed=0'],'possible':['d.confirmed=0','EXISTS(SELECT 1 FROM identity_candidates p JOIN identities pi ON pi.identity_id=p.identity_id WHERE p.detection_id=d.detection_id AND pi.active=1 AND pi.subject_type=d.subject_type)']}.get(status,[]))
  where2=' AND '.join(clauses)
  total=conn.execute(f'SELECT COUNT(*) FROM clips c JOIN clip_detections d ON d.catalog_id=c.id WHERE {where2}',params).fetchone()[0]
  rows=[dict(r) for r in conn.execute(f'''SELECT d.*,c.captured_at,c.filename,c.device_name_snapshot AS camera_name,
   COALESCE(CAST(c.device_id AS TEXT),c.device_name_snapshot,'Unknown camera') AS camera,i.name,e.similarity,
   e.embedding,e.embedding_model,e.crop_jpeg,CASE WHEN e.crop_jpeg IS NOT NULL THEN1 ELSE0 END AS crop_available
   FROM clips c JOIN clip_detections d ON d.catalog_id=c.id LEFT JOIN identities i USING(identity_id)
   LEFT JOIN identity_evidence e USING(detection_id) WHERE {where2}
   ORDER BY datetime(c.captured_at) DESC,d.detection_id DESC LIMIT ? OFFSET ?'''.replace('THEN1','THEN 1').replace('ELSE0','ELSE 0'),params+[min(max(limit,1),200),max(offset,0)])]
  for row in rows:
   row['snapshot']=token(row)
   for private in ('embedding','embedding_model','crop_jpeg'):row.pop(private,None)
   row['possible_matches']=[dict(z) for z in conn.execute('SELECT i.name,p.rank FROM identity_candidates p JOIN identities i USING(identity_id) WHERE p.detection_id=? AND i.active=1 AND i.subject_type=? ORDER BY p.rank',(row['detection_id'],row['subject_type']))] if not row['confirmed'] else []
  by_id={r['identity_id']:r for r in counts}
  for item in conn.execute(f'''SELECT i.identity_id,i.name,i.subject_type,COUNT(DISTINCT c.id) AS possible_clips
   FROM identity_candidates p JOIN clip_detections d USING(detection_id) JOIN clips c ON c.id=d.catalog_id JOIN identities i ON i.identity_id=p.identity_id
   WHERE {where} AND d.confirmed=0 AND i.active=1 AND i.subject_type=d.subject_type AND (d.identity_id IS NULL OR d.identity_id<>p.identity_id) GROUP BY i.identity_id''',args):
   r=dict(item);entry=by_id.setdefault(r['identity_id'],dict(identity_id=r['identity_id'],name=r['name'],subject_type=r['subject_type'],confirmed_clips=0,suggested_clips=0));entry['possible_clips']=r['possible_clips']
  counts=sorted(by_id.values(),key=lambda r:r['name']);
  for r in counts:r.setdefault('possible_clips',0)
  return dict(counts=counts,coverage=coverage,cameras=cameras,total=total,rows=rows,offset=offset,timezone='America/Chicago',count_unit='distinct clips with a sighting, not distinct animals or total property population')

def review_groups(db,start,end,camera=None,limit=200):
 where,args=scope(start,end,camera)
 with connect(db) as conn:
  rows=[dict(r) for r in conn.execute(f'''SELECT d.*,c.captured_at,c.filename,c.device_name_snapshot AS camera_name,
   COALESCE(CAST(c.device_id AS TEXT),c.device_name_snapshot,'Unknown camera') AS camera,
   e.embedding,e.embedding_model,e.crop_jpeg,e.quality_score FROM clips c
   JOIN clip_detections d ON d.catalog_id=c.id JOIN identity_evidence e USING(detection_id)
   WHERE {where} AND d.confirmed=0 AND d.subject_type IN ('Cat','Dog') AND e.crop_jpeg IS NOT NULL
   ORDER BY datetime(c.captured_at) DESC,d.detection_id DESC LIMIT ?''',args+[min(max(limit,1),200)])]
 groups=[]
 for row in rows:
  v=vector(row['embedding']);row['_vector']=v;row['_time']=datetime.fromisoformat(row['captured_at'].replace('Z','+00:00')).replace(tzinfo=timezone.utc) if len(row['captured_at'])<=10 else datetime.fromisoformat(row['captured_at'].replace('Z','+00:00'))
  if row['_time'].tzinfo is None:row['_time']=row['_time'].replace(tzinfo=timezone.utc)
  found=None
  if v is not None and (row['quality_score'] or 0)>=.2:
   for group in groups:
    if len(group)>=20:continue
    first=group[0]
    if any(row[k]!=first[k] for k in ['camera','subject_type','embedding_model']):continue
    if any(g['catalog_id']==row['catalog_id'] or abs((g['_time']-row['_time']).total_seconds())>86400 or g['_vector'] is None or len(v)!=len(g['_vector']) or sum(a*b for a,b in zip(v,g['_vector']))<.80 for g in group):continue
    found=group;break
  if found is None:groups.append([row])
  else:found.append(row)
 public=[]
 for group in groups:
  members=[]
  for row in group:
   members.append({**{k:row[k] for k in ['detection_id','catalog_id','subject_type','captured_at','camera_name','identity_id','filename']},'snapshot':token(row)})
  public.append(dict(members=members,distinct_clips=len(members),description='Review pack: same camera, broadly similar appearance, within24hours. Review each selected thumbnail; similarity is not proof of identity.'))
 return dict(groups=public,reviewed_window=len(rows),limit=200,has_more=len(rows)==200)

def confirm_batch(db,identity_id,members):
 if not 1<=len(members)<=50 or len({m['detection_id'] for m in members})!=len(members):raise ValueError('Select1 to50 distinct sightings.')
 with connect(db) as conn:
  conn.execute('BEGIN IMMEDIATE');identity=conn.execute('SELECT * FROM identities WHERE identity_id=? AND active=1',(identity_id,)).fetchone()
  if identity is None:raise ValueError('Choose an active identity.')
  checked=[]
  for member in members:
   row=conn.execute('SELECT d.*,e.embedding,e.embedding_model,e.crop_jpeg FROM clip_detections d JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=?',(member['detection_id'],)).fetchone()
   if row is None or row['confirmed'] or row['subject_type']!=identity['subject_type'] or token(row)!=member['snapshot']:raise ValueError('A selected sighting changed. Refresh and review again; no labels were saved.')
   require_clip(conn,row['catalog_id']);checked.append(row)
  from identity_analysis import learn_confirmed
  for row in checked:
   conn.execute('UPDATE clip_detections SET identity_id=?,confirmed=1,confirmed_at=CURRENT_TIMESTAMP WHERE detection_id=?',(identity_id,row['detection_id']))
   conn.execute('DELETE FROM identity_samples WHERE detection_id=?',(row['detection_id'],));learn_confirmed(conn,row['detection_id'])
  return dict(confirmed=len(checked),identity_id=identity_id)



def track_views(db,catalog_id,detection_id):
 with connect(db) as conn:
  require_clip(conn,catalog_id)
  if not conn.execute('SELECT1 FROM clip_detections WHERE catalog_id=? AND detection_id=?'.replace('SELECT1','SELECT 1'),(catalog_id,detection_id)).fetchone():raise LookupError('Sighting not found.')
  return [dict(r) for r in conn.execute('SELECT view_id,seconds,quality_score FROM identity_track_views WHERE detection_id=? ORDER BY seconds',(detection_id,))]
def track_view_image(db,catalog_id,detection_id,view_id):
 with connect(db) as conn:
  require_clip(conn,catalog_id)
  row=conn.execute('SELECT v.crop_jpeg FROM identity_track_views v JOIN clip_detections d USING(detection_id) WHERE d.catalog_id=? AND d.detection_id=? AND v.view_id=?',(catalog_id,detection_id,view_id)).fetchone()
  if row is None:raise LookupError('Sighting view not found.')
  return row[0]


