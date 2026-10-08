import unittest,sqlite3
from contextlib import closing
from test_reference_photos import Photos
from identity_store import assign_identity,get_clip_identities,connect,create_identity,update_identity
from reference_photos import process_photos
from catalog_store import open_catalog_notifications
class ProfileEdits(Photos):
 def clips(self):
  with connect(self.db) as c:
   for cid in (1,2):c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) VALUES(?,?,?,?,1,'pir')",(cid,f'{cid}.mp4',f'{cid}.mp4','2026-10-05'))
  for cid in (1,2):assign_identity(self.db,cid,self.i,'Person')
 def test_rename_updates_every_clip_and_notifies(self):
  self.clips(); reader=open_catalog_notifications(self.db);self.addCleanup(reader.close)
  before=reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
  response=self.api.put(f'/api/v1/identities/{self.i}',json={'name':'Correct Name','subject_type':'Person'})
  self.assertEqual(response.status_code,200);self.assertEqual(response.json()['identity_id'],self.i)
  for cid in (1,2):self.assertEqual(get_clip_identities(self.db,cid)[0]['name'],'Correct Name')
  self.assertGreater(reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0],before)
 def test_type_changes_all_labels_and_requeues_photos(self):
  self.clips();p=self.upload();process_photos(self.db,self.models([1.,0.]))
  with connect(self.db) as c:
   did=c.execute('SELECT detection_id FROM clip_detections WHERE catalog_id=1').fetchone()[0]
   c.execute("INSERT INTO identity_evidence(detection_id,catalog_id,detection_key,subject_type,crop_jpeg,embedding,embedding_model,quality_score) VALUES(?,1,'fixture','Person',?,'[1,0]','face',.9)",(did,b'image'))
  r=self.api.put(f'/api/v1/identities/{self.i}',json={'name':'Test','subject_type':'Cat'});self.assertEqual(r.status_code,200)
  for cid in (1,2):self.assertEqual(get_clip_identities(self.db,cid)[0]['subject_type'],'Cat')
  with connect(self.db) as c:
   self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
   self.assertEqual(c.execute('SELECT state FROM identity_photos').fetchone()[0],'pending')
   self.assertEqual(c.execute('SELECT embedding_model FROM identity_evidence').fetchone()[0],'manual-pending')
  process_photos(self.db,self.models([1.,0.]));self.assertEqual(self.api.get(self.url).json()[0]['state'],'ready')
 def test_collision_validation_and_missing_are_atomic(self):
  self.clips();create_identity(self.db,'Existing','Cat')
  for body in ({'name':'Existing','subject_type':'Cat'},{'name':' ','subject_type':'Person'},{'name':'X','subject_type':'Bird'}):
   self.assertEqual(self.api.put(f'/api/v1/identities/{self.i}',json=body).status_code,422)
  self.assertEqual(get_clip_identities(self.db,1)[0]['name'],'Test')
  self.assertEqual(self.api.put('/api/v1/identities/99999',json={'name':'X','subject_type':'Cat'}).status_code,404)
 def test_type_change_during_photo_inference_cannot_publish_old_reference(self):
  self.upload();process_photos(self.db,self.models([1.,0.]),lambda:update_identity(self.db,self.i,'Test','Dog'))
  with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
  self.assertEqual(self.api.get(self.url).json()[0]['state'],'pending')
if __name__=='__main__':unittest.main()
