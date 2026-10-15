"""Cat locations and human-supervised Type refinement; no identity assignment."""
import hashlib
import math
import numpy as np
from subject_detail_consensus import SubjectConsensusModels
from subject_type_check import rank_types, resolve_track_type, in_person_region
from identity_analysis import normalized
from identity_worker import overlap
from animal_scene_search import containment
from cat_foreground import foreground_supported


def cat_evidence(references, vector, model, quality):
    if quality < .2:return None
    ranks=rank_types(references,vector,model)
    if len(ranks)<3 or ranks[0]['subject_type']!='Cat':return None
    top=ranks[0];support=top['independent_similarities'];margin=top['score']-ranks[1]['score']
    if top['score']<.65 or margin<.25 or len(support)<2 or support[1]<.5:return None
    return dict(strong=top['score']>=.75 and margin>=.3,score=top['score'],margin=margin)


class CatSearchModels(SubjectConsensusModels):
    def __init__(self,models,checkpoint=None,cat_references=None,proposal_detector=None,
                 background_vectors=None,catalog_id=None):
        super().__init__(models,checkpoint)
        from pathlib import Path
        import identity_models
        from cat_proposals import CatProposalDetector,MODEL_FILE
        from cat_foreground import load_backgrounds
        folder=Path(identity_models.__file__).resolve().parent/'ai-models'
        self.cat_references=getattr(models,'cat_type_references',[]) if cat_references is None else cat_references
        if proposal_detector is None:
            proposal_detector=models.__dict__.get('cat_proposal_detector')
            if proposal_detector is None and (folder/MODEL_FILE).is_file():
                proposal_detector=CatProposalDetector(self.cv2,folder/MODEL_FILE)
                models.cat_proposal_detector=proposal_detector
        self.proposal_detector=proposal_detector
        self.background_vectors=load_backgrounds(folder,models.pet_version) if background_vectors is None else background_vectors
        self.catalog_id=catalog_id
        self.teachers=[r for r in self.cat_references if r.get('support_excluded')
                       and r['catalog_id']==catalog_id and r['quality_score']>=.2]
        self.pending_cat=[];self.scene_proposals=[];self.frame_features={}
        self.reviewed_cat_anchors=[];self.current_cat_continuations=[]

    def animal_type_embedding(self,crop):
        key=hashlib.sha256(repr(crop.shape).encode()+crop.tobytes()).digest()
        if key not in self.frame_features:
            self.frame_features[key]=self.models.animal_type_embedding(crop)
        return self.frame_features[key]

    def _teacher_matches(self,vector,model):
        if vector is None or not self.teachers:return False
        try:query=np.array(normalized(vector))
        except (ValueError,TypeError):return False
        if len(query)!=384:return False
        return any(r['model_version']==model and
                   float(query@np.array(normalized(r['vector'])))>=.75 for r in self.teachers)

    def _species_veto(self,vector,model):
        ranks=rank_types(getattr(self.models,'type_references',[]),vector,model)
        if len(ranks)<3 or ranks[0]['subject_type'] not in ('Dog','Person'):return False
        top=ranks[0];support=top['independent_similarities']
        return top['score']>=.75 and top['score']-ranks[1]['score']>=.3 and len(support)>=2 and support[1]>=.5

    def _known_cat(self,vector,model,quality):
        return (self._teacher_matches(vector,model)
                and cat_evidence(self.cat_references,vector,model,quality) is not None
                and not self._species_veto(vector,model)
                and foreground_supported(self.cat_references,self.background_vectors,vector))

    def _continued_cat(self,box,vector,model,quality,index):
        if vector is None or not self.reviewed_cat_anchors:return False
        evidence=cat_evidence(self.cat_references,vector,model,quality)
        original=rank_types(getattr(self.models,'type_references',[]),vector,model)
        if (evidence is None or not original or original[0]['subject_type']!='Cat'
                or self._species_veto(vector,model)
                or not foreground_supported(self.cat_references,self.background_vectors,vector)):return False
        query=np.array(normalized(vector))
        return any(age==index-1 and m==model and overlap(previous,box)>.35
                   and float(query@np.array(normalized(v)))>=.75
                   for age,previous,v,m in self.reviewed_cat_anchors)

    def _current_continuation(self,vector,model):
        if vector is None:return False
        return any(m==model and np.allclose(vector,v,rtol=0,atol=1e-7)
                   for v,m in self.current_cat_continuations)

    def _scene_support(self,box):
        cats=[d['confidence'] for d in self.scene_proposals if d['subject_type']=='Cat' and overlap(d['box'],box)>.4]
        dogs=[d['confidence'] for d in self.scene_proposals if d['subject_type']=='Dog' and overlap(d['box'],box)>.4]
        score=max(cats,default=0.)
        return score if score>=.55 and score-max(dogs,default=0.)>=.2 else 0.

    def _trusted_native(self,box,vector,model,quality):
        if self._known_cat(vector,model,quality) or self._current_continuation(vector,model):return True
        evidence=cat_evidence(self.cat_references,vector,model,quality)
        original=rank_types(getattr(self.models,'type_references',[]),vector,model)
        return (self._scene_support(box)>=.55 and evidence is not None and evidence['strong']
                and original and original[0]['subject_type']=='Cat'
                and foreground_supported(self.cat_references,self.background_vectors,vector))

    def choose_animal_type_features(self,frame,box,kind,vector,model,quality,person_region=False):
        # Keep supported native Cat pixels rather than mixing them with nearby
        # feet, another animal, or other context that can change their Type.
        if kind in ('Cat','Dog') and self._trusted_native(box,vector,model,quality):
            return vector,model,quality,list(box)
        return self.models.choose_animal_type_features(frame,box,kind,vector,model,quality,person_region=person_region)

    def resolve_animal_type(self,kind,vector,model,person_region=False):
        if kind in ('Cat','Dog') and (self._known_cat(vector,model,1.) or self._current_continuation(vector,model)):return 'Cat'
        return self.models.resolve_animal_type(kind,vector,model,person_region=person_region)

    def detect(self,frame):
        idx=self.index;self.frame_features={};self.current_cat_continuations=[]
        kept=super().detect(frame)
        self.scene_proposals=[] if self.proposal_detector is None else self.proposal_detector.detect(frame)
        pending=[];anchors=[];h,w=frame.shape[:2]
        # Existing detections supply the preceding view for later weak
        # proposals. Keeping their crop must not discard this evidence.
        for d in kept:
            if d['subject_type'] not in ('Cat','Dog'):continue
            score=self._scene_support(d['box'])
            if score:d['cat_scene_score']=score
            if not self.teachers and not score:continue
            a,b,c,e=map(int,d['box']);a,b,c,e=max(0,a),max(0,b),min(w,c),min(h,e)
            if min(c-a,e-b)<20 or max(c-a,e-b)>192:continue
            v,m,q=self.animal_type_embedding(frame[b:e,a:c])
            if self._known_cat(v,m,q):anchors.append((idx,d['box'],v,m))
            elif self._continued_cat(d['box'],v,m,q,idx):self.current_cat_continuations.append((v,m))
            if self._trusted_native(d['box'],v,m,q):pending.append(dict(d,subject_type='Cat'))
        for d in self.scene_proposals:
            if any(overlap(p['box'],d['box'])>.35 for p in pending):continue
            if any(k['subject_type'] in ('Cat','Dog') and
                   (overlap(k['box'],d['box'])>.35 or containment(k['box'],d['box'])>.7) for k in kept):continue
            scene_score=self._scene_support(d['box'])
            if not self.teachers and not scene_score:continue
            a,b,c,e=map(int,d['box']);a,b,c,e=max(0,a),max(0,b),min(w,c),min(h,e)
            if min(c-a,e-b)<20 or max(c-a,e-b)>192:continue
            self.checkpoint();v,m,q=self.animal_type_embedding(frame[b:e,a:c])
            evidence=cat_evidence(self.cat_references,v,m,q)
            if evidence is None or self._species_veto(v,m):continue
            known=self._known_cat(v,m,q)
            continued=self._continued_cat(d['box'],v,m,q,idx)
            taught=known or continued
            if not taught and not scene_score:continue
            if not foreground_supported(self.cat_references,self.background_vectors,v):continue
            if known:anchors.append((idx,d['box'],v,m))
            if continued:self.current_cat_continuations.append((v,m))
            item=dict(d,box=[a,b,c,e],subject_type='Cat')
            if scene_score:item['cat_scene_score']=scene_score
            pending.append(item)
            prior=[p for age,p in self.pending_cat if age==idx-1 and overlap(p['box'],item['box'])>.35]
            if not evidence['strong'] and len(prior)!=1:continue
            if in_person_region(item,kept) and not (evidence['strong'] or taught):continue
            kept.append(item)
        self.pending_cat=[(idx,p) for p in pending]
        self.reviewed_cat_anchors=anchors
        return kept

    def cat_type_vote(self,detection,vector,model,quality,seconds,jpeg):
        score=detection.get('cat_scene_score',0.)
        reviewed=self._known_cat(vector,model,quality) or self._current_continuation(vector,model)
        if score<.55 and not reviewed:return None
        evidence=cat_evidence(self.cat_references,vector,model,quality)
        if evidence is None or self._species_veto(vector,model):return None
        original=rank_types(getattr(self.models,'type_references',[]),vector,model)
        if not original or original[0]['subject_type']!='Cat':return None
        if not foreground_supported(self.cat_references,self.background_vectors,vector):return None
        return dict(seconds=seconds,crop_hash=hashlib.sha256(jpeg).hexdigest(),scene_score=score,reviewed_cat_supported=reviewed,
                    score=evidence['score'],margin=evidence['margin'],embedding_model=model,quality_score=quality)

    def resolve_track_type(self,track):
        result=super().resolve_track_type(track)
        if result!='Unknown' or track['subject_type'] not in ('Cat','Dog'):return result
        model=track.get('type_embedding_model',track.get('embedding_model'))
        vectors=[]
        if track.get('type_quality_score',track.get('quality_score',0))>=.2:
            vectors.append(track.get('type_embedding',track.get('embedding')))
        vectors += [v.get('embedding') for v in track.get('type_views',track.get('views',[]))
                    if v.get('embedding_model')==model and v.get('quality_score',0)>=.2]
        if any(self._species_veto(v,model) for v in vectors):return result
        taught=any(self._known_cat(v,model,1.) for v in vectors)
        if taught:
            # Human-matching views can resolve an otherwise uncertain track.
            # Strong contrary species evidence was vetoed above; unmatched
            # weak/context views must not erase the reviewed Cat evidence.
            reviewed_votes=[v for v in track.get('cat_type_votes',[]) if v.get('reviewed_cat_supported')
                            and v.get('embedding_model')==model and v.get('quality_score',0)>=.2
                            and v.get('score',0)>=.65 and v.get('margin',0)>=.25]
            def reviewed_view(view):
                if self._known_cat(view.get('embedding'),model,view['quality_score']):return True
                jpeg=view.get('crop_jpeg')
                return jpeg and any(v.get('seconds')==view.get('seconds') and
                        v.get('crop_hash')==hashlib.sha256(jpeg).hexdigest() for v in reviewed_votes)
            matched=[v for v in track.get('type_views',track.get('views',[]))
                     if v.get('embedding_model')==model and v.get('quality_score',0)>=.2
                     and reviewed_view(v)]
            filtered=dict(track,type_views=matched)
            primary=track.get('type_embedding',track.get('embedding'))
            if not self._known_cat(primary,model,track.get('type_quality_score',track.get('quality_score',0))) and matched:
                best=max(matched,key=lambda v:v['quality_score'])
                filtered.update(type_embedding=best['embedding'],type_embedding_model=model,type_quality_score=best['quality_score'])
            return 'Cat' if resolve_track_type(self.cat_references,filtered)=='Cat' else result
        votes=[]
        for vote in track.get('cat_type_votes',[]):
            if (vote.get('embedding_model')!=model or vote.get('quality_score',0)<.2
                    or vote.get('scene_score',0)<.55 or vote.get('score',0)<.65
                    or vote.get('margin',0)<.25 or not vote.get('crop_hash')):continue
            try:seconds=float(vote['seconds'])
            except (ValueError,TypeError,KeyError):continue
            if not math.isfinite(seconds):continue
            vote=dict(vote,seconds=seconds)
            if any(abs(vote['seconds']-v['seconds'])<.5 or vote['crop_hash']==v['crop_hash'] for v in votes):continue
            votes.append(vote)
        if not taught and len(votes)<2:return result
        return 'Cat' if resolve_track_type(self.cat_references,track)=='Cat' else result
