"""Disabled-by-default evidence grouping for review hints only. Never learns or merges crops."""
import os, time, math, hashlib, json, logging
from pathlib import Path
from collections import OrderedDict

PILOT_ENV = 'BLINK_SAVED_VIEW_REVIEW_PILOT'
INFERENCE_ENV = 'BLINK_SAVED_VIEW_REVIEW_INFERENCE'
MAX_GROUP = 8

SETTINGS_PATH = Path(__file__).with_name('.saved-view-review-pilot.json')

def enabled():
    if PILOT_ENV in os.environ: return os.environ[PILOT_ENV] == '1'
    try: return json.loads(SETTINGS_PATH.read_text()).get('enabled') is True
    except (OSError,ValueError,AttributeError): return False

class PilotBudgetExceeded(RuntimeError):
    pass

class CheckpointAborted(Exception):
    def __init__(self, original): self.original=original

def _check(checkpoint):
    if checkpoint:
        try: checkpoint()
        except Exception as exc: raise CheckpointAborted(exc) from exc

class GrayFrames:
    """Bounded frame cache, separate read handle; checkpoint/budget on every access."""
    def __init__(self, path, cv2, checkpoint=None, budget=20.):
        self.cv2, self.checkpoint = cv2, checkpoint
        self.cap = cv2.VideoCapture(str(path))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not self.cap.isOpened() or not math.isfinite(self.fps) or self.fps <= 0 or self.count < 1:
            self.cap.release(); raise ValueError('Invalid pilot recording')
        self.deadline = time.monotonic() + budget
        self.cache, self.next_index = OrderedDict(), 0
        self.previous_request = -1
    def __len__(self):
        return self.count
    def __getitem__(self, index):
        _check(self.checkpoint)
        if time.monotonic() > self.deadline: raise PilotBudgetExceeded('Review-link time budget reached')
        if type(index) is not int or not 0 <= index < self.count: raise IndexError(index)
        previous = self.previous_request
        self.previous_request = index
        if index in self.cache:
            self.cache.move_to_end(index); return self.cache[index]
        # Two 16-frame blocks retain both sides of the short velocity window.
        # Decode each block sequentially instead of seeking for every backward step.
        start = max(0,index-15) if index < previous else index
        stop = index if index < previous else min(self.count-1,index+15)
        if start != self.next_index: self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, start)
        for position in range(start,stop+1):
            _check(self.checkpoint)
            if time.monotonic() > self.deadline: raise PilotBudgetExceeded('Review-link time budget reached')
            ok,pixels = self.cap.read(); self.next_index = position+1
            if not ok: raise ValueError('Could not decode pilot frame')
            self.cache[position] = self.cv2.cvtColor(pixels,self.cv2.COLOR_BGR2GRAY)
            self.cache.move_to_end(position)
            while len(self.cache)>32: self.cache.popitem(last=False)
        result=self.cache[index]; self.cache.move_to_end(index); return result
    def close(self):
        self.cap.release()

def observations(tracks):
    result = []
    for track in tracks:
        views = []
        source = list(track.get('views', []))
        if track.get('review_crop_box') is not None and track.get('crop_seconds') is not None:
            source.append(dict(seconds=track['crop_seconds'],box=track['review_crop_box'],embedding=track.get('embedding'),embedding_model=track.get('embedding_model'),quality_score=track.get('quality_score')))
        for view in source:
            box = view.get('box')
            if not isinstance(box, (list,tuple)) or len(box)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) and x>=0 for x in box): continue
            if box[2]<=box[0] or box[3]<=box[1]: continue
            seconds = view.get('seconds')
            if not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not track['first_seen_seconds']<=seconds<=track['last_seen_seconds']: continue
            views.append(dict(seconds=seconds,box=list(box),vector=view.get('embedding'),model=view.get('embedding_model') or '',quality=view.get('quality_score') or 0))
        result.append(dict(id=track['key'],kind=track['subject_type'],start=track['first_seen_seconds'],end=track['last_seen_seconds'],views=views))
    return result

