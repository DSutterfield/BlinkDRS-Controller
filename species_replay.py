"""Isolated species replay; never publishes labels or trains models."""
import argparse,hashlib,json,sqlite3,time,math,os
from pathlib import Path
from datetime import datetime,timezone

KINDS=('Person','Cat','Dog','Vehicle','Unknown')

def write_json(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(path)

def presence_comparison(expected,observations):
    expected=set(expected);observed={o['subject_type'] for o in observations}
    return dict(expected_types=sorted(expected),observed_types=sorted(observed),
                expected_types_observed=sorted(expected & observed),
                expected_types_not_observed=sorted(expected-observed),
                additional_types_to_review=sorted(observed-expected-{'Unknown','Vehicle'}),
                unknown_appearances=sum(o['subject_type']=='Unknown' for o in observations),
                interpretation='Presence checks only. Reviews may be incomplete; extra types and missing types need visual review, not automatic error counts.')

def prepare(snapshot,output,clip_ids,neighbor_seconds=300):
    from identity_store import ensure_identities
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'images').mkdir()
    src=sqlite3.connect('file:'+str(Path(snapshot).resolve())+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
    try:
        clips=[];answers=[];excluded=set(clip_ids);held_hashes=set()
        for cid in clip_ids:
            clip=src.execute('SELECT * FROM clips WHERE id=? AND local_present=1',(cid,)).fetchone()
            if clip is None:raise ValueError(f'Clip {cid} is not locally available')
            labels=[dict(r) for r in src.execute('SELECT d.*,i.name,e.crop_jpeg FROM clip_detections d LEFT JOIN identities i USING(identity_id) LEFT JOIN identity_evidence e USING(detection_id) WHERE d.catalog_id=? AND d.confirmed=1 ORDER BY d.detection_id',(cid,))]
            if not labels:raise ValueError(f'Clip {cid} has no confirmed labels')
            for label in labels:
                jpg=label.pop('crop_jpeg')
                if jpg:
                    digest=hashlib.sha256(jpg).hexdigest();held_hashes.add(digest)
                    path=f"images/answer-{cid}-{label['detection_id']}.jpg";(output/path).write_bytes(jpg)
                    label.update(image=path,crop_sha256=digest)
            excluded.update(r[0] for r in src.execute('SELECT id FROM clips WHERE ABS(julianday(captured_at)-julianday(?))*86400<=?',(clip['captured_at'],neighbor_seconds)))
            clips.append(dict(clip));answers.append(dict(catalog_id=cid,camera=clip['device_name_snapshot'],captured_at=clip['captured_at'],confirmed_labels=labels))
        refs=[]
        for row in src.execute('SELECT t.*,d.catalog_id FROM subject_type_samples t JOIN clip_detections d USING(detection_id) WHERE d.confirmed=1 AND d.subject_type=t.subject_type AND t.embedding IS NOT NULL AND t.quality_score>=.2'):
            item=dict(row)
            if item['catalog_id'] not in excluded and item['crop_hash'] not in held_hashes:refs.append(item)
        catalog=output/'inference.db';dst=sqlite3.connect(catalog);dst.row_factory=sqlite3.Row
        try:
            dst.executescript((Path(__file__).parent/'sql/catalog_schema_v1.sql').read_text(encoding='utf-8'))
            ensure_identities(dst)
            # Clip camera/system keys remain valid without copying any identity labels.
            for table in ('systems','devices'):
                target_cols=[r[1] for r in dst.execute('PRAGMA table_info('+table+')')]
                source_cols={r[1] for r in src.execute('PRAGMA table_info('+table+')')}
                common=[c for c in target_cols if c in source_cols]
                for row in src.execute('SELECT * FROM '+table):
                    dst.execute('INSERT INTO '+table+'('+','.join(common)+') VALUES('+','.join('?' for _ in common)+')',tuple(row[c] for c in common))
            cols=[r[1] for r in dst.execute('PRAGMA table_info(clips)')]
            source_cols={r[1] for r in src.execute('PRAGMA table_info(clips)')};cols=[c for c in cols if c in source_cols]
            for cid in sorted(set(clip_ids)|{r['catalog_id'] for r in refs}):
                row=src.execute('SELECT * FROM clips WHERE id=?',(cid,)).fetchone()
                dst.execute(f"INSERT INTO clips({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",tuple(row[c] for c in cols))
            for r in refs:
                dst.execute('INSERT INTO clip_detections(detection_id,catalog_id,subject_type,confirmed) VALUES(?,?,?,1)',(r['detection_id'],r['catalog_id'],r['subject_type']))
                dst.execute('INSERT INTO subject_type_samples VALUES(?,?,?,?,?,?)',tuple(r[c] for c in ('detection_id','subject_type','embedding','model_version','quality_score','crop_hash')))
            dst.commit()
            assert not dst.execute('PRAGMA foreign_key_check').fetchall()
            assert not dst.execute('SELECT 1 FROM clip_detections WHERE catalog_id IN ('+','.join('?' for _ in clip_ids)+')',clip_ids).fetchone()
        finally:dst.close()
        manifest=dict(clip_ids=clip_ids,excluded_clip_ids=sorted(excluded),neighbor_seconds=neighbor_seconds,
                      duplicate_reference_hashes_excluded=len(held_hashes),reference_samples=len(refs),
                      reference_clips_by_type={kind:len({r['catalog_id'] for r in refs if r['subject_type']==kind}) for kind in ('Person','Cat','Dog','Vehicle')},
                      answers_provisional=True,created_utc=datetime.now(timezone.utc).isoformat(),
                      named_identity_matching=False,live_catalog_written=False)
        write_json(output/'answer-key.json',answers);write_json(output/'manifest.json',manifest)
        # The inference runner receives only these paths/metadata, not the answer key.
        write_json(output/'replay-input.json',[{k:c[k] for k in ('id','video_path','device_name_snapshot','captured_at')} for c in clips])
        return manifest
    finally:src.close()

def replay(output,archive,model_folder,lock_path,controller_url=None):
    import fcntl
    from identity_models import LocalModels,file_hash
    from identity_worker import analyze_video,archive_video
    from subject_type_learning import load_references
    from release_info import VERSION,UPDATED
    output=Path(output);cpu_start=time.process_time();wall_start=time.monotonic()
    with open(lock_path,'a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.nice(10)
        manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
        if any(manifest['reference_clips_by_type'][k]<2 for k in ('Person','Cat','Dog')):raise ValueError('Insufficient independent Type reference clips after holdout')
        sources={n:file_hash(Path(__file__).parent/n) for n in ('species_replay.py','identity_worker.py','identity_models.py','subject_type_check.py','subject_type_learning.py','release_info.py')}
        write_json(output/'progress.json',dict(state='loading',completed=0,total=len(manifest['clip_ids'])))
        models=LocalModels(model_folder);rows=[]
        provenance=dict(controller_version=VERSION,date_updated=UPDATED,source_sha256=sources,detector_model=models.detector_version,type_model=models.pet_version,max_frames=16)
        write_json(output/'provenance.json',provenance)
        def checkpoint():
            if controller_url:
                from urllib.request import urlopen
                while True:
                    try:
                        active=json.load(urlopen(controller_url+'/api/v1/liveview/status',timeout=3))['active']
                        state=json.load(urlopen(controller_url+'/api/v1/liveview/recording',timeout=3))['state']
                        busy=active or state in ('starting','recording','saving','stopping')
                    except Exception:busy=True
                    if not busy:break
                    time.sleep(5)
            # Match the production worker's approximate 75% of one CPU budget.
            delay=(time.process_time()-cpu_start)/.75-(time.monotonic()-wall_start)
            if delay>0:time.sleep(delay)
        for clip in json.loads((output/'replay-input.json').read_text(encoding='utf-8')):
            cid=clip['id'];write_json(output/'progress.json',dict(state='analyzing',clip=cid,completed=len(rows),total=len(manifest['clip_ids'])))
            checkpoint();path=archive_video(archive,clip['video_path']);fingerprint=file_hash(path)
            models.type_references=load_references(output/'inference.db',models.pet_version,cid)
            assert not any(r['catalog_id'] in set(manifest['excluded_clip_ids']) for r in models.type_references)
            tracks=analyze_video(path,models,checkpoint=checkpoint)
            if file_hash(path)!=fingerprint:raise RuntimeError(f'Clip {cid} changed during replay')
            observations=[]
            for i,t in enumerate(tracks,1):
                image=f'images/fresh-{cid}-{i}.jpg';(output/image).write_bytes(t['crop_jpeg'])
                observations.append(dict(subject_type=t['subject_type'],first_seen_seconds=t['first_seen_seconds'],last_seen_seconds=t['last_seen_seconds'],image=image,sample_hits=t.get('sample_hits',1)))
            rows.append(dict(catalog_id=cid,camera=clip['device_name_snapshot'],captured_at=clip['captured_at'],recording_sha256=fingerprint,observations=observations))
            write_json(output/'fresh-results.json',rows)
            print('Replayed',cid,'appearances',len(observations),flush=True)
        assert all(file_hash(Path(__file__).parent/n)==h for n,h in sources.items()),'Source changed during replay'
        write_json(output/'progress.json',dict(state='complete',completed=len(rows),total=len(manifest['clip_ids'])))
    return rows

def compare(output):
    output=Path(output);answers=json.loads((output/'answer-key.json').read_text(encoding='utf-8'))
    fresh=json.loads((output/'fresh-results.json').read_text(encoding='utf-8'));by_id={r['catalog_id']:r for r in answers}
    rows=[dict(catalog_id=r['catalog_id'],**presence_comparison([x['subject_type'] for x in by_id[r['catalog_id']]['confirmed_labels']],r['observations'])) for r in fresh]
    write_json(output/'comparison.json',rows);return rows

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--clips',required=True);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--models',type=Path,required=True)
    parser.add_argument('--lock',type=Path,required=True);parser.add_argument('--controller-url')
    args=parser.parse_args();ids=[int(x) for x in args.clips.split(',')]
    if not ids or len(ids)!=len(set(ids)):parser.error('Choose distinct clip numbers')
    prepare(args.snapshot,args.output,ids)
    replay(args.output,args.archive,args.models,args.lock,args.controller_url)
    print(json.dumps(compare(args.output),indent=2))
