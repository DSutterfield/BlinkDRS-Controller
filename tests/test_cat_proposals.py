import unittest,tempfile
from pathlib import Path
from unittest.mock import Mock
import cv2,numpy as np
from cat_proposals import CatProposalDetector


class CatProposalTests(unittest.TestCase):
    def test_modified_model_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.onnx';path.write_bytes(b'wrong model')
            engine=Mock()
            with self.assertRaisesRegex(ValueError,'fingerprint'):
                CatProposalDetector(engine,path)
            engine.dnn.readNetFromONNX.assert_not_called()

    def test_letterboxed_boxes_return_to_original_frame(self):
        detector=CatProposalDetector.__new__(CatProposalDetector)
        detector.cv2=cv2;detector.version='test';detector.net=Mock()
        raw=np.zeros((1,84,8400),np.float32)
        raw[0,:4,0]=[320,320,64,128];raw[0,4+15,0]=.9
        detector.net.forward.return_value=raw
        found=detector.detect(np.zeros((720,1280,3),np.uint8))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['subject_type'],'Cat')
        np.testing.assert_allclose(found[0]['box'],[576,232,704,488])

    def test_person_and_nonfinite_outputs_are_not_cat_locations(self):
        detector=CatProposalDetector.__new__(CatProposalDetector)
        detector.cv2=cv2;detector.version='test';detector.net=Mock()
        raw=np.zeros((1,84,8400),np.float32)
        raw[0,:4,0]=[320,320,100,200];raw[0,4,0]=.99
        detector.net.forward.return_value=raw
        self.assertEqual(detector.detect(np.zeros((720,1280,3),np.uint8)),[])
        raw[0,0,0]=float('nan')
        with self.assertRaisesRegex(ValueError,'Invalid'):
            detector.detect(np.zeros((720,1280,3),np.uint8))

    def test_person_proposals_are_separate_and_reuse_one_forward_pass(self):
        detector=CatProposalDetector.__new__(CatProposalDetector)
        detector.cv2=cv2;detector.version='test';detector.net=Mock()
        raw=np.zeros((1,84,8400),np.float32);raw[0,:4,0]=[320,320,100,200];raw[0,4,0]=.88
        detector.net.forward.return_value=raw
        self.assertEqual(detector.detect(np.zeros((720,1280,3),np.uint8)),[])
        self.assertEqual(len(detector.people),1);self.assertEqual(detector.people[0]['subject_type'],'Person')
        detector.net.forward.assert_called_once()
        np.testing.assert_allclose(detector.people[0]['box'],[540,160,740,560])
        raw[0,4,0]=.79
        detector.detect(np.zeros((720,1280,3),np.uint8));self.assertEqual(detector.people,[])

    def test_person_needs_top_class_and_native_size(self):
        detector=CatProposalDetector.__new__(CatProposalDetector)
        detector.cv2=cv2;detector.version='test';detector.net=Mock()
        raw=np.zeros((1,84,8400),np.float32);raw[0,:4,0]=[320,320,100,200];raw[0,4,0]=.88;raw[0,4+15,0]=.9
        detector.net.forward.return_value=raw
        detector.detect(np.zeros((720,1280,3),np.uint8));self.assertEqual(detector.people,[])
        raw[0,4+15,0]=0;raw[0,:4,0]=[320,320,10,20]
        detector.detect(np.zeros((720,1280,3),np.uint8));self.assertEqual(detector.people,[])


if __name__=='__main__':unittest.main()
