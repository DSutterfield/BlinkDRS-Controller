"""Fixed, review-only tiers; rank scores are not probabilities."""
from statistics import median as midpoint
def choose(canonical,views):
    qualified={v['qualified'] for v in [canonical]+views if v.get('qualified') is not None}
    if len(qualified)>1:return None  # Strong conflict withdraws every tier.
    if not views:views=[canonical]
    if sum(bool(v['veto']) for v in views)*2>=len(views):return None
    ids={r['identity_id'] for v in views for r in v['ranks']}
    if len(ids)<2:return None
    weights=[min(1.,max(.2,v['quality'])) for v in views]
    scores={i:sum(next((r['score'] for r in v['ranks'] if r['identity_id']==i),0)*w for v,w in zip(views,weights))/sum(weights) for i in ids}
    ordered=sorted(scores,key=lambda i:(-scores[i],i));leader=ordered[0];score=scores[leader];margin=score-scores[ordered[1]]
    useful=[v for v in views if not v['veto']];votes={i:sum(v['ranks'][0]['identity_id']==i for v in useful) for i in ids};support=votes[leader];majority=support>len(useful)/2
    nearest=sorted(next(r['nearest'] for r in v['ranks'] if r['identity_id']==leader) for v in views);median=midpoint(nearest);sources=set().union(*(v['sources'].get(leader,set()) for v in views));tier=None
    if len(views)>=2 and majority and support>=2 and score>=.45 and margin>=.15 and median>=.55 and len(sources)>=2:tier='probable'
    anchor=canonical.get('qualified')
    if tier is None and anchor is not None:
        if len(views)<2:return anchor,'probable'
        if anchor==leader and majority and support>=2 and margin>=.15 and median>=.55:return anchor,'probable'
    # Borderline single-view or small stream majorities remain explicitly Possible.
    if tier is None and len(views)>=2 and majority and support>=2 and score>=.40 and margin>=.05 and median>=.55 and len(sources)>=2:tier='possible'
    if tier is None and len(views)==1 and score>=.50 and margin>=.10 and median>=.55 and len(sources)>=2:tier='possible'
    return (leader,tier) if tier else None
