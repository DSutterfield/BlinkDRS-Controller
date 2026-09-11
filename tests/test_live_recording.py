"""Isolated archive/media integration checks; never touch the production catalog."""
import asyncio,json,sqlite3,tempfile,subprocess,sys,contextlib
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
from live_recording import RecordingSettings,LiveRecorder,catalog_recording
from catalog_store import list_clips,get_clip_by_catalog_id,set_clip_watched
from controller_api import coordinate_clip_delete,create_app

async def main():
  with tempfile.TemporaryDirectory(prefix='blink-recording-test-') as folder:
    root=Path(folder);(root/'clips').mkdir()
    db=root/'catalog.db'
    with sqlite3.connect(db) as conn:conn.executescript(Path(sys.argv[2]).read_text())
    c=SimpleNamespace(archive_root=root,catalog_db_path=db,archive_lock=asyncio.Lock(),status_changed=asyncio.Condition(),status_revision=0,blink=None)
    recorder=LiveRecorder(c)
    app=create_app(c)
    schema=app.openapi()
    assert '/api/v1/settings/live-recording' in schema['paths']
    assert '/api/v1/liveview/recording/start' in schema['paths']
    for shutdown in app.router.on_shutdown:
      result=shutdown()
      if asyncio.iscoroutine(result):await result
    print('PASS recording API registration and shutdown',flush=True)
    assert recorder.settings.limit==150
    for bad in [0,-1,4,1801,True,2.5,'150']:
      try:recorder.settings.save(bad)
      except ValueError:pass
      else:raise AssertionError('Invalid limit accepted')
    recorder.settings.save(5);assert RecordingSettings(recorder.settings.path).limit==5
    sample=root/'sample.ts'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc=size=160x120:rate=10','-f','lavfi','-i','sine=frequency=600:sample_rate=16000','-t','12','-c:v','libx264','-pix_fmt','yuv420p','-g','10','-c:a','aac','-f','mpegts',str(sample)],check=True)
    payload=sample.read_bytes()
    clients=set()
    async def serve(reader,writer):
      clients.add(asyncio.current_task())
      try:
        chunk=188*7
        for i in range(0,len(payload),chunk):
          writer.write(payload[i:i+chunk]);await writer.drain()
          await asyncio.sleep(12*chunk/len(payload))
      except (ConnectionError,asyncio.CancelledError):pass
      finally:
        writer.close();clients.discard(asyncio.current_task())
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    url=f'tcp://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    identity={'blink_device_id':'test-camera','device_name':'Synthetic Camera','blink_system_id':'test-system','system_name':'Synthetic System','device_type':'test'}
    try:
      first=await recorder.start(url,'session-one',identity)
      assert (await recorder.start(url,'session-one',identity))['recording_id']==first['recording_id']
      try:await recorder.stop('wrong-session')
      except ValueError:pass
      else:raise AssertionError('Stale stop accepted')
      assert list_clips(db)['total']==0
      await asyncio.wait_for(asyncio.shield(recorder.task),35)
      assert recorder.state['state']=='saved',recorder.state
      assert recorder.state['stop_reason']=='duration_limit',recorder.state
      assert recorder.state['has_audio']
      clip=list_clips(db)['clips'][0]
      assert clip['trigger_type']=='Recorded Live' and clip['id'] is None
      assert clip['duration_ms']<=6500,clip
      assert set_clip_watched(db,clip['catalog_id'],True)
      await recorder.recover()
      assert list_clips(db)['total']==1 and list_clips(db)['clips'][0]['watched']
      print('PASS settings persistence/bounds, repeated start, stale stop, automatic cutoff, audio/video MP4, local trigger, recovery/review preservation',flush=True)
      recorder.settings.save(30)
      await recorder.start(url,'session-two',identity)
      await asyncio.sleep(4)
      result=await recorder.stop('session-two')
      assert result['state']=='saved',result
      assert result['stop_reason']=='manual'
      assert list_clips(db)['total']==2
      row=list_clips(db)['clips'][0]
      # Delete only synthetic files from the isolated test archive. Blink is disconnected.
      await coordinate_clip_delete(c,row['catalog_id'])
      assert list_clips(db)['total']==1
      assert not (root/'clips'/row['filename']).exists()
      print('PASS manual finalization, catalog listing, offline local-only deletion',flush=True)
      # Natural camera end must finalize without a Stop request.
      await recorder.start(url,'natural-end',identity)
      await asyncio.wait_for(asyncio.shield(recorder.task),35)
      assert recorder.state['state']=='saved',recorder.state
      assert recorder.state['stop_reason']=='stream_ended',recorder.state
      assert list_clips(db)['total']==2
      import import_catalog
      import_catalog.ARCHIVE_ROOT=root
      import_catalog.CLIPS_DIR=root/'clips'
      import_catalog.THUMBS_DIR=root/'clip_thumbs'
      assert not import_catalog.build_recovery_maps()[0]
      # Recovery must recreate a missing row from a complete recording manifest.
      with sqlite3.connect(db) as conn:conn.execute('DELETE FROM clips')
      await recorder.recover()
      assert list_clips(db)['total']==2
      print('PASS natural stream end, archive importer compatibility, missing-row recovery',flush=True)
      async def empty(reader,writer):writer.close()
      empty_server=await asyncio.start_server(empty,'127.0.0.1',0)
      try:
        await recorder.start(f'tcp://127.0.0.1:{empty_server.sockets[0].getsockname()[1]}','bad',identity)
        await asyncio.wait_for(asyncio.shield(recorder.task),20)
        assert recorder.state['state']=='failed',recorder.state
        assert list_clips(db)['total']==2
        print('PASS invalid recording remains outside catalog',flush=True)
      finally:empty_server.close();await empty_server.wait_closed()
    finally:
      await recorder.stop()
      server.close();await server.wait_closed()
      for task in list(clients):task.cancel()
      await asyncio.gather(*clients,return_exceptions=True)
asyncio.run(main())
