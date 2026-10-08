import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_api import install_identity_api
from identity_store import (create_identity, assign_identity, get_clip_identities,
                            remove_detection, ensure_identities, delete_identity, list_identities, mark_unknown_motion)
from catalog_store import list_clips, open_catalog_notifications, delete_clip_by_catalog_id


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / 'catalog.db'
        schema = Path(__file__).resolve().parents[1] / 'sql/catalog_schema_v1.sql'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(schema.read_text())
            for cid, source in ((1, 'pir'), (2, 'local_recording')):
                conn.execute('INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) '
                             'VALUES(?,?,?,?,1,?)', (cid, f'{cid}.mp4', f'{cid}.mp4', '2026-10-03', source))

    def test_multiple_subjects_live_recording_and_notifications(self):
        reader = open_catalog_notifications(self.db)
        self.addCleanup(reader.close)
        baseline = reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
        cat = create_identity(self.db, 'Fred', 'Cat')
        person = create_identity(self.db, 'Dan', 'Person')
        for cid in (1, 2):
            assign_identity(self.db, cid, cat['identity_id'], 'Cat')
            assign_identity(self.db, cid, person['identity_id'], 'Person')
            assign_identity(self.db, cid, None, 'Dog')
        result = list_clips(self.db)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['clips'][0]['identities']), 3)
        self.assertIs(result['clips'][0]['identities'][0]['confirmed'], True)
        self.assertEqual(reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0], baseline + 8)
        assign_identity(self.db, 1, cat['identity_id'], 'Cat')
        self.assertEqual(len(get_clip_identities(self.db, 1)), 3)

    def test_correction_unknown_sample_invalidation_and_deletion(self):
        cat = create_identity(self.db, 'Fred', 'Cat')
        labels = assign_identity(self.db, 1, cat['identity_id'], 'Cat')
        did = labels[0]['detection_id']
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding) VALUES(?,?,?)',
                         (cat['identity_id'], did, b'old'))
        updated = assign_identity(self.db, 1, None, 'Cat', did)
        self.assertIsNone(updated[0]['identity_id'])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0], 0)
        self.assertEqual(remove_detection(self.db, 1, did), [])
        assign_identity(self.db, 1, cat['identity_id'], 'Cat')
        delete_clip_by_catalog_id(self.db, 1)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM clip_detections').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identities').fetchone()[0], 1)

    def test_validation_and_idempotent_migration(self):
        cat = create_identity(self.db, ' Fred ', 'Cat')
        self.assertEqual(create_identity(self.db, 'fred', 'Cat')['identity_id'], cat['identity_id'])
        with self.assertRaises(ValueError): create_identity(self.db, ' ', 'Cat')
        with self.assertRaises(ValueError): assign_identity(self.db, 1, cat['identity_id'], 'Dog')
        with self.assertRaises(LookupError): assign_identity(self.db, 999, None, 'Dog')
        with self.assertRaises(LookupError): assign_identity(self.db, 2, None, 'Dog', 999)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            ensure_identities(conn)
            ensure_identities(conn)
        self.assertEqual(list_clips(self.db)['total'], 2)

    def test_http_routes(self):
        app = FastAPI()
        install_identity_api(app, SimpleNamespace(catalog_db_path=self.db))
        with TestClient(app) as client:
            response = client.post('/api/v1/identities', json={'name': 'Fred', 'subject_type': 'Cat'})
            self.assertEqual(response.status_code, 200)
            iid = response.json()['identity_id']
            url = '/api/v1/clips/catalog/2/identities'
            response = client.put(url, json={'identity_id': iid, 'subject_type': 'Cat'})
            self.assertEqual(response.status_code, 200)
            did = response.json()[0]['detection_id']
            self.assertEqual(client.get(url).json()[0]['name'], 'Fred')
            self.assertEqual(client.put(url, json={'identity_id': iid, 'subject_type': 'Dog'}).status_code, 422)
            self.assertEqual(client.put('/api/v1/clips/catalog/999/identities', json={'subject_type': 'Cat'}).status_code, 404)
            self.assertEqual(client.delete(f'{url}/{did}').json(), [])
            self.assertEqual(client.delete(f'{url}/{did}').status_code, 404)

    def test_delete_profile_retains_subjects_and_removes_references(self):
        reader = open_catalog_notifications(self.db)
        self.addCleanup(reader.close)
        lucy = create_identity(self.db, 'Lucy', 'Person')
        dan = create_identity(self.db, 'Dan', 'Person')
        for cid in (1, 2): assign_identity(self.db, cid, lucy['identity_id'], 'Person')
        assign_identity(self.db, 1, dan['identity_id'], 'Person')
        did = get_clip_identities(self.db, 1)[0]['detection_id']
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('INSERT INTO identity_samples(identity_id,detection_id,embedding) VALUES(?,?,?)',
                         (lucy['identity_id'], did, b'reference'))
        revision = reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
        delete_identity(self.db, lucy['identity_id'])
        self.assertEqual([i['name'] for i in list_identities(self.db)], ['Dan'])
        self.assertEqual(reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0], revision + 3)
        for cid in (1, 2):
            unknown = get_clip_identities(self.db, cid)[0]
            self.assertIsNone(unknown['identity_id'])
            self.assertIsNone(unknown['name'])
            self.assertTrue(unknown['confirmed'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM clips').fetchone()[0], 2)
        corrected = create_identity(self.db, 'Lucy', 'Cat')
        self.assertEqual(corrected['subject_type'], 'Cat')
        with self.assertRaises(LookupError): delete_identity(self.db, 999)

    def test_delete_profile_route(self):
        app = FastAPI()
        install_identity_api(app, SimpleNamespace(catalog_db_path=self.db))
        with TestClient(app) as client:
            iid = client.post('/api/v1/identities', json={'name': 'Lucy', 'subject_type': 'Person'}).json()['identity_id']
            self.assertEqual(client.delete(f'/api/v1/identities/{iid}').status_code, 200)
            self.assertEqual(client.get('/api/v1/identities').json(), [])
            self.assertEqual(client.delete(f'/api/v1/identities/{iid}').status_code, 404)

    def test_unknown_motion_manual_auto_and_subject_override(self):
        from identity_analysis import queue_clip, publish_detections
        self.assertEqual(get_clip_identities(self.db, 1), [])
        queue_clip(self.db, 1)
        publish_detections(self.db, 1, 'clip', [])
        automatic = get_clip_identities(self.db, 1)[0]
        self.assertEqual(automatic['subject_type'], 'Motion')
        self.assertFalse(automatic['confirmed'])
        marked = mark_unknown_motion(self.db, 1)[0]
        self.assertTrue(marked['confirmed'])
        # Reanalysis cannot replace the explicit no-visible-subject review.
        publish_detections(self.db, 1, 'clip', [{'key': 'person'}])
        self.assertTrue(get_clip_identities(self.db, 1)[0]['confirmed'])
        dan = create_identity(self.db, 'Dan', 'Person')
        labels = assign_identity(self.db, 1, dan['identity_id'], 'Person')
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]['name'], 'Dan')
        with self.assertRaises(ValueError): mark_unknown_motion(self.db, 1)
        remove_detection(self.db, 1, labels[0]['detection_id'])
        mark_unknown_motion(self.db, 1)
        self.assertEqual(remove_detection(self.db, 1, 0), [])
        self.assertEqual(len(list_identities(self.db)), 1)

    def test_unknown_motion_route_and_clip_deletion(self):
        reader = open_catalog_notifications(self.db)
        self.addCleanup(reader.close)
        baseline = reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0]
        app = FastAPI()
        install_identity_api(app, SimpleNamespace(catalog_db_path=self.db))
        with TestClient(app) as client:
            url = '/api/v1/clips/catalog/2/unknown-motion'
            response = client.post(url)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()[0]['confirmed'])
            self.assertEqual(client.post('/api/v1/clips/catalog/999/unknown-motion').status_code, 404)
            self.assertEqual(reader.execute('SELECT revision FROM catalog_notifications').fetchone()[0], baseline + 1)
        delete_clip_by_catalog_id(self.db, 2)
        self.assertEqual(reader.execute('SELECT COUNT(*) FROM clip_unknown_motion').fetchone()[0], 0)

    def test_link_image_replaces_manual_name_and_preserves_other_subjects(self):
        from identity_analysis import publish_detections, crop_image
        dan = create_identity(self.db, 'Dan', 'Person')
        trish = create_identity(self.db, 'Trish', 'Person')
        assign_identity(self.db, 1, dan['identity_id'], 'Person')
        assign_identity(self.db, 1, trish['identity_id'], 'Person')
        observation = {'key':'frame1', 'subject_type':'Person', 'confidence':.8,
                       'first_seen_seconds':1., 'last_seen_seconds':3., 'detector_model':'detector',
                       'crop_jpeg':b'jpeg', 'embedding':[1.,0.], 'embedding_model':'face', 'quality_score':.8}
        publish_detections(self.db, 1, 'hash', [observation])
        detected = next(row for row in get_clip_identities(self.db, 1) if row['crop_available'])
        labels = assign_identity(self.db, 1, dan['identity_id'], 'Person', detected['detection_id'])
        named = [row for row in labels if row['identity_id'] == dan['identity_id']]
        self.assertEqual(len(named), 1)
        self.assertTrue(named[0]['crop_available'])
        self.assertEqual(named[0]['first_seen_seconds'], 1.)
        self.assertEqual(crop_image(self.db, 1, named[0]['detection_id']), b'jpeg')
        self.assertEqual([row['name'] for row in labels if not row['crop_available']], ['Trish'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0], 1)


if __name__ == '__main__': unittest.main()

