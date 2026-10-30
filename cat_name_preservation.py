"""Review-only Cat preservation using existing per-track prepared evidence.

This adapter owns no database connection or learning. It preserves the tested
ordinary-result fallback, adds guarded Possible hints, and never pools tracks.
"""
from collections import Counter
from cat_name_preservation_policy import choose,overwhelming

def baseline(prepared,item):
    result=prepared(item)
    if result:return result
    kind=item.get('subject_type')
    hint=prepared.hint(item,kind) if kind in ('Cat','Dog') else None
    if not hint:return None
    return dict(suggested_identity_id=hint['identity_id'],suggested_name=hint['name'],suggested_subject_type=kind,
                suggestion_tier=hint['tier'],review_required=True,confirmed=False)

def recommend(prepared,item):
    if prepared is None:return None
    if item.get('confirmed') or item.get('analysis_excluded'):return None
    before=baseline(prepared,item);kind=item.get('subject_type')
    if kind not in ('Cat','Unknown'):return before
    anchor=prepared.evidence(item,'Cat',True)
    views=prepared.separated(item)
    evidence=[prepared.evidence(v,'Cat') for v in views];evidence=[v for v in evidence if v]
    chosen=choose(anchor,evidence)
    if not chosen:return before
    iid,tier=chosen;profile=prepared.profiles[iid]
    if before and before['suggested_identity_id']==iid and (kind=='Cat' or before['suggested_subject_type']=='Cat'):
        return before
    matches=[prepared.pooled_match(v) for v in [item]+views];matches=[m for m in matches if m]
    if kind=='Unknown' and not any(m['identity_id']==iid for m in matches):return before
    votes=[prepared.pooled_match(v) for v in views]
    counts=Counter(m['identity_id'] for m in votes if m)
    if (any(other!=iid and count>=2 for other,count in counts.items())
            or any(m['identity_id']!=iid and overwhelming(m['evidence']) for m in matches)):return before
    for v in [item]+views:
        ranks=prepared.type_ranks(v)
        if len(ranks)<3:continue
        top=ranks[0];support=top['independent_similarities'];two=len(support)>=2 and support[1]>=.5
        margin=top['score']-ranks[1]['score']
        if two and ((top['subject_type']=='Person' and top['score']>=.8 and margin>=.3)
                    or (top['subject_type']=='Dog' and top['score']>=.75 and margin>=.3)):return before
    return dict(suggested_identity_id=iid,suggested_name=profile['name'],suggested_subject_type='Cat',
                suggestion_tier='possible' if kind=='Unknown' else tier,review_required=True,confirmed=False)
