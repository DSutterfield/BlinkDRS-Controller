"""Conservative, unconfirmed dog suggestions from the household's confirmed references."""
import json
import math

from identity_analysis import normalized
from identity_candidates import rank_candidates


def _vector(view, model):
    if view.get('embedding_model') != model or view.get('quality_score', 0) < .2:
        return None
    try:
        vector = normalized(view.get('embedding'))
    except (TypeError, ValueError):
        return None
    return vector if len(vector) == 384 else None


def _supporting_clips(conn, identity_id, model, vector, catalog_id, threshold):
    sources = set()
    for row in conn.execute('''SELECT d.catalog_id,s.embedding FROM identity_samples s
            JOIN clip_detections d USING(detection_id) JOIN identities i USING(identity_id)
            WHERE s.identity_id=? AND i.active=1 AND i.subject_type='Dog'
            AND d.confirmed=1 AND d.subject_type='Dog' AND d.identity_id=s.identity_id
            AND d.catalog_id<>? AND s.model_version=? AND s.quality_score>=.2''',
            (identity_id, catalog_id, model)):
        try:
            sample = normalized(json.loads(row['embedding']))
        except (TypeError, ValueError):
            continue
        if len(sample) == len(vector) and sum(a*b for a,b in zip(sample,vector)) >= threshold:
            sources.add(row['catalog_id'])
    return len(sources)


def _unknown_veto(conn, model, vector, catalog_id, nearest):
    # Explicit human-confirmed unknowns are negative examples, never named training data.
    for row in conn.execute('''SELECT e.embedding FROM clip_detections d
            JOIN identity_evidence e USING(detection_id)
            WHERE d.subject_type='Dog' AND e.subject_type='Dog' AND d.confirmed=1
            AND d.identity_id IS NULL AND d.catalog_id<>? AND e.embedding_model=?
            AND e.quality_score>=.2 AND e.embedding IS NOT NULL''', (catalog_id,model)):
        try:
            sample = normalized(json.loads(row['embedding']))
        except (TypeError,ValueError):
            continue
        if len(sample) == len(vector):
            similarity = sum(a*b for a,b in zip(sample,vector))
            if similarity >= .65 and similarity >= nearest - .03:
                return True
    return False


def _rank(conn, model, vector, catalog_id):
    ranks = rank_candidates(conn,'Dog',model,vector,catalog_id)
    if len(ranks) < 2:
        return None
    top, runner = ranks[:2]
    if top['score'] < .8 or top['score'] - runner['score'] < .3 or top['nearest'] < .65:
        return None
    if top['reference_clips'] < 2 or _unknown_veto(conn,model,vector,catalog_id,top['nearest']):
        return None
    return top, runner


def recommend_identity(conn, item, catalog_id):
    if item['subject_type'] != 'Dog':
        return None
    model = item.get('embedding_model')
    if not model or not model.startswith('dinov2-small-cls-v1:'):
        return None
    canonical = _vector(item,model)
    canonical_rank = _rank(conn,model,canonical,catalog_id) if canonical is not None else None
    supported = []
    for view in item.get('views',[])[:4]:
        vector = _vector(view,model)
        seconds = view.get('seconds')
        if vector is None or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds < 0:
            continue
        rank = _rank(conn,model,vector,catalog_id)
        if rank and _supporting_clips(conn,rank[0]['identity_id'],model,vector,catalog_id,.60) >= 2:
            supported.append((rank[0]['identity_id'],seconds))
    identities = {iid for iid,seconds in supported}
    if canonical_rank:
        identities.add(canonical_rank[0]['identity_id'])
    if len(identities) > 1:
        return None  # Crossing subjects or inconsistent evidence must stay unnamed.
    if len(supported) >= 2 and max(s for _,s in supported) - min(s for _,s in supported) >= .5:
        return supported[0][0]
    if canonical_rank:
        top, runner = canonical_rank
        if (top['score'] >= .8 and top['score'] - runner['score'] >= .4 and top['nearest'] >= .65
                and _supporting_clips(conn,top['identity_id'],model,canonical,catalog_id,.60) >= 3):
            return top['identity_id']
    return None
