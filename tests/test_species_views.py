import unittest
from unittest.mock import patch
from subject_type_check import resolve_track_type
from test_subject_type_check import gallery,vector,MODEL
class SpeciesViews(unittest.TestCase):
 def track(self):
  return dict(subject_type='Dog',quality_score=.8,embedding_model=MODEL,embedding=vector(2),crop_jpeg=b'primary',views=[])
 def view(self,t=0,jpeg=b'one',v=None):
  return dict(seconds=t,crop_jpeg=jpeg,quality_score=.8,embedding_model=MODEL,embedding=vector(2) if v is None else v)
 def moderate(self):
  return [dict(subject_type='Dog',score=.7,independent_similarities=[.7,.6]),dict(subject_type='Cat',score=.2),dict(subject_type='Person',score=.1)]
 def test_strong_single_kept_and_other_types_unchanged(self):
  self.assertEqual(resolve_track_type(gallery(),self.track()),'Dog')
  for kind in ('Unknown','Person','Vehicle'):
   self.assertEqual(resolve_track_type([],dict(subject_type=kind)),kind)
 def test_moderate_requires_two_distinct_separated_views(self):
  with patch('subject_type_check.rank_types',return_value=self.moderate()):
   for views in ([],[self.view()],[self.view(),self.view(1)],[self.view(),self.view(.4,b'two')]):
    self.assertEqual(resolve_track_type([],dict(self.track(),views=views)),'Unknown')
   self.assertEqual(resolve_track_type([],dict(self.track(),views=[self.view(),self.view(.5,b'two')])),'Dog')
 def test_invalid_low_quality_other_model_and_conflicting_view_do_not_support(self):
  with patch('subject_type_check.rank_types',return_value=self.moderate()):
   for change in (dict(seconds=float('nan')),dict(seconds=None),dict(quality_score=.1),dict(embedding_model='different'),dict(crop_jpeg=None)):
    self.assertEqual(resolve_track_type([],dict(self.track(),views=[self.view(),dict(self.view(1,b'two'),**change)])),'Unknown')
  mixed=dict(self.track(),embedding=vector(0),views=[self.view(),self.view(1,b'two',vector(1))])
  self.assertEqual(resolve_track_type(gallery(),mixed),'Unknown')
 def test_strong_single_cannot_override_a_strong_conflicting_view(self):
  t=dict(self.track(),views=[self.view(1,b'cat',vector(1))])
  self.assertEqual(resolve_track_type(gallery(),t),'Unknown')
 def test_worker_applies_final_guard_without_removing_crop(self):
  import tempfile,cv2,numpy as np
  from pathlib import Path
  from types import SimpleNamespace
  from identity_worker import analyze_video
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'sample.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),4,(64,64))
   for _ in range(4):out.write(np.zeros((64,64,3),dtype=np.uint8))
   out.release()
   models=SimpleNamespace(cv2=cv2,detector_version='test',detect=lambda f:[dict(subject_type='Dog',confidence=.9,box=[0,0,60,60])],embedding=lambda c,k:(vector(2),MODEL,.8),resolve_animal_type=lambda *a,**kw:'Dog',resolve_track_type=lambda t:resolve_track_type([],t))
   with patch('subject_type_check.rank_types',return_value=self.moderate()),patch('identity_worker.time.sleep'):
    tracks=analyze_video(path,models)
   self.assertEqual(len(tracks),1);self.assertEqual(tracks[0]['subject_type'],'Unknown');self.assertTrue(tracks[0]['crop_jpeg'])
 def test_uncertain_frames_share_track_and_require_final_species_evidence(self):
  import tempfile,cv2,numpy as np
  from pathlib import Path
  from types import SimpleNamespace
  from identity_worker import analyze_video
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'sample.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),2,(64,64))
   for i in range(4):out.write(np.full((64,64,3),i*30,dtype=np.uint8))
   out.release()
   responses=iter(['Unknown','Dog','Unknown','Dog'])
   models=SimpleNamespace(cv2=cv2,detector_version='test',detect=lambda f:[dict(subject_type='Dog',confidence=.9,box=[0,0,60,60])],embedding=lambda c,k:(vector(2+int(c.mean()>45)),MODEL,.8),resolve_animal_type=lambda *a,**kw:next(responses),resolve_track_type=lambda t:resolve_track_type([],t))
   with patch('subject_type_check.rank_types',return_value=self.moderate()),patch('identity_worker.time.sleep'):
    tracks=analyze_video(path,models)
   self.assertEqual(len(tracks),1);self.assertEqual(tracks[0]['sample_hits'],4)
   self.assertEqual(tracks[0]['subject_type'],'Dog')
   responses=iter(['Unknown']*4)
   models.resolve_animal_type=lambda *a,**kw:next(responses)
   with patch('subject_type_check.rank_types',return_value=[]),patch('identity_worker.time.sleep'):
    tracks=analyze_video(path,models)
   self.assertEqual(len(tracks),1);self.assertEqual(tracks[0]['subject_type'],'Unknown')
 def test_clear_species_views_survive_sharper_uncertain_views(self):
  from identity_worker import retain_view
  t={}
  retain_view(t,0,b'dog-one',vector(2),MODEL,.4,type_result='Dog')
  for i in range(1,5):retain_view(t,i*3,('uncertain'+str(i)).encode(),vector(3),MODEL,.9,type_result='Unknown')
  retain_view(t,15,b'dog-two',vector(4),MODEL,.3,type_result='Dog')
  self.assertEqual(len(t['views']),4)
  self.assertEqual(sum(v.get('type_result')=='Dog' for v in t['views']),2)
  with patch('subject_type_check.rank_types',return_value=self.moderate()):
   self.assertEqual(resolve_track_type([],dict(self.track(),views=t['views'])),'Dog')
 def test_conflicting_species_view_is_retained_for_final_veto(self):
  from identity_worker import retain_view
  t={}
  for i in range(4):retain_view(t,i*3,('dog'+str(i)).encode(),vector(2),MODEL,.8,type_result='Dog')
  retain_view(t,15,b'cat',vector(1),MODEL,.3,type_result='Cat')
  retain_view(t,18,b'dog-new',vector(2),MODEL,.95,type_result='Dog')
  self.assertTrue(any(v.get('type_result')=='Cat' for v in t['views']))
  self.assertEqual(resolve_track_type(gallery(),dict(self.track(),views=t['views'])),'Unknown')
if __name__=='__main__':unittest.main()
