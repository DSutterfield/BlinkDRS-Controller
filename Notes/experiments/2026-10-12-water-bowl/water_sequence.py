"""Experimental advisory links only; never copies a name, label or confirmation."""
import math

def cosine(a,b):
    if not a or not b or len(a)!=len(b):return None
    if not all(math.isfinite(x) for x in a+b):return None
    n=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b))/n if n else None

def link_sequences(observations,max_gap=60.,min_cosine=.90,margin=.05):
    """Link mutually unique appearances across adjacent represented clips.

    Unknown stays Unknown. The link offers review context, not a new Type vote.
    Multiple processing variants within a frame must be consolidated upstream.
    """
    out=[]
    clips={}
    for r in observations:clips.setdefault((r['camera'],r['clip']),[]).append(r)
    cameras={camera for camera,clip in clips}
    for camera in cameras:
        ordered=sorted((rs for (cam,_),rs in clips.items() if cam==camera),key=lambda rs:min(r['time'] for r in rs))
        for previous,current in zip(ordered,ordered[1:]):
            candidates=[]
            for a in previous:
                for b in current:
                    gap=b['time']-a['time']
                    if not 0<gap<=max_gap or a['clip']==b['clip']:continue
                    if a.get('quality',0)<.2 or b.get('quality',0)<.2:continue
                    if a.get('model')!=b.get('model') or not a.get('model'):continue
                    if not a.get('hash') or not b.get('hash') or a['hash']==b['hash']:continue
                    ka,kb=a.get('species'),b.get('species')
                    if ka not in ('Cat','Dog','Unknown') or kb not in ('Cat','Dog','Unknown'):continue
                    if ka!='Unknown' and kb!='Unknown' and ka!=kb:continue
                    aa,bb=a['box'],b['box']
                    # Full-frame normalized coordinates; reject large jumps/size changes.
                    distance=math.hypot((aa[0]+aa[2]-bb[0]-bb[2])/2,(aa[1]+aa[3]-bb[1]-bb[3])/2)
                    ar=(aa[2]-aa[0])*(aa[3]-aa[1]);br=(bb[2]-bb[0])*(bb[3]-bb[1])
                    if ar<=0 or br<=0 or distance>.20 or not .25<=ar/br<=4:continue
                    score=cosine(a.get('vector'),b.get('vector'))
                    if score is not None and score>=min_cosine:candidates.append((score,a,b))
            for score,a,b in candidates:
                rivals=[s for s,x,y in candidates if (x['id']==a['id'] or y['id']==b['id']) and (x['id'],y['id'])!=(a['id'],b['id'])]
                if rivals and score-max(rivals)<margin:continue
                out.append({'from':a['id'],'to':b['id'],'cosine':score,'gap_seconds':b['time']-a['time'],'status':'possible continuity; review context only','species_before':[a['species'],b['species']]})
    return out
