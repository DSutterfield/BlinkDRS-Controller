import unittest
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock
import numpy as np,cv2
from ssd_views import iter_ssd_views
from identity_models import ssd_configuration

class SsdViewTests(unittest.TestCase):
 def models(self,size):
  m=SimpleNamespace(ssd_batch_size=size,cv2=cv2,torch=SimpleNamespace(inference_mode=nullcontext),detector_transform=lambda im:np.asarray(im))
  m.detector=Mock(side_effect=lambda images:[dict(pixel=int(im[0,0,0])) for im in images])
  return m
 def test_pair_order_offsets_odd_tail_and_checkpoints(self):
  m=self.models(2);events=[]
  def checkpoint():events.append(m.detector.call_count)
  views=[(np.full((3,4,3),i,np.uint8),i*7,i*9) for i in range(5)]
  results=list(iter_ssd_views(m,views,checkpoint))
  self.assertEqual([r[3]['pixel'] for r in results],list(range(5)))
  self.assertEqual([(r[1],r[2]) for r in results],[(i*7,i*9) for i in range(5)])
  self.assertEqual([len(c.args[0]) for c in m.detector.call_args_list],[2,2,1]);self.assertEqual(events,[0,1,2])
 def test_legacy_is_one_view_and_original_order(self):
  m=self.models(1);v=[(np.full((2,2,3),i,np.uint8),0,0) for i in range(3)]
  self.assertEqual([x[3]['pixel'] for x in iter_ssd_views(m,v)],list(range(3)))
  self.assertEqual([len(c.args[0]) for c in m.detector.call_args_list],[1,1,1])
 def test_pause_before_transform_and_inference(self):
  m=self.models(2);m.detector_transform=Mock()
  def stop():raise InterruptedError('paused')
  with self.assertRaises(InterruptedError):list(iter_ssd_views(m,[(np.zeros((2,2,3),np.uint8),0,0)],stop))
  m.detector_transform.assert_not_called();m.detector.assert_not_called()
 def test_bad_output_count_fails_without_associating_wrong_view(self):
  m=self.models(2);m.detector.return_value=[];m.detector.side_effect=None
  with self.assertRaisesRegex(RuntimeError,'number of views'):list(iter_ssd_views(m,[(np.zeros((2,2,3),np.uint8),0,0)]))
 def test_configuration_and_safe_floor(self):
  self.assertEqual(ssd_configuration({}),(2,.29));self.assertEqual(ssd_configuration({'BLINK_SSD_OPTIMIZED':'0'}),(1,.001))
  self.assertLess(ssd_configuration({})[1],.30)
  for value in ('2','yes','',None):
   with self.assertRaises(ValueError):ssd_configuration({'BLINK_SSD_OPTIMIZED':value})
 def test_empty_inputs_make_no_calls(self):
  m=self.models(2);self.assertEqual(list(iter_ssd_views(m,[])),[]);m.detector.assert_not_called()
