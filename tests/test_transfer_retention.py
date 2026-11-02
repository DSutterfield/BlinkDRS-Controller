import unittest,sqlite3,tempfile
from pathlib import Path
from pc_helper_bridge import cleanup_transfers
import test_identity_analysis
from identity_analysis import record_job_error,queue_clip,claim_job
from identity_store import connect
class TransferTests(unittest.TestCase):
 def test_only_inactive_known_copies_are_removed(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);db=p/'db';c=sqlite3.connect(db);c.execute('create table pc_helper_leases(token,status)')
   for n,state in [(1,'released'),(2,'accepted'),(3,'leased')]:
    token=f'{n:032x}';c.execute('insert into pc_helper_leases values(?,?)',(token,state));q=p/'outgoing'/token;q.mkdir(parents=True);(q/'video.mp4').write_bytes(b'copied-video');(q/'references.sqlite').write_bytes(b'copy');(p/'incoming').mkdir(exist_ok=True);(p/'incoming'/(token+'.partial')).write_bytes(b'copy')
   unknown=p/'outgoing'/('4'*32);unknown.mkdir();(unknown/'video.mp4').write_bytes(b'unknown')
   original=p/'recording.mp4';original.write_bytes(b'original');c.commit();c.close()
   self.assertEqual(cleanup_transfers(p,db,1),1);self.assertEqual(cleanup_transfers(p,db),1)
   self.assertTrue((p/'outgoing'/f'{3:032x}'/'video.mp4').exists());self.assertTrue((unknown/'video.mp4').exists());self.assertEqual(original.read_bytes(),b'original')
 def test_two_slots_cleanup_concurrently(self):
  from concurrent.futures import ThreadPoolExecutor
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);db=p/'db';c=sqlite3.connect(db);c.execute('create table pc_helper_leases(token,status)');(p/'incoming').mkdir()
   for n in range(30):
    token=f'{n:032x}';c.execute('insert into pc_helper_leases values(?,?)',(token,'released'));folder=p/'outgoing'/token;folder.mkdir(parents=True);(folder/'video.mp4').write_bytes(b'copy');(p/'incoming'/(token+'.partial')).write_bytes(b'copy')
   c.commit();c.close()
   with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:cleanup_transfers(p,db,100),range(2)))
   self.assertEqual(list((p/'incoming').iterdir()),[]);self.assertEqual(list((p/'outgoing').iterdir()),[])
class BusyTests(unittest.TestCase):
 setUp=test_identity_analysis.AnalysisTests.setUp
 def test_bounded_busy_retry_and_explicit_reset(self):
  queue_clip(self.db,1)
  e=sqlite3.OperationalError('database is locked');e.sqlite_errorcode=sqlite3.SQLITE_BUSY
  for n in range(4):
   claim_job(self.db);record_job_error(self.db,1,e)
   with connect(self.db) as c:self.assertEqual(c.execute('select state from identity_analysis_jobs where catalog_id=1').fetchone()[0],'queued' if n<3 else 'failed')
  queue_clip(self.db,1)
  with connect(self.db) as c:self.assertEqual(c.execute('select count(*) from analysis_transient_retries').fetchone()[0],0)
 def test_bad_input_not_retried_and_done_not_overwritten(self):
  queue_clip(self.db,1);claim_job(self.db);record_job_error(self.db,1,ValueError('bad video'))
  with connect(self.db) as c:self.assertEqual(c.execute('select state from identity_analysis_jobs where catalog_id=1').fetchone()[0],'failed')
