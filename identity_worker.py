"""Low-priority local AI supervisor, separate from the DVR/API process."""
import argparse
import logging
import math
import os
import time
from pathlib import Path
from identity_analysis import (claim_job, enable_new_clip_queue, finish_job,
                              publish_detections, set_worker_status, pending_type_corrections, rebuild_reference,
                              WorkerHeartbeat)
from identity_store import connect
from identity_models import file_hash


def archive_video(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != '.mp4' or not path.is_file():
        raise ValueError('Video must be an existing MP4 inside the archive.')
    return path


def overlap(a, b):
    intersection = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union > 0 else 0


def track_candidate(track, detection, seconds, sample_period):
    animal = track['subject_type'] in ('Cat','Dog') and detection['subject_type'] in ('Cat','Dog')
    if track['subject_type'] != detection['subject_type'] and not animal:
        return False
    elapsed = seconds-track['last_seen_seconds']
    if elapsed < 0:
        return False
    if elapsed <= sample_period*1.5:
        return True
    # Four fine-search anchors can be several seconds apart. Bridge only a
    # nearly unchanged animal box; do not extend Person tracking or moving boxes.
    return animal and elapsed <= min(12.,sample_period*6.) and overlap(track['box'],detection['box']) >= .75


def retain_view(track, seconds, jpeg, vector, version, quality, type_result=None):
    """Bounded complementary views; not additional confirmed training labels."""
    if vector is None or quality < .2 or not version:
        return
    from identity_analysis import normalized
    current = normalized(vector)
    views = track.setdefault('views', [])
    priority = lambda v: (v.get('type_result') in ('Cat','Dog'), v['quality_score'])
    incoming = dict(quality_score=quality,type_result=type_result)
    same = [v for v in views if v['embedding_model'] == version]
    if any(abs(seconds-v['seconds']) < .5 for v in same):
        return
    near = [v for v in same if abs(seconds-v['seconds']) < 2.0 and len(v['embedding']) == len(current)
            and sum(a*b for a,b in zip(normalized(v['embedding']),current)) >= .98]
    near = [v for v in near if not (type_result in ('Cat','Dog') and v.get('type_result') in ('Cat','Dog') and type_result != v['type_result'])]
    if near:
        old = max(near,key=priority)
        if priority(incoming) <= priority(old):
            return
        views.remove(old)
    view = dict(seconds=seconds,crop_jpeg=jpeg,embedding=current,
                embedding_model=version,quality_score=quality,type_result=type_result)
    if len(views) >= 4:
        replaceable = [v for v in views if v.get('type_result') not in ('Cat','Dog')
                       or v.get('type_result') == type_result
                       or sum(other.get('type_result') == v.get('type_result') for other in views) > 1]
        worst = min(replaceable,key=priority)
        new_species = type_result in ('Cat','Dog') and not any(v.get('type_result') == type_result for v in views)
        if priority(incoming) <= priority(worst) and not new_species:
            return
        views.remove(worst)
    views.append(view)
    views.sort(key=lambda v:v['seconds'])


def sampled_frame_indices(frames, max_frames=16):
    """Keep regular coverage and inspect two views of the previously skipped tail."""
    step = max(1, int(math.ceil(frames/max_frames)))
    indices = list(range(0, int(frames), step))
    last = int(frames)-1
    if indices[-1] != last:
        middle = (indices[-1]+last)//2
        if middle > indices[-1]:
            indices.append(middle)
        indices.append(last)
    return indices, step


def analyze_video(path, models, max_frames=16, checkpoint=None):
    cv2 = models.cv2
    capture = cv2.VideoCapture(str(path))
    tracks = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or not math.isfinite(frames) or frames < 1:
            raise ValueError('Video duration/frame rate could not be read.')
        frame_indices, step = sampled_frame_indices(frames, max_frames)
        sampled = 0
        for frame_index in frame_indices:
            if checkpoint:
                checkpoint()
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                raise ValueError('A sampled video frame could not be decoded.')
            sampled += 1
            seconds = frame_index/fps
            used = set()
            from subject_type_check import in_person_region
            detections = sorted(models.detect(frame), key=lambda item: item['subject_type'] != 'Person')
            for detection in detections:
                height,width = frame.shape[:2]
                box = detection['box']
                x1,y1,x2,y2 = max(0,int(box[0])),max(0,int(box[1])),min(width,int(box[2])),min(height,int(box[3]))
                if x2-x1 < 16 or y2-y1 < 16:
                    continue
                box = [x1,y1,x2,y2]
                crop = frame[y1:y2,x1:x2]
                if detection['subject_type'] == 'Vehicle' and not models.accepts_vehicle(crop):
                    continue
                vector, version, quality = models.embedding(crop, detection['subject_type'])
                type_vector, type_version, type_quality = vector, version, quality
                if detection['subject_type'] in ('Cat','Dog'):
                    type_features = getattr(models, 'animal_type_embedding', None)
                    if type_features is not None:
                        if vector is None or version != models.pet_version:
                            type_vector, type_version, type_quality = type_features(crop)
                type_box = box
                choose_context = getattr(models, 'choose_animal_type_features', None)
                if detection['subject_type'] in ('Cat','Dog') and choose_context is not None:
                    type_vector,type_version,type_quality,type_box = choose_context(
                        frame,box,detection['subject_type'],type_vector,type_version,type_quality,
                        person_region=in_person_region(dict(detection,box=box),detections))
                frame_type = detection['subject_type']
                resolve_type = getattr(models, 'resolve_animal_type', None)
                if detection['subject_type'] in ('Cat','Dog') and resolve_type is not None:
                    kind = resolve_type(detection['subject_type'], type_vector, type_version,
                                        person_region=in_person_region(dict(detection, box=box), detections))
                    if kind is None:
                        continue
                    frame_type = kind
                    # An uncertain animal frame still belongs to its spatial track.
                    # Final species evidence must include its clearer neighboring
                    # views instead of splitting Dog/Unknown into separate tracks.
                    if kind == 'Unknown' and getattr(models, 'resolve_track_type', None) is not None:
                        kind = detection['subject_type']
                    detection = dict(detection, subject_type=kind)
                # Keep source pixels for embeddings; store a bounded JPEG for review.
                review_crop=crop;review_box=box;review_rank=None
                if (detection['subject_type']=='Person' and detection.get('detail_person_candidate')
                        and detection.get('person_model_consensus') and vector is None and quality>=.2):
                    from person_review_crop import review_region
                    review_box,review_rank=review_region(frame.shape,box,detection['confidence'])
                    ra,rb,rc,rd=review_box;review_crop=frame[rb:rd,ra:rc]
                scale = min(1., 256/max(review_crop.shape[:2]))
                preview = cv2.resize(review_crop, (max(1,int(review_crop.shape[1]*scale)),max(1,int(review_crop.shape[0]*scale))))
                encoded_ok, jpeg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY,85])
                if not encoded_ok:
                    raise ValueError('Could not encode a subject image.')
                type_jpeg = jpeg.tobytes()
                if detection['subject_type'] in ('Cat','Dog') and type_box != box:
                    tx1,ty1,tx2,ty2=type_box
                    type_crop=frame[ty1:ty2,tx1:tx2]
                    ts=min(1.,256/max(type_crop.shape[:2]))
                    type_preview=cv2.resize(type_crop,(max(1,int(type_crop.shape[1]*ts)),max(1,int(type_crop.shape[0]*ts))))
                    type_ok,type_encoded=cv2.imencode('.jpg',type_preview,[cv2.IMWRITE_JPEG_QUALITY,85])
                    if not type_ok:
                        raise ValueError('Could not encode animal Type context.')
                    type_jpeg=type_encoded.tobytes()
                candidates = [(overlap(track['box'],box),index) for index,track in enumerate(tracks)
                              if index not in used and track_candidate(track,detection,seconds,step/fps)]
                if detection.get('detail_temporal_candidate'):
                    from animal_type_temporal import temporal_animal_match
                    eligible = [index for index,track in enumerate(tracks)
                                if index not in used and temporal_animal_match(
                                    track,detection,box,seconds,step/fps,vector,
                                    type_vector,type_version,type_quality,models)]
                    # An uncertain new view cannot start a track or choose
                    # between nearby animals. Individual-ID tracks are excluded.
                    if len(eligible) != 1:
                        continue
                    candidates = [(1.,eligible[0])]
                best = max(candidates,default=(0,-1))
                if best[0] >= .3:
                    index = best[1]
                    track = tracks[index]
                    track['sample_hits'] = track.get('sample_hits', 1) + 1
                    track['last_seen_seconds'] = seconds
                    track['box'] = box
                    retain_view(track,seconds,jpeg.tobytes(),vector,version,quality,type_result=frame_type)
                    if detection['subject_type'] in ('Cat','Dog'):
                        holder = {'views': track.setdefault('type_views', [])}
                        retain_view(holder,seconds,type_jpeg,type_vector,type_version,type_quality,type_result=frame_type)
                        if type_quality > track.get('type_quality_score', -1):
                            track.update(type_embedding=type_vector,type_embedding_model=type_version,type_quality_score=type_quality,type_crop_box=type_box,type_crop_jpeg=type_jpeg,type_crop_seconds=seconds)
                    keep_best_crop = (track.get('embedding') is not None and vector is None) or (bool(vector) == bool(track.get('embedding')) and quality <= track['quality_score'])
                    if (review_rank is not None and track.get('person_review_rank') is not None
                            and track.get('embedding') is None and vector is None
                            and quality>=track['quality_score']-.05
                            and review_rank>track['person_review_rank']):
                        keep_best_crop=False
                else:
                    keep_best_crop = False
                    if len(tracks) >= 100:
                        continue
                    index = len(tracks)
                    track = {'key': f"{frame_index}:{detection['subject_type']}:{box}",
                             'first_seen_seconds': seconds,'last_seen_seconds':seconds}
                    tracks.append(track)
                    retain_view(track,seconds,jpeg.tobytes(),vector,version,quality,type_result=frame_type)
                    if detection['subject_type'] in ('Cat','Dog'):
                        holder = {'views': track.setdefault('type_views', [])}
                        retain_view(holder,seconds,type_jpeg,type_vector,type_version,type_quality,type_result=frame_type)
                        if type_quality > track.get('type_quality_score', -1):
                            track.update(type_embedding=type_vector,type_embedding_model=type_version,type_quality_score=type_quality,type_crop_box=type_box,type_crop_jpeg=type_jpeg,type_crop_seconds=seconds)
                used.add(index)
                if detection['subject_type']=='Person':
                    track['detail_person_only'] = (track.get('detail_person_only',True)
                        and detection.get('detail_person_candidate',False)
                        and not detection.get('person_model_consensus',False))
                vote_method = getattr(models,'detail_type_vote',None)
                if vote_method is not None:
                    vote = vote_method(detection,type_vector,type_version,type_quality,seconds,type_jpeg)
                    if vote is not None:
                        track.setdefault('detail_type_votes',[]).append(vote)
                cat_vote_method = getattr(models,'cat_type_vote',None)
                if cat_vote_method is not None:
                    vote = cat_vote_method(detection,type_vector,type_version,type_quality,seconds,type_jpeg)
                    if vote is not None:
                        track.setdefault('cat_type_votes',[]).append(vote)
                if keep_best_crop:
                    continue
                track.update(subject_type=detection['subject_type'],confidence=detection['confidence'],
                             crop_jpeg=jpeg.tobytes(),embedding=vector,embedding_model=version,
                             quality_score=quality,detector_model=detection.get('detector_model',models.detector_version),box=box,
                             person_review_rank=review_rank,review_crop_box=review_box,crop_seconds=seconds)
            time.sleep(.05)
        if sampled == 0:
            raise ValueError('No frames could be sampled.')
        finalize_type = getattr(models, 'resolve_track_type', None)
        if finalize_type is not None:
            for track in tracks:
                if track['subject_type'] in ('Cat','Dog'):
                    track['subject_type'] = finalize_type(track)
        return [track for track in tracks if (track['subject_type'] != 'Vehicle' and not track.get('detail_person_only')) or track.get('sample_hits', 1) >= 2]
    finally:
        capture.release()


