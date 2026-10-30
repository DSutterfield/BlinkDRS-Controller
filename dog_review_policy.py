"""Review-only dog pilot: sole qualifying match survives weak votes."""
from cat_review_policy import choose as previous_choose

def choose(canonical, views):
    evidence=[canonical]+list(views)
    qualified={v['qualified'] for v in evidence if v.get('qualified') is not None}
    if len(qualified)>1:return None
    considered=views or [canonical]
    if sum(bool(v['veto']) for v in considered)*2>=len(considered):return None
    if qualified:return next(iter(qualified)),'probable'
    return previous_choose(canonical,views)
