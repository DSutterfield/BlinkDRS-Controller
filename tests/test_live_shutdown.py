import asyncio
import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from blinkpy.livestream import BlinkLiveStream
from liveview_bridge import LiveViewBridge
from liveview_diagnostics import StartupDiagnostics


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self):
        b = object.__new__(LiveViewBridge)
        b._active = True
        b._startup = StartupDiagnostics()
        b._frame_condition = threading.Condition()
        b._audio_condition = threading.Condition()
        b._audio_chunks = deque([b'test'])
        b._frame_history = deque([(1,b'test')])
        b._last_error = 'original'
        b._camera_name = 'Test'
        b._latest_frame = b'test'
        b._frame_number = 1
        b._first_frame_event = asyncio.Event()
        for name in ('_ffmpeg','_stream','_feed_task','_frame_task','_audio_task',
                     '_stderr_task','_recording_relay','_audio_read_fd'):
            setattr(b,name,None)
        return b

    async def test_real_blink_poll_sleep_is_woken_and_command_done_finishes(self):
        b = self.bridge()
        stream = object.__new__(BlinkLiveStream)
        closed = False
        def close():
            nonlocal closed
            closed = True
        stream.stop = Mock(side_effect=close)
        stream.auth = AsyncMock()
        stream.recv = AsyncMock(side_effect=lambda: None)
        async def pending():
            await asyncio.sleep(60)
        stream.recv = pending
        stream.send = pending
        stream.target_reader = SimpleNamespace(at_eof=lambda:closed)
        stream.camera = SimpleNamespace(sync=SimpleNamespace(blink=object()),network_id=1)
        stream.command_id = 10
        stream.polling_interval = 10
        completed = False
        async def done(*args):
            nonlocal completed
            await asyncio.sleep(.04)
            completed = True
            return {}
        b._stream = stream
        with patch('blinkpy.livestream.api.request_command_status',new=AsyncMock(return_value={
            'status_code':908,'commands':[{'id':10,'state_condition':'running'}]})) as status, patch(
                'blinkpy.livestream.api.request_command_done',new=AsyncMock(side_effect=done)) as finish:
            b._feed_task = asyncio.create_task(b._feed_blink_stream())
            while not status.await_count:
                await asyncio.sleep(.001)
            feed = b._feed_task
            started = time.monotonic()
            await b._stop_async()
            elapsed = time.monotonic()-started
            self.assertTrue(completed)
            finish.assert_awaited_once()
            self.assertTrue(feed.done())
            self.assertLess(elapsed,.5)
            self.assertFalse(b._active)
            self.assertIsNone(b._stream)
            self.assertFalse(b._frame_history)
            self.assertFalse(b._audio_chunks)
            marks=b._startup.snapshot()['elapsed_ms']
            self.assertIn('stop_completed',marks)
            print('Real Blink feed/poll shutdown including command-done:',round(elapsed,3),'seconds')

    async def test_transport_closes_before_decoder_wait_and_feed_cleanup_overlaps(self):
        b=self.bridge();closed=asyncio.Event();feed_done=asyncio.Event()
        b._stream=SimpleNamespace(stop=closed.set)
        async def feed():
            try:await asyncio.sleep(60)
            finally:
                await asyncio.sleep(.02)
                feed_done.set()
        b._feed_task=asyncio.create_task(feed());await asyncio.sleep(0)
        async def wait():
            self.assertTrue(closed.is_set())
            await asyncio.wait_for(feed_done.wait(),.2)
        b._ffmpeg=SimpleNamespace(returncode=None,terminate=Mock(),wait=wait,kill=Mock())
        await b._stop_async()
        self.assertTrue(feed_done.is_set())

    async def test_decoder_timeout_kills_and_reaps(self):
        b=self.bridge();finished=asyncio.Event();killed=Mock(side_effect=finished.set)
        b._stream=SimpleNamespace(stop=Mock())
        b._ffmpeg=SimpleNamespace(returncode=None,terminate=Mock(),wait=finished.wait,kill=killed)
        with patch('liveview_bridge.FFMPEG_STOP_TIMEOUT_SECONDS',.02):
            await b._stop_async()
        killed.assert_called_once()
        self.assertIsNone(b._ffmpeg)

    async def test_idempotent_stop_and_preserve_error(self):
        b=self.bridge()
        await b._stop_async(preserve_error=True)
        self.assertEqual(b._last_error,'original')
        await b._stop_async()
        self.assertIsNone(b._last_error)

    async def test_feed_error_waits_for_sibling_finalizers(self):
        b=self.bridge();finished=[]
        async def broken():
            await asyncio.sleep(.01)
            raise OSError('transport failure')
        async def sibling(name):
            try:await asyncio.sleep(60)
            finally:
                await asyncio.sleep(.02)
                finished.append(name)
        b._stream=SimpleNamespace(auth=AsyncMock(),recv=broken,
            send=lambda:sibling('send'),poll=lambda:sibling('command-done'),stop=Mock())
        with self.assertRaises(OSError):
            await b._feed_blink_stream()
        self.assertEqual(set(finished),{'send','command-done'})
        b._stream.stop.assert_called_once()

    async def test_cancel_during_auth_closes_transport(self):
        b=self.bridge();entered=asyncio.Event()
        async def auth():
            entered.set()
            await asyncio.sleep(60)
        b._stream=SimpleNamespace(auth=auth,recv=Mock(),send=Mock(),poll=Mock(),stop=Mock())
        feed=asyncio.create_task(b._feed_blink_stream());await entered.wait();feed.cancel()
        with self.assertRaises(asyncio.CancelledError):await feed
        b._stream.stop.assert_called_once()
        b._stream.poll.assert_not_called()


if __name__=='__main__':unittest.main()
