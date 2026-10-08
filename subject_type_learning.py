"""Human-confirmed Type references; no detector weight training or self-learning."""
import hashlib
import json
from identity_store import connect
from identity_analysis import normalized


def pending(db_path, model):
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute("""SELECT d.detection_id,d.subject_type,e.crop_jpeg
            FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            LEFT JOIN subject_type_samples t USING(detection_id)
            WHERE d.confirmed=1 AND (t.detection_id IS NULL OR t.subject_type<>d.subject_type
                OR t.model_version<>?) ORDER BY d.detection_id LIMIT 4""", (model,))]


def save_reference(db_path, item, vector, model, quality):
    with connect(db_path) as conn:
        row = conn.execute("""SELECT d.subject_type,d.confirmed,e.crop_jpeg
            FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.detection_id=?""", (item['detection_id'],)).fetchone()
        if not row or not row['confirmed'] or row['subject_type'] != item['subject_type'] or row['crop_jpeg'] != item['crop_jpeg']:
            return  # A later human edit wins over work already in flight.
        conn.execute("""INSERT INTO subject_type_samples VALUES(?,?,?,?,?,?)
            ON CONFLICT(detection_id) DO UPDATE SET subject_type=excluded.subject_type,
            embedding=excluded.embedding,model_version=excluded.model_version,
            quality_score=excluded.quality_score,crop_hash=excluded.crop_hash""",
            (item['detection_id'],item['subject_type'],
             json.dumps(normalized(vector)) if vector is not None and quality >= .2 else None,
             model,quality,hashlib.sha256(item['crop_jpeg']).hexdigest()))


def learn_pending(db_path, models, checkpoint):
    import numpy as np
    for item in pending(db_path, models.pet_version):
        checkpoint()
        crop = models.cv2.imdecode(np.frombuffer(item['crop_jpeg'], dtype=np.uint8), models.cv2.IMREAD_COLOR)
        vector, model, quality = (None, models.pet_version, 0) if crop is None else models.type_embedding(crop)
        # Record unusable images too, so they cannot monopolize the work queue.
        save_reference(db_path, item, vector, models.pet_version, quality)


def load_references(db_path, model, exclude_clip):
    references = []
    with connect(db_path) as conn:
        for kind in ('Person','Cat','Dog','Vehicle'):
            rows = conn.execute("""SELECT t.*,d.catalog_id FROM subject_type_samples t
                JOIN clip_detections d USING(detection_id)
                WHERE d.confirmed=1 AND d.subject_type=t.subject_type AND t.subject_type=?
                AND t.model_version=? AND t.embedding IS NOT NULL AND t.quality_score>=.2
                AND d.catalog_id<>? ORDER BY t.detection_id DESC LIMIT 128""", (kind,model,exclude_clip))
            for row in rows:
                try:
                    item = dict(row)
                    item['vector'] = normalized(json.loads(item['embedding']))
                    references.append(item)
                except (ValueError, TypeError):
                    continue
    return references


def vehicle_supported(references, vector, model):
    """Veto only with two close nonvehicle examples from distinct clips/images.

    Similarity is not a probability. Ambiguous or missing evidence leaves the
    conservative detector decision alone, rather than inventing another Type.
    """
    vector = normalized(vector)
    scores = {}
    vehicle = -1.
    for item in references:
        if item['model_version'] != model or len(item['vector']) != len(vector):
            continue
        score = sum(a*b for a,b in zip(item['vector'],vector))
        if item['subject_type'] == 'Vehicle':
            vehicle = max(vehicle, score)
        elif score >= .92:
            scores.setdefault(item['subject_type'], []).append((score,item['catalog_id'],item['crop_hash']))
    for matches in scores.values():
        for score,clip,image in matches:
            if score-vehicle < .05:
                continue
            if any(other_clip != clip and other_image != image and other_score-vehicle >= .05
                   for other_score,other_clip,other_image in matches):
                return False
    return True
