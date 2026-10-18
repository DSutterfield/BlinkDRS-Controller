import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import cat_type_variants as variants


class ColdCacheTests(unittest.TestCase):
    def exercise(self, kind):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db, cache = root/'catalog.db', root/'cache.db'
            records = {}
            with sqlite3.connect(db) as conn:
                conn.executescript('''CREATE TABLE clip_detections(detection_id INTEGER,
                    catalog_id INTEGER,subject_type TEXT,confirmed INTEGER,model_version TEXT);
                    CREATE TABLE identity_evidence(detection_id INTEGER,crop_jpeg BLOB);''')
                for did in (1, 2, 3):
                    ok, jpeg = cv2.imencode('.jpg', np.full((64, 64, 3), did*40, np.uint8))
                    self.assertTrue(ok)
                    records[did] = jpeg.tobytes()
                    conn.execute('INSERT INTO clip_detections VALUES(?,?,?,?,?)',
                                 (did, did, 'Cat', 1, 'manual-crop:v1' if did == 1 else 'detector'))
                    conn.execute('INSERT INTO identity_evidence VALUES(?,?)', (did, records[did]))
            refs = [dict(detection_id=2, catalog_id=2, subject_type='Cat',
                         vector=[1.]+[0.]*383, model_version='model', quality_score=.8,
                         crop_hash=hashlib.sha256(records[2]).hexdigest())]
            barrier, local = threading.Barrier(2), threading.local()
            def embedding(image):
                if not getattr(local, 'started', False):
                    local.started = True
                    # Both workers have already observed the same missing key.
                    barrier.wait(timeout=10)
                return [1.]+[0.]*383, 'model', .8
            models = SimpleNamespace(cv2=cv2, pet_version='model', animal_type_embedding=embedding)
            kwargs = dict(cache_path=cache, review_manifest=False)
            manifest_hash = variants.REVIEW_MANIFEST_SHA256
            if kind == 'manual':
                kwargs['teaching_clip'] = 1
            elif kind == 'reviewed':
                (root/'review.jpg').write_bytes(records[3])
                manifest = root/'review.json'
                manifest.write_text(json.dumps({'examples': [dict(subject_type='Cat',
                    catalog_id=3, image_file='review.jpg',
                    crop_hash=hashlib.sha256(records[3]).hexdigest())]}))
                kwargs['review_manifest'] = manifest
                manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
            with patch.object(variants, 'REVIEW_MANIFEST_SHA256', manifest_hash):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(variants.load_variants, db, models, refs, **kwargs)
                               for _ in range(2)]
                    results = [f.result() for f in futures]
                self.assertEqual(results[0], results[1])
                models.animal_type_embedding = lambda image: self.fail('Warm cache recomputed')
                self.assertEqual(variants.load_variants(db, models, refs, **kwargs), results[0])
            with sqlite3.connect(cache) as conn:
                total, distinct = conn.execute('select count(*),count(distinct key) from variants').fetchone()
                self.assertGreater(total, 0)
                self.assertEqual(total, distinct)

    def test_both_workers_compute_same_ordinary_variant(self):
        self.exercise('ordinary')

    def test_both_workers_compute_same_manual_teacher(self):
        self.exercise('manual')

    def test_both_workers_compute_same_reviewed_example(self):
        self.exercise('reviewed')
