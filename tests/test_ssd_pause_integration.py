import unittest,threading,time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from types import SimpleNamespace
import json,numpy as np,cv2
from ssd_views import iter_ssd_views
from identity_worker import wait_for_controller

class PairPauseIntegrationTests(unittest.TestCase):
 def test_three_iterators_pause_between_pairs_through_liveview_and_saving(self):
  state={'active':False,'recording':'idle'};lock=threading.Lock();counts={};paused=threading.Event()
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    payload=json.dumps({'active':state['active']} if self.path.endswith('/status') else {'state':state['recording']}).encode();self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  barrier=threading.Barrier(3,action=lambda:state.update(active=True));calls=[0,0,0]
  def note():
   with lock:
    tid=threading.get_ident();counts[tid]=counts.get(tid,0)+1
    if len(counts)==3:paused.set()
  def task(index):
   def detector(images):
    calls[index]+=1
    if calls[index]==1:barrier.wait(timeout=10)
    return [{'n':int(im[0,0,0])} for im in images]
   m=SimpleNamespace(ssd_batch_size=2,cv2=cv2,torch=SimpleNamespace(inference_mode=nullcontext),detector_transform=np.asarray,detector=detector)
   checkpoint=lambda:wait_for_controller(True,'http://127.0.0.1:'+str(server.server_port),note)
   return [x[3]['n'] for x in iter_ssd_views(m,[(np.full((2,2,3),i,np.uint8),0,0) for i in range(3)],checkpoint)]
  try:
   with ThreadPoolExecutor(max_workers=3) as pool:
    futures=[pool.submit(task,i) for i in range(3)]
    self.assertTrue(paused.wait(10));self.assertEqual(calls,[1,1,1])
    state.update(active=False,recording='saving');time.sleep(5.3)
    self.assertEqual(calls,[1,1,1]);self.assertFalse(any(f.done() for f in futures))
    state['recording']='idle'
    self.assertEqual([f.result(timeout=8) for f in futures],[[0,1,2]]*3)
    self.assertEqual(calls,[2,2,2])
  finally:
   state.update(active=False,recording='idle');server.shutdown();server.server_close()
