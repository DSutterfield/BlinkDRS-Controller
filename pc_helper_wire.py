"""Bounded JSON evidence envelope. No executable/pickle input crosses the bridge."""
import base64,hashlib,json,math
MAX_PACKET=32*1024*1024
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(data):return hashlib.sha256(data).hexdigest()
def number(value,low=0,high=86400):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid numeric evidence')
    return value
def text(value,limit=512):
    if not isinstance(value,str) or not value or len(value)>limit:raise ValueError('Invalid evidence identifier')
    return value
def vector(value):
    if value is None:return None
    if not isinstance(value,list) or not 1<=len(value)<=4096:raise ValueError('Invalid vector dimensions')
    for v in value:number(v,-100,100)
    if sum(v*v for v in value)<1e-12:raise ValueError('Empty vector')
    return value
def jpeg(value):
    text(value,12*1024*1024);data=base64.b64decode(value,validate=True)
    if len(data)>8*1024*1024 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):raise ValueError('Invalid JPEG evidence')
    return data
def decode(data):
    if len(data)>MAX_PACKET:raise ValueError('Evidence packet too large')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    packet=json.loads(data,object_pairs_hook=unique,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(packet,dict) or type(packet.get('schema')) is not int or packet.get('schema')!=1:raise ValueError('Unsupported evidence schema')
    for field in ['token','manifest_sha256','video_sha256','contract']:text(packet.get(field))
    rows=packet.get('observations')
    if not isinstance(rows,list) or len(rows)>256:raise ValueError('Too many observations')
    observations=[];keys=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid observation')
        key=text(row.get('key'));kind=row.get('subject_type')
        if key in keys or kind not in ['Cat','Dog','Person','Vehicle','Unknown']:raise ValueError('Duplicate/invalid observation')
        keys.add(key);first=number(row.get('first_seen_seconds'));last=number(row.get('last_seen_seconds'))
        if last<first:raise ValueError('Reversed observation time')
        item=dict(key=key,subject_type=kind,confidence=number(row.get('confidence'),0,1),first_seen_seconds=first,last_seen_seconds=last,detector_model=text(row.get('detector_model')),crop_jpeg=jpeg(row.get('crop_jpeg')),embedding=vector(row.get('embedding')),quality_score=number(row.get('quality_score'),0,1),views=[])
        item['embedding_model']=text(row.get('embedding_model')) if item['embedding'] is not None else None
        views=row.get('views',[])
        if not isinstance(views,list) or len(views)>4:raise ValueError('Too many evidence views')
        for view in views:
            item['views'].append(dict(seconds=number(view.get('seconds')),crop_jpeg=jpeg(view.get('crop_jpeg')),embedding=vector(view.get('embedding')),embedding_model=text(view.get('embedding_model')),quality_score=number(view.get('quality_score'),0,1)))
            if item['views'][-1]['embedding'] is None:raise ValueError('Missing view vector')
        observations.append(item)
    return packet,observations
def encode(manifest,observations):
    def image(data):return base64.b64encode(data).decode('ascii')
    rows=[]
    for row in observations:
        item={k:row.get(k) for k in ['key','subject_type','confidence','first_seen_seconds','last_seen_seconds','detector_model','embedding','embedding_model','quality_score']};item['crop_jpeg']=image(row['crop_jpeg']);item['views']=[dict(seconds=v['seconds'],crop_jpeg=image(v['crop_jpeg']),embedding=v['embedding'],embedding_model=v['embedding_model'],quality_score=v['quality_score']) for v in row.get('views',[])[:4]];rows.append(item)
    data=canonical(dict(schema=1,token=manifest['token'],manifest_sha256=sha(canonical(manifest)),video_sha256=manifest['video_sha256'],contract=manifest['contract'],observations=rows));decode(data);return data
