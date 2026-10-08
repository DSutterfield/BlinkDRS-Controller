import unittest
from unittest.mock import patch
import test_sightings
from identity_store import connect,assign_identity,get_clip_identities
from backfill_identity_candidates import backfill
class BackfillTests(test_sightings.SightingsTests):
 def test_concurrent_human_confirmation_skips_indexing(self):
  iid=self.prepare()
  with connect(self.db) as c:c.execute("UPDATE identity_evidence SET embedding_model='dinov2-small-cls-v1:test'")
  def rank(c,kind,model,v,cid):
   did=get_clip_identities(self.db,cid)[0]['detection_id'];assign_identity(self.db,cid,iid,'Cat',did)
   return [dict(identity_id=iid,score=.8)]
  with patch('backfill_identity_candidates.rank_candidates',side_effect=rank):result=backfill(self.db)
  self.assertEqual(result['skipped_changed'],2)
  self.assertTrue(all(get_clip_identities(self.db,cid)[0]['confirmed'] for cid in [1,2]))
if __name__=='__main__':unittest.main()
