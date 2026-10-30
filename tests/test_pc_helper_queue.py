import json,sqlite3,tempfile,unittest,threading,shutil,time
from contextlib import closing
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from identity_store import connect,create_identity,assign_identity,mark_unknown_motion,remove_detection
from identity_analysis import queue_clip,claim_job,publish_detections
from pc_helper_queue import Coordinator,Conflict,Paused,transaction,recover_local_jobs
from pc_helper_wire import encode,decode,canonical,sha

class HelperQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.archive=self.root/'archive';self.archive.mkdir();self.db=self.root/'catalog.sqlite'
        with closing(sqlite3.connect(self.db)) as c,c:
            c.executescript((Path(__file__).resolve().parents[1]/'sql/catalog_schema_v1.sql').read_text())
            for cid in range(1,5):
                c.execute('INSERT INTO clips(id,filename,video_path,captured_at,local_present,blink_media_id) VALUES(?,?,?,?,1,?)',(cid,str(cid)+'.mp4',str(cid)+'.mp4','2026-10-01',str(cid)));(self.archive/(str(cid)+'.mp4')).write_bytes(b'video'+str(cid).encode())
        self.now=time.time();self.ready=True;self.zones=dict(basic_rows=1,basic_columns=1,sub_rows=1,sub_columns=1,cells=[1],revision='one');self.q=Coordinator(self.db,self.archive,'contract',lambda cid:dict(self.zones),lambda:self.ready,lambda:self.now)
        queue_clip(self.db,1)
    def row(self,sql,args=()):
        with closing(sqlite3.connect(self.db)) as c:return c.execute(sql,args).fetchone()
    def observation(self,key='cat'):
        return dict(key=key,subject_type='Cat',confidence=.8,first_seen_seconds=1.,last_seen_seconds=2.,detector_model='model',crop_jpeg=b'\xff\xd8data\xff\xd9',embedding=[1.,0.],embedding_model='pet',quality_score=.8)
    def lease(self,ttl=900):
        m=self.q.claim('PC-slot-1','contract',ttl);return self.q.export(m['token'],self.root/m['token'])
    def packet(self,m,rows=None):return encode(m,[self.observation()] if rows is None else rows)
    def test_atomic_accept_and_exact_duplicate_ack(self):
        m=self.lease();data=self.packet(m);self.assertFalse(self.q.commit(data)['duplicate']);self.now+=10000;self.assertTrue(self.q.commit(data)['duplicate']);self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],1);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'done')
    def test_changed_duplicate_is_rejected(self):
        m=self.lease();self.q.commit(self.packet(m));self.assertRaises(Conflict,self.q.commit,self.packet(m,[]))
    def test_expiry_fences_old_attempt_after_reclaim(self):
        old=self.lease(30);self.now+=31;new=self.lease();self.assertNotEqual(old['token'],new['token']);self.assertRaises(Conflict,self.q.commit,self.packet(old));self.q.commit(self.packet(new))
    def test_expired_without_reclaim_cannot_commit(self):
        m=self.lease(30);self.now+=30;self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_heartbeat_renews_only_live_lease(self):
        m=self.lease(30);self.now+=20;self.q.heartbeat(m['token'],30);self.now+=20;self.q.commit(self.packet(m))
    def test_late_heartbeat_cannot_resurrect(self):
        m=self.lease(30);self.now+=31;self.assertRaises(Conflict,self.q.heartbeat,m['token'])
    def test_cancel_prevents_late_result(self):
        m=self.lease();self.q.cancel(m['token']);self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_release_requeues_with_new_token(self):
        m=self.lease();self.q.release(m['token']);n=self.lease();self.assertNotEqual(m['token'],n['token']);self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_three_expired_attempts_stop_poison_loop(self):
        for _ in range(3):self.assertIsNotNone(self.q.claim('PC-slot-1','contract',30));self.now+=31
        self.assertIsNone(self.q.claim('PC-slot-2','contract',30));self.assertEqual(self.row('SELECT COUNT(*) FROM pc_helper_leases')[0],3);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'failed')
    def test_three_releases_fail_job(self):
        for _ in range(3):m=self.q.claim('PC-slot-1','contract');self.q.release(m['token'])
        self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'failed');self.assertIsNone(self.q.claim('next','contract'))
    def test_successful_runs_do_not_consume_later_retry_budget(self):
        for _ in range(3):m=self.lease();self.q.commit(self.packet(m));queue_clip(self.db,1)
        m=self.q.claim('PC-slot-1','contract',30);self.now+=31;self.assertIsNotNone(self.q.claim('PC-slot-2','contract'))
    def test_manual_retry_resets_failed_attempt_budget(self):
        for _ in range(3):m=self.q.claim('PC-slot-1','contract');self.q.release(m['token'])
        queue_clip(self.db,1);self.q.claim('PC-slot-1','contract',30);self.now+=31;self.assertIsNotNone(self.q.claim('PC-slot-2','contract'))
    def test_local_claim_cannot_take_helper_job(self):
        self.lease();self.assertIsNone(claim_job(self.db))
    def test_pi_alone_reclaims_expired_helper_and_fences_old_result(self):
        m=self.lease(30);self.now+=31
        with patch('time.time',return_value=self.now):job=claim_job(self.db)
        self.assertEqual(job['catalog_id'],1);self.assertRaises(Conflict,self.q.commit,self.packet(m));self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'running')
    def test_local_restart_keeps_live_helper_job(self):
        m=self.lease();recover_local_jobs(self.db);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'running');self.q.commit(self.packet(m))
    def test_local_restart_recovers_local_work(self):
        claim_job(self.db);recover_local_jobs(self.db);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'failed')
    def test_competing_claims_no_duplicate_and_limit_two(self):
        for cid in [2,3,4]:queue_clip(self.db,cid)
        with ThreadPoolExecutor(8) as pool:rows=list(pool.map(lambda i:self.q.claim('slot'+str(i),'contract'),range(8)))
        ids=[x['catalog_id'] for x in rows if x];self.assertEqual(len(ids),2);self.assertEqual(len(set(ids)),2)
    def test_one_live_job_per_slot(self):
        self.lease();queue_clip(self.db,2);self.assertIsNone(self.q.claim('PC-slot-1','contract'))
    def test_unknown_contract_does_not_claim(self):self.assertRaises(Conflict,self.q.claim,'pc','wrong');self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'queued')
    def test_pause_fails_closed_for_claim_and_commit(self):
        m=self.lease();self.ready=False;self.assertRaises(Paused,self.q.claim,'pc','contract');self.assertRaises(Paused,self.q.commit,self.packet(m));self.ready=True;self.q.commit(self.packet(m))
    def test_recording_changed_rejected_without_publication(self):
        m=self.lease();(self.archive/'1.mp4').write_bytes(b'changed');self.assertRaises(Conflict,self.q.commit,self.packet(m));self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],0)
    def test_motion_zone_change_rejected(self):
        m=self.lease();self.zones['revision']='new';self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_reference_change_rejected(self):
        m=self.lease();create_identity(self.db,'New cat','Cat');self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_human_confirmation_wins(self):
        identity=create_identity(self.db,'Gus','Cat');m=self.lease();assign_identity(self.db,1,identity['identity_id'],'Cat');before=self.row('SELECT identity_id,confirmed FROM clip_detections');self.assertRaises(Conflict,self.q.commit,self.packet(m));self.assertEqual(self.row('SELECT identity_id,confirmed FROM clip_detections'),before)
    def test_partial_evidence_failure_restores_previous_evidence(self):
        publish_detections(self.db,1,'original',[self.observation('old')]);queue_clip(self.db,1);m=self.lease();before=self.row('SELECT detection_key FROM identity_evidence')
        with transaction(self.db) as c:c.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON clip_detections WHEN NEW.first_seen_seconds=2 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        second=dict(self.observation('second'),first_seen_seconds=2,last_seen_seconds=3);data=self.packet(m,[self.observation('first'),second]);self.assertRaises(sqlite3.IntegrityError,self.q.commit,data)
        self.assertEqual(self.row('SELECT detection_key FROM identity_evidence'),before);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'running')
        with transaction(self.db) as c:c.execute('DROP TRIGGER injected_failure')
        self.q.commit(data);self.assertEqual(self.row('SELECT COUNT(*) FROM identity_evidence')[0],2)
    def test_failed_heartbeat_stops_inference_before_publication(self):
        from pc_helper_client import run_one,LostLease
        outer=self
        class Transport:
            def rpc(self,r):
                if r['action']=='claim':return outer.lease()
                if r['action']=='release':return outer.q.release(r['token'])
                raise ConnectionError('Injected heartbeat loss')
            def download(self,relative,target):shutil.copyfile(outer.root/Path(relative).parts[1]/Path(relative).name,target)
        def analyze(*args):time.sleep(.05);args[-1]();raise AssertionError('Unreachable after lost lease')
        with patch('pc_helper_client.analyze',side_effect=analyze):self.assertRaises(LostLease,run_one,Transport(),None,self.root/'pc','contract','slot',.01)
        self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],0)
    def test_bridge_requires_sandbox_marker(self):
        from pc_helper_bridge import dispatch
        self.assertRaises(FileNotFoundError,dispatch,self.root,dict(action='claim'))
        (self.root/'.pc-helper-sandbox.json').write_text('{"test_only":false}')
        self.assertRaises(ValueError,dispatch,self.root,dict(action='claim'))
    def test_bridge_rejects_path_and_token_mismatch(self):
        from pc_helper_bridge import dispatch
        (self.root/'zones.json').write_text(json.dumps({'1':self.zones}));(self.root/'ready.json').write_text('{"ready":true}');config=dict(test_only=True,db='catalog.sqlite',archive='archive',contract='contract',zones='zones.json');(self.root/'.pc-helper-sandbox.json').write_text(json.dumps(config));m=self.lease();data=self.packet(m);(self.root/'incoming').mkdir();wrong='0'*32;(self.root/'incoming'/f'{wrong}.partial').write_bytes(data)
        self.assertRaises(ValueError,dispatch,self.root,dict(action='commit',token=wrong,sha256=sha(data)))
        config['db']='../outside';(self.root/'.pc-helper-sandbox.json').write_text(json.dumps(config));self.assertRaises(ValueError,dispatch,self.root,dict(action='claim',owner='pc',contract='contract'))
    def test_no_subject_confirmation_wins(self):
        m=self.lease();mark_unknown_motion(self.db,1);self.assertRaises(Conflict,self.q.commit,self.packet(m));self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],0)
    def test_removed_evidence_not_recreated(self):
        publish_detections(self.db,1,sha((self.archive/'1.mp4').read_bytes()),[self.observation()]);queue_clip(self.db,1);m=self.lease();did=self.row('SELECT detection_id FROM clip_detections')[0];remove_detection(self.db,1,did);self.assertRaises(Conflict,self.q.commit,self.packet(m));self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],0)
    def test_lost_ack_client_retries_exact_saved_packet(self):
        from pc_helper_client import run_one
        outer=self
        class Transport:
            lost=False;commits=0
            def rpc(self,request):
                if request['action']=='claim':return outer.lease()
                if request['action']=='commit':
                    self.commits+=1;result=outer.q.commit((outer.root/'incoming.json').read_bytes())
                    if not self.lost:self.lost=True;raise ConnectionError('Injected lost acknowledgement')
                    return result
                if request['action']=='release':return outer.q.release(request['token'])
                return outer.q.heartbeat(request['token'])
            def download(self,relative,target):shutil.copyfile(outer.root/Path(relative).parts[1]/Path(relative).name,target)
            def upload(self,source,relative):shutil.copyfile(source,outer.root/'incoming.json')
        transport=Transport()
        with patch('pc_helper_client.analyze',side_effect=lambda m,*args:outer.packet(m)):
            result=run_one(transport,None,self.root/'pc','contract','slot')
        self.assertTrue(result['receipt']['duplicate']);self.assertEqual(transport.commits,2);self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],1)
    def test_client_rejects_incomplete_input_without_publishing(self):
        from pc_helper_client import run_one
        outer=self
        class Transport:
            def rpc(self,r):return outer.lease() if r['action']=='claim' else outer.q.release(r['token'])
            def download(self,relative,target):Path(target).write_bytes(b'truncated')
        self.assertRaises(ValueError,run_one,Transport(),None,self.root/'pc','contract','slot');self.assertEqual(self.row('SELECT COUNT(*) FROM clip_detections')[0],0);self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'queued')
    def test_deleted_clip_does_not_resurrect(self):
        m=self.lease()
        with transaction(self.db) as c:c.execute('DELETE FROM clips WHERE id=1')
        self.assertRaises(Conflict,self.q.commit,self.packet(m))
    def test_atomic_rollback_on_publication_failure(self):
        m=self.lease()
        def fail(c,*args):c.execute("UPDATE identity_analysis_jobs SET state='done'");raise RuntimeError('injected write failure')
        with patch('identity_analysis.publish_prepared',side_effect=fail):self.assertRaises(RuntimeError,self.q.commit,self.packet(m))
        self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'running');self.assertEqual(self.row('SELECT status FROM pc_helper_leases')[0],'leased');self.q.commit(self.packet(m))
    def test_missing_input_does_not_claim(self):
        (self.archive/'1.mp4').unlink();self.assertIsNone(self.q.claim('pc','contract'));self.assertEqual(self.row('SELECT state FROM identity_analysis_jobs')[0],'failed')
    def test_escaping_path_rejected(self):
        with transaction(self.db) as c:c.execute("UPDATE clips SET video_path='../outside.mp4' WHERE id=1")
        self.assertIsNone(self.q.claim('pc','contract'))
    def test_snapshot_removes_holdout_answers_and_samples(self):
        with connect(self.db) as c:
            c.execute("INSERT INTO evaluation_holdout_clips(blink_media_id,catalog_id,dataset_id,device_id,camera,captured_at,video_sha256,copy_path) VALUES('1',1,'test',1,'catio','2026-10-01','hash','copy')")
            c.execute("INSERT INTO evaluation_holdout_labels(blink_media_id,detection_id,subject_type,name) VALUES('1',42,'Cat','secret scoring answer')")
        m=self.lease()
        with closing(sqlite3.connect(self.root/m['token']/'references.sqlite')) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM evaluation_holdout_labels').fetchone()[0],0);self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
        self.assertEqual(self.row('SELECT COUNT(*) FROM evaluation_holdout_labels')[0],1)
    def test_wire_rejects_nonfinite_duplicate_keys_and_pickle(self):
        m=self.lease();p=json.loads(self.packet(m));p['observations'][0]['confidence']=float('nan');self.assertRaises(ValueError,decode,json.dumps(p).encode());self.assertRaises(ValueError,decode,b'{"schema":1,"schema":1}');self.assertRaises(Exception,decode,b'\x80\x04pickle')
    def test_wire_rejects_bad_crop_vector_times_and_limits(self):
        m=self.lease();original=json.loads(self.packet(m))
        for key,value in [('crop_jpeg','eA=='),('embedding',[0,0]),('first_seen_seconds',3),('confidence',2),('views',[{}]*5)]:
            p=json.loads(json.dumps(original));p['observations'][0][key]=value;self.assertRaises(ValueError,decode,canonical(p))
    def test_packet_bound_to_exact_manifest(self):
        m=self.lease();p=json.loads(self.packet(m));p['manifest_sha256']='wrong';self.assertRaises(Conflict,self.q.commit,canonical(p))
    def test_confirmations_and_training_tables_unchanged_on_accept(self):
        m=self.lease();before={t:self.row('SELECT COUNT(*) FROM '+t)[0] for t in ['identity_samples','subject_type_samples']};self.q.commit(self.packet(m));self.assertEqual(before,{t:self.row('SELECT COUNT(*) FROM '+t)[0] for t in before})
if __name__=='__main__':unittest.main()
