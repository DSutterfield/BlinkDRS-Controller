import json
import unittest
import test_identities
from identity_analysis import (claim_job, queue_clip, finish_job, publish_detections,
                              job_status, enable_new_clip_queue, match_identity, crop_image,
                              set_worker_status)
from identity_store import (assign_identity, create_identity, get_clip_identities,
                            remove_detection, connect)
from identity_worker import archive_video, overlap
from identity_analysis import pending_type_corrections, rebuild_reference
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from identity_api import install_identity_api


class AnalysisTests(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp
    def observation(self, key='frame1', vector=None, kind='Cat', model='test-model'):
        return {'key':key, 'subject_type':kind,'confidence':.8,
                'first_seen_seconds':1.,'last_seen_seconds':3.,'detector_model':'test-detector',
                'crop_jpeg':b'jpeg','embedding': vector or [1.,0.], 'embedding_model':model,'quality_score':.8}

    def test_confirmed_image_learning_and_cross_model_isolation(self):
        queue_clip(self.db,1)
        publish_detections(self.db,1,'hash',[self.observation()])
        did = get_clip_identities(self.db,1)[0]['detection_id']
        fred = create_identity(self.db,'Fred','Cat')['identity_id']
        assign_identity(self.db,1,fred,'Cat',did)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],1)
            self.assertEqual(match_identity(conn,'Cat','test-model',[1.,0.],.9,.05)[0],fred)
            self.assertEqual(match_identity(conn,'Cat','other-model',[1.,0.],.9,.05),(None,None))
            self.assertEqual(match_identity(conn,'Dog','test-model',[1.,0.],.9,.05),(None,None))
        publish_detections(self.db,2,'hash2',[self.observation()])
        second = get_clip_identities(self.db,2)[0]
        self.assertEqual(second['identity_id'],fred)
        self.assertFalse(second['confirmed'])
        self.assertAlmostEqual(second['similarity'],1.)
        self.assertEqual(crop_image(self.db,1,did),b'jpeg')

    def test_reanalysis_preserves_confirmation_and_rejections(self):
        observation = self.observation()
        publish_detections(self.db,1,'hash',[observation])
        did = get_clip_identities(self.db,1)[0]['detection_id']
        fred = create_identity(self.db,'Fred','Cat')['identity_id']
        assign_identity(self.db,1,fred,'Cat',did)
        publish_detections(self.db,1,'hash',[self.observation(vector=[0.,1.])])
        self.assertEqual(get_clip_identities(self.db,1)[0]['identity_id'],fred)
        remove_detection(self.db,1,did)
        publish_detections(self.db,1,'hash',[observation])
        self.assertEqual(get_clip_identities(self.db,1),[])

    def test_unknown_and_type_correction_remove_reference(self):
        publish_detections(self.db,1,'hash',[self.observation()])
        did = get_clip_identities(self.db,1)[0]['detection_id']
        fred = create_identity(self.db,'Fred','Cat')['identity_id']
        assign_identity(self.db,1,fred,'Cat',did)
        assign_identity(self.db,1,None,'Cat',did)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
        dog = create_identity(self.db,'Spot','Dog')['identity_id']
        assign_identity(self.db,1,dog,'Dog',did)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)

    def test_ambiguous_similarities_stay_unknown(self):
        for cid,name in ((1,'Fred'),(2,'Barney')):
            publish_detections(self.db,cid,str(cid),[self.observation()])
            did=get_clip_identities(self.db,cid)[0]['detection_id']
            iid=create_identity(self.db,name,'Cat')['identity_id']
            assign_identity(self.db,cid,iid,'Cat',did)
        with connect(self.db) as conn:
            self.assertIsNone(match_identity(conn,'Cat','test-model',[1.,0.],.9,.05)[0])

    def test_queue_retries_and_only_new_clip_auto_queue(self):
        enable_new_clip_queue(self.db)
        self.assertIsNone(claim_job(self.db))
        queue_clip(self.db,1)
        self.assertEqual(claim_job(self.db)['catalog_id'],1)
        self.assertIsNone(claim_job(self.db))
        self.assertEqual(queue_clip(self.db,1)['state'],'running')
        finish_job(self.db,1,'failed','Unable to decode')
        self.assertEqual(job_status(self.db,1)['message'],'Unable to decode')
        self.assertEqual(queue_clip(self.db,1)['state'],'queued')
        with connect(self.db) as conn:
            conn.execute("INSERT INTO clips(id,filename,video_path,captured_at) VALUES(3,'3.mp4','3.mp4','2026-10-03')")
            self.assertEqual(conn.execute('SELECT state FROM identity_analysis_jobs WHERE catalog_id=3').fetchone()[0],'queued')

    def test_archive_boundary_and_overlap(self):
        root = self.db.parent
        clip = root/'test.mp4'; clip.write_bytes(b'test')
        self.assertEqual(archive_video(root,'test.mp4'),clip)
        with self.assertRaises(ValueError): archive_video(root,'../test.mp4')
        with self.assertRaises(ValueError): archive_video(root,'catalog.db')
        self.assertEqual(overlap([0,0,10,10],[0,0,10,10]),1.)
        self.assertEqual(overlap([0,0,10,10],[20,20,30,30]),0.)

    def test_type_correction_rebuild_uses_current_human_label(self):
        publish_detections(self.db,1,'hash',[self.observation()])
        did=get_clip_identities(self.db,1)[0]['detection_id']
        dog=create_identity(self.db,'Spot','Dog')['identity_id']
        assign_identity(self.db,1,dog,'Dog',did)
        correction=pending_type_corrections(self.db)[0]
        rebuild_reference(self.db,correction,[0.,1.],'dog-model',.8)
        self.assertEqual(pending_type_corrections(self.db),[])
        with connect(self.db) as conn:
            self.assertEqual(match_identity(conn,'Dog','dog-model',[0.,1.],.9,.05)[0],dog)
        assign_identity(self.db,1,None,'Cat',did)
        rebuild_reference(self.db,correction,[0.,1.],'dog-model',.8)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)

    def test_correction_rolls_back_if_learning_fails(self):
        publish_detections(self.db,1,'hash',[self.observation()])
        did=get_clip_identities(self.db,1)[0]['detection_id']
        fred=create_identity(self.db,'Fred','Cat')['identity_id']
        assign_identity(self.db,1,fred,'Cat',did)
        with patch('identity_analysis.learn_confirmed',side_effect=ValueError('bad sample')):
            with self.assertRaises(ValueError): assign_identity(self.db,1,None,'Cat',did)
        self.assertEqual(get_clip_identities(self.db,1)[0]['identity_id'],fred)
        with connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],1)

    def test_analysis_api_worker_requirement_and_crop_scope(self):
        app=FastAPI(); install_identity_api(app,SimpleNamespace(catalog_db_path=self.db))
        with TestClient(app) as client:
            url='/api/v1/clips/catalog/1/analysis'
            self.assertEqual(client.post(url).status_code,503)
            set_worker_status(self.db,'ready','Ready')
            self.assertEqual(client.post(url).json()['state'],'queued')
            publish_detections(self.db,1,'hash',[self.observation()])
            did=get_clip_identities(self.db,1)[0]['detection_id']
            response=client.get(f'/api/v1/clips/catalog/1/identities/{did}/image')
            self.assertEqual(response.content,b'jpeg')
            self.assertEqual(response.headers['content-type'],'image/jpeg')
            self.assertEqual(client.get(f'/api/v1/clips/catalog/2/identities/{did}/image').status_code,404)
            with connect(self.db) as conn:
                conn.execute("UPDATE identity_worker_status SET updated_at='2020-01-01 00:00:00'")
            self.assertEqual(client.post(url).status_code,503)


if __name__ == '__main__': unittest.main()
