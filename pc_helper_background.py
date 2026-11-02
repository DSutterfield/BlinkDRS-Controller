"""BlinkDRS PC helper 1.0.6: two independent, supervised background slots."""
import os,sys,json,time,uuid,subprocess,threading,logging,gc,re,shutil
from pathlib import Path
VERSION='1.0.6'
def atomic_json(path,data):
    path=Path(path);stage=path.with_name(path.name+'.'+uuid.uuid4().hex+'.partial');stage.write_text(json.dumps(data,indent=2));os.replace(stage,path)
class Transport:
    def __init__(self,config):self.config=config
    def command(self,*args):
        r=subprocess.run([self.config['transport'],*map(str,args)],capture_output=True,text=True,timeout=180,creationflags=0x08000000 if os.name=='nt' else 0)
        if r.returncode:raise ConnectionError((r.stderr or 'Pi connection failed')[-1000:])
        return r.stdout
    def rpc(self,request):
        import shlex
        config=self.config;folder=Path(config['data'])/'requests';folder.mkdir(exist_ok=True);script=folder/(uuid.uuid4().hex+'.sh')
        command='cd '+shlex.quote(config['remote_source'])+'\nOPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '+shlex.quote(config['remote_python'])+' pc_helper_bridge.py --root '+shlex.quote(config['remote_root'])+" <<'REQUEST'\n"+json.dumps(request)+"\nREQUEST\n"
        try:
            script.write_text(command,newline='\n');response=json.loads(self.command(script))
        finally:
            try:script.unlink()
            except FileNotFoundError:pass
        if not response.get('ok'):
            from pc_helper_queue import Conflict,Paused,ReferenceChanged,ReviewChanged
            kind=response.get('error');error_types=dict(Conflict=Conflict,Paused=Paused,ReferenceChanged=ReferenceChanged,ReviewChanged=ReviewChanged)
            raise error_types.get(kind,RuntimeError)(response.get('message','Pi helper request failed'))
        return response['result']
    def download(self,name,path):self.command('download',self.config['remote_root']+'/'+name,path)
    def upload(self,path,name):self.command('upload',path,self.config['remote_root']+'/'+name)
def configured(path):return json.loads(Path(path).read_text())
def cleanup_completed(workspace):
    root=Path(workspace).resolve()
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch('[a-f0-9]{32}',folder.name) or not folder.resolve().is_relative_to(root):continue
        if (folder/'receipt.json').is_file():
            for name in ['video.mp4','references.sqlite']:
                try:(folder/name).unlink()
                except (FileNotFoundError,PermissionError):pass
        # Retain small pending packets/manifests/receipts for diagnosis; only
        # redundant recording and reference copies are reclaimed here.
def log_setup(data,name):
    from logging.handlers import RotatingFileHandler
    logs=Path(data)/'logs';logs.mkdir(exist_ok=True);stream=(logs/(name+'-console.log')).open('a',buffering=1);sys.stdout=stream;sys.stderr=stream
    handler=RotatingFileHandler(logs/(name+'.log'),maxBytes=2*1024*1024,backupCount=3);handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logging.basicConfig(handlers=[handler],level=logging.INFO,force=True)
def review_pilot_environment(config):
    value='1' if config.get('saved_view_review_pilot') is True else '0'
    return dict(BLINK_SAVED_VIEW_REVIEW_PILOT=value,BLINK_SAVED_VIEW_REVIEW_INFERENCE=value)

