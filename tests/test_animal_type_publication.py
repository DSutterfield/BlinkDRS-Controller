"""A confirmed Type must survive publication even with a weak detector score."""
import unittest
import test_identity_analysis
from identity_analysis import publish_detections,queue_clip,crop_image
from identity_store import connect,get_clip_identities

class AnimalTypePublicationTests(unittest.TestCase):
 setUp=test_identity_analysis.AnalysisTests.setUp
 def test_small_cat_with_low_detector_score_keeps_type_without_identity(self):
  queue_clip(self.db,1)
  item=dict(key='small-confirmed-type',subject_type='Cat',confidence=.16,first_seen_seconds=5.62,last_seen_seconds=5.62,detector_model='detail-test',crop_jpeg=b'native-cat-crop',embedding=None,embedding_model=None,quality_score=0,views=[])
  publish_detections(self.db,1,'fixture-video',[item]);row=get_clip_identities(self.db,1)[0]
  self.assertEqual(row['subject_type'],'Cat');self.assertIsNone(row['identity_id']);self.assertFalse(row['confirmed'])
  self.assertEqual(crop_image(self.db,1,row['detection_id']),b'native-cat-crop')
  with connect(self.db) as conn:
   self.assertAlmostEqual(conn.execute('SELECT confidence FROM clip_detections').fetchone()[0],.16)
   self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
   self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_candidates').fetchone()[0],0)

if __name__=='__main__':unittest.main()
