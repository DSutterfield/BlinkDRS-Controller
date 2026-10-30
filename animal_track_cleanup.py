"""Conservative consolidation of short-gap animal fragments; never learns labels."""
import math
import ast
from identity_analysis import normalized

MIN_APPEARANCE = .98


def fragments_match(a,b,period):
    if a['last_seen_seconds'] >= b['first_seen_seconds']:
        return False  # Concurrent observations may be two different animals.
    gap=b['first_seen_seconds']-a['last_seen_seconds']
    if gap>min(2.,2.*period)+1e-6:return False
    kinds={a['subject_type'],b['subject_type']}-{'Unknown'}
    if len(kinds)>1 or not kinds.issubset({'Cat','Dog'}):return False
    model=a.get('embedding_model')
    if not model or not model.startswith('dinov2-small-cls-v1:') or model!=b.get('embedding_model'):return False
    if min(a.get('quality_score',0),b.get('quality_score',0))<.2:return False
    av,bv=a.get('embedding'),b.get('embedding')
    if av is None or bv is None or len(av)!=384 or len(bv)!=384:return False
    av,bv=normalized(av),normalized(bv)
    appearance=sum(x*y for x,y in zip(av,bv))
    x=a['box']
    try:y=b.get('first_box') or ast.literal_eval(b['key'].split(':',2)[2])
    except (ValueError,SyntaxError,KeyError,IndexError):y=b['box']
    aw,ah=x[2]-x[0],x[3]-x[1];bw,bh=y[2]-y[0],y[3]-y[1]
    if min(aw,ah,bw,bh)<=0 or not .5<=aw*ah/(bw*bh)<=2:return False
    distance=math.hypot((x[0]+x[2]-y[0]-y[2])/2,(x[1]+x[3]-y[1]-y[3])/2)
    if distance>.25*(math.hypot(aw,ah)+math.hypot(bw,bh)):return False
    from identity_worker import overlap
    same_supported=a['subject_type']==b['subject_type'] and a['subject_type']=='Cat'
    return appearance>=MIN_APPEARANCE or (same_supported and overlap(x,y)>=.5 and appearance>=.7)


def consolidate(tracks,period,models):
    from identity_worker import retain_view
    tracks=list(tracks);audit=[];rejected=set()
    while True:
        candidates=[(i,j) for i,a in enumerate(tracks) for j,b in enumerate(tracks)
                    if i!=j and (a['key'],b['key']) not in rejected and fragments_match(a,b,period)]
        # Both fragments must have a unique eligible counterpart; do not choose
        # between similar nearby animals or greedily collapse an ambiguous group.
        pairs=[(i,j) for i,j in candidates if sum(i in p for p in candidates)==1 and sum(j in p for p in candidates)==1]
        if not pairs:break
        i,j=min(pairs,key=lambda p:tracks[p[0]]['first_seen_seconds']);a,b=tracks[i],tracks[j]
        similarity=sum(x*y for x,y in zip(normalized(a['embedding']),normalized(b['embedding'])))
        if similarity<MIN_APPEARANCE:
            from cat_foreground import foreground_supported
            refs=getattr(models,'cat_references',[]);neg=getattr(models,'background_vectors',[])
            if not all(foreground_supported(refs,neg,t['embedding']) for t in (a,b)):
                rejected.add((a['key'],b['key']));continue
        joined=dict(a);best=max([a,b],key=lambda t:(t['subject_type']!='Unknown',t.get('embedding') is not None,t.get('quality_score',0)))
        # Preserve the earlier stable key and all bounded complementary evidence.
        for k in ['crop_jpeg','embedding','embedding_model','quality_score','confidence','detector_model','crop_seconds','person_review_rank','review_crop_box']:
            if k in best:joined[k]=best[k]
        joined['box']=b['box'];joined['last_seen_seconds']=b['last_seen_seconds'];joined['sample_hits']=a.get('sample_hits',1)+b.get('sample_hits',1)
        for field in ['views','type_views']:
            holder={'views':[]}
            for view in sorted(a.get(field,[])+b.get(field,[]),key=lambda v:v['seconds']):
                retain_view(holder,view['seconds'],view['crop_jpeg'],view['embedding'],view['embedding_model'],view['quality_score'],view.get('type_result'))
            joined[field]=holder['views']
        type_best=max([a,b],key=lambda t:t.get('type_quality_score',-1))
        for k in ['type_embedding','type_embedding_model','type_quality_score','type_crop_box','type_crop_jpeg','type_crop_seconds']:
            if k in type_best:joined[k]=type_best[k]
        for field in ['cat_type_votes','detail_type_votes']:joined[field]=a.get(field,[])+b.get(field,[])
        kinds={a['subject_type'],b['subject_type']}-{'Unknown'};joined['subject_type']=next(iter(kinds),'Unknown')
        if joined['subject_type'] in ('Cat','Dog'):
            resolved=models.resolve_track_type(joined)
            if resolved!=joined['subject_type']:
                rejected.add((a['key'],b['key']));continue
        audit.append(dict(earlier_key=a['key'],later_key=b['key'],result_key=joined['key']))
        tracks[i]=joined;tracks.pop(j)
    return tracks,audit


def background_unknown(track,models):
    """Veto only unresolved Cat proposals consistently rejected as foreground.

    Supported species, missing evidence, other-species evidence and any usable
    foreground-supporting glimpse are retained. No stillness or size veto.
    """
    from cat_foreground import foreground_score
    from subject_type_check import rank_types
    if track['subject_type']!='Unknown' or ':Cat:' not in track.get('key',''):return False
    model=track.get('embedding_model')
    if model!=getattr(models,'pet_version',None) or track.get('quality_score',0)<.2 or track.get('embedding') is None:return False
    refs=getattr(models,'cat_references',[]);neg=getattr(models,'background_vectors',[])
    vectors=[track['embedding']]
    for field in ['views','type_views']:
        vectors += [v['embedding'] for v in track.get(field,[]) if v.get('embedding_model')==model and v.get('quality_score',0)>=.2 and v.get('embedding') is not None]
    if track.get('type_embedding_model')==model and track.get('type_quality_score',0)>=.2 and track.get('type_embedding') is not None:vectors.append(track['type_embedding'])
    for vector in vectors:
        ranks=rank_types(getattr(models,'type_references',[]),vector,model)
        if not ranks or ranks[0]['subject_type']!='Cat':return False
        score=foreground_score(refs,neg,vector)
        if score is None or score>=.5:return False
    return True


def cleanup(tracks,period,models):
    tracks,merges=consolidate(tracks,period,models)
    def near_supported_cat(track):
        for other in tracks:
            if other is track or other['subject_type']!='Cat':continue
            gap=max(track['first_seen_seconds'],other['first_seen_seconds'])-min(track['last_seen_seconds'],other['last_seen_seconds'])
            if gap>min(2.,2.*period)+1e-6:continue
            a,b=track['box'],other['box']
            distance=math.hypot((a[0]+a[2]-b[0]-b[2])/2,(a[1]+a[3]-b[1]-b[3])/2)
            if distance<=max(math.hypot(a[2]-a[0],a[3]-a[1]),math.hypot(b[2]-b[0],b[3]-b[1])):return True
        return False
    removed=[t['key'] for t in tracks if not near_supported_cat(t) and background_unknown(t,models)]
    return [t for t in tracks if t['key'] not in removed],dict(merged_fragments=merges,background_unknowns=removed)
