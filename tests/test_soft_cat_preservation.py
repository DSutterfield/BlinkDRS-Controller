from pathlib import Path
import os,sys,tempfile,unittest,copy,sqlite3,json,io
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
os.environ.update(BLINK_NAME_FIRST_PILOT='1',BLINK_DOG_REVIEW_PILOT='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import test_name_first_review as fixtures
from identity_store import connect,get_clip_identities,create_identity,assign_identity
from cat_identity_review import review_suggestions
import name_first_review as nf
from name_first_evidence import prepare,build
from cat_name_preservation import recommend

class NumericalIntegration(unittest.TestCase):
    setUp=fixtures.NumericalPolicy.setUp
    view=fixtures.NumericalPolicy.view
    def item(self,kind='Cat',borderline=False):
        anchor=self.view(2,0,10)
        if borderline:
            anchor['embedding']=[0.]*384;anchor['embedding'][2]=.8;anchor['embedding'][3]=.6
        weak=[]
        for n in range(2):
            v=self.view(3,n+1.,20+n);v['embedding']=[0.]*384;v['embedding'][2]=.6;v['embedding'][3]=.8;weak.append(v)
        return dict(anchor,subject_type=kind,confirmed=False,identity_id=None,views=[anchor]+weak,catalog_id=999)
    def test_qualified_anchor_survives_weak_votes(self):
        item=self.item();p=prepare(self.c,item,999,[]);result=recommend(p,item)
        self.assertEqual((result['suggested_name'],result['suggestion_tier']),('Levi','possible'))
        self.assertTrue(result['review_required']);self.assertFalse(result['confirmed'])
    def test_native_Possible_anchor_survives_weak_votes(self):
        item=self.item(borderline=True);p=prepare(self.c,item,999,[])
        self.assertIsNone(p.evidence(item,'Cat',True)['qualified'])
        result=recommend(p,item);self.assertEqual((result['suggested_name'],result['suggestion_tier']),('Levi','possible'))
    def test_Unknown_single_view_only_proposes_Possible_Cat(self):
        item=dict(self.view(2,0,30),subject_type='Unknown',confirmed=False,identity_id=None,catalog_id=999,views=[self.view(2,0,30)])
        original=copy.deepcopy(item);p=prepare(self.c,item,999,[]);self.assertIsNone(p(item))
        r=recommend(p,item);self.assertEqual((r['suggested_name'],r['suggested_subject_type'],r['suggestion_tier']),('Levi','Cat','possible'))
        self.assertEqual(item,original)
    def test_two_qualifying_views_keep_Probable(self):
        item=dict(self.view(2,0,31),subject_type='Cat',confirmed=False,catalog_id=999,views=[self.view(2,0,31),self.view(2,1,32)])
        r=recommend(prepare(self.c,item,999,[]),item);self.assertEqual(r['suggestion_tier'],'probable')
    def test_overwhelming_opposing_name_withdraws(self):
        item=self.item();item['views']=[self.view(2,0,31),self.view(3,1,32)]
        self.assertIsNone(recommend(prepare(self.c,item,999,[]),item))
    def test_strong_Person_evidence_vetoes_new_Possible(self):
        ranks=[dict(subject_type='Person',score=.95,independent_similarities=[.9,.8]),dict(subject_type='Dog',score=.1),dict(subject_type='Cat',score=0.)]
        item=self.item()
        with patch('name_first_evidence.rank_types',return_value=ranks):self.assertIsNone(recommend(prepare(self.c,item,999,[]),item))
    def test_confirmed_or_excluded_never_get_a_proposal(self):
        item=self.item();p=prepare(self.c,item,999,[])
        self.assertIsNone(recommend(p,dict(item,confirmed=True)));self.assertIsNone(recommend(p,dict(item,analysis_excluded=True)))
    def test_prepared_and_original_name_first_outputs_agree(self):
        for kind in ['Unknown','Cat','Dog','Person']:
            item=self.item(kind);self.assertEqual(prepare(self.c,item,999,[])(item),build(self.c,item,999,[])(item))
    def test_service_routes_only_opted_in_Cat_scoring_through_soft_policy(self):
        item=self.item(borderline=True)
        with patch('name_first_review.references',return_value=[]):
            with patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='0'):self.assertIsNone(nf.score(self.c,item))
            with patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='1'):
                result=nf.score(self.c,item);self.assertEqual((result['suggested_name'],result['suggestion_tier']),('Levi','possible'))

