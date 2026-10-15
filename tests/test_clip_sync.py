import sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clip_sync import sync_clips
from catalog_store import open_catalog_notifications,list_clips
class SyncTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'catalog.db';self.writer=sqlite3.connect(self.db);self.writer.executescript(Path('sql/catalog_schema_v1.sql').read_text());open_catalog_notifications(self.db).close()
 def tearDown(self):self.writer.close();self.temp.cleanup()
 def add(self,key,date='2026-10-13'):
  self.writer.execute('INSERT INTO clips(id,filename,video_path,captured_at,local_present) VALUES(?,?,?,?,1)',(key,f'{key}.mp4',f'clips/{key}.mp4',date));self.writer.commit()
 def sync(self,token=None):return sync_clips(self.db,token)
 def execute(self,sql):self.writer.execute(sql);self.writer.commit()
 def test_full_new_older_import_and_quiet(self):
  self.add(1);full=self.sync();self.assertTrue(full['is_full']);self.add(2,'2025-01-01');delta=self.sync(full['sync_token']);self.assertFalse(delta['is_full']);self.assertEqual([c['catalog_id'] for c in delta['clips']],[2]);self.assertEqual(delta['total'],2);self.assertEqual(self.sync(delta['sync_token'])['count'],0)
 def test_review_delete_validation_rollback(self):
  self.add(1);self.add(2);full=self.sync();self.writer.execute('UPDATE clips SET watched=1 WHERE id=1');self.writer.rollback();self.assertEqual(self.sync(full['sync_token'])['count'],0)
  self.execute('UPDATE clips SET watched=1 WHERE id=1');self.execute('DELETE FROM clips WHERE id=2');delta=self.sync(full['sync_token']);self.assertTrue(delta['clips'][0]['watched']);self.assertEqual(delta['removed_ids'],[2]);self.assertEqual(delta['unreviewed_total'],0)
  self.execute("INSERT INTO clip_validation VALUES(1,'damaged','test','hash','today')");hidden=self.sync(delta['sync_token']);self.assertEqual(hidden['removed_ids'],[1]);self.execute("UPDATE clip_validation SET status='valid' WHERE catalog_id=1");self.assertEqual(self.sync(hidden['sync_token'])['count'],1)
 def test_identity_name_analysis_and_quiet_metadata(self):
  self.add(1);full=self.sync();self.execute("INSERT INTO identities(identity_id,name,subject_type) VALUES(1,'Cat','Cat')");self.execute("INSERT INTO clip_detections(detection_id,catalog_id,identity_id,subject_type) VALUES(1,1,1,'Cat')");delta=self.sync(full['sync_token']);self.assertEqual(delta['clips'][0]['identities'][0]['name'],'Cat');self.execute("UPDATE identities SET name='New Name' WHERE identity_id=1");renamed=self.sync(delta['sync_token']);self.assertEqual(renamed['clips'][0]['identities'][0]['name'],'New Name');self.execute("INSERT INTO identity_analysis_jobs(catalog_id,state) VALUES(1,'running')");analysis=self.sync(renamed['sync_token']);self.assertEqual(analysis['clips'][0]['analysis_state'],'running');self.execute("UPDATE clips SET catalog_updated_at='quiet' WHERE id=1");self.assertEqual(self.sync(analysis['sync_token'])['count'],0)
 def test_invalid_and_restart_tokens(self):
  self.add(1)
  for token in ['bad','bad:1','bad:-1',self.sync()['sync_token'].rsplit(':',1)[0]+':99999']:self.assertTrue(self.sync(token)['is_full'])
  old=self.sync()['sync_token']
  with patch('clip_sync.INSTANCE','restart'):self.assertTrue(self.sync(old)['is_full'])
 def test_snapshot_concurrent_insert(self):
  self.add(1);original=list_clips
  def concurrent(*a,**kw):self.add(2);return original(*a,**kw)
  with patch('catalog_store.list_clips',side_effect=concurrent):baseline=self.sync()
  self.assertEqual([c['catalog_id'] for c in baseline['clips']],[1]);self.assertEqual([c['catalog_id'] for c in self.sync(baseline['sync_token'])['clips']],[2])
 def test_unchanged_skips_identity_work(self):
  for key in range(1,101):self.add(key)
  baseline=self.sync()
  with patch('identity_store.clip_identities',side_effect=AssertionError('Unchanged identities loaded')):self.assertEqual(self.sync(baseline['sync_token'])['count'],0)
 def test_completed_clip_reanalysis_api_and_sync(self):
  from identity_analysis import queue_clip,finish_job,set_worker_status
  from identity_api import install_identity_api
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from types import SimpleNamespace
  self.add(1);queue_clip(self.db,1);finish_job(self.db,1,'done');set_worker_status(self.db,'ready','Ready')
  before=self.sync();app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db))
  response=TestClient(app).post('/api/v1/clips/catalog/1/analysis')
  self.assertEqual(response.status_code,200);self.assertEqual(response.json()['state'],'queued')
  delta=self.sync(before['sync_token']);self.assertEqual(delta['clips'][0]['analysis_state'],'queued')
 def test_migration_preserves_cursor_changes_and_repairs_legacy_trigger(self):
  from clip_sync import ensure_clip_sync
  from identity_analysis import queue_clip,finish_job
  self.add(1);queue_clip(self.db,1);finish_job(self.db,1,'done')
  before=self.sync();state=self.writer.execute('SELECT * FROM clip_sync_state').fetchone();changes=self.writer.execute('SELECT * FROM clip_sync_changes').fetchall()
  triggers=self.writer.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'sync_%'").fetchall()
  for name,sql in triggers:
   self.writer.execute('DROP TRIGGER '+name)
   self.writer.execute(sql.replace('INSERT INTO clip_sync_changes','INSERT OR REPLACE INTO clip_sync_changes').replace('ON CONFLICT(catalog_id) DO UPDATE SET revision=excluded.revision;',';'))
  self.writer.commit()
  with self.assertRaises(sqlite3.IntegrityError):queue_clip(self.db,1)
  ensure_clip_sync(self.writer)
  self.assertEqual(self.writer.execute('SELECT * FROM clip_sync_state').fetchone(),state)
  self.assertEqual(self.writer.execute('SELECT * FROM clip_sync_changes').fetchall(),changes)
  ensure_clip_sync(self.writer);queue_clip(self.db,1)
  self.assertEqual(self.sync(before['sync_token'])['clips'][0]['analysis_state'],'queued')
if __name__=='__main__':unittest.main()
