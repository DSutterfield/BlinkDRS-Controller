import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from contextlib import nullcontext
import numpy as np,cv2
from animal_detail_models import DetailAnimalDetector,DetailedAnimalSceneModels
from animal_scene_search import AnimalSceneModels

class AnimalDetailTests(unittest.TestCase):
 def scene(self,details):
  m=Mock();m.animal_type_embedding.return_value=([1.],'visual',1)
  m.choose_animal_type_features.side_effect=lambda frame,box,kind,v,ver,q:(v,ver,q,box)
  m.resolve_animal_type.return_value='Cat';m.resolve_track_type.return_value='Cat'
  s=DetailedAnimalSceneModels(m,detail=Mock());s.detail_detections=Mock(return_value=details)
  return s,m
 def test_detail_detector_ignores_people(self):
  d=DetailAnimalDetector.__new__(DetailAnimalDetector);d.version='detail'
  d.models=SimpleNamespace(cv2=cv2,torch=SimpleNamespace(inference_mode=nullcontext));d.transform=lambda im:im
  d.detector=lambda images:[dict(labels=[1,17,18],scores=[.99,.8,.9],boxes=[np.array([0,0,10,10])]*3)]
  found=d.detect(np.zeros((100,100,3),np.uint8),[10,20,60,70])
  self.assertEqual([x['subject_type'] for x in found],['Cat','Dog']);self.assertEqual(found[0]['box'],[10,20,20,30])
 def test_original_people_preserved_exactly(self):
  person=dict(subject_type='Person',confidence=.7,box=[0,0,50,100]);s,_=self.scene([])
  with patch.object(AnimalSceneModels,'detect',return_value=[person]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[person])
 def test_added_animal_needs_existing_strong_type_gate(self):
  d=dict(subject_type='Dog',confidence=.4,box=[100,100,140,150]);s,m=self.scene([d]);m.resolve_track_type.return_value='Unknown'
  with patch.object(AnimalSceneModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[])
 def test_type_can_correct_new_detector_dog_claim(self):
  d=dict(subject_type='Dog',confidence=.4,box=[100,100,140,150]);s,_=self.scene([d])
  with patch.object(AnimalSceneModels,'detect',return_value=[]):found=s.detect(np.zeros((200,200,3),np.uint8))
  self.assertEqual(found[0]['subject_type'],'Cat');self.assertEqual(found[0]['confidence'],.4)
 def test_detail_cannot_replace_successful_existing_crop(self):
  original=dict(subject_type='Cat',confidence=.35,box=[100,100,140,150]);s,_=self.scene([dict(original,confidence=.99)])
  with patch.object(AnimalSceneModels,'detect',return_value=[original]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[original])
 def test_person_coverage_blocks_supplemental_animal(self):
  person=dict(subject_type='Person',confidence=.9,box=[0,0,100,150]);s,m=self.scene([dict(subject_type='Cat',confidence=.9,box=[20,20,60,80])])
  with patch.object(AnimalSceneModels,'detect',return_value=[person]):self.assertEqual(s.detect(np.zeros((200,200,3),np.uint8)),[person])
  m.animal_type_embedding.assert_not_called()
 def test_every_other_sample_and_expired_seeds(self):
  s,_=self.scene([]);s.recent=[(0,dict(subject_type='Cat',confidence=.8,box=[50,50,80,80]))];s.index=3
  with patch.object(AnimalSceneModels,'detect',return_value=[]):s.detect(np.zeros((200,200,3),np.uint8))
  s.detail_detections.assert_not_called();self.assertEqual(s.recent,[])
 def test_small_crops_do_not_bypass_native_gate(self):
  s,m=self.scene([]);self.assertEqual(s.type_evidence(np.zeros((200,200,3),np.uint8),dict(subject_type='Cat',box=[20,20,30,30]),[]),(None,None));m.animal_type_embedding.assert_not_called()
 def test_new_proposal_uses_tight_animal_evidence(self):
  s,m=self.scene([]);m.resolve_track_type.return_value='Unknown'
  s.type_evidence(np.zeros((200,200,3),np.uint8),dict(subject_type='Cat',box=[20,20,60,80]),[])
  m.choose_animal_type_features.assert_not_called()

if __name__=='__main__':unittest.main()
