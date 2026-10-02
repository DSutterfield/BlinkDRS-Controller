import asyncio,unittest
from collections import deque
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,Mock
import test_live_shutdown as shutdown

class SessionEndTests(unittest.IsolatedAsyncioTestCase):
 def bridge(self):
  b=shutdown.ShutdownTests.bridge(self);b._last_error=None;b._faults=None;b._ffmpeg_messages=deque();b._end_task=None;b._ended_session=None
  return b
 async def test_natural_end_releases_relay_producers_and_retains_terminal_identity(self):
  b=self.bridge();session=b._startup
  async def pending():await asyncio.Event().wait()
  async def ended():return
  b._feed_task=asyncio.create_task(pending());b._frame_task=asyncio.create_task(ended())
  b._audio_task=asyncio.create_task(pending());b._stream=NS(stop=Mock())
  relay=NS(stop=AsyncMock());b._recording_relay=relay
  tasks=[b._feed_task,b._frame_task,b._audio_task]
  b._end_task=asyncio.create_task(b._watch_session_end(session))
  await asyncio.wait_for(b._end_task,1)
  status=b.status();self.assertFalse(status['active']);self.assertEqual(status['ended_session']['reason'],'stream_ended')
  self.assertEqual(status['ended_session']['session_id'],session.snapshot()['session_id'])
  self.assertEqual(status['ended_session']['camera'],'Test');relay.stop.assert_awaited_once()
  self.assertTrue(all(t.done() for t in tasks));self.assertIsNone(b._stream)
  self.assertFalse(b._frame_history);self.assertFalse(b._audio_chunks)
  with self.assertRaises(ValueError):b.next_frame(0,session.snapshot()['session_id'],0)
 async def test_stream_failure_retains_diagnostics(self):
  b=self.bridge()
  async def fail():raise OSError('synthetic network failure')
  async def pending():await asyncio.Event().wait()
  b._feed_task=asyncio.create_task(fail());b._frame_task=asyncio.create_task(pending())
  b._end_task=asyncio.create_task(b._watch_session_end(b._startup))
  await asyncio.wait_for(b._end_task,1)
  self.assertEqual(b.status()['ended_session']['reason'],'stream_error')
  self.assertIn('synthetic network failure',b._last_error)
 async def test_manual_stop_cancels_watcher_without_claiming_natural_end(self):
  b=self.bridge()
  async def pending():await asyncio.Event().wait()
  b._feed_task=asyncio.create_task(pending());b._frame_task=asyncio.create_task(pending())
  watcher=asyncio.create_task(b._watch_session_end(b._startup));b._end_task=watcher
  await asyncio.sleep(0)
  await asyncio.wait_for(b._stop_async(),1)
  self.assertTrue(watcher.cancelled());self.assertIsNone(b._ended_session)
 async def test_stop_waits_for_automatic_cleanup_then_is_idempotent(self):
  b=self.bridge();entered=asyncio.Event();release=asyncio.Event()
  async def ended():return
  b._feed_task=asyncio.create_task(ended());b._frame_task=asyncio.create_task(ended())
  async def slow_stop():entered.set();await release.wait()
  relay=NS(stop=AsyncMock(side_effect=slow_stop));b._recording_relay=relay
  b._end_task=asyncio.create_task(b._watch_session_end(b._startup));await entered.wait()
  self.assertEqual(b.status()['ended_session']['reason'],'stream_ended')
  stop=asyncio.create_task(b._stop_async());await asyncio.sleep(0);self.assertFalse(stop.done())
  release.set();await asyncio.wait_for(asyncio.gather(b._end_task,stop),1)
  relay.stop.assert_awaited_once();self.assertIsNone(b._ended_session)
 async def test_old_session_watch_does_not_stop_new_session(self):
  b=self.bridge();old=b._startup
  async def ended():return
  b._feed_task=asyncio.create_task(ended());b._frame_task=asyncio.create_task(ended());b._startup=NS()
  await b._watch_session_end(old);self.assertTrue(b._active)

if __name__=='__main__':unittest.main()
