"""Fingerprint-pinned SSH queue RPC for test sandboxes and configured production."""
import argparse,json,sys,re,os,shutil
from pathlib import Path
from pc_helper_queue import Coordinator,path_in,ReferenceChanged
from pc_helper_wire import MAX_PACKET,sha,decode
def cleanup_transfers(root,db,limit=16):
    """Remove only known inactive lease transport copies; never catalog evidence."""
    import sqlite3
    from contextlib import closing
    root=Path(root).resolve()
    with closing(sqlite3.connect(db,timeout=30)) as c:
        rows=c.execute("SELECT token FROM pc_helper_leases WHERE status<>'leased' ORDER BY rowid DESC").fetchall()
    cleaned=0
    for (token,) in rows:
        if cleaned>=limit:break
        if not re.fullmatch('[a-f0-9]{32}',token):continue
        folder=root/'outgoing'/token;packet=root/'incoming'/(token+'.partial')
        if folder.is_symlink() or packet.is_symlink():continue
        if not folder.exists() and not packet.exists():continue
        if folder.exists():
            for name in ('video.mp4','references.sqlite','manifest.json'):
                file=folder/name
                if file.is_file() and not file.is_symlink():
                    try:file.unlink()
                    except FileNotFoundError:pass
            try:folder.rmdir()
            except OSError:pass
        if packet.is_file():
            try:packet.unlink()
            except FileNotFoundError:pass
        cleaned+=1
    return cleaned

def dispatch(root,request):
    root=Path(root).resolve();sandbox=root/'.pc-helper-sandbox.json';live=not sandbox.is_file()
    config=json.loads((root/'config.json' if live else sandbox).read_text())
    if live:
        if config.get('enabled') is not True or config.get('production') is not True:raise ValueError('PC helper is disabled')
        if Path(config['db']).resolve()!=Path('/home/dan/BlinkDVR/blink_catalog.db') or Path(config['archive']).resolve()!=Path('/home/dan/BlinkDVR'):raise ValueError('Unrecognized production catalog/archive')
    elif config.get('test_only') is not True:raise ValueError('Explicit test sandbox required')
    for name,digest in config.get('source_sha256',{}).items():
        if Path(name).name!=name or sha(Path(__file__).with_name(name).read_bytes())!=digest:raise ValueError('Controller helper source changed')
    db=Path(config['db']) if live else path_in(root,config['db']);archive=Path(config['archive']) if live else path_in(root,config['archive'])
    def ready():
        if not live:return json.loads(path_in(root,'ready.json').read_text()).get('ready') is True
        try:
            from urllib.request import urlopen
            base=config.get('controller_url','http://127.0.0.1:8000')
            with urlopen(base+'/api/v1/liveview/status',timeout=3) as r:busy=json.load(r)['active']
            with urlopen(base+'/api/v1/liveview/recording',timeout=3) as r:recording=json.load(r)['state']
            return busy is False and recording in ('idle','saved')
        except Exception:return False
    def zones(cid):
        if not live:return json.loads(path_in(root,config['zones']).read_text())[str(cid)]
        # Claim uses a prefetched policy (no network under the SQLite lock).
        if cid not in policies:raise ValueError('Camera settings changed; retry helper claim')
        return policies[cid]
    policies={}
    if live and request.get('action') in ['claim','commit']:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(db))) as c:
            if request['action']=='claim':
                row=c.execute("SELECT j.catalog_id FROM identity_analysis_jobs j JOIN clips c ON c.id=j.catalog_id WHERE j.state='queued' AND c.local_present=1 ORDER BY j.updated_at,j.catalog_id LIMIT 1").fetchone()
            else:row=c.execute('SELECT catalog_id FROM pc_helper_leases WHERE token=?',(request.get('token'),)).fetchone()
        if row:
            from identity_motion_zones import load_camera_zones,cache_path
            policy=load_camera_zones(db,row[0],config.get('controller_url','http://127.0.0.1:8000'))
            # MotionZones retains cells/dimensions; reconstruct the validated
            # response without changing any region or falling back to all-on.
            from contextlib import closing
            with closing(sqlite3.connect(str(db))) as c:
                mapping=c.execute('SELECT d.blink_device_id,s.blink_system_id FROM clips c JOIN devices d ON d.id=c.device_id JOIN systems s ON s.id=d.system_id WHERE c.id=?',(row[0],)).fetchone()
            cached=json.loads(cache_path(db,*mapping).read_text());policies[row[0]]=cached['zones']
    queue=Coordinator(db,archive,config['contract'],zones,ready);action=request.get('action')
    if action=='claim':
        cleanup_transfers(root,db)
        manifest=queue.claim(request['owner'],request['contract'])
        if manifest is None:return None
        try:return queue.export(manifest['token'],path_in(root,'outgoing/'+manifest['token']))
        except ReferenceChanged as exc:
            queue.retry_reference_change(manifest['token'],exc);cleanup_transfers(root,db);raise
        except BaseException:
            queue.release(manifest['token']);cleanup_transfers(root,db);raise
    token=request.get('token')
    if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid attempt token')
    if action=='heartbeat':return queue.heartbeat(token)
    if action=='release':queue.release(token);cleanup_transfers(root,db);return dict(status='released')
    if action=='cancel':queue.cancel(token);cleanup_transfers(root,db);return dict(status='canceled')
    if action=='commit':
        path=path_in(root,'incoming/'+str(token)+'.partial')
        if path.stat().st_size>MAX_PACKET:raise ValueError('Evidence packet too large')
        data=path.read_bytes()
        if sha(data)!=request['sha256']:raise ValueError('Incomplete evidence transfer')
        if decode(data)[0]['token']!=token:raise ValueError('Attempt token does not match packet')
        try:result=queue.commit(data)
        except ReferenceChanged as exc:
            queue.retry_reference_change(token,exc);cleanup_transfers(root,db);raise
        # Only redundant transport copies are removed, never archive files or
        # evidence in the catalog. Receipts survive lost acknowledgements.
        folder=path_in(root,'outgoing/'+token)
        if folder.exists():
            for name in ['video.mp4','references.sqlite','manifest.json']:
                file=folder/name
                if file.is_file():file.unlink()
            try:folder.rmdir()
            except OSError:pass
        try:path.unlink()
        except FileNotFoundError:pass
        return result
    raise ValueError('Unknown queue operation')
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);args=parser.parse_args()
    data=sys.stdin.buffer.read(65537)
    try:
        if len(data)>65536:raise ValueError('Request too large')
        print(json.dumps(dict(ok=True,result=dispatch(args.root,json.loads(data)))))
    except Exception as exc:print(json.dumps(dict(ok=False,error=type(exc).__name__,message=str(exc))))
if __name__=='__main__':main()
