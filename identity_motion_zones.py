"""Camera motion cells gate discovery without changing recorded/crop pixels."""
import json
import math
import logging
import sqlite3
import time
import hashlib
import tempfile
import os
from pathlib import Path
from contextlib import closing, contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen


class MotionZones:
    def __init__(self, response):
        dims = [response.get(k) for k in ('basic_rows','basic_columns','sub_rows','sub_columns')]
        if any(type(n) is not int or not 1 <= n <= 32 for n in dims):
            raise ValueError('Unrecognized identity motion-zone dimensions')
        self.rows, self.columns = dims[0]*dims[2], dims[1]*dims[3]
        cells = response.get('cells')
        if (not isinstance(cells,list) or len(cells) != self.rows*self.columns or len(cells)>4096
                or any(type(n) is not int or n not in (0,1) for n in cells)):
            raise ValueError('Unrecognized identity motion-zone cells')
        self.cells = tuple(cells)
        self.active = tuple((i//self.columns,i%self.columns) for i,n in enumerate(cells) if n)
        self.revision = response.get('revision')
        self.all_active = all(cells)
        self.suppressed = 0
        self.skipped_views = 0

    def clipped(self, box, shape):
        h,w = shape[:2]
        if h<=0 or w<=0 or len(box)!=4 or not all(math.isfinite(n) for n in box):
            return None
        a,b,c,d=box
        a,b,c,d=max(0,a),max(0,b),min(w,c),min(h,d)
        return (a,b,c,d) if c>a and d>b else None

    def allows(self, box, shape):
        box=self.clipped(box,shape)
        if box is None:return False
        a,b,c,d=box;h,w=shape[:2]
        row=min(self.rows-1,int((b+d)*self.rows/(2*h)))
        col=min(self.columns-1,int((a+c)*self.columns/(2*w)))
        allowed=bool(self.cells[row*self.columns+col])
        if not allowed:self.suppressed+=1
        return allowed

    def intersects(self, box, shape):
        box=self.clipped(box,shape)
        if box is None:return False
        a,b,c,d=box;h,w=shape[:2]
        return any(min(c,(col+1)*w/self.columns)>max(a,col*w/self.columns)
                   and min(d,(row+1)*h/self.rows)>max(b,row*h/self.rows)
                   for row,col in self.active)


def permits(models, box, shape):
    policy=getattr(models,'motion_zones',None)
    return not isinstance(policy,MotionZones) or policy.allows(box,shape)


def search_view(models, box, shape):
    policy=getattr(models,'motion_zones',None)
    if not isinstance(policy,MotionZones):return True
    keep=policy.intersects(box,shape)
    if not keep:policy.skipped_views+=1
    return keep


MAX_CACHE_AGE = 24*60*60


def cache_path(db_path, device_id, system_id):
    key = hashlib.sha256((str(system_id)+'\0'+str(device_id)).encode()).hexdigest()
    return Path(db_path).resolve().parent/'identity-motion-zones'/(key+'.json')


@contextmanager
def cache_write_lock(target):
    with target.with_suffix('.lock').open('a+b') as lock:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX)
        else:
            import msvcrt
            if os.fstat(lock.fileno()).st_size==0:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        try:yield
        finally:
            if os.name == 'posix':fcntl.flock(lock,fcntl.LOCK_UN)
            else:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def remember_zones(db_path, device_id, system_id, response, received_at):
    target = cache_path(db_path,device_id,system_id)
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    payload = dict(camera_id=str(device_id),system_id=str(system_id),received_at=received_at,zones=response)
    with cache_write_lock(target):
        try:
            previous=json.loads(target.read_text(encoding='utf-8'))
            stamp=previous.get('received_at')
            # A delayed GET must not overwrite a newer confirmed editor save.
            if (previous.get('camera_id')==str(device_id) and previous.get('system_id')==str(system_id)
                    and type(stamp) in (int,float) and math.isfinite(stamp)
                    and received_at < stamp <= time.time()):return
        except (OSError,ValueError,AttributeError):pass
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,
                                             prefix=target.stem+'.',suffix='.tmp',delete=False) as f:
                temporary=Path(f.name)
                json.dump(payload,f)
            os.replace(temporary,target)
        finally:
            if temporary is not None and temporary.exists():temporary.unlink()


def cached_zones(db_path, device_id, system_id, now):
    data=json.loads(cache_path(db_path,device_id,system_id).read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('Malformed motion-zone cache')
    if data.get('camera_id')!=str(device_id) or data.get('system_id')!=str(system_id):
        raise ValueError('Cached motion zones belong to another camera/system')
    received=data.get('received_at')
    if type(received) not in (int,float) or not math.isfinite(received) or not 0<=now-received<=MAX_CACHE_AGE:
        raise ValueError('Cached motion zones are expired or have an invalid timestamp')
    try:policy=MotionZones(data['zones'])
    except (ValueError,TypeError,AttributeError) as exc:raise ValueError('Malformed cached motion zones') from exc
    policy.settings_source='cached';policy.settings_age_seconds=now-received
    return policy


def read_camera_zones(db_path, device_id, system_id, controller_url):
    url=controller_url.rstrip('/')+'/api/v1/devices/'+quote(str(device_id),safe='')+'/motion-zones?'+urlencode({'system_id':str(system_id)})
    request_started=time.time()
    try:
        with urlopen(url,timeout=30) as r:response=json.load(r)
    except json.JSONDecodeError as exc:
        raise ValueError('Unrecognized camera motion zones; refresh settings before retrying analysis') from exc
    except (HTTPError,URLError,TimeoutError,OSError) as exc:
        # A removed/unsupported camera is not a temporary outage. Never hide
        # format errors or an unknown camera behind a former configuration.
        transient=not isinstance(exc,HTTPError) or exc.code in (429,500,502,503,504)
        if isinstance(exc,HTTPError) and transient:
            try:detail=str(json.loads(exc.read(8192)).get('detail','')).lower()
            except (OSError,ValueError,AttributeError):detail=''
            if any(word in detail for word in ('unrecognized','unsupported','incomplete','format')):transient=False
        if transient:
            try:policy=cached_zones(db_path,device_id,system_id,time.time())
            except (OSError,ValueError,KeyError,TypeError):pass
            else:
                logging.warning('Using last verified motion zones for camera %s/system %s (age %.0fs; refresh unavailable)',
                                device_id,system_id,policy.settings_age_seconds)
                return policy
        raise ValueError('Camera motion zones unavailable; retry analysis after camera settings reconnect') from exc
    try:policy=MotionZones(response)
    except (ValueError,TypeError,AttributeError) as exc:
        raise ValueError('Unrecognized camera motion zones; refresh settings before retrying analysis') from exc
    policy.settings_source='live';policy.settings_age_seconds=0
    try:remember_zones(db_path,device_id,system_id,response,request_started)
    except OSError:
        logging.warning('Motion-zone cache could not be saved for camera %s/system %s',device_id,system_id)
    return policy


def load_camera_zones(db_path, catalog_id, controller_url):
    # Mapping is read-only: do not run catalog schema initialization/migrations
    # just to resolve a camera while another clip is publishing.
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as c:
        row=c.execute('''SELECT d.blink_device_id,s.blink_system_id FROM clips c
            JOIN devices d ON d.id=c.device_id JOIN systems s ON s.id=d.system_id
            WHERE c.id=?''',(catalog_id,)).fetchone()
    if row is None or not row[0] or not row[1]:
        raise ValueError('Camera motion zones unavailable: clip has no camera/system mapping')
    return read_camera_zones(db_path,row[0],row[1],controller_url)
