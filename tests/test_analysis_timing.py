import sqlite3,tempfile,unittest
from pathlib import Path
from catalog_store import open_catalog_notifications
from clip_sync import sync_clips
from identity_analysis import queue_clip,claim_job,finish_job
class TimingTests(unittest.TestCase):
 def test_timing_lifecycle_and_delta(self):
  with tempfile.TemporaryDirectory() as d:
   db=Path(d)/'test.db'
   with sqlite3.connect(db) as c:
    c.executescript(Path('sql/catalog_schema_v1.sql').read_text())
   open_catalog_notifications(db).close()
   with sqlite3.connect(db) as c:c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present) VALUES(1,'test.mp4','test.mp4','2026-10-16',1)")
   queue_clip(db,1);before=sync_clips(db)
   self.assertEqual(before['queued_analysis_total'],1)
   self.assertIsNone(before['clips'][0]['analysis_started_at'])
   claim_job(db);delta=sync_clips(db,before['sync_token']);clip=delta['clips'][0]
   self.assertEqual(clip['analysis_state'],'running')
   self.assertEqual(delta['queued_analysis_total'],0)
   from datetime import datetime,timezone
   start=datetime.fromisoformat(clip['analysis_started_at']);self.assertEqual(start.tzinfo,timezone.utc)
   self.assertLess(abs((datetime.now(timezone.utc)-start).total_seconds()),5)
   self.assertEqual(sync_clips(db)['clips'][0]['analysis_started_at'],clip['analysis_started_at'])
   finish_job(db,1,'done');done=sync_clips(db,delta['sync_token'])
   self.assertEqual(done['clips'][0]['analysis_state'],'done');self.assertIsNone(done['clips'][0]['analysis_started_at'])
   with sqlite3.connect(db) as c:
    c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present) VALUES(2,'bad.mp4','bad.mp4','2026-10-16',1)")
    c.execute("INSERT INTO clip_validation VALUES(2,'damaged','fixture','hash','today')")
   queue_clip(db,2)
   hidden=sync_clips(db,done['sync_token']);self.assertEqual(hidden['queued_analysis_total'],1)
   self.assertEqual(hidden['count'],0)
   self.assertEqual(sync_clips(db,hidden['sync_token'])['queued_analysis_total'],1)
if __name__=='__main__':unittest.main()