class PublicationIntegration(unittest.TestCase):
    setUp=fixtures.test_pc_helper_queue.HelperQueueTests.setUp
    def gallery_and_item(self):
        import json
        flags=patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='1');flags.start();self.addCleanup(flags.stop)
        numeric=fixtures.NumericalPolicy();numeric.setUp();self.addCleanup(numeric.doCleanups)
        with connect(self.db) as c:
            for row in numeric.c.execute('select * from identities'):
                c.execute('insert into identities(identity_id,name,subject_type,active) values(?,?,?,?)',tuple(row))
            for row in numeric.c.execute('select * from clip_detections'):
                did,cid,confirmed,iid,kind=tuple(row)
                c.execute("insert into clips(id,filename,video_path,captured_at,local_present,blink_media_id) values(?,?,?,'2026-10-20',0,?)",(cid,str(cid)+'.mp4',str(cid)+'.mp4','fixture-ref-'+str(cid)))
                c.execute('insert into clip_detections(detection_id,catalog_id,confirmed,identity_id,subject_type) values(?,?,?,?,?)',(did,cid,confirmed,iid,kind))
            for row in numeric.c.execute('select * from identity_samples'):
                c.execute('insert into identity_samples(sample_id,identity_id,detection_id,embedding,model_version,quality_score) values(?,?,?,?,?,?)',tuple(row))
        v=numeric.view(2,0,70)
        return dict(v,key='0:Cat:[0,0,32,32]',subject_type='Unknown',confidence=.8,first_seen_seconds=0.,last_seen_seconds=1.,detector_model='fixture',views=[v])
    def check(self,cid):
        before=get_clip_identities(self.db,cid)[0]
        self.assertEqual((before['subject_type'],before['identity_id'],before['confirmed']),('Unknown',None,False))
        completed=nf.backfill(self.db,1);self.assertEqual(completed,[before['detection_id']])
        after=get_clip_identities(self.db,cid)[0]
        self.assertEqual((after['subject_type'],after['identity_id'],after['confirmed']),('Unknown',None,False))
        self.assertEqual((after['suggested_subject_type'],after['suggested_name'],after['suggestion_tier']),('Cat','Levi','possible'))
        with connect(self.db) as c:
            self.assertEqual(c.execute('select count(*) from identity_samples').fetchone()[0],12)
            self.assertEqual(c.execute('select count(*) from subject_type_samples').fetchone()[0],0)
    def test_Pi_publication_background_cache_and_review_API(self):
        from identity_analysis import publish_detections
        item=self.gallery_and_item();publish_detections(self.db,1,'fixture-hash',[item]);self.check(1)
        result=review_suggestions(self.db,1)[0];self.assertTrue(result['review_required']);self.assertFalse(result['confirmed'])
    def test_PC_wire_publication_background_cache_and_review_API(self):
        from pc_helper_wire import encode
        item=self.gallery_and_item();manifest=self.q.claim('PC-soft-cat-fixture','contract',900);manifest=self.q.export(manifest['token'],self.root/manifest['token'])
        self.q.commit(encode(manifest,[item]));self.check(manifest['catalog_id'])
        result=review_suggestions(self.db,manifest['catalog_id'])[0];self.assertEqual(result['suggestion_tier'],'possible')

