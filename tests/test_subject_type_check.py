import unittest
from unittest.mock import patch
from subject_type_check import animal_type_supported,rank_types,resolve_animal_type,in_person_region

MODEL='dinov2-small-cls-v1:test'
def vector(index):return [float(i==index) for i in range(384)]
def gallery():
 return [dict(subject_type=kind,model_version=MODEL,quality_score=.8,vector=vector(i),catalog_id=100*i+j,crop_hash=f'{kind}-{j}') for i,kind in enumerate(('Person','Cat','Dog')) for j in range(6)]
class TypeCheckTests(unittest.TestCase):
 def test_person_rejected_but_real_animals_kept(self):
  refs=gallery()
  self.assertFalse(animal_type_supported(refs,'Dog',vector(0),MODEL))
  self.assertFalse(animal_type_supported(refs,'Cat',vector(0),MODEL))
  self.assertTrue(animal_type_supported(refs,'Dog',vector(2),MODEL))
  self.assertTrue(animal_type_supported(refs,'Cat',vector(1),MODEL))
  self.assertTrue(animal_type_supported(refs,'Person',vector(2),MODEL))
 def test_strong_cat_dog_disagreement_is_rejected(self):
  self.assertFalse(animal_type_supported(gallery(),'Dog',vector(1),MODEL))
  self.assertFalse(animal_type_supported(gallery(),'Cat',vector(2),MODEL))
 def test_species_corrections_and_person_suppression(self):
  self.assertEqual(resolve_animal_type(gallery(),'Dog',vector(1),MODEL),'Cat')
  self.assertEqual(resolve_animal_type(gallery(),'Cat',vector(2),MODEL),'Dog')
  self.assertIsNone(resolve_animal_type(gallery(),'Dog',vector(0),MODEL))
  with patch('subject_type_check.rank_types',return_value=[dict(subject_type='Person',score=.57,independent_similarities=[.52,.48]),dict(subject_type='Cat',score=.46),dict(subject_type='Dog',score=-.03)]):
   self.assertEqual(resolve_animal_type(gallery(),'Dog',vector(0),MODEL),'Unknown')
 def test_missing_invalid_low_quality_or_wrong_model_abstains(self):
  for refs,v,model in [( [],vector(0),MODEL),(gallery(),None,MODEL),(gallery(),[0]*384,MODEL),(gallery(),[float('nan')]*384,MODEL),(gallery(),vector(0),'wrong')]:
   self.assertFalse(animal_type_supported(refs,'Dog',v,model))
  refs=[dict(r,quality_score=.1) for r in gallery()]
  self.assertFalse(animal_type_supported(refs,'Dog',vector(0),MODEL))
 def test_duplicate_clip_or_image_cannot_provide_two_supports(self):
  for field in ('catalog_id','crop_hash'):
   refs=[dict(r,**{field:'same'}) if r['subject_type']=='Person' else r for r in gallery()]
   self.assertFalse(animal_type_supported(refs,'Dog',vector(0),MODEL))
 def test_score_margin_and_nearest_guards(self):
  top=dict(subject_type='Person',score=.9,independent_similarities=[.8,.7]);rest=[dict(subject_type='Dog',score=.1),dict(subject_type='Cat',score=0)]
  with patch('subject_type_check.rank_types',return_value=[top,*rest]):self.assertFalse(animal_type_supported([], 'Dog',vector(0),MODEL))
  for change in [dict(score=.79),dict(independent_similarities=[.9]),dict(independent_similarities=[.8,.49])]:
   with patch('subject_type_check.rank_types',return_value=[dict(top,**change),*rest]):self.assertFalse(animal_type_supported([],'Dog',vector(0),MODEL))
  with patch('subject_type_check.rank_types',return_value=[dict(top,score=.85),dict(subject_type='Dog',score=.6),rest[1]]):self.assertFalse(animal_type_supported([],'Dog',vector(0),MODEL))
 def test_person_region_requires_strong_same_frame_containment(self):
  animal=dict(subject_type='Dog',confidence=.9,box=[20,20,40,40])
  person=dict(subject_type='Person',confidence=.9,box=[0,0,60,60])
  self.assertTrue(in_person_region(animal,[person]))
  self.assertFalse(in_person_region(animal,[dict(person,confidence=.69)]))
  self.assertFalse(in_person_region(animal,[dict(person,box=[30,0,60,60])]))
  self.assertFalse(in_person_region(animal,[dict(person,box=[45,0,60,60])]))
  self.assertFalse(in_person_region(dict(animal,box=[20,20,20,40]),[person]))
 def test_person_priority_needs_crop_evidence_and_spatial_support(self):
  top=dict(subject_type='Person',score=.6,independent_similarities=[.55,.48])
  rest=[dict(subject_type='Cat',score=.45),dict(subject_type='Dog',score=-.05)]
  with patch('subject_type_check.rank_types',return_value=[top,*rest]):
   self.assertEqual(resolve_animal_type([],'Dog',vector(0),MODEL),'Unknown')
   self.assertIsNone(resolve_animal_type([],'Dog',vector(0),MODEL,person_region=True))
  for change in [dict(score=.54),dict(score=.53),dict(independent_similarities=[.8]),dict(independent_similarities=[.8,.44])]:
   with patch('subject_type_check.rank_types',return_value=[dict(top,**change),*rest]):
    self.assertEqual(resolve_animal_type([],'Dog',vector(0),MODEL,person_region=True),'Unknown')
  self.assertEqual(resolve_animal_type(gallery(),'Dog',vector(2),MODEL,person_region=True),'Dog')
  self.assertEqual(resolve_animal_type(gallery(),'Cat',vector(1),MODEL,person_region=True),'Cat')
 def test_worker_reuses_pet_embedding_and_keeps_person_detection(self):
  import tempfile
  from pathlib import Path
  from types import SimpleNamespace
  import cv2,numpy as np
  from identity_worker import analyze_video
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'sample.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),4,(64,64))
   for _ in range(4):out.write(np.zeros((64,64,3),dtype=np.uint8))
   out.release()
   check=unittest.mock.Mock(return_value=None)
   embedding=unittest.mock.Mock(return_value=(vector(0),MODEL,.8))
   models=SimpleNamespace(cv2=cv2,detector_version='test',embedding=embedding,resolve_animal_type=check,
     detect=lambda frame:[dict(subject_type='Dog',confidence=.9,box=[0,0,60,60]),dict(subject_type='Person',confidence=.9,box=[0,0,60,60])])
   with patch('identity_worker.time.sleep'):tracks=analyze_video(path,models)
   self.assertEqual({t['subject_type'] for t in tracks},{'Person'})
   self.assertEqual(embedding.call_count,8);self.assertEqual(check.call_count,4)
   self.assertEqual(check.call_args.args,('Dog',vector(0),MODEL))
   self.assertEqual(check.call_args.kwargs,{'person_region':True})
   self.assertEqual(embedding.call_args_list[0].args[1],'Person')
   check.return_value='Cat'
   with patch('identity_worker.time.sleep'):tracks=analyze_video(path,models)
   self.assertEqual({t['subject_type'] for t in tracks},{'Person','Cat'})
   self.assertTrue(all(t['embedding_model']==MODEL for t in tracks))
   check.return_value='Unknown'
   with patch('identity_worker.time.sleep'):tracks=analyze_video(path,models)
   self.assertEqual({t['subject_type'] for t in tracks},{'Person','Unknown'})
   self.assertTrue(all(t['crop_jpeg'] for t in tracks))
   embedding.side_effect=lambda crop,kind:(vector(0 if kind=='Person' else 2),MODEL,.8)
   models.resolve_animal_type=lambda kind,v,m,**kwargs:resolve_animal_type(gallery(),kind,v,m,**kwargs)
   with patch('identity_worker.time.sleep'):tracks=analyze_video(path,models)
   self.assertEqual({t['subject_type'] for t in tracks},{'Person','Dog'})
if __name__=='__main__':unittest.main()

