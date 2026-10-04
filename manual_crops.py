"""Human-selected image evidence from an unchanged archived recording."""
import json
import math
import subprocess
import uuid
from pathlib import Path
from identity_store import connect, require_clip
from identity_worker import archive_video


def clip_frame(db_path, archive, catalog_id, seconds, box=None):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Choose a valid frame time.')
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        relative = conn.execute('SELECT video_path FROM clips WHERE id=?', (catalog_id,)).fetchone()[0]
    path = archive_video(Path(archive), relative)
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(path)],
            capture_output=True, check=True, timeout=10)
        info = json.loads(probe.stdout)
        width, height = (int(info['streams'][0][k]) for k in ('width','height'))
        duration = float(info['format']['duration'])
        if not math.isfinite(duration) or seconds >= duration or width*height > 16_000_000:
            raise ValueError('Frame time or recording dimensions are out of range.')
        command = ['ffmpeg','-v','error','-threads','1','-noautorotate','-ss',str(seconds),'-i',str(path),'-frames:v','1']
        if box is not None:
            x,y,w,h = box
            if any(type(v) is not int for v in box) or x<0 or y<0 or w<16 or h<16 or x+w>width or y+h>height:
                raise ValueError('Draw a rectangle inside the frame, at least 16 pixels wide and high.')
            command += ['-vf',f'crop={w}:{h}:{x}:{y}:exact=1']
        result = subprocess.run(command+['-f','image2pipe','-vcodec','mjpeg','-q:v','2','pipe:1'],
                                capture_output=True, check=True, timeout=15)
        if not result.stdout or len(result.stdout)>8_000_000:
            raise ValueError('Frame image could not be produced.')
        return result.stdout
    except (subprocess.SubprocessError, OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ValueError('Unable to read this recording frame. Choose another frame or retry.') from exc


def save_crop(db_path, archive, catalog_id, seconds, box, identity_id, subject_type):
    if subject_type not in ('Person','Cat','Dog'):
        raise ValueError('Choose Person, Cat, or Dog.')
    jpeg = clip_frame(db_path, archive, catalog_id, seconds, box)
    with connect(db_path) as conn:
        require_clip(conn, catalog_id)
        if identity_id is not None and not conn.execute(
                'SELECT 1 FROM identities WHERE identity_id=? AND active=1 AND subject_type=?',
                (identity_id,subject_type)).fetchone():
            raise ValueError('Choose an identity of the same type.')
        conn.execute('DELETE FROM clip_unknown_motion WHERE catalog_id=?',(catalog_id,))
        did = conn.execute('INSERT INTO clip_detections(catalog_id,identity_id,subject_type,first_seen_seconds,last_seen_seconds,confirmed,confirmed_at,model_version) VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP,?)',
                           (catalog_id,identity_id,subject_type,seconds,seconds,'manual-crop:v1')).lastrowid
        conn.execute('INSERT INTO identity_evidence VALUES(?,?,?,?,?,?,?,?,?)',
                     (did,catalog_id,'manual:'+uuid.uuid4().hex,subject_type,jpeg,None,'manual-pending',0,None))
        # Whole-clip labels for the selected name are redundant after explicit image confirmation.
        if identity_id is not None:
            conn.execute('DELETE FROM identity_samples WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id=? AND identity_id=? AND first_seen_seconds IS NULL)', (catalog_id,identity_id))
            conn.execute('DELETE FROM clip_detections WHERE catalog_id=? AND identity_id=? AND first_seen_seconds IS NULL',(catalog_id,identity_id))
    from identity_store import get_clip_identities
    return get_clip_identities(db_path,catalog_id)
