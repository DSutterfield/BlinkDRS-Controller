"""Exercise production polling/API coordination against an isolated archive."""
import ast
import asyncio
import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from controller_api import create_app, coordinate_clip_delete
from fault_log import FaultLog
from archive_io import archive_io


class Viewer:
    async def is_disconnected(self):
        return False


class PollingPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        clips = self.root / 'clips'
        clips.mkdir()
        self.source = clips / 'one.mp4'
        self.source.write_bytes(b'synthetic video')
        self.cached = self.root / 'playback_cache/one.loudnorm-v2.mp4'
        db = self.root / 'catalog.db'
        with sqlite3.connect(db) as conn:
            conn.executescript(Path('sql/catalog_schema_v1.sql').read_text())
            conn.execute("INSERT INTO clips (id,filename,video_path,captured_at,source) "
                         "VALUES (1,'one.mp4','clips/one.mp4','2026-09-28T00:00:00','recorded_live')")
        self.controller = SimpleNamespace(
            faults=FaultLog(self.root/'logs', self.root/'settings.ini'),
            archive_root=self.root, archive_dir=clips, catalog_db_path=db,
            archive_lock=asyncio.Lock(), playback_lock=asyncio.Lock(), blink=None,
            refresh_blink_status=AsyncMock(return_value=True))
        self.app = create_app(self.controller)
        self.endpoint = next(route.endpoint for route in self.app.routes
                             if getattr(route, 'path', '') == '/api/v1/clips/{filename}/video')

        # Load the actual poll_once method without blink_dvr's import-time
        # production directory creation, config reads, or log handler setup.
        tree = ast.parse(Path('blink_dvr.py').read_text(encoding='utf-8-sig'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BlinkController')
        method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'poll_once')
        self.download = AsyncMock(return_value=0)
        self.cleanup = Mock()
        namespace = dict(archive_io=archive_io, download_new_clips=self.download, cleanup_old_clips=self.cleanup,
                         log=logging.getLogger('test'))
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'blink_dvr.py', 'exec'), namespace)
        self.poll = lambda: namespace['poll_once'](self.controller)

    async def asyncTearDown(self):
        for shutdown in self.app.router.on_shutdown:
            await shutdown()

    async def convert(self, source, cached):
        cached.write_bytes(b'prepared')

    async def test_uncached_api_playback_completes_while_cloud_work_is_blocked(self):
        for phase in ('status', 'download'):
            with self.subTest(phase=phase):
                self.cached.unlink(missing_ok=True)
                entered, release = asyncio.Event(), asyncio.Event()
                async def cloud(*args):
                    entered.set()
                    await release.wait()
                    return 0
                target = self.controller.refresh_blink_status if phase == 'status' else self.download
                target.side_effect = cloud
                pending = asyncio.create_task(self.poll())
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    self.assertTrue(self.controller.archive_lock.locked())
                    with patch('clip_playback._convert', side_effect=self.convert):
                        response = await asyncio.wait_for(self.endpoint('one.mp4', Viewer()), 1)
                    self.assertEqual(response.path, self.cached)
                    self.assertFalse(pending.done())
                finally:
                    release.set()
                    await asyncio.wait_for(pending, 1)
                    target.side_effect = None

    async def test_retention_waits_for_conversion_but_cloud_work_can_finish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def convert(source, cached):
            entered.set()
            await release.wait()
            await self.convert(source, cached)
        with patch('clip_playback._convert', side_effect=convert):
            video = asyncio.create_task(self.endpoint('one.mp4', Viewer()))
            await asyncio.wait_for(entered.wait(), 1)
            poll = asyncio.create_task(self.poll())
            try:
                await asyncio.sleep(.02)
                self.download.assert_awaited_once()
                self.cleanup.assert_not_called()
                self.assertFalse(poll.done())
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(video, poll), 1)
        self.cleanup.assert_called_once()
        self.assertFalse(self.controller.archive_lock.locked())
        self.assertFalse(self.controller.playback_lock.locked())

    async def test_delete_waits_for_conversion_and_removes_completed_cache(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def convert(source, cached):
            entered.set()
            await release.wait()
            await self.convert(source, cached)
        with patch('clip_playback._convert', side_effect=convert):
            video = asyncio.create_task(self.endpoint('one.mp4', Viewer()))
            await asyncio.wait_for(entered.wait(), 1)
            deletion = asyncio.create_task(coordinate_clip_delete(self.controller, 1))
            try:
                await asyncio.sleep(.02)
                self.assertFalse(deletion.done())
                self.assertTrue(self.source.exists())
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(video, deletion), 1)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.cached.exists())
        self.assertFalse(self.controller.archive_lock.locked())
        self.assertFalse(self.controller.playback_lock.locked())


if __name__ == '__main__':
    unittest.main()
