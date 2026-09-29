import asyncio
import ast
import json
import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from archive_io import archive_io, read_archive_sidecars, missing_sidecars
from responsive_blink import ResponsiveBlink
from blinkpy.blinkpy import Blink
from controller_api import create_app, LiveViewStartRequest
from liveview_diagnostics import StartupDiagnostics
from fault_log import FaultLog


class ResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    def client(self, root):
        client = object.__new__(ResponsiveBlink)
        client._format_filename_default = lambda date, camera, path: str(Path(path)/(date+'.mp4'))
        response = SimpleNamespace(read=AsyncMock(return_value=b'test'), release=Mock())
        client.do_http_get = AsyncMock(return_value=response)
        return client, response

    def item(self, name, **extra):
        return dict(created_at=name, device_name='Test', deleted=False, media='/test', **extra)

    async def test_download_pacing_keeps_api_loop_responsive(self):
        with tempfile.TemporaryDirectory() as root:
            client, response = self.client(root)
            ticks = 0
            async def heartbeat():
                nonlocal ticks
                while True:
                    await asyncio.sleep(.01)
                    ticks += 1
            beat = asyncio.create_task(heartbeat())
            started = time.monotonic()
            try:
                await client._parse_downloaded_items([self.item('a'), self.item('b')],
                                                     ['all'], root, .15, False)
            finally:
                beat.cancel()
                await asyncio.gather(beat, return_exceptions=True)
            self.assertGreaterEqual(time.monotonic()-started, .3)
            self.assertGreater(ticks, 15)
            self.assertEqual(client.do_http_get.await_count, 2)
            self.assertEqual(response.release.call_count, 2)
            self.assertEqual((Path(root)/'a.mp4').read_bytes(), b'test')

    async def test_filtering_naming_and_existing_files_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            client, _ = self.client(root)
            (Path(root)/'old.mp4').write_bytes(b'original')
            deleted = self.item('deleted'); deleted['deleted'] = True
            other = self.item('other'); other['device_name'] = 'Other'
            with patch('responsive_blink.asyncio.sleep', new_callable=AsyncMock) as wait:
                await client._parse_downloaded_items([{}, deleted, other, self.item('old'), self.item('new')],
                                                     ['Test'], root, 1, False)
                wait.assert_awaited_once_with(1)
            client.do_http_get.assert_awaited_once_with('/test')
            self.assertEqual((Path(root)/'old.mp4').read_bytes(), b'original')
            await client._parse_downloaded_items([self.item('custom')], ['all'], root, 0, False,
                                                filename_format=lambda *args: str(Path(root)/'custom-name.mp4'))
            self.assertTrue((Path(root)/'custom-name.mp4').exists())

    async def test_cancel_during_pacing_stops_following_download(self):
        with tempfile.TemporaryDirectory() as root:
            client, _ = self.client(root)
            task = asyncio.create_task(client._parse_downloaded_items(
                [self.item('a'), self.item('b')], ['all'], root, 10, False))
            for _ in range(100):
                if (Path(root)/'a.mp4').exists() and client.do_http_get.return_value.release.called:
                    break
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(client.do_http_get.await_count, 1)
            self.assertFalse((Path(root)/'b.mp4').exists())

    async def test_failed_read_releases_response_and_propagates(self):
        with tempfile.TemporaryDirectory() as root:
            client, response = self.client(root)
            response.read.side_effect = OSError('read failed')
            with self.assertRaises(OSError):
                await client._parse_downloaded_items([self.item('a')], ['all'], root, 1, False)
            response.release.assert_called_once()

    async def test_disk_work_retains_lock_until_finished_after_repeated_cancel(self):
        entered, release = threading.Event(), threading.Event()
        lock = asyncio.Lock()
        def disk():
            entered.set()
            release.wait(3)
        async def work():
            async with lock:
                await archive_io(disk)
        task = asyncio.create_task(work())
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            await asyncio.sleep(.02)
            self.assertTrue(lock.locked())
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(lock.locked())

    async def test_disk_errors_propagate(self):
        def fail():
            raise OSError('disk failure')
        with self.assertRaises(OSError):
            await archive_io(fail)

    async def test_scans_find_missing_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder/'a.mp4').write_bytes(b'test')
            (folder/'a.json').write_text('{"id": 7}')
            (folder/'b.mp4').write_bytes(b'test')
            rows = await archive_io(read_archive_sidecars, folder)
            self.assertEqual(len(rows), 2)
            self.assertEqual(next(data for p,data in rows if p.name=='a.mp4')['id'], 7)
            self.assertEqual(await archive_io(missing_sidecars, folder), [folder/'b.mp4'])

    async def test_actual_poll_function_preserves_metadata_updates(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder/'a.mp4').write_bytes(b'original')
            (folder/'a.json').write_text('{"id": 7, "watched": false}')
            tree = ast.parse(Path('blink_dvr.py').read_text(encoding='utf-8-sig'))
            function = next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)
                            and n.name=='download_new_clips')
            sync = Mock(return_value=True)
            ns = dict(OUTPUT_DIR=folder,log=logging.getLogger('test'),json=json,asyncio=asyncio,
                      archive_io=archive_io,read_archive_sidecars=read_archive_sidecars,
                      missing_sidecars=missing_sidecars,cache_clip_thumbnail=AsyncMock(return_value=False),
                      sync_catalog_clip=sync)
            exec(compile(ast.Module(body=[function],type_ignores=[]),'blink_dvr.py','exec'),ns)
            blink = SimpleNamespace(get_videos_metadata=AsyncMock(return_value=[{'id':7,'watched':True}]),
                                    download_videos=AsyncMock())
            self.assertEqual(await ns['download_new_clips'](blink), 0)
            self.assertTrue(json.loads((folder/'a.json').read_text())['watched'])
            sync.assert_called_once_with(folder/'a.mp4')
            self.assertEqual((folder/'a.mp4').read_bytes(),b'original')

    async def test_startup_timing_includes_recording_lock_and_stop(self):
        with tempfile.TemporaryDirectory() as root:
            controller = SimpleNamespace(archive_root=Path(root), archive_dir=Path(root)/'clips',
                catalog_db_path=Path(root)/'catalog.db', archive_lock=asyncio.Lock(),
                playback_lock=asyncio.Lock(), blink=None,
                faults=FaultLog(Path(root)/'logs',Path(root)/'settings.ini'))
            lock = asyncio.Lock()
            async def stop():
                await asyncio.sleep(.04)
            recorder = SimpleNamespace(stop=AsyncMock(side_effect=stop), status=lambda: {'state':'idle'})
            bridge = Mock()
            bridge.start.side_effect = lambda name, origin, diag: {'ok':True, 'diagnostics':diag.snapshot()}
            with patch('controller_api.LiveViewBridge', return_value=bridge), patch(
                    'controller_api.install_recording_api', return_value=(recorder,lock)):
                app = create_app(controller)
                try:
                    endpoint = next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/v1/liveview/start')
                    diag = StartupDiagnostics();diag.mark('api_request_received')
                    request = SimpleNamespace(state=SimpleNamespace(live_startup=diag))
                    await lock.acquire()
                    task = asyncio.create_task(endpoint(LiveViewStartRequest(name='Test'),request))
                    await asyncio.sleep(.05)
                    bridge.start.assert_not_called()
                    lock.release()
                    result = await task
                    marks=result['diagnostics']['elapsed_ms']
                    self.assertGreaterEqual(marks['recording_lock_acquired'], 40)
                    self.assertGreaterEqual(marks['previous_recording_stopped']-marks['recording_lock_acquired'],30)
                    self.assertIn('api_handler_entered',marks)
                    messages = []
                    delivered = False
                    async def receive():
                        nonlocal delivered
                        if not delivered:
                            delivered = True
                            return {'type':'http.request','body':b'{"name":"Test"}', 'more_body':False}
                        await asyncio.Event().wait()
                    async def send(message):
                        messages.append(message)
                    await asyncio.wait_for(app({'type':'http','asgi':{'version':'3.0'},
                        'http_version':'1.1','method':'POST','scheme':'http','path':'/api/v1/liveview/start',
                        'raw_path':b'/api/v1/liveview/start','query_string':b'',
                        'headers':[(b'content-type',b'application/json')],
                        'client':('127.0.0.1',1),'server':('127.0.0.1',8000)},receive,send),2)
                    self.assertEqual(next(m['status'] for m in messages if m['type']=='http.response.start'),200)
                    data=json.loads(b''.join(m.get('body',b'') for m in messages))
                    self.assertIn('api_request_received',data['diagnostics']['elapsed_ms'])
                finally:
                    for shutdown in app.router.on_shutdown:
                        await shutdown()


if __name__ == '__main__':
    unittest.main()
