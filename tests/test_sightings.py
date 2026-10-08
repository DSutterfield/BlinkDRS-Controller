import unittest,json,math
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
import test_identities
from identity_store import connect,create_identity,get_clip_identities,assign_identity
from identity_analysis import publish_detections
from sightings import sightings,review_groups,confirm_batch,window
from identity_api import install_identity_api
class SightingsTests(unittest.TestCase):
 setUp=test_identities.IdentityTests.setUp
 def prepare(self):
  with connect(self.db) as c:c.execute("UPDATE clips SET captured_at='2026-10-03T15:00:00+00:00',device_name_snapshot='Yard'")
  for cid in [1,2]:publish_detections(self.db,cid,str(cid),[dict(key='one',subject_type='Cat',confidence=.8,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'jpeg',embedding=[1,0],embedding_model='test',quality_score=.8)])
  return create_identity(self.db,'Lola','Cat')['identity_id']
 def groups(self):return review_groups(self.db,'2026-10-03','2026-10-03')['groups']
 def test_batch_labels_and_references(self):
  iid=self.prepare();group=self.groups()[0];self.assertEqual(len(group['members']),2);result=confirm_batch(self.db,iid,group['members']);self.assertEqual(result['confirmed'],2)
  self.assertTrue(all(get_clip_identities(self.db,i)[0]['confirmed'] for i in [1,2]));self.assertEqual(self.groups(),[])
  with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],2)
 def test_stale_batch_is_atomic(self):
  iid=self.prepare();members=self.groups()[0]['members'];assign_identity(self.db,1,iid,'Cat',get_clip_identities(self.db,1)[0]['detection_id'])
  with self.assertRaises(ValueError):confirm_batch(self.db,iid,members)
  self.assertFalse(get_clip_identities(self.db,2)[0]['confirmed'])
 def test_same_clip_and_model_and_camera_not_grouped(self):
  self.prepare()
  with connect(self.db) as c:c.execute("UPDATE identity_evidence SET embedding_model='other' WHERE catalog_id=2")
  self.assertEqual(len(self.groups()),2)
  with connect(self.db) as c:
   c.execute("UPDATE identity_evidence SET embedding_model='test'");c.execute("UPDATE clips SET device_name_snapshot='Porch' WHERE id=2")
  self.assertEqual(len(self.groups()),2)
 def test_grouping_requires_all_pairs(self):
  self.prepare()
  with connect(self.db) as c:
   c.execute("INSERT INTO clips(id,filename,video_path,captured_at,device_name_snapshot) VALUES(3,'3.mp4','3.mp4','2026-10-03T15:01:00+00:00','Yard')")
   c.execute('UPDATE identity_evidence SET embedding=? WHERE catalog_id=2',(json.dumps([math.cos(.4),math.sin(.4)]),))
  publish_detections(self.db,3,'3',[dict(key='one',subject_type='Cat',confidence=.8,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'jpeg',embedding=[math.cos(.8),math.sin(.8)],embedding_model='test',quality_score=.8)])
  self.assertEqual(sorted(len(g['members']) for g in self.groups()),[1,2])
 def test_mixed_species_rejected(self):
  self.prepare();dog=create_identity(self.db,'Duke','Dog')['identity_id']
  with self.assertRaises(ValueError):confirm_batch(self.db,dog,self.groups()[0]['members'])
 def test_counts_distinct_clips_and_confirmation_priority(self):
  iid=self.prepare();did=get_clip_identities(self.db,1)[0]['detection_id'];assign_identity(self.db,1,iid,'Cat',did)
  with connect(self.db) as c:c.execute("INSERT INTO clip_detections(catalog_id,identity_id,subject_type,confirmed) VALUES(1,?,'Cat',0)",(iid,));c.execute('UPDATE clip_detections SET identity_id=? WHERE catalog_id=2',(iid,))
  result=sightings(self.db,'2026-10-03','2026-10-03');self.assertEqual(result['counts'][0]['confirmed_clips'],1);self.assertEqual(result['counts'][0]['suggested_clips'],1)
  self.assertEqual(result['coverage']['total_clips'],2)
 def test_timeline_snapshot_can_confirm_without_exposing_evidence(self):
  iid=self.prepare();result=sightings(self.db,'2026-10-03','2026-10-03')
  members=[]
  for r in result['rows']:
   self.assertNotIn('crop_jpeg',r);self.assertNotIn('embedding',r);self.assertEqual(len(r['snapshot']),64)
   members.append({'detection_id':r['detection_id'],'snapshot':r['snapshot']})
  self.assertEqual(confirm_batch(self.db,iid,members)['confirmed'],2)
 def test_dst_day(self):
  a,b=window('2026-11-01','2026-11-01');from datetime import datetime
  self.assertEqual((datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds(),25*3600)
 def test_api_validation_and_html(self):
  self.prepare();app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db));client=TestClient(app)
  self.assertEqual(client.get('/sightings').status_code,200);self.assertEqual(client.get('/api/v1/sightings?start=2026-10-03&end=2026-10-03').status_code,200)
  self.assertEqual(client.get('/api/v1/sightings?start=bad&end=2026-10-03').status_code,422)
  self.assertEqual(client.post('/api/v1/sightings/confirm-batch',json={'identity_id':1,'members':[]}).status_code,422)
if __name__=='__main__':unittest.main()

