"""Sequential low-priority local AI worker, separate from the DVR/API process."""
import argparse
import math
import os
import time
from pathlib import Path
from identity_analysis import (claim_job, enable_new_clip_queue, finish_job,
                              publish_detections, set_worker_status, pending_type_corrections, rebuild_reference)
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


def analyze_video(path, models, max_frames=16, checkpoint=None):
    cv2 = models.cv2
    capture = cv2.VideoCapture(str(path))
    tracks = []
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if not capture.isOpened() or not math.isfinite(fps) or fps <= 0 or not math.isfinite(frames) or frames < 1:
            raise ValueError('Video duration/frame rate could not be read.')
        step = max(1, int(math.ceil(frames/max_frames)))
        sampled = 0
        for frame_index in range(0, int(frames), step):
            if checkpoint:
                checkpoint()
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                raise ValueError('A sampled video frame could not be decoded.')
            sampled += 1
            seconds = frame_index/fps
            used = set()
            for detection in models.detect(frame):
                height,width = frame.shape[:2]
                box = detection['box']
                x1,y1,x2,y2 = max(0,int(box[0])),max(0,int(box[1])),min(width,int(box[2])),min(height,int(box[3]))
                if x2-x1 < 16 or y2-y1 < 16:
                    continue
                box = [x1,y1,x2,y2]
                crop = frame[y1:y2,x1:x2]
                vector, version, quality = models.embedding(crop, detection['subject_type'])
                # Keep source pixels for embeddings; store a bounded JPEG for review.
                scale = min(1., 256/max(crop.shape[:2]))
                preview = cv2.resize(crop, (max(1,int(crop.shape[1]*scale)),max(1,int(crop.shape[0]*scale))))
                encoded_ok, jpeg = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY,85])
                if not encoded_ok:
                    raise ValueError('Could not encode a subject image.')
                candidates = [(overlap(track['box'],box),index) for index,track in enumerate(tracks)
                              if index not in used and track['subject_type']==detection['subject_type']
                              and seconds-track['last_seen_seconds'] <= step/fps*1.5]
                best = max(candidates,default=(0,-1))
                if best[0] >= .3:
                    index = best[1]
                    track = tracks[index]
                    track['last_seen_seconds'] = seconds
                    track['box'] = box
                    if (track.get('embedding') is not None and vector is None) or (bool(vector) == bool(track.get('embedding')) and quality <= track['quality_score']):
                        used.add(index)
                        continue
                else:
                    if len(tracks) >= 100:
                        continue
                    index = len(tracks)
                    track = {'key': f"{frame_index}:{detection['subject_type']}:{box}",
                             'first_seen_seconds': seconds,'last_seen_seconds':seconds}
                    tracks.append(track)
                used.add(index)
                track.update(subject_type=detection['subject_type'],confidence=detection['confidence'],
                             crop_jpeg=jpeg.tobytes(),embedding=vector,embedding_model=version,
                             quality_score=quality,detector_model=models.detector_version,box=box)
            time.sleep(.05)
        if sampled == 0:
            raise ValueError('No frames could be sampled.')
        return tracks
    finally:
        capture.release()


def process_one(db_path, archive_root, job, models, checkpoint=None):
    path = archive_video(archive_root, job['video_path'])
    fingerprint = file_hash(path)
    observations = analyze_video(path, models, checkpoint=checkpoint)
    if file_hash(path) != fingerprint:
        raise ValueError('Recording changed during analysis; retry once it is stable.')
    publish_detections(db_path,job['catalog_id'],fingerprint,observations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--prepare-models', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--new-clips', action='store_true')
    parser.add_argument('--pause-during-liveview', action='store_true')
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
    # Kernel lock releases even after a crash; never run two inference workers.
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
        try:
            set_worker_status(args.db,'loading','Loading local models')
            from identity_models import LocalModels
            models = LocalModels(args.models)
            with connect(args.db) as conn:
                conn.execute("UPDATE identity_analysis_jobs SET state='failed',message='Worker interrupted; retry analysis' WHERE state='running'")
            if args.new_clips:
                enable_new_clip_queue(args.db)
            def checkpoint():
                if not args.pause_during_liveview:
                    return
                import json
                from urllib.request import urlopen
                while True:
                    try:
                        with urlopen(args.controller_url.rstrip('/')+'/api/v1/liveview/status', timeout=3) as response:
                            busy = json.load(response)['active']
                    except Exception:
                        busy = True
                    if not busy:
                        return
                    set_worker_status(args.db,'paused','Waiting for Live View to finish or the controller to reconnect')
                    time.sleep(5)
            while True:
                checkpoint()
                set_worker_status(args.db,'ready','Local AI worker ready')
                for correction in pending_type_corrections(args.db):
                    checkpoint()
                    import numpy as np
                    crop = models.cv2.imdecode(np.frombuffer(correction['crop_jpeg'],dtype=np.uint8),models.cv2.IMREAD_COLOR)
                    if crop is None:
                        raise ValueError('A confirmed subject image could not be decoded')
                    vector,version,quality = models.embedding(crop,correction['subject_type'])
                    rebuild_reference(args.db,correction,vector,version,quality)
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
            with connect(args.db) as conn:
                conn.execute("UPDATE identity_analysis_jobs SET state='failed',message='Worker interrupted; retry analysis',updated_at=CURRENT_TIMESTAMP WHERE state='running'")
            if not failed:
                set_worker_status(args.db,'stopped','AI worker stopped')


if __name__ == '__main__':
    main()
