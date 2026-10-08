import unittest
from unittest.mock import patch
from identity_candidates import dog_recommendation,rank_candidates
from identity_store import connect
from sightings import sightings
import test_sightings
class CandidateTests(test_sightings.SightingsTests):
 def test_possible_search_never_confirms(self):
  iid=self.prepare()
  with connect(self.db) as c:
   did=c.execute('SELECT detection_id FROM clip_detections WHERE catalog_id=1').fetchone()[0]
   c.execute('INSERT INTO identity_candidates VALUES(?,?,?,?,?)',(did,iid,1,.6,'test-possible'))
  result=sightings(self.db,'2026-10-03','2026-10-03',identity_id=iid,status='possible');self.assertEqual(result['total'],1);self.assertEqual(result['counts'][0]['possible_clips'],1);self.assertFalse(result['rows'][0]['confirmed']);self.assertIsNone(result['rows'][0]['identity_id'])
 def test_dog_recommendation_uses_dog_only_policy(self):
  with patch('dog_identity.recommend_identity',return_value=7) as policy:
   item=dict(subject_type='Dog')
   self.assertEqual(dog_recommendation(None,item,1),7);policy.assert_called_once_with(None,item,1)
 def test_model_space_guard(self):self.assertEqual(rank_candidates(None,'Cat','wrong-model',[1]*384),[])
if __name__=='__main__':unittest.main()
