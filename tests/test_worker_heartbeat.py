import unittest
from unittest.mock import Mock
import test_identities
from identity_analysis import WorkerHeartbeat, set_worker_status, job_status
from identity_store import connect
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_api import install_identity_api
from types import SimpleNamespace


class HeartbeatTests(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp

    def tick(self):
        heartbeat = WorkerHeartbeat(self.db)
        heartbeat.stop = Mock()
        heartbeat.stop.wait.side_effect = [False, True]
        heartbeat._run()

    def test_long_analysis_remains_available_and_accepts_another_clip(self):
        set_worker_status(self.db, 'analyzing', 'Analyzing clip 1')
        with connect(self.db) as conn:
            conn.execute("UPDATE identity_worker_status SET updated_at=datetime('now','-18 minutes')")
        self.assertFalse(job_status(self.db, 2)['worker_available'])
        self.tick()
        status = job_status(self.db, 2)
        self.assertTrue(status['worker_available'])
        self.assertEqual(status['worker']['state'], 'analyzing')
        self.assertEqual(status['worker']['message'], 'Analyzing clip 1')
        app = FastAPI()
        install_identity_api(app, SimpleNamespace(catalog_db_path=self.db))
        response = TestClient(app).post('/api/v1/clips/catalog/2/analysis')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state'], 'queued')

    def test_heartbeat_never_revives_failed_or_stopped_worker(self):
        for state in ('failed', 'stopped'):
            set_worker_status(self.db, state, 'test')
            with connect(self.db) as conn:
                conn.execute("UPDATE identity_worker_status SET updated_at='2000-01-01 00:00:00'")
            self.tick()
            status = job_status(self.db, 1)
            self.assertFalse(status['worker_available'])
            self.assertEqual(status['worker']['updated_at'], '2000-01-01 00:00:00')

    def test_loading_and_paused_states_keep_their_message(self):
        for state in ('loading', 'paused', 'ready'):
            set_worker_status(self.db, state, 'original message')
            self.tick()
            status = job_status(self.db, 1)
            self.assertTrue(status['worker_available'])
            self.assertEqual(status['worker']['state'], state)
            self.assertEqual(status['worker']['message'], 'original message')

    def test_heartbeat_thread_stops_on_exception(self):
        heartbeat = WorkerHeartbeat(self.db)
        with self.assertRaises(ValueError):
            with heartbeat:
                self.assertTrue(heartbeat.thread.is_alive())
                raise ValueError('inference failed')
        self.assertTrue(heartbeat.stop.is_set())
        self.assertFalse(heartbeat.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
