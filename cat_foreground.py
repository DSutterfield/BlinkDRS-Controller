"""Background veto for supplemental Cat proposals; never assigns identities."""
import hashlib
import json
from pathlib import Path
import numpy as np
from identity_analysis import normalized

BACKGROUND_FILE='cat-background-v1.json'
BACKGROUND_SHA256='9e1bcd5ca9610404f99ca51760fd32bc3438f2da4705171a85ae93422026664f'
_CACHE={}


def load_backgrounds(folder, model):
    path=Path(folder)/BACKGROUND_FILE
    if not path.is_file():return []
    if hashlib.sha256(path.read_bytes()).hexdigest()!=BACKGROUND_SHA256:
        raise ValueError('Cat background model fingerprint mismatch')
    data=json.loads(path.read_text())
    if data['model']!=model:return []
    vectors=data['vectors']
    if len(vectors)<4 or any(len(v)!=384 for v in vectors):return []
    return vectors


def foreground_supported(references, negatives, vector):
    if vector is None or len(negatives)<4:return False
    query=normalized(vector)
    if len(query)!=384:return False
    positives=[r for r in references if r['subject_type']=='Cat' and r['quality_score']>=.2]
    clips={r['catalog_id'] for r in positives}
    if len(clips)<2:return False
    counts={clip:sum(r['catalog_id']==clip for r in positives) for clip in clips}
    x=np.array([normalized(r['vector']) for r in positives]+negatives)
    if x.shape[1]!=384 or not np.isfinite(x).all():return False
    key=hashlib.sha256(x.tobytes()+repr([r['catalog_id'] for r in positives]).encode()).hexdigest()
    if key not in _CACHE:
        weights=np.array([1/(len(clips)*counts[r['catalog_id']]) for r in positives]
                         +[1/len(negatives)]*len(negatives))
        weights*=len(weights)/weights.sum()
        y=np.array([1.]*len(positives)+[0.]*len(negatives))
        center=np.average(x,axis=0,weights=weights)
        mean=np.average(y,weights=weights)
        a=(x-center)*np.sqrt(weights[:,None]);b=(y-mean)*np.sqrt(weights)
        coefficient=a.T@np.linalg.solve(a@a.T+np.eye(len(a)),b)
        if len(_CACHE)>=4:_CACHE.clear()
        _CACHE[key]=(center,mean,coefficient)
    center,mean,coefficient=_CACHE[key]
    # This is a ranking score, not a calibrated probability. It is a veto in
    # addition to, never instead of, the independent Cat-Type evidence gate.
    return float((np.array(query)-center)@coefficient+mean)>=.5
