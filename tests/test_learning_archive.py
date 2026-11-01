import base64,hashlib,json,os,sqlite3,tempfile,unittest,zlib
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from identity_store import connect,create_identity
from identity_candidates import rank_candidates
from name_first_review import references
from subject_type_check import rank_types
from catalog_store import list_clips
from archive_maintenance import cleanup_expired_clips,reconcile_local_presence

class LearningArchiveTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.videos=self.root/'clips';self.videos.mkdir();self.db=self.root/'catalog.db'
  with closing(sqlite3.connect(self.db)) as c,c:c.executescript((Path(__file__).parents[1]/'sql/catalog_schema_v1.sql').read_text())
  self.ids=[create_identity(self.db,n,k)['identity_id'] for n,k in [('Jiminy','Cat'),('Levi','Cat'),('Duke','Dog'),('Dan','Person')]];self.model='dinov2-small-cls-v1:test'
  with connect(self.db) as c:
   for cid in range(1,9):
    which=(cid-1)//2;kind=['Cat','Cat','Dog','Person'][which];v=[0.]*384;v[which]=1.;v[(cid%2)+10]=.02;v=json.dumps(v)
    c.execute('INSERT INTO clips(id,filename,video_path,captured_at,local_present,blink_media_id) VALUES(?,?,?,?,1,?)',(cid,f'{cid}.mp4',f'clips/{cid}.mp4','2026-10-03',str(cid)))
    c.execute('INSERT INTO clip_detections(detection_id,catalog_id,identity_id,subject_type,confirmed,first_seen_seconds,last_seen_seconds) VALUES(?,?,?,?,1,0,2)',(cid,cid,self.ids[which],kind))
    c.execute('INSERT INTO identity_evidence(detection_id,catalog_id,detection_key,subject_type,crop_jpeg,embedding,embedding_model,quality_score) VALUES(?,?,?,?,?,?,?,1)',(cid,cid,str(cid),kind,b'original-crop-'+str(cid).encode(),v,self.model))
    c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) VALUES(?,?,?,?,1)',(self.ids[which],cid,v,self.model))
    c.execute('INSERT INTO subject_type_samples VALUES(?,?,?,?,1,?)',(cid,kind,v,self.model,'hash'+str(cid)))
    c.execute('INSERT INTO identity_track_views(detection_id,seconds,crop_jpeg,embedding,embedding_model,quality_score) VALUES(?,1,?,?,?,1)',(cid,b'earlier-useful-view',v,self.model))
    video=self.videos/f'{cid}.mp4';video.write_bytes(b'original-video-'+str(cid).encode());os.utime(video,(1,1))
  self.archive=self.root/'learning/confirmed-learning.sqlite'
 def signatures(self):
  with connect(self.db) as c:
   q=[1.]+[0.]*383
   return (rank_candidates(c,'Cat',self.model,q),rank_types(references(c,self.model,-1),q,self.model),[tuple(r) for r in c.execute('select detection_id,catalog_id,identity_id,confirmed from clip_detections order by detection_id')])
 def test_expiry_preserves_exact_matching_and_hidden_source_history(self):
  before=self.signatures();self.assertEqual(cleanup_expired_clips(self.db,self.videos,15),8);self.assertEqual(before,self.signatures());self.assertEqual(list_clips(self.db)['total'],0)
  with connect(self.db) as c:
   self.assertEqual(c.execute('select count(*) from retained_learning_clips').fetchone()[0],8);self.assertEqual(c.execute('select count(*) from identity_samples where detection_id is null').fetchone()[0],0)
   self.assertEqual(c.execute('select count(*) from identity_track_views').fetchone()[0],8)
  self.assertEqual(reconcile_local_presence(self.db,self.videos),0)
 def test_separate_file_has_original_crops_vectors_labels_and_provenance(self):
  cleanup_expired_clips(self.db,self.videos,15,max_delete=1)
  with closing(sqlite3.connect(self.archive)) as c:
   digest,blob=c.execute('select snapshot_sha256,payload from clip_snapshots').fetchone();raw=zlib.decompress(blob);self.assertEqual(hashlib.sha256(raw).hexdigest(),digest);p=json.loads(raw)
   self.assertEqual(p['clip']['id'],1);self.assertEqual(p['clip']['blink_media_id'],'1');self.assertEqual(p['identities'][0]['name'],'Jiminy');self.assertEqual(p['video_sha256'],hashlib.sha256(b'original-video-1').hexdigest())
   self.assertEqual(base64.b64decode(p['tables']['identity_evidence'][0]['crop_jpeg']['__blob_base64__']),b'original-crop-1');self.assertEqual(len(p['tables']['identity_track_views']),1);self.assertEqual(len(p['tables']['subject_type_samples']),1)
 def test_archive_failure_keeps_original_video_and_confirmation(self):
  before=self.signatures()
  with patch('learning_archive.preserve',side_effect=OSError('archive full')),self.assertRaises(OSError):cleanup_expired_clips(self.db,self.videos,15)
  self.assertTrue((self.videos/'1.mp4').exists());self.assertEqual(before,self.signatures());self.assertEqual(list_clips(self.db)['total'],8)
 def test_failed_unlink_keeps_video_then_retry_preserves_references(self):
  with patch.object(Path,'unlink',side_effect=PermissionError('busy')),self.assertRaises(PermissionError):cleanup_expired_clips(self.db,self.videos,15)
  self.assertTrue((self.videos/'1.mp4').exists());before=self.signatures();cleanup_expired_clips(self.db,self.videos,15);self.assertEqual(before,self.signatures())
 def test_missing_archive_does_not_cascade_retained_metadata(self):
  cleanup_expired_clips(self.db,self.videos,15,max_delete=1);self.archive.rename(self.archive.with_suffix('.saved'))
  with self.assertRaises(sqlite3.OperationalError):reconcile_local_presence(self.db,self.videos)
  with connect(self.db) as c:self.assertEqual(c.execute('select count(*) from clip_detections').fetchone()[0],8)
 def test_corrupt_archive_stops_before_next_video_delete(self):
  cleanup_expired_clips(self.db,self.videos,15,max_delete=1)
  with closing(sqlite3.connect(self.archive)) as c,c:c.execute("update clip_snapshots set payload=X'00'")
  with self.assertRaises(zlib.error):cleanup_expired_clips(self.db,self.videos,15)
  self.assertTrue((self.videos/'2.mp4').exists())
 def test_unexpected_missing_video_also_preserves_confirmed_learning(self):
  before=self.signatures();(self.videos/'1.mp4').unlink();reconcile_local_presence(self.db,self.videos);self.assertEqual(before,self.signatures());self.assertTrue(self.archive.exists());self.assertEqual(list_clips(self.db)['total'],7)
 def test_no_identifiable_exclusion_and_unknown_confirmation_survive(self):
  with connect(self.db) as c:
   c.execute('update clip_detections set identity_id=null where detection_id=1');c.execute("insert into subject_analysis_exclusions(detection_id,crop_sha256) values(1,'confirmed-unusable-crop')")
  cleanup_expired_clips(self.db,self.videos,15,max_delete=1)
  with connect(self.db) as c:
   self.assertEqual(c.execute('select count(*) from subject_analysis_exclusions where detection_id=1').fetchone()[0],1);self.assertEqual(c.execute('select confirmed from clip_detections where detection_id=1').fetchone()[0],1)
 def test_active_analysis_is_not_expired(self):
  with connect(self.db) as c:c.execute("insert into identity_analysis_jobs(catalog_id,state) values(1,'running')")
  self.assertEqual(cleanup_expired_clips(self.db,self.videos,15),7);self.assertTrue((self.videos/'1.mp4').exists())

if __name__=='__main__':unittest.main()
