import unittest,tempfile,cv2,numpy as np
from unittest.mock import Mock,patch
from types import SimpleNamespace
from pathlib import Path
from animal_type_temporal import nearby_small_boxes,temporal_animal_match
from test_subject_type_check import vector,MODEL,gallery
from subject_type_check import resolve_track_type

class TemporalAnimalTests(unittest.TestCase):
 def args(self):
  track=dict(subject_type='Cat',embedding=None,first_seen_seconds=0,last_seen_seconds=0,box=[0,0,30,30],type_embedding=vector(1),type_embedding_model=MODEL,type_quality_score=1)
  return [track,dict(subject_type='Cat'),[35,0,65,30],.5,.5,None,vector(1),MODEL,1,SimpleNamespace(resolve_track_type=lambda t:'Cat')]
 def test_adjacent_small_type_only_animal_can_match(self):self.assertTrue(temporal_animal_match(*self.args()))
 def test_native_size_and_distance_are_bounded(self):
  for box in ([0,0,15,30],[0,0,48,48],[500,0,530,30]):self.assertFalse(nearby_small_boxes([0,0,30,30],box))
 def test_individual_features_prevent_temporal_fallback(self):
  args=self.args();args[5]=vector(1);self.assertFalse(temporal_animal_match(*args))
  args=self.args();args[0]['embedding']=vector(1);self.assertFalse(temporal_animal_match(*args))
 def test_old_or_same_frame_track_rejected(self):
  for seconds in (0,.56):
   args=self.args();args[3]=seconds;self.assertFalse(temporal_animal_match(*args))
 def test_different_type_feature_model_rejected(self):
  args=self.args();args[7]='other';self.assertFalse(temporal_animal_match(*args))
 def test_inconsistent_appearance_rejected(self):
  args=self.args();args[6]=vector(2);self.assertFalse(temporal_animal_match(*args))
 def test_prior_track_must_pass_original_type_verifier(self):
  args=self.args();args[-1]=SimpleNamespace(resolve_track_type=lambda t:'Unknown');self.assertFalse(temporal_animal_match(*args))
 def test_species_conflict_rejected(self):
  args=self.args();args[1]['subject_type']='Dog';self.assertFalse(temporal_animal_match(*args))
 def replay(self,rows):
  from identity_worker import analyze_video
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'sample.mp4';out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),2,(96,64))
   for idx in range(len(rows)):out.write(np.full((64,96,3),idx*30,dtype=np.uint8))
   out.release();ds=iter(rows)
   later=[.8*a+.6*b for a,b in zip(vector(1),vector(3))]
   m=SimpleNamespace(cv2=cv2,pet_version=MODEL,detector_version='test',detect=lambda f:next(ds),embedding=lambda c,k:(None,None,0),animal_type_embedding=lambda c:(later if c.mean()>10 else vector(1),MODEL,1),resolve_animal_type=lambda *a,**kw:'Cat',resolve_track_type=lambda t:resolve_track_type(gallery(),t))
   with patch('identity_worker.time.sleep'):return analyze_video(path,m)
 def cat(self,box,temporal=False):return dict(subject_type='Cat',confidence=.2,box=box,detail_temporal_candidate=temporal)
 def test_temporal_proposal_cannot_start_a_track(self):self.assertEqual(self.replay([[],[self.cat([35,0,65,30],True)]]),[])
 def test_unique_small_animal_track_retains_both_views(self):
  result=self.replay([[self.cat([0,0,30,30])],[self.cat([35,0,65,30],True)]])
  self.assertEqual(len(result),1);self.assertEqual(result[0]['sample_hits'],2);self.assertEqual(result[0]['subject_type'],'Cat');self.assertIsNone(result[0]['embedding']);self.assertEqual(len(result[0]['type_views']),2)
 def test_two_nearby_animals_make_temporal_proposal_ambiguous(self):
  result=self.replay([[self.cat([0,0,30,30]),self.cat([40,0,70,30])],[self.cat([20,0,50,30],True)]])
  self.assertEqual(len(result),2);self.assertTrue(all(t.get('sample_hits',1)==1 for t in result))

if __name__=='__main__':unittest.main()
