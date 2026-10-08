import json
import unittest
from unittest.mock import patch
import test_identities
from identity_store import connect,create_identity,get_clip_identities
from identity_analysis import ensure_analysis,publish_detections
from dog_identity import recommend_identity,_supporting_clips

MODEL='dinov2-small-cls-v1:test'
VECTOR=[1.0]+[0.0]*383
OTHER=[0.0,1.0]+[0.0]*382

class DogTests(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp

    def prepare(self):
        self.duke=create_identity(self.db,'Duke','Dog')['identity_id']
        self.stubby=create_identity(self.db,'Stubby','Dog')['identity_id']
        with connect(self.db) as c:
            ensure_analysis(c)
            for cid in range(3,9):
                c.execute('INSERT INTO clips(id,filename,video_path,captured_at) VALUES(?,?,?,?)',(cid,f'{cid}.mp4',f'{cid}.mp4','2026-10-08'))
                iid=self.duke if cid<6 else self.stubby
                v=VECTOR if cid<6 else OTHER
                did=c.execute("INSERT INTO clip_detections(catalog_id,identity_id,subject_type,confirmed) VALUES(?,?,'Dog',1)",(cid,iid)).lastrowid
                c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) VALUES(?,?,?,?,?)',(iid,did,json.dumps(v),MODEL,.8))
        return dict(subject_type='Dog',embedding=VECTOR,embedding_model=MODEL,quality_score=.8)

    def ranks(self,iid=None,strong=True):
        iid=iid or self.duke
        return [dict(identity_id=iid,score=.9 if strong else .82,reference_clips=3,nearest=.85),dict(identity_id=self.stubby if iid==self.duke else self.duke,score=.1 if strong else .45)]

    def test_strong_single_view_needs_three_other_confirmed_clips(self):
        item=self.prepare()
        with connect(self.db) as c,patch('dog_identity.rank_candidates',return_value=self.ranks()):
            self.assertEqual(recommend_identity(c,item,1),self.duke)
            self.assertIsNone(recommend_identity(c,item,3))
            c.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding,model_version,quality_score) SELECT identity_id,detection_id,embedding,model_version,quality_score FROM identity_samples WHERE identity_id=?',(self.duke,))
            self.assertEqual(_supporting_clips(c,self.duke,MODEL,VECTOR,3,.75),2)
            self.assertIsNone(recommend_identity(c,item,3))

    def test_stable_time_separated_views_can_agree(self):
        item=self.prepare();item['views']=[dict(item,seconds=0),dict(item,seconds=1)]
        with connect(self.db) as c,patch('dog_identity.rank_candidates',return_value=self.ranks(strong=False)):
            self.assertEqual(recommend_identity(c,item,1),self.duke)
            item['views'][1]['seconds']=0
            self.assertIsNone(recommend_identity(c,item,1))
            item['views'][1]['seconds']=float('nan')
            self.assertIsNone(recommend_identity(c,item,1))

    def test_disagreeing_supported_views_abstain(self):
        item=self.prepare();item['views']=[dict(item,seconds=0),dict(item,seconds=1,embedding=OTHER)]
        with connect(self.db) as c,patch('dog_identity.rank_candidates',side_effect=[self.ranks(),self.ranks(),self.ranks(self.stubby)]):
            self.assertIsNone(recommend_identity(c,item,1))

    def test_confirmed_unknown_image_vetoes_name(self):
        item=self.prepare()
        with connect(self.db) as c:
            did=c.execute("INSERT INTO clip_detections(catalog_id,subject_type,confirmed) VALUES(2,'Dog',1)").lastrowid
            c.execute('INSERT INTO identity_evidence VALUES(?,?,?,?,?,?,?,?,?)',(did,2,'unknown','Dog',b'jpg',json.dumps(VECTOR),MODEL,.8,None))
            with patch('dog_identity.rank_candidates',return_value=self.ranks()):
                self.assertIsNone(recommend_identity(c,item,1))
                # The test clip cannot veto itself.
                self.assertEqual(recommend_identity(c,item,2),self.duke)

    def test_quality_model_and_species_guards(self):
        item=self.prepare()
        with connect(self.db) as c:
            for changes in [dict(subject_type='Cat'),dict(quality_score=.1),dict(embedding_model='other'),dict(embedding=[0]*384),dict(embedding=[float('nan')]*384)]:
                self.assertIsNone(recommend_identity(c,dict(item,**changes),1))

    def test_cosine_match_cannot_bypass_dog_guards(self):
        self.prepare()
        item=dict(key='dog',subject_type='Dog',confidence=.9,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'jpg',embedding=VECTOR,embedding_model=MODEL,quality_score=.8)
        with patch('identity_candidates.dog_recommendation',return_value=None),patch('identity_analysis.match_identity') as cosine:
            publish_detections(self.db,1,'hash',[item]);cosine.assert_not_called()
        row=get_clip_identities(self.db,1)[0]
        self.assertIsNone(row['identity_id']);self.assertFalse(row['confirmed'])

if __name__=='__main__':unittest.main()
