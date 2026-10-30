from pathlib import Path
import os,sys,tempfile,unittest,copy,sqlite3,json,io
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
os.environ.update(BLINK_NAME_FIRST_PILOT='1',BLINK_DOG_REVIEW_PILOT='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
from identity_store import connect,create_identity,get_clip_identities,assign_identity,mark_unknown_motion
from identity_analysis import publish_detections
import name_first_review as nf
import test_identities

class Pilot(unittest.TestCase):
    setUp=test_identities.IdentityTests.setUp
    def prepare(self,kind='Unknown'):
        self.iid=create_identity(self.db,'Duke','Dog')['identity_id']
        item=dict(key='one',subject_type=kind,confidence=.9,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',crop_jpeg=b'original',embedding=[1,0],embedding_model='test-model',quality_score=.8)
        publish_detections(self.db,1,'hash',[item]);self.did=get_clip_identities(self.db,1)[0]['detection_id']
        self.proposal=dict(suggested_identity_id=self.iid,suggested_name='Duke',suggested_subject_type='Dog',suggestion_tier='probable')
    def save(self):
        with connect(self.db) as c:return nf.store(c,self.did,self.proposal,nf.epoch(c),nf.signature(c,self.did))
    def test_unknown_proposal_is_display_only_and_never_teaches(self):
        self.prepare();self.assertTrue(self.save());r=get_clip_identities(self.db,1)[0]
        self.assertEqual((r['subject_type'],r['identity_id'],r['confirmed']),('Unknown',None,False))
        self.assertEqual(r['suggested_subject_type'],'Dog');self.assertTrue(r['review_required'])
        with connect(self.db) as c:
            for t in ('identity_samples','subject_type_samples'):self.assertEqual(c.execute('SELECT count(*) FROM '+t).fetchone()[0],0)
    def test_disabled_pilot_hides_optional_proposals(self):
        self.prepare();self.save()
        with patch.dict(os.environ,BLINK_NAME_FIRST_PILOT='0'):self.assertNotIn('suggested_subject_type',get_clip_identities(self.db,1)[0])
    def test_explicit_confirmation_wins_and_late_result_rejected(self):
        self.prepare()
        with connect(self.db) as c:ep=nf.epoch(c);sig=nf.signature(c,self.did)
        assign_identity(self.db,1,self.iid,'Dog',self.did)
        with connect(self.db) as c:self.assertFalse(nf.store(c,self.did,self.proposal,ep,sig))
        r=get_clip_identities(self.db,1)[0];self.assertTrue(r['confirmed']);self.assertEqual(r['name'],'Duke');self.assertNotIn('suggested_subject_type',r)
    def test_changed_target_rejects_inflight_result(self):
        self.prepare()
        with connect(self.db) as c:
            ep=nf.epoch(c);sig=nf.signature(c,self.did);c.execute('UPDATE identity_evidence SET quality_score=.7 WHERE detection_id=?',(self.did,))
            self.assertFalse(nf.store(c,self.did,self.proposal,ep,sig))
    def test_new_recording_does_not_expire_hints(self):
        self.prepare();self.save()
        with connect(self.db) as c:
            old=nf.epoch(c);c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) VALUES(3,'3.mp4','3.mp4','2026-10-30',1,'pir')")
            self.assertEqual(nf.epoch(c),old);self.assertTrue(nf.fresh(c,self.did))
    def test_reference_change_withdraws_then_requests_refresh(self):
        self.prepare();self.save()
        create_identity(self.db,'Stubby','Dog')
        self.assertNotIn('suggested_subject_type',get_clip_identities(self.db,1)[0])
        with connect(self.db) as c:
            nf.request_review(c,1);self.assertEqual(c.execute('SELECT count(*) FROM name_first_requests').fetchone()[0],1)
    def test_accepted_cat_type_is_never_replaced_with_dog(self):
        self.prepare('Cat');self.assertFalse(self.save())
    def test_exclusion_and_no_visible_hide_cached_result(self):
        self.prepare();self.save()
        with connect(self.db) as c:c.execute("INSERT INTO subject_analysis_exclusions(detection_id,crop_sha256) VALUES(?,'isolated')",(self.did,))
        self.assertNotIn('suggested_subject_type',get_clip_identities(self.db,1)[0])
        with connect(self.db) as c:
            c.execute('DELETE FROM subject_analysis_exclusions');self.assertIsNotNone(nf.target(c,self.did))
        with connect(self.db) as c:c.execute('INSERT INTO clip_unknown_motion(catalog_id) VALUES(1)')
        with connect(self.db) as c:self.assertIsNone(nf.target(c,self.did))
    def test_api_is_cache_only(self):
        self.prepare();self.save()
        from cat_identity_review import review_suggestions
        with patch('name_first_review.score',side_effect=AssertionError('API called scorer')):
            r=review_suggestions(self.db,1);self.assertEqual(r[0]['suggested_name'],'Duke')
    def test_background_is_bounded_and_outside_writer(self):
        self.prepare()
        def scorer(c,item):
            # A read transaction is allowed; no SQLite RESERVED writer lock.
            with sqlite3.connect(self.db,timeout=.1) as other:
                other.execute('BEGIN IMMEDIATE');other.rollback()
            other.close()
            return self.proposal
        with patch('name_first_review.score',side_effect=scorer):self.assertEqual(nf.backfill(self.db,1),[self.did])
        self.assertEqual(get_clip_identities(self.db,1)[0]['suggested_name'],'Duke')
    def test_empty_gallery_and_non_dino_abstain(self):
        self.prepare()
        with connect(self.db) as c:self.assertIsNone(nf.score(c,nf.target(c,self.did)))
    def test_holdouts_remain_excluded_and_have_no_samples(self):
        self.prepare()
        with connect(self.db) as c:
            protected={29703,29699,29700,29702,29678,29682,29689,29698}
            # Seed isolated holdout membership and prove the existing guards
            # also protect the pilot; no production catalog is opened here.
            for cid in protected:
                c.execute("INSERT INTO clips(id,blink_media_id,filename,video_path,captured_at,local_present,source) VALUES(?,?,?,?,?,1,'pir')",(cid,str(cid),str(cid)+'.mp4',str(cid)+'.mp4','2026-10-30'))
                c.execute("INSERT INTO evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) VALUES(?,?,'baseline',0,'test','2026-10-30','hash','isolated')",(str(cid),cid))
            self.assertTrue(protected.issubset({r[0] for r in c.execute('SELECT catalog_id FROM evaluation_excluded_clips')}))
            for t in ('identity_samples','subject_type_samples'):self.assertEqual(c.execute('SELECT count(*) FROM '+t+' JOIN clip_detections d USING(detection_id) WHERE catalog_id IN ('+','.join('?'*8)+')',tuple(protected)).fetchone()[0],0)
    def test_reference_refresh_cannot_starve_legacy_dog_requests(self):
        self.prepare();self.save()
        from cat_identity_review import backfill_requested
        with patch('name_first_review.backfill',return_value=[self.did]),patch('cat_identity_review._dog_refresh') as legacy:
            with connect(self.db) as c:
                c.execute("UPDATE clip_detections SET subject_type='Dog' WHERE detection_id=?",(self.did,))
                c.execute('INSERT INTO dog_review_requests VALUES(?,0)',(self.did,))
                c.execute("UPDATE dog_review_requests SET requested_at=CAST(strftime('%s','now') AS REAL)")
            backfill_requested(self.db,1);legacy.assert_called_once()

import test_pc_helper_queue
class PCHelperPublication(unittest.TestCase):
    setUp=test_pc_helper_queue.HelperQueueTests.setUp
    def test_unknown_from_helper_keeps_type_and_only_attaches_review_proposal(self):
        from pc_helper_wire import encode
        manifest=self.q.claim('PC-test-slot','contract',900)
        manifest=self.q.export(manifest['token'],self.root/manifest['token'])
        item=dict(key='pc-unknown',subject_type='Unknown',confidence=.8,first_seen_seconds=1.,last_seen_seconds=2.,detector_model='fixture',crop_jpeg=b'\xff\xd8original\xff\xd9',embedding=[1.,0.],embedding_model='fixture',quality_score=.8)
        self.q.commit(encode(manifest,[item]))
        rows=get_clip_identities(self.db,manifest['catalog_id']);did=rows[0]['detection_id']
        iid=create_identity(self.db,'Duke','Dog')['identity_id']
        with connect(self.db) as c:
            self.assertTrue(nf.store(c,did,dict(suggested_identity_id=iid,suggested_name='Duke',suggested_subject_type='Dog',suggestion_tier='probable'),nf.epoch(c),nf.signature(c,did)))
        row=get_clip_identities(self.db,manifest['catalog_id'])[0]
        self.assertEqual((row['subject_type'],row['identity_id'],row['confirmed']),('Unknown',None,False));self.assertEqual(row['suggested_name'],'Duke')

class NumericalPolicy(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;self.addCleanup(self.c.close)
        self.c.executescript('''CREATE TABLE identities(identity_id,name,subject_type,active);
            CREATE TABLE clip_detections(detection_id,catalog_id,confirmed,identity_id,subject_type);
            CREATE TABLE identity_samples(sample_id,identity_id,detection_id,embedding,model_version,quality_score);
            CREATE TABLE identity_evidence(detection_id,embedding,embedding_model,quality_score);
            CREATE TABLE subject_analysis_exclusions(detection_id);
            CREATE TABLE evaluation_excluded_clips(catalog_id);''')
        self.model='dinov2-small-cls-v1:synthetic-policy-test'
        self.vectors=[]
        for i,(name,kind) in enumerate([('Duke','Dog'),('Stubby','Dog'),('Levi','Cat'),('Kushka','Cat')]):
            v=[0.]*384;v[i]=1.;self.vectors.append(v)
            self.c.execute('INSERT INTO identities VALUES(?,?,?,1)',(i+1,name,kind))
            for j in range(3):
                did=10*i+j+1;cid=100+did
                self.c.execute('INSERT INTO clip_detections VALUES(?,?,1,?,?)',(did,cid,i+1,kind))
                self.c.execute('INSERT INTO identity_samples VALUES(?,?,?,?,?,1)',(did,i+1,did,json.dumps(v),self.model))
        self.np=np
    def view(self,index,seconds,seed):
        from PIL import Image
        data=self.np.random.default_rng(seed).integers(0,256,size=(32,32,3),dtype=self.np.uint8)
        buf=io.BytesIO();Image.fromarray(data).save(buf,format='JPEG')
        return dict(embedding=self.vectors[index],embedding_model=self.model,quality_score=1.,seconds=seconds,crop_jpeg=buf.getvalue())
    def score(self,indices=(0,0),kind='Unknown',types=None):
        from name_first_evidence import build
        item=dict(self.view(0,0,9),subject_type=kind,views=[self.view(i,float(n),n+20) for n,i in enumerate(indices)])
        with patch('name_first_evidence.rank_types',return_value=types or []):return build(self.c,item,999,[])(item)
    def test_two_separated_votes_qualify_but_one_does_not(self):
        self.assertEqual(self.score()['suggested_name'],'Duke');self.assertIsNone(self.score((0,)))
    def test_strong_cross_name_conflict_abstains(self):self.assertIsNone(self.score((0,0,1)))
    def test_accepted_cat_type_cannot_be_changed_by_dog_votes(self):self.assertIsNone(self.score(kind='Cat'))
    def test_strong_person_veto(self):
        ranks=[dict(subject_type='Person',score=.95,independent_similarities=[.9,.8]),dict(subject_type='Dog',score=.1),dict(subject_type='Cat',score=.05)]
        self.assertIsNone(self.score(types=ranks))
    def test_contradictory_animal_veto(self):
        ranks=[dict(subject_type='Cat',score=.9,independent_similarities=[.9,.8]),dict(subject_type='Dog',score=.1),dict(subject_type='Person',score=.0)]
        self.assertIsNone(self.score(types=ranks))
    def test_identical_images_and_close_moments_cannot_supply_two_votes(self):
        from name_first_evidence import build
        v=self.view(0,0,8);item=dict(v,subject_type='Unknown',views=[v,dict(v,seconds=1.)])
        self.assertIsNone(build(self.c,item,999,[])(item))
        item['views']=[v,self.view(0,.25,9)];self.assertIsNone(build(self.c,item,999,[])(item))
    def test_confirmed_unknown_reference_veto(self):
        self.c.execute("INSERT INTO clip_detections VALUES(999,998,1,NULL,'Dog')")
        self.c.execute('INSERT INTO identity_evidence VALUES(?,?,?,1)',(999,json.dumps(self.vectors[0]),self.model))
        self.assertIsNone(self.score())

if __name__=='__main__':unittest.main(verbosity=2)
