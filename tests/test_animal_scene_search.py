import unittest
from unittest.mock import Mock,patch
from animal_scene_search import AnimalSceneModels

class SearchTests(unittest.TestCase):
 def test_original_people_and_vehicles_and_sample_schedule(self):
  people=[dict(subject_type='Person',confidence=.75,box=[0,0,100,200]),dict(subject_type='Vehicle',confidence=.9,box=[200,0,300,100])]
  base=Mock();base.detect.return_value=people
  search=AnimalSceneModels(base);search.grid=Mock(return_value=[])
  for i in range(8):
   self.assertEqual(search.detect(None),people)
  sizes=[c.args[2] for c in search.grid.call_args_list]
  self.assertEqual(sizes,[384,192,384,384,384,384,192,384,384,384])
 def test_all_scene_runs_despite_existing_animal(self):
  base=Mock();base.detect.return_value=[dict(subject_type='Dog',confidence=.8,box=[0,0,50,50])]
  s=AnimalSceneModels(base)
  s.grid=Mock(side_effect=[[dict(subject_type='Cat',confidence=.7,box=[100,100,120,140])],[]])
  self.assertEqual([d['subject_type'] for d in s.detect(None)],['Dog','Cat'])
 def test_species_overlap_deduplication_and_threshold(self):
  base=Mock();base.detect.return_value=[];s=AnimalSceneModels(base)
  s.grid=Mock(side_effect=[[dict(subject_type='Dog',confidence=.6,box=[0,0,50,50]),dict(subject_type='Cat',confidence=.8,box=[0,0,50,50]),dict(subject_type='Cat',confidence=.54,box=[100,100,150,150])],[]])
  import numpy as np
  base.confirm_weak_animal_proposal.return_value=False
  self.assertEqual([d['subject_type'] for d in s.detect(np.zeros((200,200,3),np.uint8))],['Cat'])
 def test_worker_uses_new_search_per_clip(self):
  import identity_worker as w
  models=Mock();job=dict(video_path='test.mp4',catalog_id=1)
  with patch.object(w,'archive_video',return_value='video'),patch.object(w,'file_hash',return_value='hash'),patch('subject_type_learning.load_references',return_value=[]),patch.object(w,'analyze_video',return_value=[]) as analyze,patch.object(w,'publish_detections') as publish:
   w.process_one('db','archive',job,models)
   self.assertIsInstance(analyze.call_args.args[1],AnimalSceneModels)
   self.assertIs(analyze.call_args.args[1].models,models)
   publish.assert_called_once_with('db',1,'hash',[])
