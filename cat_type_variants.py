"""Cached Type-only variants of current human-confirmed reference images."""
from pathlib import Path
import sqlite3
import json
import hashlib
import numpy as np
from identity_analysis import normalized

REVIEW_MANIFEST_SHA256='5c45ee3eb9292f2620c40eeff1e2f4e02a759bcfe6bacac910c1dccd1741e875'


def load_variants(db_path, models, references, checkpoint=None, cache_path=None, teaching_clip=None, review_manifest=None):
    if not references:return []
    import identity_models
    root=Path(identity_models.__file__).resolve().parent
    cache_path=Path(cache_path or root/'ai-models/cat-type-variants.sqlite')
    cache_path.parent.mkdir(parents=True,exist_ok=True)
    result=list(references);selected=[]
    review_crops={}
    for kind,limit in (('Cat',32),('Dog',16),('Person',16)):
        rows=[r for r in references if r['subject_type']==kind]
        selected+=rows[::max(1,len(rows)//limit)][:limit]
    with sqlite3.connect(cache_path) as cache, sqlite3.connect(
            f'file:{Path(db_path).resolve()}?mode=ro',uri=True) as source:
        cache.execute('CREATE TABLE IF NOT EXISTS variants (key TEXT PRIMARY KEY, vector TEXT, quality REAL)')
        from evaluation_holdout import excluded_ids
        excluded=excluded_ids(source)
        result=[r for r in result if r['catalog_id'] not in excluded]
        selected=[r for r in selected if r['catalog_id'] not in excluded]
        historical=set()
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='evaluation_holdout_clips'").fetchone():
            historical={r[0] for r in source.execute('SELECT catalog_id FROM evaluation_holdout_clips')}
        if review_manifest is not False:
            manifest=Path(review_manifest or root/'ai-models/cat-reviewed-examples-v1.json')
            if manifest.is_file():
                if hashlib.sha256(manifest.read_bytes()).hexdigest()!=REVIEW_MANIFEST_SHA256:
                    raise ValueError('Reviewed Cat example manifest fingerprint mismatch')
                for record in json.loads(manifest.read_text())['examples'][:16]:
                    if record['subject_type']!='Cat':continue
                    if record['catalog_id'] in excluded or record['catalog_id'] in historical:continue
                    path=(manifest.parent/record['image_file']).resolve()
                    if not path.is_relative_to(manifest.parent.resolve()):
                        raise ValueError('Reviewed Cat image must stay in its model directory')
                    jpeg=path.read_bytes();crop_hash=hashlib.sha256(jpeg).hexdigest()
                    if crop_hash!=record['crop_hash']:raise ValueError('Reviewed Cat image fingerprint mismatch')
                    key=hashlib.sha256((crop_hash+models.pet_version+'originalmanualv1').encode()).hexdigest()
                    saved=cache.execute('SELECT vector,quality FROM variants WHERE key=?',(key,)).fetchone()
                    if saved is None:
                        if checkpoint:checkpoint()
                        image=models.cv2.imdecode(np.frombuffer(jpeg,np.uint8),models.cv2.IMREAD_COLOR)
                        if image is None:continue
                        vector,model,quality=models.animal_type_embedding(image)
                        value=json.dumps(normalized(vector)) if vector and quality>=.2 and model==models.pet_version else None
                        cache.execute('INSERT INTO variants VALUES(?,?,?)',(key,value,quality));cache.commit();saved=(value,quality)
                    if saved[0] is not None and saved[1]>=.2:
                        did='review:'+crop_hash[:20]
                        teacher=dict(detection_id=did,catalog_id=record['catalog_id'],subject_type='Cat',
                                     vector=json.loads(saved[0]),model_version=models.pet_version,
                                     quality_score=saved[1],crop_hash=crop_hash,support_excluded=True)
                        review_crops[did]=jpeg;result.append(teacher);selected.append(teacher)
        if teaching_clip is not None and teaching_clip not in excluded:
            # A deliberate human crop can teach Type to other views of that
            # recording. It must never count as independent other-clip support.
            teachers=source.execute('''SELECT d.detection_id,d.catalog_id,e.crop_jpeg
                FROM clip_detections d JOIN identity_evidence e USING(detection_id)
                WHERE d.catalog_id=? AND d.confirmed=1 AND d.subject_type='Cat'
                AND d.model_version='manual-crop:v1'
                ORDER BY d.detection_id DESC LIMIT 4''',(teaching_clip,)).fetchall()
            for did,cid,jpeg in teachers:
                if checkpoint:checkpoint()
                crop_hash=hashlib.sha256(jpeg).hexdigest()
                key=hashlib.sha256((crop_hash+models.pet_version+'originalmanualv1').encode()).hexdigest()
                saved=cache.execute('SELECT vector,quality FROM variants WHERE key=?',(key,)).fetchone()
                if saved is None:
                    image=models.cv2.imdecode(np.frombuffer(jpeg,np.uint8),models.cv2.IMREAD_COLOR)
                    if image is None:continue
                    vector,model,quality=models.animal_type_embedding(image)
                    value=json.dumps(normalized(vector)) if vector and quality>=.2 and model==models.pet_version else None
                    cache.execute('INSERT INTO variants VALUES(?,?,?)',(key,value,quality));cache.commit()
                    saved=(value,quality)
                if saved[0] is not None and saved[1]>=.2:
                    teacher=dict(detection_id=did,catalog_id=cid,subject_type='Cat',
                                 vector=json.loads(saved[0]),model_version=models.pet_version,
                                 quality_score=saved[1],crop_hash=crop_hash,support_excluded=True)
                    result.append(teacher);selected.append(teacher)
        for row in selected:
            jpeg=(review_crops[row['detection_id']],) if row['detection_id'] in review_crops else source.execute('''SELECT e.crop_jpeg FROM clip_detections d
                JOIN identity_evidence e USING(detection_id)
                WHERE d.detection_id=? AND d.confirmed=1 AND d.subject_type=?''',
                (row['detection_id'],row['subject_type'])).fetchone()
            if not jpeg or hashlib.sha256(jpeg[0]).hexdigest()!=row['crop_hash']:continue
            image=None
            for variant in ('gray','wire','rotate'):
                if checkpoint:checkpoint()
                key=hashlib.sha256((row['crop_hash']+models.pet_version+variant+'v1').encode()).hexdigest()
                saved=cache.execute('SELECT vector,quality FROM variants WHERE key=?',(key,)).fetchone()
                if saved is None:
                    if image is None:image=models.cv2.imdecode(np.frombuffer(jpeg[0],np.uint8),models.cv2.IMREAD_COLOR)
                    if image is None:break
                    q=image.copy()
                    if variant in ('gray','wire'):
                        q=models.cv2.cvtColor(models.cv2.cvtColor(q,models.cv2.COLOR_BGR2GRAY),models.cv2.COLOR_GRAY2BGR)
                    if variant=='wire':
                        h,w=q.shape[:2]
                        for x in range(8,w,max(8,w//5)):models.cv2.line(q,(x,0),(x,h-1),(35,35,35),1)
                        for y in range(12,h,max(12,h//5)):models.cv2.line(q,(0,y),(w-1,y),(35,35,35),1)
                    if variant=='rotate':q=models.cv2.rotate(q,models.cv2.ROTATE_90_CLOCKWISE)
                    vector,model,quality=models.animal_type_embedding(q)
                    value=json.dumps(normalized(vector)) if vector and quality>=.2 and model==models.pet_version else None
                    cache.execute('INSERT INTO variants VALUES(?,?,?)',(key,value,quality));cache.commit()
                    saved=(value,quality)
                if saved[0] is not None and saved[1]>=.2:
                    # Variants are the same original image, not independent
                    # reference photographs. Preserve its clip and image key.
                    result.append(dict(row,vector=json.loads(saved[0]),quality_score=min(row['quality_score'],saved[1])))
    return result
