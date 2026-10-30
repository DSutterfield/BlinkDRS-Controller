import unittest
from animal_track_cleanup import fragments_match,consolidate
class Models:
 def resolve_track_type(self,t):return t['subject_type']
def track(start,end,box=None,kind='Cat',vector=None):
 box=box or [0,0,100,100]
 return dict(key=f'{start}:{kind}:{box}',first_seen_seconds=start,last_seen_seconds=end,box=box,subject_type=kind,embedding=vector or [1.]+[0.]*383,embedding_model='dinov2-small-cls-v1:test',quality_score=1.,crop_jpeg=b'jpeg',confidence=.9,detector_model='test',sample_hits=1)
class CleanupTests(unittest.TestCase):
 def test_short_unique_fragment_merges(self):
  a,b=track(0,1),track(1.5,2,[5,0,105,100]);r,audit=consolidate([a,b],.5,Models());self.assertEqual(len(r),1);self.assertEqual(r[0]['last_seen_seconds'],2);self.assertEqual(r[0]['key'],a['key']);self.assertEqual(len(audit),1)
 def test_simultaneous_animals_not_merged(self):self.assertFalse(fragments_match(track(0,2),track(1,3),1))
 def test_different_appearance_not_merged(self):self.assertFalse(fragments_match(track(0,1),track(1.5,2,vector=[0.,1.]+[0.]*382),1))
 def test_far_apart_not_merged(self):self.assertFalse(fragments_match(track(0,1),track(1.5,2,[200,0,300,100]),1))
 def test_long_gap_not_merged(self):self.assertFalse(fragments_match(track(0,1),track(4,5),1))
 def test_conflicting_species_not_merged(self):self.assertFalse(fragments_match(track(0,1),track(1.5,2,kind='Dog'),1))
 def test_ambiguous_counterparts_not_chosen(self):
  r,audit=consolidate([track(0,1),track(1.5,2),track(1.5,2,[1,0,101,100])],1,Models());self.assertEqual(len(r),3);self.assertEqual(audit,[])
 def test_supported_short_gap_overlap_merges_across_pose(self):
  import math
  a=track(0,0);b=track(.8,.8,[5,0,105,100],vector=[.75,math.sqrt(1-.75**2)]+[0.]*382);self.assertTrue(fragments_match(a,b,.5))
 def test_low_similarity_overlap_not_merged(self):
  import math
  a=track(0,0);b=track(.8,.8,[5,0,105,100],vector=[.6,.8]+[0.]*382);self.assertFalse(fragments_match(a,b,.5))
 def test_people_not_merged(self):self.assertFalse(fragments_match(track(0,1,kind='Person'),track(1.5,2,kind='Person'),1))
 def test_first_box_used_for_moving_later_fragment(self):
  b=track(1.5,5,[300,0,400,100]);b['first_box']=[5,0,105,100];self.assertTrue(fragments_match(track(0,1),b,1))
if __name__=='__main__':unittest.main()
