import unittest
from unittest.mock import patch
from subject_type_check import resolve_animal_type,resolve_track_type
from identity_worker import track_candidate
from test_subject_type_check import MODEL,vector

def ranked(kind,score,other,margin_support=.55):
    rest=[x for x in ('Cat','Dog','Person') if x!=kind]
    return [dict(subject_type=kind,score=score,independent_similarities=[.65,margin_support]),
            dict(subject_type=rest[0],score=other),dict(subject_type=rest[1],score=1-score-other)]

class SpeciesDecisionTests(unittest.TestCase):
    def test_cat_dog_detector_claim_cannot_raise_species_evidence_requirement(self):
        for top,claim in [('Cat','Dog'),('Dog','Cat')]:
            with patch('subject_type_check.rank_types',return_value=ranked(top,.70,.25)):
                self.assertEqual(resolve_animal_type([],claim,vector(1),MODEL),top)
                self.assertEqual(resolve_animal_type([],top,vector(1),MODEL),top)

    def test_person_suppression_keeps_existing_stronger_gate(self):
        with patch('subject_type_check.rank_types',return_value=ranked('Person',.79,.15)):
            self.assertEqual(resolve_animal_type([],'Cat',vector(1),MODEL),'Unknown')
        with patch('subject_type_check.rank_types',return_value=ranked('Person',.85,.10)):
            self.assertIsNone(resolve_animal_type([],'Cat',vector(1),MODEL))

    def test_stationary_cat_consensus_can_outvote_wrong_detector_species(self):
        track=dict(subject_type='Dog',quality_score=0,type_quality_score=1,type_embedding_model=MODEL,
                   type_embedding=vector(1),type_views=[dict(seconds=7.5,crop_jpeg=b'a',embedding_model=MODEL,quality_score=1,embedding=vector(1)),dict(seconds=15,crop_jpeg=b'b',embedding_model=MODEL,quality_score=1,embedding=vector(2))])
        def scores(refs,v,model):return ranked('Cat',.629,.398) if v==vector(1) else ranked('Cat',.701,.317)
        with patch('subject_type_check.rank_types',side_effect=scores):
            self.assertEqual(resolve_track_type([],track),'Cat')
            track['type_views'][1]['crop_jpeg']=b'a'
            self.assertEqual(resolve_track_type([],track),'Unknown')

    def test_single_moderate_view_cannot_promote_wrong_species(self):
        t=dict(subject_type='Cat',quality_score=1,embedding_model=MODEL,embedding=vector(1),views=[dict(seconds=0,crop_jpeg=b'a',embedding_model=MODEL,quality_score=1,embedding=vector(1))])
        with patch('subject_type_check.rank_types',return_value=ranked('Cat',.61,.253)):
            self.assertEqual(resolve_track_type([],t),'Unknown')

    def test_stationary_bridge_preserves_person_time_limit(self):
        t=dict(subject_type='Dog',box=[10,10,40,70],last_seen_seconds=0)
        d=dict(subject_type='Cat',box=[10,10,40,70])
        self.assertTrue(track_candidate(t,d,7.5,1.875))
        self.assertFalse(track_candidate(t,dict(d,box=[30,10,60,70]),7.5,1.875))
        self.assertFalse(track_candidate(t,d,13,1.875))
        self.assertFalse(track_candidate(dict(t,subject_type='Person'),dict(d,subject_type='Person'),7.5,1.875))
        self.assertFalse(track_candidate(t,dict(d,subject_type='Person'),1,1.875))

if __name__=='__main__':unittest.main()
