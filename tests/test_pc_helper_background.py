import json,sys,subprocess,tempfile,unittest,os,time
from pathlib import Path
from unittest.mock import patch
from pc_helper_background import WindowsJobs,cleanup_completed,atomic_json,Transport
class BackgroundTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_atomic_control_roundtrip(self):
  path=self.root/'config.json';atomic_json(path,dict(enabled=False));self.assertFalse(json.loads(path.read_text())['enabled']);self.assertEqual(list(self.root.glob('*.partial')),[])
 def test_cleanup_only_completed_transport_copies(self):
  folder=self.root/('a'*32);folder.mkdir();(folder/'receipt.json').write_text('{}');(folder/'video.mp4').write_bytes(b'copy');(folder/'references.sqlite').write_bytes(b'copy');(folder/'pending-result.json').write_bytes(b'evidence');original=self.root/'original.mp4';original.write_bytes(b'original');cleanup_completed(self.root);self.assertFalse((folder/'video.mp4').exists());self.assertTrue((folder/'pending-result.json').exists());self.assertEqual(original.read_bytes(),b'original')
 def test_unfinished_inputs_are_retained(self):
  folder=self.root/('b'*32);folder.mkdir();(folder/'video.mp4').write_bytes(b'pending');cleanup_completed(self.root);self.assertTrue((folder/'video.mp4').exists())
 @unittest.skipUnless(os.name=='nt','Windows process ownership')
 def test_job_close_terminates_owned_child(self):
  jobs=WindowsJobs();child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=0x08000000);self.addCleanup(lambda:child.kill() if child.poll() is None else None);jobs.add(child.pid);jobs.close();child.wait(timeout=10);self.assertIsNotNone(child.returncode)
 @unittest.skipUnless(os.name=='nt','Windows single instance')
 def test_duplicate_supervisor_exits_before_work(self):
  import msvcrt
  config=self.root/'config.json';config.write_text(json.dumps(dict(enabled=False,data=str(self.root))));lock=(self.root/'supervisor.lock').open('w+b');lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
  try:
   script=Path(__file__).resolve().parents[1]/'pc_helper_background.py';p=subprocess.run([sys.executable,str(script),'--config',str(config)],timeout=15,creationflags=0x08000000);self.assertEqual(p.returncode,0);self.assertIn('Another helper supervisor',(self.root/'logs/supervisor.log').read_text());self.assertFalse((self.root/'status.json').exists())
  finally:lock.close()
 def test_transport_hides_console(self):
  transport=Transport(dict(transport='transport.exe'))
  with patch('subprocess.run',return_value=type('Result',(),dict(returncode=0,stdout='ok',stderr=''))()) as run:self.assertEqual(transport.command('probe'),'ok');self.assertEqual(run.call_args.kwargs['creationflags'],0x08000000 if os.name=='nt' else 0)
if __name__=='__main__':unittest.main()
