import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_identities
from identity_analysis import set_worker_status, claim_job, queue_clip, publish_detections
from identity_store import connect, assign_identity, get_clip_identities

class LockingTests(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp

    def test_status_lock_does_not_abort_worker_and_recovers(self):
        set_worker_status(self.db, 'ready', 'ready')
        with sqlite3.connect(self.db) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE identity_worker_status SET message='held'")
            started = time.monotonic()
            set_worker_status(self.db, 'analyzing', 'busy')
            self.assertLess(time.monotonic()-started, 2)
        set_worker_status(self.db, 'analyzing', 'recovered')
        with connect(self.db) as c:
            self.assertEqual(c.execute('SELECT message FROM identity_worker_status').fetchone()[0], 'recovered')

    def test_claim_defers_without_losing_job(self):
        queue_clip(self.db, 1)
        with sqlite3.connect(self.db) as writer:
            writer.execute('BEGIN IMMEDIATE')
            self.assertIsNone(claim_job(self.db))
        self.assertEqual(claim_job(self.db)['catalog_id'], 1)

    def test_unrelated_database_error_is_not_hidden(self):
        with patch('identity_analysis.connect', side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(sqlite3.OperationalError):
                set_worker_status(self.db, 'ready', 'test')
            with self.assertRaises(sqlite3.OperationalError):
                claim_job(self.db)

    def test_ranking_releases_writer_and_rechecks_human_edit(self):
        # WAL permits a ranking reader and independent status/human writes.
        with sqlite3.connect(self.db) as c:
            c.execute('PRAGMA journal_mode=WAL')
        item = dict(key='cat',subject_type='Cat',confidence=.8,first_seen_seconds=0.,
                    last_seen_seconds=1.,detector_model='test',crop_jpeg=b'jpeg',
                    embedding=[1.,0.],embedding_model='test',quality_score=.8)
        publish_detections(self.db,1,'hash',[item])
        did = get_clip_identities(self.db,1)[0]['detection_id']
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            if not release.wait(10):
                raise AssertionError('ranking held database writer')
            return []
        with ThreadPoolExecutor(max_workers=1) as pool, patch('identity_candidates.rank_candidates', side_effect=slow):
            future = pool.submit(publish_detections,self.db,1,'hash',[item])
            try:
                self.assertTrue(entered.wait(5))
                set_worker_status(self.db,'analyzing','still responsive')
                assign_identity(self.db,1,None,'Cat',did)
                with connect(self.db) as c:
                    self.assertEqual(c.execute('SELECT message FROM identity_worker_status').fetchone()[0],'still responsive')
            finally:
                release.set()
            future.result(timeout=10)
        rows=get_clip_identities(self.db,1)
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['confirmed'])
        self.assertEqual(rows[0]['detection_id'],did)
