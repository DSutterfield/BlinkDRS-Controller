"""Supplement uncertain Dog/Person Types with independent full-scene evidence."""
import hashlib
import io
from PIL import Image
from animal_detail_refinement import RefinedAnimalSceneModels
from identity_worker import overlap
from subject_type_check import rank_types, resolve_animal_type


class SubjectConsensusModels(RefinedAnimalSceneModels):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.crop_checks = 0

    def detect(self, frame):
        kept = super().detect(frame)
        h, w = frame.shape[:2]
        for person in getattr(self, 'detail_people', []):
            a,b,c,d = person['box']
            box = [max(0,a),max(0,b),min(w,c),min(h,d)]
            if min(box[2]-box[0],box[3]-box[1]) < 48:
                continue
            matches = [p for p in kept if p['subject_type']=='Person' and overlap(p['box'],box)>=.4]
            if matches:
                # Keep the original detection/identity crop; improve only the
                # same-frame region used to reject animal-shaped human parts.
                for p in matches:
                    p['detail_person_box'] = box
            else:
                kept.append(dict(person, box=box, detail_person_candidate=True))
        dogs = [d for d in self.raw_cache.get(None, [])
                if d['subject_type']=='Dog' and d['confidence']>=.80]
        for item in kept:
            if item['subject_type'] not in ('Cat','Dog'):
                continue
            if any(overlap(item['box'],d['box'])>=.5 for d in dogs):
                item['detail_dog_supported'] = True
        return kept

    def detail_type_vote(self, detection, vector, model, quality, seconds, jpeg):
        if not detection.get('detail_dog_supported') or quality<.2:
            return None
        ranks = rank_types(getattr(self.models,'type_references',[]),vector,model)
        if len(ranks)<3:
            return None
        top = ranks[0]; support = top['independent_similarities']
        margin = top['score']-ranks[1]['score']
        if (top['subject_type']!='Dog' or top['score']<.50 or margin<.10
                or len(support)<2 or support[1]<.55):
            return None
        return dict(seconds=seconds,crop_hash=hashlib.sha256(jpeg).hexdigest(),
                    score=top['score'],margin=margin)

    def resolve_track_type(self, track):
        result = self.models.resolve_track_type(track)
        if result!='Unknown' or track['subject_type'] not in ('Cat','Dog'):
            return result
        refs = getattr(self.models,'type_references',[])
        # Conflicting Cat evidence must remain Unknown, even if a detector
        # repeatedly calls it Dog. Never weaken existing supported Cat Types.
        vectors = [(track.get('type_embedding'),track.get('type_embedding_model'))]
        vectors += [(v.get('embedding'),v.get('embedding_model')) for v in track.get('type_views',[])]
        if any(resolve_animal_type(refs,'Dog',v,m)=='Cat' for v,m in vectors if v is not None):
            return result
        votes=[]
        for vote in track.get('detail_type_votes',[]):
            if any(abs(vote['seconds']-v['seconds'])<.5 or vote['crop_hash']==v['crop_hash'] for v in votes):
                continue
            votes.append(vote)
        if (len(votes)>=2 and sum(v['score'] for v in votes)/len(votes)>=.55
                and sum(v['margin'] for v in votes)/len(votes)>=.15):
            return 'Dog'
        if self.confirm_uncertain_dog_crop(track):
            return 'Dog'
        return result

    def confirm_uncertain_dog_crop(self, track):
        """A bounded second-model check for usable, Dog-favoring native crops."""
        if track['subject_type']!='Dog' or self.crop_checks>=4:
            return False
        vector=track.get('type_embedding');model=track.get('type_embedding_model')
        if vector is None or track.get('type_quality_score',0)<.2:
            return False
        ranks=rank_types(getattr(self.models,'type_references',[]),vector,model)
        if len(ranks)<3:
            return False
        top=ranks[0];support=top['independent_similarities']
        if (top['subject_type']!='Dog' or top['score']<.60
                or top['score']-ranks[1]['score']<.15
                or len(support)<2 or support[1]<.60):
            return False
        data=track.get('crop_jpeg')
        if not data:
            return False
        image=Image.open(io.BytesIO(data)).convert('RGB')
        w,h=image.size
        if min(w,h)<48:
            return False
        self.checkpoint();self.crop_checks+=1
        with self.models.torch.inference_mode():
            result=self.detail.detector([self.detail.transform(image)])[0]
        scores={'Dog':0.,'Cat':0.,'Person':0.}
        for label,score,box in zip(result['labels'],result['scores'],result['boxes']):
            kind={1:'Person',17:'Cat',18:'Dog'}.get(int(label))
            if kind is None:
                continue
            a,b,c,d=box.tolist()
            coverage=max(0,min(w,c)-max(0,a))*max(0,min(h,d)-max(0,b))/(w*h)
            if coverage>=.5:
                scores[kind]=max(scores[kind],float(score))
        return scores['Dog']>=.95 and scores['Dog']-max(scores['Cat'],scores['Person'])>=.30
