import unittest
import sqlite3
from contextlib import closing
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_identities
from identity_store import create_identity, assign_identity, get_clip_identities
from identity_analysis import pending_type_corrections, rebuild_reference, publish_detections
from identity_api import install_identity_api
from manual_crops import save_crop, clip_frame


class ManualCropTests(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp

    def test_manual_image_confirmation_learning_and_reanalysis(self):
        cat=create_identity(self.db,'Lucy','Cat')
        assign_identity(self.db,1,cat['identity_id'],'Cat')
        with patch('manual_crops.clip_frame',return_value=b'jpeg'):
            labels=save_crop(self.db,self.db.parent,1,2.,(1,2,64,80),cat['identity_id'],'Cat')
        self.assertEqual(len(labels),1)
        self.assertTrue(labels[0]['crop_available'])
        self.assertEqual(labels[0]['first_seen_seconds'],2.)
        correction=pending_type_corrections(self.db)[0]
        rebuild_reference(self.db,correction,[1.,0.],'pet-model',.8)
        self.assertEqual(pending_type_corrections(self.db),[])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],1)
        publish_detections(self.db,1,'fingerprint',[])
        self.assertEqual(get_clip_identities(self.db,1)[0]['name'],'Lucy')

    def test_unknown_crop_and_invalid_identity(self):
        dog=create_identity(self.db,'Stubby','Dog')
        with patch('manual_crops.clip_frame',return_value=b'jpeg'):
            with self.assertRaises(ValueError):save_crop(self.db,self.db.parent,1,1.,(0,0,64,64),dog['identity_id'],'Cat')
            labels=save_crop(self.db,self.db.parent,1,1.,(0,0,64,64),None,'Dog')
        self.assertTrue(labels[0]['crop_available'])
        self.assertEqual(pending_type_corrections(self.db),[])
        assign_identity(self.db,1,dog['identity_id'],'Dog',labels[0]['detection_id'])
        self.assertEqual(len(pending_type_corrections(self.db)),1)

    def test_frame_validation_and_http(self):
        for seconds in (-1.,float('nan'),float('inf')):
            with self.assertRaises(ValueError):clip_frame(self.db,self.db.parent,1,seconds)
        app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db,archive_root=self.db.parent))
        with TestClient(app) as client, patch('manual_crops.clip_frame',return_value=b'jpeg'):
            base='/api/v1/clips/catalog/1'
            self.assertEqual(client.get(base+'/frame?seconds=2').content,b'jpeg')
            body={'seconds':2,'x':0,'y':0,'width':64,'height':64,'subject_type':'Cat'}
            self.assertEqual(client.post(base+'/manual-crops',json=body).status_code,200)
            self.assertEqual(client.post(base+'/manual-crops',json={**body,'width':1}).status_code,422)
            self.assertEqual(client.post('/api/v1/clips/catalog/999/manual-crops',json=body).status_code,404)
