import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from clip_playback import clip_video_response


class Viewer:
    gone = False

    async def is_disconnected(self):
        return self.gone


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clips = self.root / 'clips'
        self.clips.mkdir()
        self.source = self.clips / 'one.mp4'
        self.source.write_bytes(b'source')
        self.cached = self.root / 'playback_cache/one.loudnorm-v2.mp4'
        self.lock = asyncio.Lock()
        self.viewer = Viewer()

    def cache(self):
        self.cached.parent.mkdir(exist_ok=True)
        self.cached.write_bytes(bytes(range(100)))
        os.utime(self.cached, ns=(self.source.stat().st_atime_ns,
                                 self.source.stat().st_mtime_ns + 1000000000))

    async def response(self):
        return await clip_video_response(self.clips, self.lock, 'one.mp4', self.viewer)

    async def test_cached_video_does_not_wait_for_archive_lock(self):
        self.cache()
        await self.lock.acquire()
        try:
            response = await asyncio.wait_for(self.response(), .2)
            self.assertEqual(response.path, self.cached)
        finally:
            self.lock.release()

    async def test_disconnected_lock_waiter_is_removed_without_conversion(self):
        await self.lock.acquire()
        try:
            with patch('clip_playback._convert') as convert:
                pending = asyncio.create_task(self.response())
                await asyncio.sleep(.02)
                self.viewer.gone = True
                with self.assertRaises(HTTPException) as caught:
                    await asyncio.wait_for(pending, .5)
                self.assertEqual(caught.exception.status_code, 499)
                convert.assert_not_called()
            self.assertTrue(self.lock.locked())
        finally:
            self.lock.release()

    async def test_stale_cache_rebuilt_under_archive_lock(self):
        self.cache()
        os.utime(self.cached, (1, 1))

        async def convert(source, cached):
            self.assertTrue(self.lock.locked())
            self.cache()

        with patch('clip_playback._convert', side_effect=convert) as mocked:
            await self.response()
            mocked.assert_awaited_once()

    async def test_deleted_source_does_not_serve_orphan_cache(self):
        self.cache()
        self.source.unlink()
        with self.assertRaises(HTTPException) as caught:
            await self.response()
        self.assertEqual(caught.exception.status_code, 404)

    async def test_path_traversal_rejected(self):
        for name in ('../one.mp4', 'a/one.mp4', 'a\\one.mp4', 'one.txt'):
            with self.assertRaises(HTTPException) as caught:
                await clip_video_response(self.clips, self.lock, name, self.viewer)
            self.assertEqual(caught.exception.status_code, 400)

    async def test_cancel_active_conversion_kills_process_and_removes_partial(self):
        await self.check_conversion_cancel(spawn_delay=0)

    async def test_cancel_during_spawn_reaps_process(self):
        await self.check_conversion_cancel(spawn_delay=.15)

    async def check_conversion_cancel(self, spawn_delay):
        gate = asyncio.Event()
        spawned = asyncio.Event()
        class Process:
            returncode = None
            killed = False
            async def communicate(self):
                await gate.wait()
                return b'', b''
            def kill(self):
                self.killed = True
                self.returncode = -9
                gate.set()
        process = Process()
        async def spawn(*args, **kwargs):
            spawned.set()
            await asyncio.sleep(spawn_delay)
            Path(args[-1]).write_bytes(b'partial')
            return process
        with patch('clip_playback.asyncio.create_subprocess_exec', side_effect=spawn):
            pending = asyncio.create_task(self.response())
            await spawned.wait()
            self.viewer.gone = True
            with self.assertRaises(HTTPException):
                await asyncio.wait_for(pending, 1)
        self.assertTrue(process.killed)
        self.assertFalse(self.lock.locked())
        self.assertFalse(self.cached.exists())
        self.assertEqual(list(self.cached.parent.glob('*.tmp.mp4')), [])

    async def test_simultaneous_same_clip_prepares_once(self):
        async def convert(source, cached):
            await asyncio.sleep(.02)
            self.cache()
        with patch('clip_playback._convert', side_effect=convert) as mocked:
            await asyncio.gather(self.response(), self.response())
            self.assertEqual(mocked.await_count, 1)

    async def test_conversion_failure_releases_lock_and_partial(self):
        class Process:
            returncode = 1
            async def communicate(self):
                return b'', b'ffmpeg failed'
        async def spawn(*args, **kwargs):
            Path(args[-1]).write_bytes(b'partial')
            return Process()
        with patch('clip_playback.asyncio.create_subprocess_exec', side_effect=spawn):
            with self.assertRaises(HTTPException) as caught:
                await self.response()
        self.assertEqual(caught.exception.status_code, 500)
        self.assertFalse(self.lock.locked())
        self.assertEqual(list(self.cached.parent.glob('*.tmp.mp4')), [])

    async def test_endpoint_cancellation_also_cleans_worker(self):
        entered = asyncio.Event()
        exited = asyncio.Event()
        async def convert(source, cached):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                exited.set()
        with patch('clip_playback._convert', side_effect=convert):
            pending = asyncio.create_task(self.response())
            await entered.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertTrue(exited.is_set())
        self.assertFalse(self.lock.locked())

    async def test_cached_http_range_response(self):
        self.cache()
        response = await self.response()
        messages = []
        async def send(message):
            messages.append(message)
        async def receive():
            return {'type': 'http.request', 'body': b''}
        await response({'type': 'http', 'method': 'GET',
                        'headers': [(b'range', b'bytes=10-19')]}, receive, send)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        body = b''.join(m.get('body', b'') for m in messages)
        self.assertEqual(start['status'], 206)
        self.assertEqual(body, bytes(range(10, 20)))
        self.assertEqual(dict(start['headers'])[b'content-range'], b'bytes 10-19/100')

if __name__ == '__main__':
    unittest.main()