def slot(config_path,number):
    config=configured(config_path);data=Path(config['data']);log_setup(data,'slot-'+str(number));os.environ.update(BLINK_AI_THREADS='2',BLINK_AI_CV_THREADS='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1');sys.path[:0]=config['libraries']
    os.environ.update(review_pilot_environment(config))
    logging.info('Saved-view review pilot configured: %s; all suggestions require review',config.get('saved_view_review_pilot') is True)
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
    from pc_helper_wire import sha
    from pc_helper_client import run_one
    from identity_models import LocalModels
    source=Path(__file__).resolve().parent
    for name,digest in config['source_sha256'].items():
        if sha((source/name).read_bytes())!=digest:raise RuntimeError('Helper source fingerprint mismatch: '+name)
    for name,digest in config['asset_sha256'].items():
        if sha((Path(config['models'])/name).read_bytes())!=digest:raise RuntimeError('Helper model fingerprint mismatch: '+name)
    models=LocalModels(config['models']);transport=Transport(config);workspace=data/('slot-'+str(number));workspace.mkdir(exist_ok=True);owner=os.environ.get('COMPUTERNAME','PC')+'-slot-'+str(number);logging.info('Ready: two-thread inference slot %s',owner)
    while configured(config_path).get('enabled') is True:
        try:
            result=run_one(transport,models,workspace,config['contract'],owner,stop_check=lambda:configured(config_path).get('enabled') is not True)
            if result:
                logging.info('Accepted clip %s in %.1fs, duplicate=%s',result['catalog_id'],result['seconds'],result['receipt'].get('duplicate'));atomic_json(data/('slot-'+str(number)+'-last.json'),result)
                gc.collect();cleanup_completed(workspace)
            else:time.sleep(15)
        except Exception as exc:
            logging.warning('%s: %s',type(exc).__name__,str(exc)[:1000]);time.sleep(15)
    logging.info('Slot stopped; unfinished work returned or will expire')
class WindowsJobs:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c=c;self.k=c.WinDLL('kernel32',use_last_error=True)
        class Basic(c.Structure):_fields_=[('process_time',c.c_longlong),('job_time',c.c_longlong),('flags',w.DWORD),('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):_fields_=[(name,c.c_ulonglong) for name in ['read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes']]
        class Extended(c.Structure):_fields_=[('basic',Basic),('io',IO)]+[(name,c.c_size_t) for name in ['process_memory','job_memory','peak_process','peak_job']]
        self.k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];self.k.CreateJobObjectW.restype=w.HANDLE;self.k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD];self.k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.k.OpenProcess.restype=w.HANDLE;self.k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];self.k.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.k.CreateJobObjectW(None,None);info=Extended();info.basic.flags=0x2000
        if not self.handle or not self.k.SetInformationJobObject(self.handle,9,c.byref(info),c.sizeof(info)):raise c.WinError(c.get_last_error())
    def add(self,pid):
        handle=self.k.OpenProcess(0x101,False,pid)
        try:
            if not handle or not self.k.AssignProcessToJobObject(self.handle,handle):raise self.c.WinError(self.c.get_last_error())
        finally:
            if handle:self.k.CloseHandle(handle)
    def close(self):
        if self.handle:self.k.CloseHandle(self.handle);self.handle=None
def main():
    import argparse,multiprocessing,msvcrt
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args();path=Path(args.config).resolve();config=configured(path);data=Path(config['data']);data.mkdir(parents=True,exist_ok=True);log_setup(data,'supervisor')
    with (data/'supervisor.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:logging.info('Another helper supervisor already owns this instance');return
        jobs=WindowsJobs();children={};restart_at={};ctx=multiprocessing.get_context('spawn');logging.info('BlinkDRS PC helper %s started',VERSION)
        try:
            while configured(path).get('enabled') is True:
                for number in [1,2]:
                    child=children.get(number)
                    if child and child.is_alive():continue
                    if child:
                        child.join();logging.warning('Slot %s exited %s; retry after 30s',number,child.exitcode);children.pop(number);restart_at[number]=time.monotonic()+30
                    if time.monotonic()<restart_at.get(number,0):continue
                    child=ctx.Process(target=slot,args=(str(path),number),name='BlinkDRS-helper-'+str(number));child.start()
                    try:jobs.add(child.pid)
                    except Exception:child.terminate();child.join();raise
                    children[number]=child
                atomic_json(data/'status.json',dict(version=VERSION,state='running',pid=os.getpid(),updated_at=time.time(),slots={str(n):dict(pid=p.pid,alive=p.is_alive()) for n,p in children.items()}));time.sleep(5)
        finally:
            # Config disable is graceful; forced supervisor exit also closes
            # the Windows job and kills its workers/transport children.
            for child in children.values():child.join(timeout=30)
            jobs.close()
            for child in children.values():child.join(timeout=5)
            atomic_json(data/'status.json',dict(version=VERSION,state='stopped',updated_at=time.time()));logging.info('Supervisor stopped')
if __name__=='__main__':main()
