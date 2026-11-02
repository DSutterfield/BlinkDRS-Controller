import unittest,os
from unittest.mock import patch
import test_name_first_review
from identity_store import connect,get_clip_identities,clip_identities,assign_identity,create_identity
from dog_review_cache import store

class DogPending(test_name_first_review.Pilot):
    # Reuse real isolated catalog setup and the existing regression suite.
    def test_missing_stale_ready_and_completed_unknown(self):
        self.prepare('Dog')
        with connect(self.db) as c:
            c.execute('DELETE FROM dog_review_cache');c.execute('DELETE FROM dog_review_requests')
            self.assertTrue(clip_identities(c,1)[0]['refresh_pending'])
            self.assertEqual(c.execute('SELECT count(*) FROM dog_review_requests').fetchone()[0],0)
        with patch('name_first_review.score',side_effect=AssertionError('Scoring on API thread')):
            self.assertTrue(get_clip_identities(self.db,1)[0]['refresh_pending'])
        with connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM dog_review_requests').fetchone()[0],1)
            store(c,self.did,self.proposal)
        r=get_clip_identities(self.db,1)[0]
        self.assertFalse(r['refresh_pending']);self.assertEqual(r['suggested_name'],'Duke')
        create_identity(self.db,'Stubby','Dog')
        r=get_clip_identities(self.db,1)[0]
        self.assertTrue(r['refresh_pending']);self.assertNotIn('suggested_name',r)
        with connect(self.db) as c:store(c,self.did,None)
        r=get_clip_identities(self.db,1)[0]
        self.assertFalse(r['refresh_pending']);self.assertNotIn('suggested_name',r)
        with connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM dog_review_requests').fetchone()[0],0)
            for table in ('identity_samples','subject_type_samples'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_exclusions_no_visible_and_missing_features_not_checking(self):
        self.prepare('Dog')
        with connect(self.db) as c:
            c.execute('DELETE FROM dog_review_cache')
            c.execute("INSERT INTO subject_analysis_exclusions(detection_id,crop_sha256) VALUES(?,'test')",(self.did,))
        self.assertFalse(get_clip_identities(self.db,1)[0]['refresh_pending'])
        with connect(self.db) as c:
            c.execute('DELETE FROM subject_analysis_exclusions');c.execute('INSERT INTO clip_unknown_motion(catalog_id) VALUES(1)')
        self.assertFalse(get_clip_identities(self.db,1)[0]['refresh_pending'])
        with connect(self.db) as c:
            c.execute('DELETE FROM clip_unknown_motion');c.execute('UPDATE identity_evidence SET embedding=NULL')
        self.assertFalse(get_clip_identities(self.db,1)[0]['refresh_pending'])
    def test_confirmation_not_pending(self):
        self.prepare('Dog');assign_identity(self.db,1,self.iid,'Dog',self.did)
        r=get_clip_identities(self.db,1)[0]
        self.assertTrue(r['confirmed']);self.assertFalse(r.get('refresh_pending',False));self.assertEqual(r['name'],'Duke')
