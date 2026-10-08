"""Confirmed-Type crop checks. Ranking scores are not probabilities."""
import hashlib
from collections import Counter
from identity_analysis import normalized

_CACHE = {}
KINDS = ('Person', 'Cat', 'Dog')


def rank_types(references, vector, model):
    if not model or not model.startswith('dinov2-small-cls-v1:') or vector is None:
        return []
    try:
        query = normalized(vector)
    except (ValueError, TypeError):
        return []
    if len(query) != 384:
        return []
    rows = []
    for item in references:
        if item['subject_type'] not in KINDS or item['model_version'] != model:
            continue
        if item.get('quality_score', 0) < .2:
            continue
        try:
            sample = normalized(item['vector'])
        except (ValueError, TypeError):
            continue
        if len(sample) == 384:
            rows.append((item, sample))
    sources = {kind: len({r['catalog_id'] for r, v in rows if r['subject_type'] == kind}) for kind in KINDS}
    if any(count < 2 for count in sources.values()):
        return []
    import numpy as np
    key = hashlib.sha256(repr([(r['subject_type'], r['catalog_id'], r['crop_hash'], v) for r,v in rows]).encode()).hexdigest()
    if key in _CACHE:
        center, means, coefficients = _CACHE[key]
    else:
        counts = Counter((r['subject_type'], r['catalog_id']) for r, v in rows)
        weights = np.array([1/(sources[r['subject_type']]*counts[(r['subject_type'],r['catalog_id'])]) for r,v in rows])
        weights *= len(weights)/weights.sum()
        x = np.array([v for r,v in rows])
        y = np.array([[float(r['subject_type'] == kind) for kind in KINDS] for r,v in rows])
        center = np.average(x, axis=0, weights=weights)
        means = np.average(y, axis=0, weights=weights)
        a = (x-center)*np.sqrt(weights[:,None])
        b = (y-means)*np.sqrt(weights[:,None])
        coefficients = a.T@np.linalg.solve(a@a.T+np.eye(len(a)),b)
        if len(_CACHE) >= 8:
            _CACHE.clear()
        _CACHE[key] = center, means, coefficients
    query = np.array(query)
    scores = (query-center)@coefficients+means
    result = []
    for kind,score in zip(KINDS,scores):
        matches = sorted([(float(query@np.array(v)),r['catalog_id'],r['crop_hash']) for r,v in rows if r['subject_type']==kind],reverse=True)
        distinct = []
        clips,images = set(),set()
        for similarity,clip,image in matches:
            if clip in clips or image in images:
                continue
            distinct.append(similarity);clips.add(clip);images.add(image)
        result.append(dict(subject_type=kind,score=float(score),independent_similarities=distinct[:3]))
    return sorted(result,key=lambda r:r['score'],reverse=True)


def resolve_animal_type(references, claimed_type, vector, model, person_region=False):
    """Reject animal labels contradicted by well-supported confirmed Types.

    Person, Cat and Dog alternatives use the same conservative evidence gate.
    Missing or ambiguous evidence retains the crop as Unknown. Strong cat/dog disagreements correct Type; person-as-animal is suppressed.
    """
    if claimed_type not in ('Cat','Dog'):
        return claimed_type
    ranks = rank_types(references,vector,model)
    if len(ranks) < 3:
        return 'Unknown'
    top = ranks[0]
    support = top['independent_similarities']
    # A strong same-frame Person box is independent evidence. Still require
    # the crop itself to favor Person, so held/nearby pets are retained.
    if (person_region and top['subject_type'] == 'Person'
            and top['score'] >= .55 and top['score']-ranks[1]['score'] >= .1
            and len(support) >= 2 and support[1] >= .45):
        return None
    reject = (top['subject_type'] != claimed_type and top['score'] >= .8
              and top['score']-ranks[1]['score'] >= .3
              and len(support) >= 2 and support[1] >= .5)
    if reject:
        return None if top['subject_type'] == 'Person' else top['subject_type']
    if (top['subject_type'] == claimed_type and top['score'] >= .65
            and top['score']-ranks[1]['score'] >= .2
            and len(support) >= 2 and support[1] >= .45):
        return claimed_type
    return 'Unknown'



def animal_type_supported(references, claimed_type, vector, model):
    return resolve_animal_type(references, claimed_type, vector, model) == claimed_type


def in_person_region(animal, detections):
    """Require >=90% of the animal box inside a strong same-frame Person box."""
    a = animal['box']
    area = max(0, a[2]-a[0])*max(0, a[3]-a[1])
    if area <= 0:
        return False
    for item in detections:
        if item['subject_type'] != 'Person' or item['confidence'] < .7:
            continue
        b = item['box']
        intersection = max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        if intersection/area >= .9:
            return True
    return False


def resolve_track_type(references, track):
    """Accept an animal with strong single-view or repeated species evidence."""
    import math
    kind = track['subject_type']
    if kind not in ('Cat', 'Dog'):
        return kind
    if track.get('quality_score', 0) < .2:
        return 'Unknown'
    model = track.get('embedding_model')
    ranks = rank_types(references, track.get('embedding'), model)
    strong_single = False
    if len(ranks) >= 3:
        top = ranks[0]
        support = top['independent_similarities']
        if (top['subject_type'] == kind and top['score'] >= .75
                and top['score']-ranks[1]['score'] >= .3
                and len(support) >= 2 and support[1] >= .5):
            strong_single = True
    primary = resolve_animal_type(references, kind, track.get('embedding'), model)
    if primary is None or primary in ('Cat','Dog') and primary != kind:
        return 'Unknown'
    accepted = []
    for view in track.get('views', []):
        if view.get('embedding_model') != model or view.get('quality_score', 0) < .2:
            continue
        try:
            seconds = float(view['seconds'])
        except (ValueError, TypeError, KeyError):
            continue
        jpeg = view.get('crop_jpeg')
        if not math.isfinite(seconds) or not jpeg:
            continue
        digest = hashlib.sha256(jpeg).digest()
        if any(abs(seconds-old_time) < .5 or digest == old_hash for old_time, old_hash in accepted):
            continue
        resolved = resolve_animal_type(references, kind, view.get('embedding'), model)
        if resolved is None or resolved in ('Cat','Dog') and resolved != kind:
            return 'Unknown'
        if resolved == kind:
            accepted.append((seconds,digest))
    return kind if strong_single or len(accepted) >= 2 else 'Unknown'