def annotate(path, tracks, models, checkpoint=None):
    if not enabled() or os.environ.get(INFERENCE_ENV)!='1': return tracks
    # Fail closed: pilot metadata supplied by a previous attempt is never trusted.
    output = [dict(t) for t in tracks]
    for t in output: t.pop('_review_link_keys',None)
    if len({t['key'] for t in output}) != len(output): return output
    reader = None
    try:
        from review_link_core import evaluate
        reader = GrayFrames(path, models.cv2, checkpoint)
        groups, links, refused, diagnostics = evaluate(observations(output), reader, reader.fps, models.cv2)
        for group in groups:
            if 1 < len(group) <= MAX_GROUP:
                keys = sorted(output[i]['key'] for i in group)
                for i in group: output[i]['_review_link_keys'] = keys
        models.__dict__['_review_link_audit'] = dict(review_only=True,groups=[list(g) for g in groups],links=links,refused=refused)
        logging.info('Saved-view review pilot: %s tracks, %s linked groups; review required',len(output),sum(len(g)>1 for g in groups))
        return output
    except CheckpointAborted as exc:
        raise exc.original
    except (PilotBudgetExceeded, ValueError, IndexError) as exc:
        models.__dict__['_review_link_audit'] = dict(review_only=True,skipped=str(exc))
        logging.info('Saved-view review pilot safely skipped: %s',exc)
        return output
    finally:
        if reader is not None: reader.close()

def scoring_item(item, observations):
    """Ephemeral view union; caller must publish ORIGINAL item/views, never this copy."""
    if not enabled() or item.get('confirmed') or item.get('identity_id') is not None: return item
    keys = item.get('_review_link_keys')
    if not isinstance(keys,list) or not 2 <= len(keys) <= MAX_GROUP or item['key'] not in keys or len(set(keys))!=len(keys): return item
    by_key = {t['key']:t for t in observations}
    if any(k not in by_key for k in keys): return item
    members = [by_key[k] for k in keys]
    if item['subject_type'] not in ('Cat','Dog') or any(t['subject_type']!=item['subject_type'] or t.get('_review_link_keys')!=keys or t.get('confirmed') or t.get('identity_id') is not None for t in members): return item
    times = sorted((t['first_seen_seconds'],t['last_seen_seconds']) for t in members)
    if any(a[1]>=b[0] for a,b in zip(times,times[1:])): return item
    views,seen=[],set()
    for member in members:
        candidates=list(member.get('views',[]))
        candidates.append(dict(seconds=member.get('crop_seconds',member['first_seen_seconds']),crop_jpeg=member.get('crop_jpeg'),embedding=member.get('embedding'),embedding_model=member.get('embedding_model'),quality_score=member.get('quality_score')))
        for v in candidates:
            if not v.get('crop_jpeg') or v.get('embedding') is None: continue
            stamp=(v['seconds'],hashlib.sha256(v['crop_jpeg']).digest())
            if stamp in seen: continue
            seen.add(stamp); views.append(dict(v))
    return dict(item,views=sorted(views,key=lambda v:v['seconds']))


def publication_sources(conn,catalog_id,fingerprint,observations):
    """Never borrow a source which publication would preserve or reject."""
    output=[dict(t) for t in observations]
    if not fingerprint:
        for t in output:t.pop('_review_link_keys',None)
        return output
    blocked={r[0] for r in conn.execute('SELECT detection_key FROM identity_rejections WHERE catalog_id=?',(catalog_id,))}
    blocked.update(r[0] for r in conn.execute("""SELECT e.detection_key FROM identity_evidence e JOIN clip_detections d USING(detection_id)
        LEFT JOIN subject_analysis_exclusions x USING(detection_id)
        WHERE e.catalog_id=? AND (d.confirmed=1 OR x.detection_id IS NOT NULL)""",(catalog_id,)))
    unusable={t['key'] for t in output if hashlib.sha256(f"{fingerprint}:{t['key']}".encode()).hexdigest() in blocked}
    groups=[t.get('_review_link_keys',[]) for t in output]
    affected=set().union(*(set(g) for g in groups if unusable.intersection(g))) if groups else set()
    for t in output:
        if t['key'] in affected:t.pop('_review_link_keys',None)
    return output
