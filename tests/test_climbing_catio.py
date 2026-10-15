import unittest
from unittest.mock import Mock,patch
import numpy as np
from identity_models import LocalModels
from animal_scene_search import AnimalSceneModels
from subject_type_learning import diverse_cat_references
from test_subject_type_check import vector,MODEL
from test_species_decisions import ranked

class ClimbingCatioTests(unittest.TestCase):
 def model(self):
  m=LocalModels.__new__(LocalModels);m.type_references=[]
  m.animal_type_embedding=Mock(side_effect=[(vector(i),MODEL,1) for i in (1,2,3)])
  return m
 def test_two_strong_contexts_can_confirm_weak_location(self):
  m=self.model()
  with patch('subject_type_check.rank_types',return_value=ranked('Cat',.85,.1)):
   self.assertIsNotNone(m.confirmed_animal_context(np.zeros((300,300,3),np.uint8),[100,100,150,150]))
 def test_one_strong_context_is_insufficient(self):
  m=self.model()
  with patch('subject_type_check.rank_types',side_effect=[ranked('Cat',.85,.1),ranked('Cat',.6,.3),ranked('Cat',.6,.3)]):
   self.assertIsNone(m.confirmed_animal_context(np.zeros((300,300,3),np.uint8),[100,100,150,150]))
 def test_strong_disagreement_vetoes_context_selection(self):
  m=self.model()
  with patch('subject_type_check.rank_types',side_effect=[ranked('Cat',.85,.1),ranked('Cat',.85,.1),ranked('Dog',.85,.1)]):
   self.assertIsNone(m.confirmed_animal_context(np.zeros((300,300,3),np.uint8),[100,100,150,150]))
 def test_strong_person_context_vetoes_animal(self):
  m=self.model()
  with patch('subject_type_check.rank_types',side_effect=[ranked('Cat',.85,.1),ranked('Cat',.85,.1),ranked('Person',.85,.1)]):
   self.assertIsNone(m.confirmed_animal_context(np.zeros((300,300,3),np.uint8),[100,100,150,150]))
 def test_person_region_never_rescues_weak_animal(self):
  m=self.model();self.assertFalse(m.confirm_weak_animal_proposal(None,[0,0,30,30],'Cat',person_region=True));m.animal_type_embedding.assert_not_called()
 def test_weak_proposal_needs_explicit_confirmation_and_preserves_people(self):
  person=dict(subject_type='Person',confidence=.9,box=[200,200,290,290]);cat=dict(subject_type='Cat',confidence=.35,box=[20,20,50,50])
  m=Mock();m.detect.return_value=[person];m.confirm_weak_animal_proposal.return_value=False
  for accepted in (False,True):
   s=AnimalSceneModels(m);s.grid=Mock(side_effect=[[cat],[]]);m.confirm_weak_animal_proposal.return_value=accepted
   ds=s.detect(np.zeros((300,300,3),np.uint8));self.assertEqual(ds[0],person);self.assertEqual(len(ds),2 if accepted else 1)
   if accepted:self.assertEqual(ds[1]['confidence'],.35)
 def test_recent_plus_diverse_selection_covers_older_appearance(self):
  rows=[dict(detection_id=i,vector=vector(1)) for i in range(7)]+[dict(detection_id=7,vector=vector(2))]
  out=diverse_cat_references(rows,4)
  self.assertEqual([r['detection_id'] for r in out[:2]],[0,1]);self.assertIn(7,[r['detection_id'] for r in out]);self.assertEqual(len({r['detection_id'] for r in out}),4)
 def test_small_gallery_retained(self):
  rows=[dict(vector=vector(1))];self.assertEqual(diverse_cat_references(rows),rows)
 def test_reference_selection_does_not_require_individual_names(self):
  rows=[dict(vector=vector(1 if i<7 else 2),detection_id=i) for i in range(8)]
  self.assertEqual(len(diverse_cat_references(rows,4)),4)

if __name__=='__main__':unittest.main()
