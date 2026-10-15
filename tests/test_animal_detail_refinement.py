import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from contextlib import nullcontext
import numpy as np,cv2
from animal_detail_models import DetailedAnimalSceneModels
from animal_detail_refinement import RefinedAnimalSceneModels

class AnimalRefinementTests(unittest.TestCase):
 def scene(self,full=None):
  m=Mock();m.animal_type_embedding.return_value=([1.],'visual',1)
  m.resolve_animal_type.return_value='Cat';m.resolve_track_type.return_value='Cat'
  s=RefinedAnimalSceneModels(m,detail=Mock())
  def raw(frame,region=None):
   found=list(full or []) if region is None else [];s.raw_cache[tuple(region) if region else None]=found;return found
  s.raw_detail_detections=Mock(side_effect=raw)
  return s,m
 def test_raw_search_discards_people_and_caches_by_region(self):
  model=SimpleNamespace(cv2=cv2,torch=SimpleNamespace(inference_mode=nullcontext))
  detector=Mock(return_value=[dict(labels=[1,17,18,17],scores=[.99,.16,.2,.09],boxes=[np.array([0,0,20,30])]*4)])
  detail=SimpleNamespace(detector=detector,transform=lambda im:im,version='weights')
  s=RefinedAnimalSceneModels(model,detail=detail);s.raw_cache={};im=np.zeros((200,200,3),np.uint8)
  found=s.raw_detail_detections(im,[10,20,110,120]);s.raw_detail_detections(im,[10,20,110,120])
  self.assertEqual([d['subject_type'] for d in found],['Cat','Dog']);self.assertEqual(found[0]['box'],[10,20,30,50]);detector.assert_called_once()
 def test_original_results_have_priority(self):
  person=dict(subject_type='Person',confidence=.8,box=[0,0,60,150]);cat=dict(subject_type='Cat',confidence=.4,box=[100,100,140,140])
  s,m=self.scene([dict(cat,confidence=.99,subject_type='Dog')])
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[person,cat]):result=s.detect(np.zeros((300,300,3),np.uint8))
  self.assertEqual(result,[person,cat])
 def test_low_detector_score_still_needs_strong_type(self):
  s,m=self.scene([dict(subject_type='Dog',confidence=.16,box=[100,100,140,140])]);m.resolve_track_type.return_value='Unknown'
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((300,300,3),np.uint8)),[])
 def test_strong_type_corrects_weak_detector_species(self):
  s,m=self.scene([dict(subject_type='Dog',confidence=.16,box=[100,100,140,140])])
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):result=s.detect(np.zeros((300,300,3),np.uint8))
  self.assertEqual(result[0]['subject_type'],'Cat');self.assertEqual(result[0]['confidence'],.16)
 def test_person_coverage_still_blocks_animals(self):
  person=dict(subject_type='Person',confidence=.9,box=[0,0,150,200]);s,m=self.scene([dict(subject_type='Cat',confidence=.8,box=[50,50,90,90])])
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[person]):self.assertEqual(s.detect(np.zeros((300,300,3),np.uint8)),[person])
  m.animal_type_embedding.assert_not_called()
 def test_previous_cat_is_search_hint_and_cannot_supply_type(self):
  s,m=self.scene();s.recent=[(0,dict(subject_type='Cat',confidence=.9,box=[200,200,240,240]))];s.index=1
  proposal=dict(subject_type='Dog',confidence=.4,box=[190,200,230,240])
  def raw(frame,region=None):
   found=[proposal] if region else [];s.raw_cache[tuple(region) if region else None]=found;return found
  s.raw_detail_detections.side_effect=raw;m.resolve_track_type.return_value='Unknown'
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((500,500,3),np.uint8)),[])
 def test_expired_hint_is_not_searched(self):
  s,m=self.scene();s.index=3;s.recent=[(0,dict(subject_type='Cat',confidence=.9,box=[200,200,240,240]))]
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):s.detect(np.zeros((500,500,3),np.uint8))
  self.assertEqual(s.raw_detail_detections.call_count,1);self.assertEqual(s.recent,[])
 def test_native_size_floor_not_bypassed(self):
  s,m=self.scene([dict(subject_type='Cat',confidence=.9,box=[20,20,30,30])])
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):self.assertEqual(s.detect(np.zeros((300,300,3),np.uint8)),[])
  m.animal_type_embedding.assert_not_called()
 def test_focused_searches_bounded_to_two(self):
  full=[dict(subject_type='Cat',confidence=.5,box=[a,100,a+30,140]) for a in (50,150,250,350)];s,m=self.scene(full);m.resolve_track_type.return_value='Unknown'
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]):s.detect(np.zeros((500,500,3),np.uint8))
  regions=[call.args[1] for call in s.raw_detail_detections.call_args_list if len(call.args)>1]
  self.assertEqual(len(regions),2);self.assertTrue(all(r[2]-r[0]==256 and r[3]-r[1]==256 for r in regions))
 def test_moderate_view_cannot_become_next_strong_search_hint(self):
  s,m=self.scene([dict(subject_type='Dog',confidence=.2,box=[190,200,230,240])]);s.index=1
  s.previous_strong=[(0,dict(subject_type='Cat',box=[220,200,260,240]))];m.resolve_track_type.return_value='Unknown'
  ranks=[dict(subject_type='Cat',score=.7,independent_similarities=[.7,.6]),dict(subject_type='Dog',score=.2),dict(subject_type='Person',score=.1)]
  with patch.object(DetailedAnimalSceneModels,'detect',return_value=[]),patch('subject_type_check.rank_types',return_value=ranks):result=s.detect(np.zeros((500,500,3),np.uint8))
  self.assertEqual(len(result),1);self.assertTrue(result[0]['detail_temporal_candidate']);self.assertEqual(s.previous_strong,[])

if __name__=='__main__':unittest.main()
