import unittest,types
import torch
from torchvision.models.detection.ssd import SSD
from torchvision.models.detection._utils import BoxCoder
from ssd_postprocess import EarlySsdPostprocess

torch.set_num_threads(1)
class SsdPostprocessTests(unittest.TestCase):
 def make(self,n=400):
  m=types.SimpleNamespace(score_thresh=.001,topk_candidates=300,detections_per_img=300,nms_thresh=.55,box_coder=BoxCoder((10.,10.,5.,5.)))
  m.postprocess_detections=types.MethodType(SSD.postprocess_detections,m)
  anchors=torch.tensor([[float(i%20*20),float(i//20*20),float(i%20*20+10),float(i//20*20+10)] for i in range(n)])
  probs=torch.full((n,91),.0011);probs[:,0]=1-probs[:,1:].sum(1)
  return m,anchors,probs
 def run_case(self,m,anchors,probs):
  logits=probs.log().unsqueeze(0);heads={'cls_logits':logits,'bbox_regression':torch.zeros((1,len(anchors),4))}
  baseline=m.postprocess_detections(heads,[anchors],[(512,512)])
  fast=EarlySsdPostprocess(m);result=fast(heads,[anchors],[(512,512)])
  def accepted(r):
   mask=(r['scores']>=.30)
   return {k:v[mask] for k,v in r.items()}
  for a,b in zip(baseline,result):
   for k in a:self.assertTrue(torch.equal(accepted(a)[k],accepted(b)[k]),k)
  return fast,baseline,result
 def put(self,p,i,label,value):p[i,label]=value;p[i,0]=1-p[i,1:].sum()
 def test_fast_path_preserves_order_boxes_and_every_consumer_boundary(self):
  m,a,p=self.make()
  for i,(label,value) in enumerate([(17,.29999),(17,.30001),(18,.54999),(18,.55001),(1,.64999),(1,.65001),(3,.84999),(3,.85001)]):self.put(p,i,label,value)
  f,_,_=self.run_case(m,a,p);self.assertEqual(f.fast_images,1)
 def test_tied_scores_use_original_path(self):
  m,a,p=self.make();self.put(p,0,17,.6);self.put(p,1,17,.6)
  f,b,r=self.run_case(m,a,p);self.assertEqual(f.fallback_images,1)
  for key in b[0]:self.assertTrue(torch.equal(b[0][key],r[0][key]))
 def test_crowded_global_limit_uses_original_path(self):
  m,a,p=self.make()
  for i,value in enumerate(torch.linspace(.31,.7,400)):self.put(p,i,17,float(value))
  f,_,_=self.run_case(m,a,p);self.assertEqual(f.fallback_images,1)
 def test_legacy_coordinate_backend_uses_original_path(self):
  m,a,p=self.make(5);self.put(p,0,18,.6)
  f,_,_=self.run_case(m,a,p);self.assertEqual(f.fallback_images,1)
 def test_near_nms_boundary_keeps_legacy_local_coordinates(self):
  for distance in (2.90322,2.90323):
   m,a,p=self.make();a[1]=torch.tensor([distance,0.,distance+10.,10.]);self.put(p,0,17,.7);self.put(p,1,17,.6)
   f,_,_=self.run_case(m,a,p);self.assertEqual(f.fast_images,1)
 def test_empty_consumed_set_fast_path(self):
  m,a,p=self.make();f,_,_=self.run_case(m,a,p);self.assertEqual(f.fast_images,1)
 def test_disabled_and_unsupported_versions_use_legacy(self):
  m,a,p=self.make();heads={'cls_logits':p.log().unsqueeze(0),'bbox_regression':torch.zeros((1,len(a),4))}
  for field in ('enabled','supported'):
   f=EarlySsdPostprocess(m);setattr(f,field,False);f(heads,[a],[(512,512)]);self.assertEqual(f.fallback_images,1)
