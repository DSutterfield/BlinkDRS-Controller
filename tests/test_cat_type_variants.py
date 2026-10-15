import unittest,tempfile,sqlite3,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np,cv2
from cat_type_variants import load_variants


class CatVariantTests(unittest.TestCase):
    def test_only_current_human_cat_crops_teach_and_keep_image_independence(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'catalog.db';cache=Path(folder)/'cache.db'
            c=sqlite3.connect(db)
            c.executescript('''CREATE TABLE clip_detections(detection_id INTEGER,catalog_id INTEGER,
                subject_type TEXT,confirmed INTEGER,model_version TEXT);
                CREATE TABLE identity_evidence(detection_id INTEGER,crop_jpeg BLOB);''')
            refs=[]
            for did,cid,confirmed in ((1,1,1),(2,2,1),(3,3,1),(4,1,0)):
                ok,jpeg=cv2.imencode('.jpg',np.full((64,64,3),did*40,np.uint8));data=jpeg.tobytes()
                c.execute('INSERT INTO clip_detections VALUES(?,?,?,?,?)',(did,cid,'Cat',confirmed,'manual-crop:v1' if cid==1 else 'detector'))
                c.execute('INSERT INTO identity_evidence VALUES(?,?)',(did,data))
                if cid!=1:refs.append(dict(detection_id=did,catalog_id=cid,subject_type='Cat',
                    vector=[1.]+[0.]*383,model_version='model',quality_score=.8,crop_hash=hashlib.sha256(data).hexdigest()))
            c.commit()
            embed=Mock(return_value=([1.]+[0.]*383,'model',.8))
            models=SimpleNamespace(cv2=cv2,pet_version='model',animal_type_embedding=embed)
            result=load_variants(db,models,refs,cache_path=cache,teaching_clip=1,review_manifest=False)
            teachers=[r for r in result if r.get('support_excluded')]
            self.assertEqual(len(teachers),4)
            self.assertEqual({r['detection_id'] for r in teachers},{1})
            self.assertEqual(len({r['crop_hash'] for r in teachers}),1)
            self.assertTrue(all(r['catalog_id']==1 for r in teachers))
            calls=embed.call_count
            self.assertEqual(load_variants(db,models,refs,cache_path=cache,teaching_clip=1,review_manifest=False),result)
            self.assertEqual(embed.call_count,calls)
            c.execute('UPDATE clip_detections SET confirmed=0 WHERE detection_id=1');c.commit()
            self.assertFalse(any(r.get('support_excluded') for r in load_variants(db,models,refs,cache_path=cache,teaching_clip=1,review_manifest=False)))
            c.close()


if __name__=='__main__':unittest.main()
