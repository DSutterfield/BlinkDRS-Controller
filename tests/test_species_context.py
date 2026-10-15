import unittest
from unittest.mock import Mock,patch
import numpy as np
from identity_models import LocalModels
from test_subject_type_check import vector,MODEL
from test_species_decisions import ranked

class SpeciesContextTests(unittest.TestCase):
    def model(self):
        m=LocalModels.__new__(LocalModels);m.type_references=[]
        m.animal_type_embedding=Mock(side_effect=[(vector(2),MODEL,1),(vector(3),MODEL,1)]*3)
        return m
    def test_uncertain_crop_uses_agreeing_supported_context(self):
        m=self.model()
        def ranks(refs,v,model):
            return {1:ranked('Cat',.61,.253),2:ranked('Dog',.515,.341),3:ranked('Dog',.894,.104)}[v.index(1)]
        with patch('subject_type_check.rank_types',side_effect=ranks):
            result=m.choose_animal_type_features(np.zeros((200,200,3),np.uint8),[60,60,100,140],'Cat',vector(1),MODEL,1)
        self.assertEqual(result[0],vector(3));self.assertEqual(result[3],[40,20,120,180])
    def test_confident_species_and_person_suppression_do_not_use_context(self):
        for kind in ('Dog','Person'):
            m=self.model()
            with patch('subject_type_check.rank_types',return_value=ranked(kind,.9,.05)):
                result=m.choose_animal_type_features(np.zeros((200,200,3),np.uint8),[60,60,100,140],'Cat',vector(1),MODEL,1)
            self.assertEqual(result[0],vector(1));m.animal_type_embedding.assert_not_called()
    def test_context_disagreement_cannot_choose_convenient_species(self):
        m=self.model()
        def ranks(refs,v,model):return ranked('Cat',.61,.253) if v==vector(1) else ranked('Cat' if v==vector(2) else 'Dog',.85,.10)
        with patch('subject_type_check.rank_types',side_effect=ranks):
            result=m.choose_animal_type_features(np.zeros((200,200,3),np.uint8),[60,60,100,140],'Cat',vector(1),MODEL,1)
        self.assertEqual(result[0],vector(1))
    def test_unusable_native_crop_does_not_gain_background_confidence(self):
        m=self.model()
        result=m.choose_animal_type_features(np.zeros((200,200,3),np.uint8),[60,60,100,140],'Cat',None,None,0)
        self.assertIsNone(result[0]);m.animal_type_embedding.assert_not_called()

if __name__=='__main__':unittest.main()
