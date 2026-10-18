import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch
from identity_parallel import ParallelClips
from identity_worker import wait_for_controller
from io import BytesIO
import test_identities
from identity_analysis import queue_clip, publish_detections
from identity_store import connect, get_clip_identities, assign_identity
from concurrent.futures import ThreadPoolExecutor


class ParallelTests(unittest.TestCase):
    def make_pool(self, workers=2):
        executor = Mock()
        executor.submit.side_effect = lambda *a: Future()
        return ParallelClips('db', 'archive', 'models', True, 'http://controller', workers=workers, executor=executor)

    @patch('identity_parallel.claim_job')
    def test_bounded_dispatch_and_refill_only_completed_slot(self, claim):
        claim.side_effect = [{'catalog_id': i} for i in (1, 2, 3)]
        pool = self.make_pool()
        pool.fill()
        pool.fill()
        self.assertEqual(claim.call_count, 2)
        self.assertEqual(len(pool.active), 2)
        first = next(iter(pool.active))
        first.set_result(None)
        pool.collect()
        pool.fill()
        self.assertEqual(claim.call_count, 3)
        self.assertEqual([j['catalog_id'] for j in pool.active.values()], [2, 3])

    @patch('identity_parallel.claim_job')
    def test_three_workers_bound_and_refill(self, claim):
        claim.side_effect = [{'catalog_id': i} for i in (1, 2, 3, 4)]
        pool = self.make_pool(workers=3)
        pool.fill()
        pool.fill()
        self.assertEqual(claim.call_count, 3)
        self.assertEqual(len(pool.active), 3)
        next(iter(pool.active)).set_result(None)
        pool.collect()
        pool.fill()
        self.assertEqual([j['catalog_id'] for j in pool.active.values()], [2, 3, 4])

    @patch('identity_parallel.finish_job')
    @patch('identity_parallel.claim_job')
    def test_failed_clip_does_not_discard_other_inflight_job(self, claim, finish):
        claim.side_effect = [{'catalog_id': 1}, {'catalog_id': 2}]
        pool = self.make_pool()
        pool.fill()
        first = next(iter(pool.active))
        first.set_exception(ValueError('broken video'))
        pool.collect()
        finish.assert_called_once_with('db', 1, 'failed', 'broken video')
        self.assertEqual([j['catalog_id'] for j in pool.active.values()], [2])

    @patch('identity_parallel.finish_job')
    @patch('identity_parallel.claim_job', return_value={'catalog_id': 1})
    def test_submission_failure_recovers_claim(self, claim, finish):
        pool = self.make_pool()
        pool.executor.submit.side_effect = RuntimeError('pool stopped')
        with self.assertRaises(RuntimeError):
            pool.fill()
        finish.assert_called_once_with('db', 1, 'failed', 'pool stopped')
        self.assertEqual(pool.active, {})

    def test_shutdown_terminates_children_before_returning(self):
        pool = self.make_pool()
        pool.active[Future()] = {'catalog_id': 1}
        del pool.executor.terminate_workers
        child = Mock()
        child.is_alive.side_effect = [True, False]
        pool.executor._processes = {1: child}
        pool.close()
        child.terminate.assert_called_once()
        child.join.assert_called_once_with(timeout=5)
        pool.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)

    def test_idle_shutdown_lets_children_exit_normally(self):
        pool = self.make_pool()
        pool.close()
        pool.executor.terminate_workers.assert_not_called()
        pool.executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)

    @patch('identity_worker.time.sleep')
    @patch('urllib.request.urlopen')
    def test_recording_and_connection_failure_pause_analysis(self, request, sleep):
        def response(value):
            stream = BytesIO(value)
            return stream
        request.side_effect = [response(b'{"active":false}'), response(b'{"state":"stopping"}'),
                               OSError('offline'), response(b'{"active":false}'),
                               response(b'{"state":"idle"}')]
        paused = Mock()
        wait_for_controller(True, 'http://controller', paused)
        self.assertEqual(paused.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    @patch('urllib.request.urlopen')
    def test_pause_can_be_disabled_for_isolated_tools(self, request):
        wait_for_controller(False, 'http://controller')
        request.assert_not_called()


class SharedCatalogTests(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp

    def test_concurrent_claims_never_share_a_clip(self):
        from identity_analysis import claim_job
        queue_clip(self.db, 1)
        queue_clip(self.db, 2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: claim_job(self.db), range(2)))
        self.assertEqual({j['catalog_id'] for j in jobs}, {1, 2})
        self.assertIsNone(claim_job(self.db))

    def test_concurrent_publication_preserves_human_confirmation(self):
        queue_clip(self.db, 1)
        queue_clip(self.db, 2)
        def observation(key, kind):
            return dict(key=key, subject_type=kind, confidence=.8,
                        first_seen_seconds=0., last_seen_seconds=1.,
                        detector_model='test', crop_jpeg=b'jpeg', quality_score=.8)
        publish_detections(self.db, 1, 'hash1', [observation('a', 'Cat')])
        detection = get_clip_identities(self.db, 1)[0]
        assign_identity(self.db, 1, None, 'Cat', detection['detection_id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish_detections, self.db, cid, 'hash'+str(cid),
                                   [observation('a', 'Dog')]) for cid in (1, 2)]
            for future in futures:
                future.result()
        confirmed = [d for d in get_clip_identities(self.db, 1) if d['confirmed']]
        self.assertEqual([(d['detection_id'], d['subject_type']) for d in confirmed],
                         [(detection['detection_id'], 'Cat')])
        with connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM identity_analysis_jobs WHERE state='done'").fetchone()[0], 2)
