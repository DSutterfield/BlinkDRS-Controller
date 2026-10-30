"""Offline cat review proposal experiment. No motion or cross-track sharing."""
from cat_review_policy import choose as current_choose

def overwhelming(v):
    if v is None or v.get('veto') or v.get('qualified') is None:return False
    top,runner=v['ranks'][:2]
    return (top['score']>=.8 and top['score']-runner['score']>=.3
            and top['nearest']>=.65 and len(v['sources'].get(top['identity_id'],set()))>=3)

def choose(canonical,views):
    """Preserve one qualifying anchor against weak votes; review stays mandatory."""
    if canonical is None:return None
    old=current_choose(canonical,views)
    considered=views or [canonical]
    if sum(bool(v['veto']) for v in considered)*2>=len(considered):return None
    qualified={v['qualified'] for v in [canonical]+list(views) if v.get('qualified') is not None and not v['veto']}
    anchor=canonical.get('qualified') if not canonical['veto'] else None
    # The user asked to preserve Possible as well as strict qualifying names.
    # Reuse the existing single-crop rule; do not lower any of its thresholds.
    single=current_choose(canonical,[])
    if anchor is None and single is not None:anchor=single[0]
    if old and old[1]=='probable' and old[0]!=anchor and canonical.get('qualified') is None:
        return old  # A borderline anchor does not replace an existing Probable.
    if anchor is None:
        if len(qualified)!=1:return old
        anchor=next(iter(qualified))
    opposing={iid for iid in qualified if iid!=anchor}
    for iid in opposing:
        count=sum(v.get('qualified')==iid and not v['veto'] for v in views)
        if count>=2 or any(v.get('qualified')==iid and overwhelming(v) for v in [canonical]+list(views)):return None
    support=sum(v.get('qualified')==anchor and not v['veto'] for v in views)
    tier='probable' if support>=2 or (old and old==(anchor,'probable')) else 'possible'
    return anchor,tier
