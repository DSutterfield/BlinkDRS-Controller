"""Person Type from agreeing full-scene detectors; individual naming is separate."""
from cat_extra import CatSearchModels
from identity_worker import overlap


class PersonConsensusModels(CatSearchModels):
    def detect(self,frame):
        kept=super().detect(frame)
        h,w=frame.shape[:2]
        people=getattr(self.proposal_detector,'people',[])
        for proposal in sorted(getattr(self,'person_search_candidates',[]),key=lambda p:p['confidence'],reverse=True):
            if proposal['subject_type']!='Person' or proposal['confidence']<.90:continue
            a,b,c,d=proposal['box'];box=[max(0,int(a)),max(0,int(b)),min(w,int(c)),min(h,int(d))]
            if min(box[2]-box[0],box[3]-box[1])<48:continue
            support=[p for p in people if p['subject_type']=='Person' and p['confidence']>=.80
                     and overlap(box,p['box'])>=.50]
            if not support:continue
            # Keep the original Person crop. Agreement adds evidence only.
            existing=[p for p in kept if p['subject_type']=='Person' and overlap(p['box'],box)>=.40]
            if existing:
                for p in existing:p['person_model_consensus']=True
            else:
                kept.append(dict(proposal,box=box,detail_person_candidate=True,person_model_consensus=True))
        return kept
