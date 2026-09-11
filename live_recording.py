"""Pi-owned live recording, atomic publication, and durable system settings."""
from collections import deque
import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
from datetime import datetime, timezone
import uuid
from catalog_store import upsert_system, upsert_device


class RecordingSettings:
    def __init__(self, path):
        self.path = Path(path)
        self.limit = 150
        if not self.path.exists():
            self.save(150)
        if self.path.exists():
            self.limit = self.validate(json.loads(self.path.read_text())['max_duration_seconds'])

    @staticmethod
    def validate(value):
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 1800:
            raise ValueError('Recording limit must be a whole number from 5 to 1800 seconds.')
        return value

    def save(self, value):
        value = self.validate(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as f:
            json.dump({'max_duration_seconds': value}, f)
            f.flush(); os.fsync(f.fileno())
        temp.replace(self.path)
        self.limit = value


def catalog_recording(db, root, path, meta):
    """Idempotent local-only insertion; never create a Blink media identifier."""
    root, path = Path(root), Path(path)
    identity = meta['identity']
    with sqlite3.connect(db, timeout=15) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        system = upsert_system(conn, identity, meta['created_at'])
        device = upsert_device(conn, identity, system, meta['created_at'])
        thumb = root/'clip_thumbs'/f'{path.stem}.jpg'
        # Existing watched state is intentionally preserved during recovery.
        conn.execute('''INSERT INTO clips
            (metadata_status,device_id,system_id,device_name_snapshot,system_name_snapshot,
             filename,video_path,sidecar_path,thumbnail_path,file_size_bytes,captured_at,
             watched,source,media_type,trigger_type,duration_ms,local_present,cloud_present)
            VALUES ('local_only',?,?,?,?,?,?,?,?,?,?,?,'recorded_live','video','Recorded Live',?,1,0)
            ON CONFLICT(video_path) DO NOTHING''',
            (device,system,identity['device_name'],identity['system_name'],path.name,
             str(path.relative_to(root)),str(path.with_suffix('.json').relative_to(root)),
             str(thumb.relative_to(root)) if thumb.is_file() else None,
             path.stat().st_size,meta['created_at'],int(bool(meta.get('watched',False))),meta['duration_ms']))


class LiveRecorder:
    def __init__(self, controller):
        self.controller = controller
        self.root = Path(controller.archive_root)
        self.settings = RecordingSettings(self.root/'live_recording_settings.json')
        self.process = None
        self.task = None
        self.stop_event = None
        self.state = {'state':'idle'}
        self.started = 0

    def status(self):
        result = dict(self.state)
        if result['state'] == 'recording':
            result['elapsed_seconds'] = round(time.monotonic()-self.started,1)
        return result

    async def recover(self):
        # Only fully published MP4s with our own manifest can be recovered.
        async with self.controller.archive_lock:
            for sidecar in (self.root/'clips').glob('live-*.json'):
                try:
                    meta=json.loads(sidecar.read_text())
                    video=sidecar.with_suffix('.mp4')
                    if meta.get('local_recording') == 1 and video.is_file():
                        await asyncio.to_thread(catalog_recording,self.controller.catalog_db_path,self.root,video,meta)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Live recording catalog recovery failed')

    async def start(self, url, session_id, identity):
        if self.task is not None and not self.task.done():
            if self.state.get('session_id') != session_id:
                raise ValueError('Another Live View session is recording.')
            return self.status()  # A retried start must not create a second file.
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise RuntimeError('Recording requires FFmpeg and FFprobe on the Pi.')
        if shutil.disk_usage(self.root).free < 256*1024*1024:
            raise RuntimeError('Not enough free archive space to start recording.')
        stage=self.root/'.live_recording_pending'
        stage.mkdir(exist_ok=True)
        name='live-'+uuid.uuid4().hex+'-'+datetime.now(timezone.utc).strftime('%Y-%m-%dt%H-%M-%S-00-00')
        temp=stage/(name+'.mp4')
        meta={'local_recording':1,'created_at':datetime.now(timezone.utc).isoformat(),
              'identity':identity,'session_id':session_id}
        limit=self.settings.limit
        self.process=await asyncio.create_subprocess_exec(
            'ffmpeg','-hide_banner','-loglevel','error','-y','-rw_timeout','10000000',
            '-analyzeduration','5000000','-probesize','4194304','-fflags','+genpts',
            '-i',url,'-map','0:v:0','-map','0:a:0?','-c','copy',
            '-t',str(limit),'-avoid_negative_ts','make_zero','-movflags','+faststart',str(temp),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE)
        errors=deque(maxlen=12)
        error_task=asyncio.create_task(self._read_errors(self.process,errors))
        self.stop_event=asyncio.Event()
        self.started=time.monotonic()
        self.state={'state':'recording','session_id':session_id,'recording_id':name,
                    'max_duration_seconds':limit,'camera':identity['device_name']}
        self.task=asyncio.create_task(self._finish(temp,meta,limit,errors,error_task))
        return self.status()

    async def stop(self, session_id=None):
        if session_id and self.state.get('session_id') not in (None,session_id):
            raise ValueError('Recording belongs to a different Live View session.')
        if self.task is not None and not self.task.done():
            self.stop_event.set()
            await asyncio.shield(self.task)
        return self.status()

    async def _read_errors(self, proc, errors):
        while True:
            chunk=await proc.stderr.read(1024)
            if not chunk:break
            errors.append(chunk.decode("utf-8",errors="replace"))

    async def _finish(self, temp, meta, limit, errors, error_task):
        proc=self.process
        ended=asyncio.create_task(proc.wait())
        stop=asyncio.create_task(self.stop_event.wait())
        try:
            done,_=await asyncio.wait({ended,stop},timeout=limit,return_when=asyncio.FIRST_COMPLETED)
            reason='manual' if stop in done else 'stream_ended' if ended in done else 'duration_limit'
            if time.monotonic()-self.started >= limit-0.2:reason='duration_limit'
            self.state.update(state='finalizing',stop_reason=reason)
            if proc.returncode is None:
                with contextlib.suppress(ConnectionError,BrokenPipeError):
                    proc.stdin.write(b'q\n');await proc.stdin.drain()
                try:await asyncio.wait_for(asyncio.shield(ended),15)
                except asyncio.TimeoutError:
                    proc.kill();await ended
                    raise RuntimeError('Recorder did not finish safely; incomplete file retained on Pi.')
            await error_task
            if proc.returncode != 0:
                raise RuntimeError('Recording failed: '+''.join(errors)[-1500:])
            probe=await asyncio.create_subprocess_exec('ffprobe','-v','error','-show_streams','-show_format',
                '-of','json',str(temp),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:raw,_=await asyncio.wait_for(probe.communicate(),15)
            except BaseException:
                if probe.returncode is None:probe.kill();await probe.wait()
                raise
            data=json.loads(raw)
            duration=float(data.get('format',{}).get('duration',0))
            if probe.returncode != 0 or not math.isfinite(duration) or duration<=0 or not any(s['codec_type']=='video' for s in data.get('streams',[])):
                raise RuntimeError('Recording contains no valid video; it was not added to Recorded Clips.')
            meta['duration_ms']=round(duration*1000)
            meta['has_audio']=any(s['codec_type']=='audio' for s in data['streams'])
            final=self.root/'clips'/temp.name
            thumbs=self.root/'clip_thumbs';thumbs.mkdir(exist_ok=True)
            thumb=thumbs/(temp.stem+'.jpg')
            # Thumbnail failure does not invalidate an otherwise playable recording.
            thumbproc=await asyncio.create_subprocess_exec('ffmpeg','-v','error','-y','-i',str(temp),
                '-frames:v','1',str(thumb),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            try:await asyncio.wait_for(thumbproc.wait(),10)
            except asyncio.TimeoutError:thumbproc.kill();await thumbproc.wait()
            async with self.controller.archive_lock:
                final.parent.mkdir(exist_ok=True)
                manifest=temp.with_suffix('.json')
                with manifest.open('w') as f:
                    json.dump(meta,f);f.flush();os.fsync(f.fileno())
                manifest.replace(final.with_suffix('.json'))
                temp.replace(final)
                await asyncio.to_thread(catalog_recording,self.controller.catalog_db_path,self.root,final,meta)
            self.state.update(state='saved',filename=final.name,duration_seconds=round(duration,2),has_audio=meta['has_audio'])
            async with self.controller.status_changed:
                self.controller.status_revision+=1
                self.controller.status_changed.notify_all()
        except Exception as exc:
            self.state.update(state='failed',error=str(exc))
        finally:
            for task in (stop,ended):
                if not task.done():task.cancel()
            await asyncio.gather(stop,ended,return_exceptions=True)
            if proc.returncode is None:
                proc.kill();await proc.wait()
            await error_task
            if proc.stdin:proc.stdin.close()


def install_recording_api(app,controller,bridge):
    from fastapi import HTTPException
    from pydantic import BaseModel, StrictInt
    recorder=LiveRecorder(controller)
    lock=asyncio.Lock()
    class LimitRequest(BaseModel):
        max_duration_seconds: StrictInt
    class SessionRequest(BaseModel):
        session_id: str
    app.router.add_event_handler('startup',recorder.recover)
    app.router.add_event_handler('shutdown',recorder.stop)
    @app.get('/api/v1/settings/live-recording')
    async def get_settings():return {'max_duration_seconds':recorder.settings.limit}
    @app.put('/api/v1/settings/live-recording')
    async def put_settings(request:LimitRequest):
        try:recorder.settings.save(request.max_duration_seconds)
        except ValueError as e:raise HTTPException(422,str(e)) from e
        return await get_settings()
    @app.get('/api/v1/liveview/recording')
    async def get_recording():return recorder.status()
    @app.post('/api/v1/liveview/recording/start')
    async def start_recording(request:SessionRequest):
        async with lock:
            try:
                source=bridge.recording_source(request.session_id)
                camera=next((c for c in controller.blink.cameras.values() if str(c.name).strip().casefold()==source['camera'].strip().casefold()),None) if controller.blink else None
                if camera is None:raise ValueError('Camera identity is unavailable.')
                identity={'blink_device_id':str(camera.camera_id),'device_name':camera.name.strip(),
                    'blink_system_id':str(camera.sync.network_id),'system_name':camera.sync.name,
                    'device_type':str(getattr(camera,'product_type','camera'))}
                return await recorder.start(source['url'],request.session_id,identity)
            except ValueError as e:raise HTTPException(409,str(e)) from e
            except RuntimeError as e:raise HTTPException(503,str(e)) from e
    @app.post('/api/v1/liveview/recording/stop')
    async def stop_recording(request:SessionRequest):
        async with lock:
            try:return await recorder.stop(request.session_id)
            except ValueError as e:raise HTTPException(409,str(e)) from e
    return recorder,lock