class ServiceIntegration(unittest.TestCase):
    setUp=fixtures.Pilot.setUp
    def prepare_cat(self):
        flags=patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='1');flags.start();self.addCleanup(flags.stop)
        fixtures.Pilot.prepare(self,'Cat')
        self.kushka=create_identity(self.db,'Kushka','Cat')['identity_id']
        self.proposal=dict(suggested_identity_id=self.kushka,suggested_name='Kushka',suggested_subject_type='Cat',suggestion_tier='possible',review_required=True,confirmed=False)
    def store(self):
        with connect(self.db) as c:return nf.store(c,self.did,self.proposal,nf.epoch(c),nf.signature(c,self.did),expected_policy=nf.policy())
    def test_soft_config_defaults_off(self):
        from cat_name_preservation_config import enabled
        with patch.dict(os.environ,{},clear=True):self.assertFalse(enabled())
    def test_Possible_cache_does_not_assign_or_teach(self):
        self.prepare_cat();self.assertTrue(self.store());r=get_clip_identities(self.db,1)[0]
        self.assertEqual((r['subject_type'],r['identity_id'],r['confirmed']),('Cat',None,False));self.assertEqual(r['suggestion_tier'],'possible')
        with connect(self.db) as c:
            for t in ['identity_samples','subject_type_samples']:self.assertEqual(c.execute('select count(*) from '+t).fetchone()[0],0)
    def test_HTTP_review_response_and_withdrawal_are_cache_only(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from types import SimpleNamespace
        from identity_api import install_identity_api
        self.prepare_cat();self.store();app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db))
        with TestClient(app) as client,patch('name_first_review.score',side_effect=AssertionError('HTTP performed scoring')):
            response=client.get('/api/v1/clips/catalog/1/identity-suggestions');self.assertEqual(response.status_code,200)
            row=response.json()[0];self.assertEqual((row['suggested_name'],row['suggestion_tier']),('Kushka','possible'));self.assertTrue(row['review_required']);self.assertFalse(row['confirmed'])
            with connect(self.db) as c:nf.store(c,self.did,None,nf.epoch(c),nf.signature(c,self.did),expected_policy=nf.policy())
            row=client.get('/api/v1/clips/catalog/1/identity-suggestions').json()[0];self.assertIsNone(row['suggested_identity_id']);self.assertIsNone(row['suggested_name'])
    def test_policy_version_change_invalidates_and_flag_off_hides_soft(self):
        self.prepare_cat();self.store()
        with patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='0'):
            with connect(self.db) as c:self.assertFalse(nf.fresh(c,self.did))
            self.assertNotIn('suggested_name',get_clip_identities(self.db,1)[0])
    def test_inflight_flag_change_rejects_result(self):
        self.prepare_cat()
        with connect(self.db) as c:
            version=nf.policy();revision=nf.epoch(c);token=nf.signature(c,self.did)
            with patch.dict(os.environ,BLINK_SOFT_CAT_REVIEW='0'):
                self.assertFalse(nf.store(c,self.did,self.proposal,revision,token,expected_policy=version))
    def test_withdrawal_clears_stale_derived_Cat_hint_and_API_sends_null(self):
        self.prepare_cat();self.store()
        from cat_review_cache import store
        with connect(self.db) as c:store(c,self.did,self.proposal)
        with connect(self.db) as c:self.assertTrue(nf.store(c,self.did,None,nf.epoch(c),nf.signature(c,self.did),expected_policy=nf.policy()))
        with patch('name_first_review.score',side_effect=AssertionError('API performed scoring')):
            rows=review_suggestions(self.db,1);self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['suggested_identity_id']);self.assertIsNone(rows[0]['suggested_name'])
        self.assertNotIn('suggested_name',get_clip_identities(self.db,1)[0])
    def test_reference_change_and_confirmation_win_over_inflight_soft_result(self):
        self.prepare_cat()
        with connect(self.db) as c:revision=nf.epoch(c);token=nf.signature(c,self.did);version=nf.policy()
        create_identity(self.db,'Levi','Cat')
        with connect(self.db) as c:self.assertFalse(nf.store(c,self.did,self.proposal,revision,token,expected_policy=version))
        assign_identity(self.db,1,self.kushka,'Cat',self.did)
        with connect(self.db) as c:self.assertFalse(nf.store(c,self.did,self.proposal,nf.epoch(c),nf.signature(c,self.did),expected_policy=nf.policy()))
        r=get_clip_identities(self.db,1)[0];self.assertTrue(r['confirmed']);self.assertEqual(r['name'],'Kushka');self.assertNotIn('suggested_name',r)
    def test_NoIdentifiable_and_NoVisible_exclusions_hide_soft_hints(self):
        self.prepare_cat();self.store()
        from subject_exclusions import mark
        mark(self.db,1,self.did);r=get_clip_identities(self.db,1)[0];self.assertTrue(r['analysis_excluded']);self.assertNotIn('suggested_name',r)
        with connect(self.db) as c:
            c.execute('delete from subject_analysis_exclusions');c.execute('update clip_detections set confirmed=0 where detection_id=?',(self.did,));c.execute('insert into clip_unknown_motion(catalog_id) values(1)')
            self.assertIsNone(nf.target(c,self.did));self.assertFalse(nf.store(c,self.did,self.proposal,nf.epoch(c),nf.signature(c,self.did),expected_policy=nf.policy()))
    def test_background_keeps_score_outside_writer_and_bounded(self):
        self.prepare_cat()
        def score(c,item):
            other=sqlite3.connect(self.db,timeout=.1)
            try:other.execute('BEGIN IMMEDIATE');other.rollback()
            finally:other.close()
            return self.proposal
        with patch('name_first_review.score',side_effect=score):self.assertEqual(nf.backfill(self.db,1),[self.did])
        self.assertEqual(get_clip_identities(self.db,1)[0]['suggestion_tier'],'possible')
    def test_background_flag_toggle_cannot_publish_wrong_policy(self):
        self.prepare_cat()
        def score(c,item):os.environ['BLINK_SOFT_CAT_REVIEW']='0';return self.proposal
        with patch('name_first_review.score',side_effect=score):self.assertEqual(nf.backfill(self.db,1),[])
        with connect(self.db) as c:self.assertEqual(c.execute('select count(*) from name_first_cache').fetchone()[0],0)

if __name__=='__main__':unittest.main(verbosity=2)
