import hashlib,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from identity_video_decode import SampledFrameReader

class Capture:
 def __init__(self,frames,fail_seek=None):self.frames=frames;self.position=0;self.fail_seek=fail_seek;self.fail=False;self.released=False;self.seeks=[];self.reads=0
 def set(self,key,index):self.seeks.append(index);self.position=index;self.fail=index==self.fail_seek
 def read(self):
  self.reads+=1
  if self.fail or self.position>=len(self.frames):return False,None
  frame=self.frames[self.position];self.position+=1;return True,frame
 def release(self):self.released=True

class DecoderRecoveryTests(unittest.TestCase):
 def reader(self,frames,fail_seek=None,sequential=None,checkpoint=None):
  first=Capture(frames,fail_seek);second=Capture(frames if sequential is None else sequential);opened=[]
  def factory(path):opened.append(path);return first if len(opened)==1 else second
  return SampledFrameReader('original.mp4',NS(VideoCapture=factory,CAP_PROP_POS_FRAMES=1),checkpoint),first,second,opened
 def test_healthy_seek_keeps_exact_fast_path(self):
  frames=[object() for _ in range(10)];r,a,b,opened=self.reader(frames)
  self.assertIs(r.read(2),frames[2]);self.assertIs(r.read(9),frames[9]);self.assertEqual(a.seeks,[2,9]);self.assertEqual(len(opened),1);r.close();self.assertTrue(a.released)
 def test_failed_tail_seek_recovers_exact_source_frame(self):
  frames=[object() for _ in range(10)];r,a,b,opened=self.reader(frames,8)
  self.assertIs(r.read(0),frames[0]);self.assertIs(r.read(8),frames[8]);self.assertIs(r.read(9),frames[9]);self.assertEqual(len(opened),2);self.assertTrue(a.released);self.assertEqual(b.seeks,[]);self.assertEqual(b.reads,10);r.close();self.assertTrue(b.released)
 def test_unavailable_frame_does_not_substitute_previous_frame(self):
  r,a,b,_=self.reader(list(range(10)),9,sequential=list(range(8)))
  with self.assertRaisesRegex(ValueError,'target 9, stopped at 8'):r.read(9)
  r.close();self.assertTrue(a.released);self.assertTrue(b.released)
 def test_midstream_failure_recovers_without_skipping_later_samples(self):
  r,a,b,_=self.reader(list(range(50)),10)
  self.assertEqual([r.read(i) for i in [0,10,20,49]],[0,10,20,49]);self.assertEqual(b.reads,50);r.close()
 def test_recovery_remains_interruptible(self):
  calls=[]
  def checkpoint():
   calls.append(1)
   if len(calls)==2:raise RuntimeError('cancelled')
  r,a,b,_=self.reader(list(range(100)),99,checkpoint=checkpoint)
  with self.assertRaisesRegex(RuntimeError,'cancelled'):r.read(99)
  r.close();self.assertTrue(b.released);self.assertEqual(b.reads,32)
 def test_nonmonotonic_recovery_is_not_silently_misread(self):
  r,*_=self.reader(list(range(10)),8);r.read(8)
  with self.assertRaisesRegex(ValueError,'increasing'):r.read(7)
  r.close()
 def test_real_video_integration_preserves_all_sample_pixels_and_original(self):
  import cv2,numpy as np
  from identity_worker import analyze_video,sampled_frame_indices
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'video.mp4';writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(48,32))
   for i in range(70):writer.write(np.full((32,48,3),i*3,np.uint8))
   writer.release();before=hashlib.sha256(path.read_bytes()).hexdigest();expected={};cap=cv2.VideoCapture(str(path));indices,_=sampled_frame_indices(cap.get(cv2.CAP_PROP_FRAME_COUNT));last=indices[-1]
   for i in indices:cap.set(cv2.CAP_PROP_POS_FRAMES,i);ok,f=cap.read();self.assertTrue(ok);expected[i]=hashlib.sha256(f.tobytes()).hexdigest()
   cap.release();opened=[]
   class FaultySeek:
    def __init__(self,path):self.raw=cv2.VideoCapture(path);self.fail=False
    def __getattr__(self,name):return getattr(self.raw,name)
    def set(self,key,value):self.fail=value==last;return self.raw.set(key,value)
    def read(self):return (False,None) if self.fail else self.raw.read()
   def factory(path):
    result=FaultySeek(path) if not opened else cv2.VideoCapture(path);opened.append(result);return result
   proxy=NS(VideoCapture=factory,CAP_PROP_FPS=cv2.CAP_PROP_FPS,CAP_PROP_FRAME_COUNT=cv2.CAP_PROP_FRAME_COUNT,CAP_PROP_POS_FRAMES=cv2.CAP_PROP_POS_FRAMES)
   seen=[];models=NS(cv2=proxy,pet_version='test',detector_version='test',detect=lambda f:seen.append(hashlib.sha256(f.tobytes()).hexdigest()) or [])
   with patch('identity_worker.time.sleep'):self.assertEqual(analyze_video(path,models),[])
   self.assertEqual(seen,[expected[i] for i in indices]);self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before);self.assertEqual(len(opened),2)
