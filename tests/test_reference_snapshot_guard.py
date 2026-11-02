import unittest,json,sqlite3,hashlib
from unittest.mock import patch
from contextlib import closing
import test_pc_helper_queue
from identity_store import connect,assign_identity,create_identity
from identity_analysis import publish_prepared
from pc_helper_queue import ReferenceChanged,ReviewChanged,transaction
from pc_helper_inputs import reference_guard,validate_reference_guard,context_parts
from subject_exclusions import mark
class ReferenceGuardTests(unittest.TestCase):
 setUp=test_pc_helper_queue.HelperQueueTests.setUp
 lease=test_pc_helper_queue.HelperQueueTests.lease
 packet=test_pc_helper_queue.HelperQueueTests.packet
 observation=test_pc_helper_queue.HelperQueueTests.observation
 row=test_pc_helper_queue.HelperQueueTests.row
 def reference(self,cid=2,key='teacher',quality=1.):
  item=dict(self.observation(key),quality_score=quality,crop_jpeg=b'\xff\xd8'+key.encode()+b'\xff\xd9')
  with connect(self.db) as c:
   c.execute('BEGIN IMMEDIATE');publish_prepared(c,cid,'source'+str(cid),[item],[(item,None,None,[])])
   did=c.execute('select detection_id from clip_detections where catalog_id=? and confirmed=0 order by detection_id desc',(cid,)).fetchone()[0]
   c.execute('update clip_detections set confirmed=1 where detection_id=?',(did,))
   c.execute('insert into subject_type_samples values(?,?,?,?,?,?)',(did,'Cat',json.dumps([1.,0.]),'pet',quality,hashlib.sha256(item['crop_jpeg']).hexdigest()))
  return did
 def test_cleanup_of_excluded_neighbor_does_not_invalidate(self):
  self.reference()
  with connect(self.db) as c:
   c.execute("insert into devices(id,blink_device_id,name) values(1,'cam','cam')")
   c.execute("update clips set device_id=1,blink_media_id='protected' where id=3")
   c.execute("update clips set device_id=1 where id=4")
   c.execute("insert into evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) values('protected',3,'test',1,'cam','2026-10-01','sha','copy')")
   self.assertIsNotNone(c.execute('select 1 from evaluation_excluded_clips where catalog_id=4').fetchone())
  m=self.lease()
  with transaction(self.db) as c:c.execute('delete from clips where id=4')
  self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
 def test_new_type_example_after_export_does_not_restart(self):
  self.reference();m=self.lease();self.reference(3,'new')
  self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
  self.assertEqual(len(m['reference_guard']['rows']),1)
 def test_new_reference_during_snapshot_copy_is_pinned(self):
  self.reference();claimed=self.q.claim('PC-slot-1','contract');self.reference(3,'new')
  m=self.q.export(claimed['token'],self.root/claimed['token'])
  self.assertEqual(len(m['reference_guard']['rows']),2)
  self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
 def test_used_reference_correction_rejected_without_publication(self):
  did=self.reference();m=self.lease()
  with transaction(self.db) as c:c.execute("update subject_type_samples set embedding='[0,1]' where detection_id=?",(did,))
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m));self.assertEqual(self.row('select count(*) from clip_detections where catalog_id=1')[0],0)
 def test_removed_reference_rejected(self):
  did=self.reference();m=self.lease()
  with transaction(self.db) as c:c.execute('delete from subject_type_samples where detection_id=?',(did,))
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))
 def test_used_reference_crop_changed_rejected(self):
  did=self.reference();m=self.lease()
  with transaction(self.db) as c:c.execute("update identity_evidence set crop_jpeg=x'FFD87374616C65FFD9' where detection_id=?",(did,))
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))
 def test_used_reference_exclusion_rejected(self):
  did=self.reference();m=self.lease();mark(self.db,2,did)
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))
 def test_unrelated_exclusion_does_not_restart(self):
  self.reference();other=self.reference(3,'unusable',quality=0.);m=self.lease();mark(self.db,3,other)
  self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
 def test_same_image_exclusion_is_relevant(self):
  self.reference();other=self.reference(3,'teacher',quality=0.);m=self.lease();mark(self.db,3,other)
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))
 def test_same_clip_confirmation_remains_protected(self):
  self.reference();m=self.lease();assign_identity(self.db,1,None,'Dog')
  self.assertRaises(ReviewChanged,self.q.commit,self.packet(m));self.assertEqual(self.row('select subject_type,confirmed from clip_detections where catalog_id=1'),('Dog',1))
 def test_pinned_record_displaced_from_cap_still_checked(self):
  did=self.reference()
  with connect(self.db) as c:
   c.execute("update clip_detections set subject_type='Dog' where detection_id=?",(did,));c.execute("update subject_type_samples set subject_type='Dog' where detection_id=?",(did,));g=reference_guard(c,1)
   for n in range(128):
    other=c.execute("insert into clip_detections(catalog_id,subject_type,confirmed) values(3,'Dog',1)").lastrowid
    jpeg=b'\xff\xd8new'+str(n).encode()+b'\xff\xd9'
    c.execute("insert into identity_evidence values(?,3,?,'Dog',?,'[1,0]','pet',1,NULL)",(other,'new'+str(n),jpeg))
    c.execute("insert into subject_type_samples values(?,'Dog','[1,0]','pet',1,?)",(other,hashlib.sha256(jpeg).hexdigest()))
   self.assertNotIn(did,dict(reference_guard(c,1)['rows']))
   self.assertTrue(validate_reference_guard(c,1,g))
   c.execute("update subject_type_samples set quality_score=.1 where detection_id=?",(did,))
   self.assertFalse(validate_reference_guard(c,1,g))
 def test_exclusion_policy_change_remains_hard_guard(self):
  self.reference();m=self.lease()
  with connect(self.db) as c:c.execute("insert into evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) values('newhold',3,'test',1,'cam','2026-09-01','sha','copy')")
  self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))

 def test_added_reference_after_snapshot_copy_does_not_restart_export(self):
  from pc_helper_snapshot import export_references
  self.reference();claimed=self.q.claim('PC-slot-1','contract')
  def copying(*args,**kwargs):
   result=export_references(*args,**kwargs);self.reference(3,'after-copy');return result
  with patch('pc_helper_snapshot.export_references',side_effect=copying):m=self.q.export(claimed['token'],self.root/claimed['token'])
  self.assertEqual(len(m['reference_guard']['rows']),1)
  self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
 def test_corrected_reference_after_snapshot_copy_is_rejected(self):
  from pc_helper_snapshot import export_references
  did=self.reference();claimed=self.q.claim('PC-slot-1','contract')
  def copying(*args,**kwargs):
   result=export_references(*args,**kwargs)
   with transaction(self.db) as c:c.execute("update subject_type_samples set embedding='[0,1]' where detection_id=?",(did,))
   return result
  with patch('pc_helper_snapshot.export_references',side_effect=copying):self.assertRaises(ReferenceChanged,self.q.export,claimed['token'],self.root/claimed['token'])
 def test_manual_reviewed_asset_exclusion_is_relevant(self):
  self.reference();other=self.reference(3,'asset-only',quality=0.)
  crop=hashlib.sha256(b'\xff\xd8asset-only\xff\xd9').hexdigest()
  record=dict(detection_id=999,catalog_id=4,crop_hash=crop,subject_type='Cat')
  with patch('pc_helper_inputs._review_asset',return_value=('asset-sha',[record])):
   m=self.lease();self.assertEqual(len(m['reference_guard']['reviewed']),1);mark(self.db,3,other)
   self.assertRaises(ReferenceChanged,self.q.commit,self.packet(m))
 def test_guard_check_has_consistent_view_while_new_review_arrives(self):
  from pc_helper_inputs import validate_reference_guard as validate
  with closing(sqlite3.connect(self.db)) as c:c.execute('pragma journal_mode=WAL')
  self.reference();m=self.lease()
  with transaction(self.db) as c:c.execute("update clips set captured_at='2026-10-02' where id=4")
  once=[]
  def checking(*args):
   result=validate(*args)
   if not once:once.append(True);self.reference(3,'during-guard')
   return result
  with patch('pc_helper_inputs.validate_reference_guard',side_effect=checking):self.assertEqual(self.q.commit(self.packet(m))['status'],'accepted')
