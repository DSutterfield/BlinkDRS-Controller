from unittest.mock import patch
from types import SimpleNamespace
from test_animal_track_cleanup import track
from animal_track_cleanup import background_unknown
import unittest
class BackgroundTests(unittest.TestCase):
 def setUp(self):
  self.t=track(0,0);self.t['subject_type']='Unknown';self.m=SimpleNamespace(pet_version='dinov2-small-cls-v1:test',cat_references=[],background_vectors=[],type_references=[])
 def check(self,t=None,scores=None,ranks=None):
  with patch('cat_foreground.foreground_score',side_effect=scores or [.3]*10),patch('subject_type_check.rank_types',return_value=ranks or [{'subject_type':'Cat','score':.6}]):return background_unknown(t or self.t,self.m)
 def test_consistent_background_unknown_removed(self):self.assertTrue(self.check())
 def test_supported_cat_retained(self):self.assertFalse(self.check(track(0,0)))
 def test_dog_retained(self):self.assertFalse(self.check(track(0,0,kind='Dog')))
 def test_other_species_evidence_retained(self):self.assertFalse(self.check(ranks=[{'subject_type':'Dog','score':.6}]))
 def test_any_foreground_glimpse_retained(self):
  self.t['views']=[dict(embedding=self.t['embedding'],embedding_model=self.m.pet_version,quality_score=1.,seconds=1.)];self.assertFalse(self.check(scores=[.3,.7]))
 def test_missing_features_retained(self):self.t['embedding']=None;self.assertFalse(self.check())
 def test_missing_background_model_retained(self):self.assertFalse(self.check(scores=[None]))
 def test_uncertain_view_near_supported_cat_retained(self):
  from animal_track_cleanup import cleanup
  a=track(0,1);b=track(1.5,1.5,vector=[0.,1.]+[0.]*382);b['subject_type']='Unknown'
  with patch('cat_foreground.foreground_score',return_value=.3),patch('subject_type_check.rank_types',return_value=[{'subject_type':'Cat','score':.6}]):
   result,audit=cleanup([a,b],.5,self.m)
  self.assertEqual(len(result),2);self.assertEqual(audit['background_unknowns'],[])
if __name__=='__main__':unittest.main()
