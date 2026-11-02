import os,copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import review_link_pilot as pilot
from pc_helper_wire import encode,decode,canonical
from identity_store import connect
from identity_analysis import ensure_analysis,publish_detections

class PilotIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{pilot.PILOT_ENV:'1',pilot.INFERENCE_ENV:'1','BLINK_DOG_REVIEW_PILOT':'1'});self.env.start();self.addCleanup(self.env.stop)
 def obs(self,key,start=0,end=1):
  jpeg=b'\xff\xd8'+key.encode()+b'\xff\xd9'
  return dict(key=key,subject_type='Cat',confidence=.8,first_seen_seconds=start,last_seen_seconds=end,detector_model='test',embedding=[1.]+[0.]*383,embedding_model='dinov2-small-cls-v1:test',quality_score=1.,crop_jpeg=jpeg,crop_seconds=start,views=[dict(seconds=start,crop_jpeg=jpeg,embedding=[1.]+[0.]*383,embedding_model='dinov2-small-cls-v1:test',quality_score=1.)])
 def linked(self):
  a,b=self.obs('a'),self.obs('b',2,3)
  for t in [a,b]:t['_review_link_keys']=['a','b']
  return [a,b]
 def test_disabled_does_not_read_recording(self):
  tracks=self.linked()
  with patch.dict(os.environ,{pilot.PILOT_ENV:'0'}),patch.object(pilot,'GrayFrames',side_effect=AssertionError('must not open video')):
   self.assertIs(pilot.annotate('missing',tracks,object()),tracks)
 def test_pool_is_ephemeral_and_nonmutating(self):
  tracks=self.linked();before=copy.deepcopy(tracks);pooled=pilot.scoring_item(tracks[0],tracks);self.assertEqual(len(pooled['views']),2);self.assertEqual(tracks,before);self.assertEqual(len(tracks[0]['views']),1)
 def test_confirmed_never_pooled(self):
  tracks=self.linked();tracks[1]['confirmed']=True;self.assertIs(pilot.scoring_item(tracks[0],tracks),tracks[0])
 def test_concurrent_never_pooled(self):
  tracks=self.linked();tracks[1]['first_seen_seconds']=.5;self.assertIs(pilot.scoring_item(tracks[0],tracks),tracks[0])
 def manifest(self):return dict(token='token',video_sha256='fingerprint',contract='test')
 def test_helper_roundtrip_keeps_review_links_and_original_views(self):
  tracks=self.linked();_,out=decode(encode(self.manifest(),tracks));self.assertEqual(out[0]['_review_link_keys'],['a','b']);self.assertEqual(len(out[0]['views']),1);self.assertEqual(len(pilot.scoring_item(out[0],out)['views']),2)
 def test_helper_rejects_asymmetric_unknown_or_concurrent_groups(self):
  for fault in ['asymmetric','unknown','overlap','species']:
   tracks=self.linked()
   if fault=='asymmetric':tracks[1].pop('_review_link_keys')
   if fault=='unknown':tracks[0]['_review_link_keys']=['a','missing']
   if fault=='overlap':tracks[1]['first_seen_seconds']=1
   if fault=='species':tracks[1]['subject_type']='Dog'
   with self.subTest(fault=fault),self.assertRaises(ValueError):encode(self.manifest(),tracks)
 def database(self,temp):
  db=Path(temp)/'fixture.sqlite';raw=sqlite3.connect(db);raw.executescript((Path(__file__).resolve().parents[1]/'sql/catalog_schema_v1.sql').read_text());raw.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present,blink_media_id) VALUES(1,'one.mp4','one.mp4','2026-11-01',1,'one')");raw.commit();raw.close()
  with connect(db) as conn:ensure_analysis(conn);conn.execute("INSERT INTO identities(identity_id,name,subject_type) VALUES(1,'Lola','Cat')")
  return db
 def test_publication_hints_only_and_no_borrowed_training_views(self):
  with tempfile.TemporaryDirectory() as temp:
   db=self.database(temp);tracks=self.linked();seen=[]
   def recommend(conn,item,cid,ranks):
    seen.append(len(item['views']));return dict(suggested_identity_id=1,suggested_name='Lola',suggestion_tier='possible',review_required=True)
   with patch('cat_review_evidence.recommend',side_effect=recommend):publish_detections(db,1,'fingerprint',tracks)
   with connect(db) as conn:
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_track_views').fetchone()[0],2)
    self.assertTrue(all(r[0] is None and not r[1] for r in conn.execute('SELECT identity_id,confirmed FROM clip_detections')))
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM cat_review_cache').fetchone()[0],2)
    conn.execute("UPDATE clip_detections SET identity_id=1,confirmed=1,confirmed_at='kept' WHERE detection_id=(SELECT MIN(detection_id) FROM clip_detections)")
   with patch('cat_review_evidence.recommend',side_effect=recommend):publish_detections(db,1,'fingerprint',tracks)
   with connect(db) as conn:
    self.assertEqual(tuple(conn.execute('SELECT identity_id,confirmed,confirmed_at FROM clip_detections WHERE detection_id=(SELECT MIN(detection_id) FROM clip_detections)').fetchone()),(1,1,'kept'))
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_track_views').fetchone()[0],2)
   self.assertEqual(seen,[2,2,1,1])
 def test_budget_failure_is_safe_and_closes_reader(self):
  tracks=self.linked();reader=SimpleNamespace(fps=30,close=lambda:None)
  with patch.object(pilot,'GrayFrames',return_value=reader),patch('review_link_core.evaluate',side_effect=pilot.PilotBudgetExceeded('limit')):
   result=pilot.annotate('unused',tracks,SimpleNamespace(cv2=None));self.assertTrue(all('_review_link_keys' not in t for t in result));self.assertEqual(tracks[0]['_review_link_keys'],['a','b'])
 def test_protected_confirmations_do_not_become_learning_samples(self):
  from identity_analysis import learn_confirmed
  with tempfile.TemporaryDirectory() as temp:
   db=self.database(temp)
   with connect(db) as conn:
    conn.execute("INSERT INTO evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) VALUES('one',1,'baseline',1,'camera','2026-11-01','hash','copy.mp4')")
   with patch('cat_review_evidence.recommend',return_value=None):publish_detections(db,1,'fingerprint',self.linked())
   with connect(db) as conn:
    did=conn.execute('SELECT MIN(detection_id) FROM clip_detections').fetchone()[0];conn.execute('UPDATE clip_detections SET identity_id=1,confirmed=1 WHERE detection_id=?',(did,));learn_confirmed(conn,did)
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM evaluation_holdout_labels WHERE active=1').fetchone()[0],1)
 def test_actual_worker_hook_retains_source_boxes(self):
  import cv2,numpy as np
  from identity_worker import analyze_video
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)/'test.avi';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(160,120));rng=np.random.default_rng(11)
   for _ in range(12):writer.write(rng.integers(0,256,(120,160,3),dtype=np.uint8))
   writer.release()
   model=SimpleNamespace(cv2=cv2,pet_version='dinov2-small-cls-v1:test',detector_version='test',detect=lambda frame:[dict(subject_type='Cat',confidence=.9,box=[20,20,100,100])],embedding=lambda crop,kind:([1.]+[0.]*383,'dinov2-small-cls-v1:test',1.))
   with patch('animal_track_cleanup.cleanup',side_effect=lambda tracks,period,models:(tracks,dict(merged_fragments=[],background_unknowns=[]))),patch('cat_review_small.enrich',side_effect=lambda tracks,models:tracks),patch('review_link_pilot.annotate',wraps=pilot.annotate) as hook:
    result=analyze_video(path,model,max_frames=2)
   self.assertEqual(hook.call_count,1);self.assertTrue(result);self.assertTrue(all('box' in v for t in result for v in t.get('views',[])))
 def test_rejected_source_is_not_borrowed(self):
  import hashlib
  with tempfile.TemporaryDirectory() as temp:
   db=self.database(temp);tracks=self.linked()
   with connect(db) as conn:
    conn.execute('INSERT INTO identity_rejections(catalog_id,detection_key) VALUES(?,?)',(1,hashlib.sha256(b'fingerprint:a').hexdigest()))
    safe=pilot.publication_sources(conn,1,'fingerprint',tracks)
    self.assertTrue(all('_review_link_keys' not in t for t in safe));self.assertIn('_review_link_keys',tracks[0])
 def test_no_identifiable_source_is_not_borrowed(self):
  with tempfile.TemporaryDirectory() as temp:
   db=self.database(temp);tracks=self.linked()
   with patch('cat_review_evidence.recommend',return_value=None):publish_detections(db,1,'fingerprint',tracks)
   with connect(db) as conn:
    did=conn.execute('SELECT MIN(detection_id) FROM clip_detections').fetchone()[0];conn.execute('INSERT INTO subject_analysis_exclusions(detection_id,crop_sha256) VALUES(?,?)',(did,'hash'))
    safe=pilot.publication_sources(conn,1,'fingerprint',tracks);self.assertTrue(all('_review_link_keys' not in t for t in safe))
 def test_existing_provisional_identity_is_preserved_without_confirmation(self):
  with tempfile.TemporaryDirectory() as temp:
   db=self.database(temp)
   with patch('identity_analysis.match_identity',return_value=(1,.96)),patch('cat_review_evidence.recommend',return_value=None):publish_detections(db,1,'fingerprint',[self.obs('a')])
   with connect(db) as conn:
    self.assertEqual(tuple(conn.execute('SELECT identity_id,confirmed FROM clip_detections').fetchone()),(1,0))
    self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
 def test_controller_scoring_flag_alone_never_starts_inference(self):
  tracks=self.linked()
  with patch.dict(os.environ,{pilot.PILOT_ENV:'1',pilot.INFERENCE_ENV:'0'}),patch.object(pilot,'GrayFrames',side_effect=AssertionError('must not decode on scoring process')):
   self.assertIs(pilot.annotate('missing',tracks,object()),tracks)
 def test_checkpoint_cancellation_is_not_swallowed_as_decode_failure(self):
  from unittest.mock import Mock
  reader=SimpleNamespace(fps=30,close=Mock())
  with patch.object(pilot,'GrayFrames',return_value=reader),patch('review_link_core.evaluate',side_effect=pilot.CheckpointAborted(ValueError('reference changed'))):
   with self.assertRaisesRegex(ValueError,'reference changed'):pilot.annotate('unused',self.linked(),SimpleNamespace(cv2=None))
  reader.close.assert_called_once()
 def test_pi_settings_enable_scoring_but_never_inference(self):
  with tempfile.TemporaryDirectory() as temp:
   settings=Path(temp)/'pilot.json';settings.write_text('{"enabled":true}')
   with patch.object(pilot,'SETTINGS_PATH',settings),patch.dict(os.environ,{},clear=False):
    os.environ.pop(pilot.PILOT_ENV,None);os.environ.pop(pilot.INFERENCE_ENV,None)
    self.assertTrue(pilot.enabled())
    with patch.object(pilot,'GrayFrames',side_effect=AssertionError('Pi must not decode')):self.assertIs(pilot.annotate('unused',self.linked(),object()).__class__,list)
 def test_background_roles_are_explicit_and_persisted_in_config(self):
  from pc_helper_background import review_pilot_environment
  self.assertEqual(set(review_pilot_environment({'saved_view_review_pilot':True}).values()),{'1'})
  self.assertEqual(set(review_pilot_environment({}).values()),{'0'})
if __name__=='__main__':unittest.main()
