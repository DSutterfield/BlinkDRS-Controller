import unittest
from identity_worker import retain_view
from identity_analysis import publish_detections
from sightings import track_views,track_view_image
import test_identities
from identity_store import get_clip_identities,create_identity,assign_identity
class MultiViewTests(unittest.TestCase):
 setUp=test_identities.IdentityTests.setUp
 def test_diverse_views_retained_and_duplicates_bounded(self):
  t={};retain_view(t,0,b'a',[1,0],'m',.8);retain_view(t,1,b'b',[1,0],'m',.7);retain_view(t,2,b'c',[0,1],'m',.7)
  self.assertEqual(len(t['views']),2)
  for i in range(3,30):retain_view(t,i,b'x',[i,30-i],'m',.9)
  self.assertLessEqual(len(t['views']),4)
 def test_temporally_separated_similar_views_are_kept_for_review(self):
  t={};retain_view(t,0,b'early',[1,0],'m',.8);retain_view(t,3,b'later',[1,0],'m',.7)
  self.assertEqual([v['crop_jpeg'] for v in t['views']],[b'early',b'later'])
 def test_store_views_and_protect_confirmed(self):
  item=dict(key='one',subject_type='Cat',confidence=.8,first_seen_seconds=0,last_seen_seconds=2,detector_model='test',crop_jpeg=b'jpeg',embedding=[1,0],embedding_model='m',quality_score=.8,views=[dict(seconds=0,crop_jpeg=b'first',embedding=[1,0],embedding_model='m',quality_score=.8),dict(seconds=2,crop_jpeg=b'second',embedding=[0,1],embedding_model='m',quality_score=.7)])
  publish_detections(self.db,1,'hash',[item]);did=get_clip_identities(self.db,1)[0]['detection_id'];views=track_views(self.db,1,did);self.assertEqual(len(views),2);self.assertEqual(track_view_image(self.db,1,did,views[0]['view_id']),b'first')
  with self.assertRaises(LookupError):track_view_image(self.db,2,did,views[0]['view_id'])
  iid=create_identity(self.db,'Lola','Cat')['identity_id'];assign_identity(self.db,1,iid,'Cat',did);publish_detections(self.db,1,'hash',[]);self.assertEqual(len(track_views(self.db,1,did)),2)
if __name__=='__main__':unittest.main()