def process_one(db_path, archive_root, job, models, checkpoint=None):
    started = time.monotonic()
    path = archive_video(archive_root, job['video_path'])
    fingerprint = file_hash(path)
    from subject_type_learning import load_references
    from subject_type_check import prepare_references
    models.type_references = prepare_references(load_references(db_path, models.pet_version, job['catalog_id']))
    from cat_type_variants import load_variants
    models.cat_type_references = prepare_references(load_variants(db_path,models,models.type_references,checkpoint,
                                              teaching_clip=job['catalog_id']))
    from person_scene_search import PersonConsensusModels as AnimalSceneModels
    observations = analyze_video(path, AnimalSceneModels(models, checkpoint, catalog_id=job['catalog_id']), checkpoint=checkpoint)
    if file_hash(path) != fingerprint:
        raise ValueError('Recording changed during analysis; retry once it is stable.')
    publish_detections(db_path,job['catalog_id'],fingerprint,observations)
    logging.info('Analyzed clip %s in %.1f seconds; %s subject appearances',
                 job['catalog_id'], time.monotonic()-started, len(observations))


def wait_for_controller(pause, controller_url, on_pause=None):
    if not pause:
        return
    import json
    from urllib.request import urlopen
    while True:
        try:
            base = controller_url.rstrip('/')
            with urlopen(base+'/api/v1/liveview/status', timeout=3) as response:
                busy = json.load(response)['active']
            with urlopen(base+'/api/v1/liveview/recording', timeout=3) as response:
                busy = busy or json.load(response)['state'] in ('recording', 'stopping', 'saving')
        except Exception:
            busy = True
        if not busy:
            return
        if on_pause:
            on_pause()
        time.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--prepare-models', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--new-clips', action='store_true')
    parser.add_argument('--pause-during-liveview', action='store_true')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--controller-url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    import signal
    def stop_worker(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_worker)
    if args.prepare_models:
        from identity_models import prepare_models
        prepare_models(args.models)
        return
    if not args.db or not args.db.is_file() or not args.archive:
        parser.error('An existing --db and --archive are required.')
    # One supervisor owns recovery and learning, even with two clip processes.
    with open(str(args.db)+'.ai.lock','a+b') as lock:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.nice(10)
        else:
            import msvcrt
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        failed = False
        heartbeat = None
        parallel = None
        try:
            set_worker_status(args.db,'loading','Loading local models')
            heartbeat = WorkerHeartbeat(args.db)
            heartbeat.__enter__()
            from identity_models import LocalModels
            models = LocalModels(args.models)
            with connect(args.db) as conn:
                conn.execute("UPDATE identity_analysis_jobs SET state='failed',message='Worker interrupted; retry analysis' WHERE state='running'")
            if args.new_clips:
                enable_new_clip_queue(args.db)
            def checkpoint():
                wait_for_controller(args.pause_during_liveview, args.controller_url,
                    lambda: set_worker_status(args.db,'paused',
                        'Waiting for Live View/recording to finish or the controller to reconnect'))
            if args.workers > 1:
                from identity_parallel import ParallelClips
                parallel = ParallelClips(args.db, args.archive, args.models,
                                         args.pause_during_liveview, args.controller_url, workers=args.workers)
            submitted_once = False
            while True:
                checkpoint()
                if parallel:
                    parallel.collect()
                    if args.once and submitted_once and not parallel.active:
                        break
                    if len(parallel.active) == args.workers or (args.once and submitted_once):
                        set_worker_status(args.db,'analyzing',parallel.message())
                        time.sleep(1)
                        continue
                set_worker_status(args.db,'ready','Local AI worker ready')
                for correction in pending_type_corrections(args.db):
                    checkpoint()
                    import numpy as np
                    crop = models.cv2.imdecode(np.frombuffer(correction['crop_jpeg'],dtype=np.uint8),models.cv2.IMREAD_COLOR)
                    if crop is None:
                        raise ValueError('A confirmed subject image could not be decoded')
                    vector,version,quality = models.embedding(crop,correction['subject_type'])
                    rebuild_reference(args.db,correction,vector,version,quality)
                from subject_type_learning import learn_pending
                learn_pending(args.db, models, checkpoint)
                from reference_photos import process_photos
                process_photos(args.db, models, checkpoint)
                if parallel:
                    parallel.fill()
                    submitted_once = True
                    if parallel.active:
                        set_worker_status(args.db,'analyzing',parallel.message())
                    elif args.once:
                        break
                    time.sleep(1 if parallel.active else 5)
                    continue
                job = claim_job(args.db)
                if job:
                    set_worker_status(args.db,'analyzing',f"Analyzing clip {job['catalog_id']}")
                    try:
                        process_one(args.db,args.archive,job,models,checkpoint=checkpoint)
                    except Exception as exc:
                        finish_job(args.db,job['catalog_id'],'failed',str(exc))
                if args.once:
                    break
                if not job:
                    time.sleep(5)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            failed = True
            set_worker_status(args.db,'failed',str(exc))
            raise
        finally:
            try:
                if parallel is not None:
                    parallel.close()
            finally:
                if heartbeat is not None:
                    heartbeat.__exit__()
                with connect(args.db) as conn:
                    conn.execute("UPDATE identity_analysis_jobs SET state='failed',message='Worker interrupted; retry analysis',updated_at=CURRENT_TIMESTAMP WHERE state='running'")
                if not failed:
                    set_worker_status(args.db,'stopped','AI worker stopped')


if __name__ == '__main__':
    main()
