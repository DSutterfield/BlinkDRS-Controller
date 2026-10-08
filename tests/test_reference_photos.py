import base64,io,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from identity_api import install_identity_api
from identity_store import create_identity,connect,delete_identity
from reference_photos import process_photos
from identity_analysis import match_identity
class Photos(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Path(self.tmp.name)/'test.db'
  with closing(sqlite3.connect(self.db)) as c:c.executescript(((Path(__file__).parent if (Path(__file__).parent/'sql').is_dir() else Path(__file__).parent.parent)/'sql/catalog_schema_v1.sql').read_text())
  self.i=create_identity(self.db,'Test','Person')['identity_id']; app=FastAPI();install_identity_api(app,SimpleNamespace(catalog_db_path=self.db));self.api=TestClient(app);self.url=f'/api/v1/identities/{self.i}/photos'
 def upload(self):
  b=io.BytesIO();Image.new('RGB',(100,100),'red').save(b,format='PNG');r=self.api.post(self.url,json={'filename':'test.png','image_base64':base64.b64encode(b.getvalue()).decode()});self.assertEqual(r.status_code,200);return r.json()['photo_id']
 def models(self,vector):
  import cv2
  return SimpleNamespace(cv2=cv2,embedding=lambda crop,kind:(vector,'fixture',.9))
 def test_ready_match_remove_and_preserve_labels(self):
  p=self.upload();self.assertEqual(self.api.get(self.url).json()[0]['state'],'pending');self.assertEqual(self.api.get(f'{self.url}/{p}/image').content[:2],b'\xff\xd8')
  process_photos(self.db,self.models([1.,0.]));self.assertEqual(self.api.get(self.url).json()[0]['state'],'ready')
  with connect(self.db) as c:self.assertEqual(match_identity(c,'Person','fixture',[1.,0.],.8,.1)[0],self.i)
  self.assertEqual(self.api.delete(f'{self.url}/{p}').status_code,200)
  with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0);self.assertEqual(c.execute('SELECT COUNT(*) FROM clip_detections').fetchone()[0],0)
 def test_reject_retry_and_deleted_during_inference(self):
  p=self.upload();process_photos(self.db,self.models(None));self.assertEqual(self.api.get(self.url).json()[0]['state'],'rejected');self.assertEqual(self.api.post(f'{self.url}/{p}/retry').status_code,200)
  process_photos(self.db,self.models([1.,0.]),lambda:self.api.delete(f'{self.url}/{p}'))
  with connect(self.db) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM identity_samples').fetchone()[0],0)
 def test_invalid_identity_invalid_image_and_profile_delete(self):
  self.assertEqual(self.api.post(self.url,json={'filename':'bad','image_base64':'abcd'}).status_code,422)
  p=self.upload();delete_identity(self.db,self.i);self.assertEqual(self.api.get(self.url).json(),[]);self.assertEqual(self.api.get(f'{self.url}/{p}/image').status_code,404)
 def test_about(self):self.assertEqual(self.api.get('/api/v1/about').json()['version'],'1.1.3')
if __name__=='__main__':unittest.main()


