"""Conservative adjacent small-animal association, independent of individual ID."""
import math

def nearby_small_boxes(a,b):
 try:
  aw,ah=a[2]-a[0],a[3]-a[1];bw,bh=b[2]-b[0],b[3]-b[1]
  if not (16<=min(aw,ah)<48 and 16<=min(bw,bh)<48):return False
  if max(aw,ah,bw,bh)>128 or not .25<=aw*ah/(bw*bh)<=4:return False
  distance=math.hypot((a[0]+a[2]-b[0]-b[2])/2,(a[1]+a[3]-b[1]-b[3])/2)
  return distance<=1.5*max(math.hypot(aw,ah),math.hypot(bw,bh))
 except (TypeError,ValueError,ZeroDivisionError,IndexError):return False

def temporal_animal_match(track,detection,box,seconds,sample_gap,vector,type_vector,type_model,type_quality,models):
 """Only a unique eligible track may be selected by the caller."""
 kind=detection['subject_type']
 if kind not in ('Cat','Dog') or track['subject_type']!=kind:return False
 if vector is not None or track.get('embedding') is not None:return False
 age=seconds-track.get('last_seen_seconds',track['first_seen_seconds'])
 if not 0<age<=sample_gap*1.1 or not nearby_small_boxes(track['box'],box):return False
 if type_quality<.2 or track.get('type_quality_score',0)<.2:return False
 if not type_model or not type_model.startswith('dinov2-small-cls-v1:') or track.get('type_embedding_model')!=type_model:return False
 a=track.get('type_embedding');b=type_vector
 if a is None or b is None or len(a)!=384 or len(b)!=384:return False
 try:
  norm=math.sqrt(sum(x*x for x in a)*sum(y*y for y in b));similarity=sum(x*y for x,y in zip(a,b))/norm
 except (ValueError,TypeError,ZeroDivisionError):return False
 if not math.isfinite(similarity) or similarity<.60:return False
 # The earlier track must already satisfy the existing final Type verifier.
 return models.resolve_track_type(track)==kind
