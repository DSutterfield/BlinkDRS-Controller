"""Geometry-only Cat crop placement. Detector species do not change track Type."""
import math
POLICY=dict(full_cat_confidence=.60,animal_confidence=.45,agreed_animal_confidence=.30,
            geometry_confidence=.15,consensus_iou=.50,minimum_original_coverage=.15,
            animal_containment=.60,minimum_area_ratio=.15,maximum_area_ratio=6.,
            peer_coverage_veto=.15,margin=.02,consensus_extension=.05)
def area(b):return max(0,b[2]-b[0])*max(0,b[3]-b[1])
def intersection(a,b):return [max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])]
def iou(a,b):
    x=area(intersection(a,b));return x/max(1,area(a)+area(b)-x)
def select(original,detections,peers,width,height):
    # An animal leaving the image cannot be restored by fabricating a larger box.
    if original[1]<=1 or original[3]>=height-1:return None
    options=[]
    for d in detections:
        if d['detector']!='FasterRCNN' or d['kind'] not in ('Cat','Dog'):continue
        b=d['box'];shared=area(intersection(b,original));coverage=shared/max(1,area(original))
        if coverage<POLICY['minimum_original_coverage']:continue
        ratio=area(b)/max(1,area(original))
        if not POLICY['minimum_area_ratio']<=ratio<=POLICY['maximum_area_ratio']:continue
        support=[p for p in detections if p['detector']!='FasterRCNN' and p['kind'] in ('Cat','Dog') and p['confidence']>=POLICY['geometry_confidence'] and iou(p['box'],b)>=POLICY['consensus_iou']]
        strong_cat=d['kind']=='Cat' and d['confidence']>=POLICY['full_cat_confidence']
        animal_geometry=(max(coverage,shared/max(1,area(b)))>=POLICY['animal_containment'] and (d['confidence']>=POLICY['animal_confidence'] or (d['confidence']>=POLICY['agreed_animal_confidence'] and support)))
        if not (strong_cat or animal_geometry):continue
        if any(area(intersection(b,p))/max(1,area(p))>=POLICY['peer_coverage_veto'] for p in peers):continue
        x,y,z,t=b;w=z-x;h=t-y
        # Preserve existing feature tips for comparable outlines. Large bad crops
        # may shrink; small partial crops may expand. Never apply a global shift.
        if .75<=ratio<=1.35:
            x=min(x,original[0]);y=min(y,original[1]);z=max(z,original[2]);t=max(t,original[3])
        for p in support:
            q=p['box'];x=min(x,max(b[0]-w*.05,q[0]));y=min(y,max(b[1]-h*.05,q[1]));z=max(z,min(b[2]+w*.05,q[2]));t=max(t,min(b[3]+h*.05,q[3]))
        box=[max(0,math.floor(x-w*.02)),max(0,math.floor(y-h*.02)),min(width,math.ceil(z+w*.02)),min(height,math.ceil(t+h*.02))]
        if min(box[2]-box[0],box[3]-box[1])<48:continue
        if any(area(intersection(box,p))/max(1,area(p))>=POLICY['peer_coverage_veto'] for p in peers):continue
        options.append(dict(box=box,full_box=b,confidence=d['confidence'],consensus=bool(support),rank=(d['confidence'],area(box))))
    options.sort(key=lambda x:x['rank'],reverse=True)
    if len(options)>1 and iou(options[0]['box'],options[1]['box'])<.35:return None
    return options[0] if options else None

def capture(models):
    """Reuse detections already computed for this frame; no extra CNN pass."""
    result=[]
    sources=[('FasterRCNN',getattr(models,'raw_cache',{}).get(None,[])),
             ('YOLO',getattr(models,'scene_proposals',[])),
             ('SSD',getattr(models,'full_scene_detections',[]))]
    for name,items in sources:
        for d in items:
            if d.get('subject_type') in ('Cat','Dog'):
                result.append(dict(detector=name,kind=d['subject_type'],confidence=float(d['confidence']),box=list(d['box'])))
    return result

def refine(path,tracks,models,proposals,fps,checkpoint=None):
    """Disabled after human review found head truncation and wrong subject."""
    return tracks
