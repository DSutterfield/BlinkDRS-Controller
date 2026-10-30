"""One helper slot. All catalog publication and identity decisions stay on Pi."""
import json,os,time,threading,re
from pathlib import Path
from pc_helper_wire import sha,encode
class LostLease(RuntimeError):pass
def analyze(manifest,video,references,models,cache,checkpoint):
    if sha(Path(video).read_bytes())!=manifest['video_sha256'] or sha(Path(references).read_bytes())!=manifest['references_sha256']:raise ValueError('Transferred inputs failed integrity validation')
    from subject_type_learning import load_references
    from subject_type_check import prepare_references
    from cat_type_variants import load_variants
    from person_scene_search import PersonConsensusModels
    from identity_motion_zones import MotionZones
    from identity_worker import analyze_video
    cid=manifest['catalog_id'];models.type_references=prepare_references(load_references(references,models.pet_version,cid));models.cat_type_references=prepare_references(load_variants(references,models,models.type_references,checkpoint,cache_path=cache,teaching_clip=cid));models.motion_zones=MotionZones(manifest['zones']);models.__dict__.pop('_exact_pet_vectors',None)
    observations=analyze_video(video,PersonConsensusModels(models,checkpoint,catalog_id=cid),checkpoint=checkpoint)
    checkpoint()
    if sha(Path(video).read_bytes())!=manifest['video_sha256']:raise ValueError('Input changed during analysis')
    return encode(manifest,observations)
def run_one(transport,models,workspace,contract,owner,heartbeat_interval=30,stop_check=lambda:False):
    """Transport exposes rpc/download/upload; SSH authentication stays outside.

    The saved packet survives a lost acknowledgement for exact replay. A failed
    heartbeat stops inference at its next checkpoint; abandoned jobs expire.
    """
    manifest=transport.rpc(dict(action='claim',owner=owner,contract=contract))
    if manifest is None:return None
    if manifest['contract']!=contract:raise ValueError('Controller analysis contract mismatch')
    token=manifest['token']
    if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid helper attempt token')
    directory=Path(workspace)/token;directory.mkdir(parents=True,exist_ok=False);(directory/'manifest.json').write_text(json.dumps(manifest));lost=threading.Event();stop=threading.Event();errors=[]
    def heartbeat():
        while not stop.wait(heartbeat_interval):
            try:transport.rpc(dict(action='heartbeat',token=token))
            except Exception as exc:errors.append(str(exc));lost.set();return
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    def checkpoint():
        if stop_check():raise LostLease('PC helper stopping; return unfinished job')
        if lost.is_set():raise LostLease('Heartbeat failed; abandon result safely')
    started=time.perf_counter()
    try:
        for name,h in [('video.mp4',manifest['video_sha256']),('references.sqlite',manifest['references_sha256'])]:
            staged=directory/(name+'.partial')
            cached=Path(workspace)/'reference-cache'/(h+'.sqlite') if name=='references.sqlite' else None
            if cached is not None and cached.is_file() and sha(cached.read_bytes())==h:
                import shutil
                shutil.copyfile(cached,staged)
            else:transport.download('outgoing/'+token+'/'+name,staged)
            if sha(staged.read_bytes())!=h:raise ValueError('Incomplete input transfer')
            os.replace(staged,directory/name)
            if cached is not None and not cached.exists():
                import shutil
                cached.parent.mkdir(exist_ok=True);shutil.copyfile(directory/name,cached)
                older=sorted(cached.parent.glob('*.sqlite'),key=lambda p:p.stat().st_mtime,reverse=True)
                for stale in older[4:]:stale.unlink()
            checkpoint()
        data=analyze(manifest,directory/'video.mp4',directory/'references.sqlite',models,Path(workspace)/'variants.sqlite',checkpoint);packet=directory/'pending-result.json';packet.write_bytes(data);checkpoint()
        request=dict(action='commit',token=token,sha256=sha(data));receipt=None
        # Same token and exact bytes on retry: acceptance survives lost ACKs.
        for attempt in range(3):
            try:
                transport.upload(packet,'incoming/'+token+'.partial');receipt=transport.rpc(request);break
            except (ConnectionError,TimeoutError,OSError):
                if attempt==2:raise
        if receipt.get('status')!='accepted':raise LostLease('Result was not accepted')
        (directory/'receipt.json').write_text(json.dumps(receipt));return dict(catalog_id=manifest['catalog_id'],token=token,seconds=time.perf_counter()-started,receipt=receipt,packet_bytes=len(data))
    except BaseException:
        try:transport.rpc(dict(action='release',token=token))
        except Exception:pass  # Expiry fences late packets even when disconnected.
        raise
    finally:stop.set();thread.join(timeout=120)
