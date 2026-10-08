import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import test_identities
from identity_store import assign_identity, get_clip_identities, connect, remove_detection
from identity_analysis import publish_detections, pending_type_corrections, rebuild_reference
from subject_type_learning import pending, save_reference, load_references, vehicle_supported
from identity_worker import analyze_video


class Learning(unittest.TestCase):
    setUp = test_identities.IdentityTests.setUp

    def evidence(self, clip, kind='Vehicle'):
        publish_detections(self.db,clip,str(clip),[dict(key='frame',subject_type=kind,
            confidence=.9,first_seen_seconds=0,last_seen_seconds=1,detector_model='test',
            crop_jpeg=('image'+str(clip)).encode(),embedding=None,embedding_model=None,quality_score=0)])
        return get_clip_identities(self.db,clip)[0]['detection_id']

    def test_save_unknown_type_learns_without_name_and_rechecks_later_edits(self):
        did=self.evidence(1)
        self.assertEqual(pending(self.db,'visual'),[])
        assign_identity(self.db,1,None,'Dog',did)
        item=pending(self.db,'visual')[0]
        self.assertEqual(item['subject_type'],'Dog')
        self.assertEqual(pending_type_corrections(self.db)[0]['detection_id'],did)
        rebuild_reference(self.db,pending_type_corrections(self.db)[0],[1,0],'visual',.8)
        save_reference(self.db,item,[1,0],'visual',.8)
        save_reference(self.db,item,[1,0],'visual',.8)
        self.assertEqual(len(load_references(self.db,'visual',2)),1)
        self.assertEqual(load_references(self.db,'visual',1),[])
        assign_identity(self.db,1,None,'Cat',did)
        self.assertEqual(load_references(self.db,'visual',2),[])
        save_reference(self.db,item,[1,0],'visual',.8)  # Stale worker result cannot undo edit.
        self.assertEqual(pending(self.db,'visual')[0]['subject_type'],'Cat')
        with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
        remove_detection(self.db,1,did)
        with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM subject_type_samples').fetchone()[0],0)

    def test_detector_vehicle_threshold_and_full_scene_only(self):
        import cv2,numpy as np
        from contextlib import nullcontext
        from identity_models import LocalModels
        model=LocalModels.__new__(LocalModels);model.cv2=cv2
        model.torch=SimpleNamespace(inference_mode=nullcontext)
        model.detector_transform=lambda image:image
        model.detector=lambda images:[dict(labels=[3,18,1],scores=[.84,.6,.7],
            boxes=[np.array([0,0,50,50]),np.array([60,0,110,50]),np.array([120,0,170,50])])]
        found=model.detect(np.zeros((200,200,3),dtype=np.uint8))
        self.assertEqual({d['subject_type'] for d in found},{'Dog','Person'})
        calls=[0]
        def detect(images):
            calls[0]+=1
            return dict(labels=[3],scores=[.86 if calls[0]==1 else .99],boxes=[np.array([0,0,50,50])]),
        model.detector=detect
        found=model.detect(np.zeros((640,640,3),dtype=np.uint8))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['confidence'],.86)

    def test_low_quality_processed_once_model_changes_requeue(self):
        did=self.evidence(1);assign_identity(self.db,1,None,'Dog',did)
        save_reference(self.db,pending(self.db,'visual')[0],None,'visual',0)
        self.assertEqual(pending(self.db,'visual'),[])
        self.assertEqual(load_references(self.db,'visual',2),[])
        self.assertEqual(len(pending(self.db,'new-model')),1)

    def test_vehicle_veto_requires_distinct_confirmed_examples_and_clear_margin(self):
        def sample(clip,image,kind='Dog',model='visual'):
            return dict(catalog_id=clip,crop_hash=image,subject_type=kind,model_version=model,vector=[1.,0.])
        a=sample(1,'a');b=sample(2,'b')
        self.assertTrue(vehicle_supported([a],[1,0],'visual'))
        self.assertTrue(vehicle_supported([a,sample(1,'b')],[1,0],'visual'))
        self.assertTrue(vehicle_supported([a,sample(2,'a')],[1,0],'visual'))
        self.assertFalse(vehicle_supported([a,b],[1,0],'visual'))
        self.assertTrue(vehicle_supported([a,b,sample(3,'c','Vehicle')],[1,0],'visual'))
        self.assertTrue(vehicle_supported([a,b],[0,1],'visual'))
        self.assertTrue(vehicle_supported([a,b],[1,0],'other-model'))

    def test_vehicle_needs_repeated_frames_other_subjects_do_not(self):
        import cv2,numpy as np
        path=Path(self.folder.name)/'sample.mp4'
        out=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),4,(64,64))
        for _ in range(4):out.write(np.zeros((64,64,3),dtype=np.uint8))
        out.release()
        def model(kind,frames):
            calls=[0]
            def detect(frame):
                calls[0]+=1
                return [dict(subject_type=kind,confidence=.9,box=[0,0,60,60])] if calls[0]<=frames else []
            return SimpleNamespace(cv2=cv2,detect=detect,embedding=lambda *args:(None,None,0),
                accepts_vehicle=lambda crop:True,detector_version='test')
        with patch('identity_worker.time.sleep'):
            self.assertEqual(analyze_video(path,model('Vehicle',1)),[])
            self.assertEqual(len(analyze_video(path,model('Vehicle',2))),1)
            self.assertEqual(len(analyze_video(path,model('Dog',1))),1)


if __name__=='__main__':unittest.main()
