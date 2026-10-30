"""Crop saves survive concurrent catalog writers without partial evidence."""
import unittest, sqlite3, contextlib, threading, time
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_identities, manual_crops, identity_store, identity_api

class Contention(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp
    def initialize(self):
        with identity_store.connect(self.db):pass
    def count(self):
        with contextlib.closing(sqlite3.connect(self.db)) as c:
            return c.execute("select count(*) from clip_detections where model_version='manual-crop:v1'").fetchone()[0]
    def test_retry_rolls_back_partial_write_and_decodes_once(self):
        self.initialize();real=manual_crops.connect;attempts=[]
        class Proxy:
            def __init__(self,c):self.c=c
            def __getattr__(self,k):return getattr(self.c,k)
            def execute(self,sql,args=()):
                if sql.startswith('INSERT INTO identity_evidence') and len(attempts)<3:
                    attempts.append(1);e=sqlite3.OperationalError('database is locked');e.sqlite_errorcode=sqlite3.SQLITE_BUSY_SNAPSHOT;raise e
                return self.c.execute(sql,args)
        @contextlib.contextmanager
        def wrapped(*a,**kw):
            with real(*a,**kw) as c:yield Proxy(c)
        with patch('manual_crops.connect',wrapped),patch('manual_crops.clip_frame',return_value=b'exact-human-crop') as decode:
            result=manual_crops.save_crop(self.db,self.db.parent,1,2.,(1,2,64,80),None,'Cat')
        self.assertEqual(len(result),1);self.assertEqual(self.count(),1);decode.assert_called_once();self.assertEqual(len(attempts),3)
        with contextlib.closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('select crop_jpeg from identity_evidence').fetchone()[0],b'exact-human-crop')
    def test_real_writer_lock_releases_then_save_succeeds(self):
        self.initialize();ready=threading.Event();errors=[]
        def writer():
            try:
                with contextlib.closing(sqlite3.connect(self.db)) as c:c.execute('BEGIN IMMEDIATE');ready.set();time.sleep(.35)
            except Exception as e:errors.append(e);ready.set()
        t=threading.Thread(target=writer);t.start();ready.wait(3)
        with patch('manual_crops.clip_frame',return_value=b'jpeg'):
            labels=manual_crops.save_crop(self.db,self.db.parent,1,2.,(1,2,64,80),None,'Cat')
        t.join(3);self.assertFalse(errors);self.assertEqual(len(labels),1);self.assertEqual(self.count(),1)
    def test_busy_exhaustion_returns_503_without_writes(self):
        self.initialize();app=FastAPI();identity_api.install_identity_api(app,SimpleNamespace(catalog_db_path=self.db,archive_root=self.db.parent))
        e=sqlite3.OperationalError('database is locked');e.sqlite_errorcode=sqlite3.SQLITE_BUSY
        with TestClient(app) as client,patch('manual_crops.clip_frame',return_value=b'jpeg') as decode,patch('manual_crops._store_crop',side_effect=e) as store:
            response=client.post('/api/v1/clips/catalog/1/manual-crops',json=dict(seconds=2,x=0,y=0,width=64,height=64,subject_type='Cat'))
        self.assertEqual(response.status_code,503);self.assertEqual(response.headers['retry-after'],'2');self.assertEqual(store.call_count,4);decode.assert_called_once();self.assertEqual(self.count(),0)
    def test_other_sql_error_not_retried(self):
        e=sqlite3.OperationalError('no such table');e.sqlite_errorcode=sqlite3.SQLITE_ERROR
        with patch('manual_crops.clip_frame',return_value=b'jpeg'),patch('manual_crops._store_crop',side_effect=e) as store:
            with self.assertRaises(sqlite3.OperationalError):manual_crops.save_crop(self.db,self.db.parent,1,2.,(1,2,64,80),None,'Cat')
        store.assert_called_once()
    def test_return_labels_does_not_reopen_catalog_after_commit(self):
        self.initialize()
        with patch('manual_crops.clip_frame',return_value=b'jpeg'),patch('identity_store.get_clip_identities',side_effect=AssertionError('second connection')):
            self.assertEqual(len(manual_crops.save_crop(self.db,self.db.parent,1,2.,(1,2,64,80),None,'Cat')),1)

if __name__ == "__main__":
    unittest.main()
