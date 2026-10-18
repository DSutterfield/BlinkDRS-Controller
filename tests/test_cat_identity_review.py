import unittest,json
from unittest.mock import patch
from types import SimpleNamespace
import test_identities
from identity_store import connect,create_identity,assign_identity
from identity_analysis import ensure_analysis,publish_detections
from cat_identity_review import suggest,review_suggestions
from identity_api import install_identity_api
from fastapi import FastAPI
from fastapi.testclient import TestClient

MODEL='dinov2-small-cls-v1:test'
V=[1.]+[0.]*383
O=[0.,1.]+[0.]*382

class LeviReviewTests(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp

    def prepare(self):
        self.levi=create_identity(self.db,'Levi','Cat')['identity_id']
        self.other=create_identity(self.db,'Other Cat','Cat')['identity_id']
        with connect(self.db) as c:
            ensure_analysis(c)
            for cid in range(3,9):
                c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present) VALUES(?,?,?,'2026-10-15',1)",(cid,f'{cid}.mp4',f'{cid}.mp4'))
                iid=self.levi if cid<6 else self.other;v=V if cid<6 else O
                did=c.execute("INSERT INTO clip_detections(catalog_id,identity_id,subject_type,confirmed) VALUES(?,?,'Cat',1)",(cid,iid)).lastrowid
                c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) VALUES(?,?,?,?,?)',(iid,did,json.dumps(v),MODEL,1))
        item=dict(key='cat',subject_type='Cat',confidence=.9,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'jpeg',embedding=V,embedding_model=MODEL,quality_score=1)
        with patch('identity_analysis.match_identity',return_value=(None,None)):
            publish_detections(self.db,1,'hash',[item])
        with connect(self.db) as c:
            row=c.execute('SELECT d.*,e.embedding,e.embedding_model,e.quality_score FROM clip_detections d JOIN identity_evidence e USING(detection_id) WHERE d.catalog_id=1').fetchone()
        return dict(row)

    def ranks(self,**changes):
        top=dict(identity_id=self.levi,score=.8,nearest=.8);top.update(changes)
        return [top,dict(identity_id=self.other,score=.1)]

    def test_eligible_levi_suggestion_does_not_assign_or_train(self):
        item=self.prepare()
        with connect(self.db) as c,patch('cat_identity_review.rank_candidates',return_value=self.ranks()):
            before=c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0]
            self.assertEqual(suggest(c,item,1)['suggested_identity_id'],self.levi)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],before)
            row=c.execute('SELECT identity_id,confirmed FROM clip_detections WHERE detection_id=?',(item['detection_id'],)).fetchone()
            self.assertEqual(tuple(row),(None,0))

    def test_confirmed_named_wrong_species_and_unusable_images_are_excluded(self):
        item=self.prepare()
        with connect(self.db) as c:
            for changes in (dict(confirmed=1),dict(identity_id=self.other),dict(subject_type='Dog'),dict(quality_score=.1),dict(quality_score=None),dict(embedding_model='wrong'),dict(embedding='null'),dict(embedding=json.dumps([0]*384)),dict(embedding=json.dumps([float('nan')]*384)),dict(embedding=json.dumps([1,0]))):
                self.assertIsNone(suggest(c,dict(item,**changes),1))

    def test_weak_or_different_top_candidate_is_not_suggested(self):
        item=self.prepare()
        with connect(self.db) as c:
            for ranks in ([],self.ranks(score=.59),self.ranks(score=.29),self.ranks(nearest=.54),self.ranks(identity_id=self.other)):
                with patch('cat_identity_review.rank_candidates',return_value=ranks):self.assertIsNone(suggest(c,item,1))

    def test_two_distinct_other_clips_required_and_duplicates_do_not_count(self):
        item=self.prepare()
        with connect(self.db) as c,patch('cat_identity_review.rank_candidates',return_value=self.ranks()):
            self.assertIsNotNone(suggest(c,item,3))
            c.execute('DELETE FROM identity_samples WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id=4)')
            c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) SELECT identity_id,detection_id,embedding,model_version,quality_score FROM identity_samples WHERE identity_id=?',(self.levi,))
            self.assertIsNone(suggest(c,item,3))

    def test_confirmed_unnamed_cat_veto_and_human_correction(self):
        item=self.prepare()
        with connect(self.db) as c:
            did=c.execute("INSERT INTO clip_detections(catalog_id,subject_type,confirmed) VALUES(2,'Cat',1)").lastrowid
            c.execute('INSERT INTO identity_evidence VALUES(?,?,?,?,?,?,?,?,?)',(did,2,'unknown','Cat',b'jpeg',json.dumps(V),MODEL,1,None))
        with patch('cat_identity_review.rank_candidates',return_value=self.ranks()):self.assertEqual(review_suggestions(self.db,1),[])
        assign_identity(self.db,1,None,'Cat',item['detection_id'])
        self.assertEqual(review_suggestions(self.db,1),[])

    def test_endpoint_returns_review_fields_until_explicit_confirmation(self):
        item=self.prepare();app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db))
        client=TestClient(app);url='/api/v1/clips/catalog/1/identity-suggestions'
        with patch('cat_identity_review.rank_candidates',return_value=self.ranks()):
            result=client.get(url);self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()[0]['suggested_name'],'Levi')
        self.assertEqual(client.get('/api/v1/clips/catalog/999/identity-suggestions').status_code,404)
        response=client.put('/api/v1/clips/catalog/1/identities',json=dict(identity_id=self.levi,subject_type='Cat',detection_id=item['detection_id']))
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()[0]['confirmed'])
        self.assertEqual(client.get(url).json(),[])

    def test_other_household_cat_is_suggested_only_with_own_support(self):
        item=self.prepare()
        item['embedding']=json.dumps(O)
        ranks=[dict(identity_id=self.other,score=.8,nearest=.8),dict(identity_id=self.levi,score=.1)]
        with connect(self.db) as c,patch('cat_identity_review.rank_candidates',return_value=ranks):
            result=suggest(c,item,1)
            self.assertEqual(result['suggested_identity_id'],self.other)
            self.assertEqual(result['suggested_name'],'Other Cat')
            c.execute('UPDATE identities SET active=0 WHERE identity_id=?',(self.other,))
            self.assertIsNone(suggest(c,item,1))

    def test_other_cat_with_only_one_teacher_is_not_suggested(self):
        item=self.prepare();item['embedding']=json.dumps(O)
        ranks=[dict(identity_id=self.other,score=.8,nearest=.8),dict(identity_id=self.levi,score=.1)]
        with connect(self.db) as c,patch('cat_identity_review.rank_candidates',return_value=ranks):
            # Remove enough independent teachers to require abstention.
            c.execute('DELETE FROM identity_samples WHERE detection_id IN (SELECT detection_id FROM clip_detections WHERE catalog_id IN (6,7))')
            self.assertIsNone(suggest(c,item,1))
