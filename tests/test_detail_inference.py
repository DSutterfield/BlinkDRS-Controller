import unittest
from unittest.mock import patch
from types import SimpleNamespace
import torch
import numpy as np
from torchvision.models.detection.faster_rcnn import TwoMLPHead
from detail_inference import Fp32RegionHead, optimize_detail_detector

torch.set_num_threads(1)


class DetailInferenceTests(unittest.TestCase):
    def make(self):
        torch.manual_seed(73)
        detector = torch.nn.Module()
        detector.roi_heads = torch.nn.Module()
        detector.roi_heads.box_head = TwoMLPHead(16, 8)
        return detector.eval()

    def test_fp32_output_and_original_weights_preserved(self):
        d=self.make();head=d.roi_heads.box_head
        before={k:v.clone() for k,v in head.state_dict().items()}
        d,mode=optimize_detail_detector(d,{'OPENBLAS_NUM_THREADS':'1'})
        self.assertEqual(mode,'channels-last-numpy-fp32')
        for n in (0,1,17,100):
            x=torch.randn(n,4,2,2)
            with torch.inference_mode(),patch('detail_inference.np.matmul',wraps=np.matmul) as matmul:
                expected=head(x);actual=d.roi_heads.box_head(x)
                self.assertEqual(matmul.call_count,2)
            self.assertEqual(actual.dtype,torch.float32)
            torch.testing.assert_close(actual,expected,rtol=1e-5,atol=1e-5)
        for k,v in before.items():self.assertTrue(torch.equal(v,head.state_dict()[k]))

    def test_noncontiguous_input(self):
        head=self.make().roi_heads.box_head;fast=Fp32RegionHead(head)
        x=torch.randn(7,4,2,2).transpose(2,3)
        with torch.inference_mode():torch.testing.assert_close(fast(x),head(x),rtol=1e-5,atol=1e-5)

    def test_weights_changed_after_setup_use_original(self):
        head=self.make().roi_heads.box_head;fast=Fp32RegionHead(head)
        with torch.no_grad():head.fc6.weight.add_(.1)
        x=torch.randn(7,16)
        with torch.inference_mode(),patch('detail_inference.np.matmul',side_effect=AssertionError('stale kernel')):
            self.assertTrue(torch.equal(fast(x),head(x)))

    def test_training_and_autograd_use_original(self):
        head=self.make().roi_heads.box_head;fast=Fp32RegionHead(head);x=torch.randn(7,16,requires_grad=True)
        with patch('detail_inference.np.matmul',side_effect=AssertionError('no grad kernel')):
            self.assertTrue(torch.equal(fast(x),head(x)))
            fast(x).sum().backward();self.assertIsNotNone(x.grad)
            fast.train()
            with torch.no_grad():self.assertTrue(torch.equal(fast(x),head(x)))

    def test_changed_dtype_uses_original(self):
        head=self.make().roi_heads.box_head;fast=Fp32RegionHead(head);fast.double();x=torch.randn(7,16,dtype=torch.float64)
        with torch.inference_mode(),patch('detail_inference.np.matmul',side_effect=AssertionError('wrong dtype')):
            self.assertTrue(torch.equal(fast(x),head(x)))

    def test_disabled_and_unsupported_preserve_model(self):
        for env in ({'BLINK_DETAIL_OPTIMIZED':'0'},{}, {'OPENBLAS_NUM_THREADS':'2'}):
            d=self.make();head=d.roi_heads.box_head
            result,mode=optimize_detail_detector(d,env)
            self.assertIs(result,d);self.assertIs(d.roi_heads.box_head,head);self.assertTrue(mode.startswith('legacy'))
        d=self.make()
        with patch('detail_inference.torch.__version__','0.0'):
            self.assertEqual(optimize_detail_detector(d,{'OPENBLAS_NUM_THREADS':'1'})[1],'legacy-unsupported')

    def test_unsupported_head_or_training_detector(self):
        d=self.make();d.train()
        self.assertEqual(optimize_detail_detector(d,{'OPENBLAS_NUM_THREADS':'1'})[1],'legacy-unsupported')
        d.eval();d.roi_heads.box_head=torch.nn.Identity()
        self.assertEqual(optimize_detail_detector(d,{'OPENBLAS_NUM_THREADS':'1'})[1],'legacy-unsupported')

    def test_invalid_flag_rejected(self):
        with self.assertRaises(ValueError):optimize_detail_detector(self.make(),{'BLINK_DETAIL_OPTIMIZED':'yes'})


if __name__=='__main__':unittest.main()
