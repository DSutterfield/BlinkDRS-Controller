import unittest,json
from unittest.mock import patch
import test_identities
from identity_store import connect,create_identity,get_clip_identities,assign_identity
from identity_analysis import publish_detections
from refresh_dog_suggestions import refresh_suggestions

class DogRefreshTests(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp
    def prepare(self):
        self.iid=create_identity(self.db,'Duke','Dog')['identity_id']
        item=dict(key='one',subject_type='Dog',confidence=.9,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'original',embedding=[1,0],embedding_model='test-model',quality_score=.8)
        publish_detections(self.db,1,'hash',[item])
        self.did=get_clip_identities(self.db,1)[0]['detection_id']
    def test_dry_run_then_apply_is_unconfirmed_and_idempotent(self):
        self.prepare()
        with patch('refresh_dog_suggestions.recommend_identity',return_value=self.iid):
            dry=refresh_suggestions(self.db,self.iid);self.assertEqual(len(dry['eligible']),1)
            self.assertIsNone(get_clip_identities(self.db,1)[0]['identity_id'])
            result=refresh_suggestions(self.db,self.iid,dry_run=False);self.assertEqual(len(result['updated']),1)
            row=get_clip_identities(self.db,1)[0];self.assertEqual(row['identity_id'],self.iid);self.assertFalse(row['confirmed'])
            self.assertEqual(refresh_suggestions(self.db,self.iid,dry_run=False)['updated'],[])
        with connect(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT crop_jpeg FROM identity_evidence').fetchone()[0],b'original')
    def test_concurrent_human_confirmation_is_preserved(self):
        self.prepare()
        def recognize(c,item,cid):assign_identity(self.db,cid,None,'Dog',self.did);return self.iid
        with patch('refresh_dog_suggestions.recommend_identity',side_effect=recognize):
            result=refresh_suggestions(self.db,self.iid,dry_run=False)
        self.assertEqual(result['skipped_changed'],1);row=get_clip_identities(self.db,1)[0];self.assertTrue(row['confirmed']);self.assertIsNone(row['identity_id'])
    def test_concurrent_gallery_change_skips_suggestion(self):
        self.prepare()
        def recognize(c,item,cid):
            with connect(self.db) as other:other.execute('INSERT INTO identity_samples(identity_id,embedding,model_version,quality_score) VALUES(?,?,?,?)',(self.iid,'[1,0]','test-model',.8))
            return self.iid
        with patch('refresh_dog_suggestions.recommend_identity',side_effect=recognize):result=refresh_suggestions(self.db,self.iid,dry_run=False)
        self.assertEqual(result['skipped_changed'],1);self.assertIsNone(get_clip_identities(self.db,1)[0]['identity_id'])
    def test_other_dog_predictions_and_confirmations_untouched(self):
        self.prepare();other=create_identity(self.db,'Stubby','Dog')['identity_id']
        with patch('refresh_dog_suggestions.recommend_identity',return_value=other):self.assertEqual(refresh_suggestions(self.db,self.iid,dry_run=False)['updated'],[])
        assign_identity(self.db,1,self.iid,'Dog',self.did)
        with patch('refresh_dog_suggestions.recommend_identity') as recognize:
            self.assertEqual(refresh_suggestions(self.db,self.iid,dry_run=False)['examined'],0);recognize.assert_not_called()
    def test_invalid_bounds_and_non_dog_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):refresh_suggestions(self.db,self.iid,501)
        cat=create_identity(self.db,'Cat','Cat')['identity_id']
        with self.assertRaises(ValueError):refresh_suggestions(self.db,cat)
if __name__=='__main__':unittest.main()
