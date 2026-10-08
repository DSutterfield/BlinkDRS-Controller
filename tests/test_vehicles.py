import sqlite3,tempfile,unittest
from pathlib import Path
from contextlib import closing
from test_reference_photos import Photos
from identity_store import connect,assign_identity,get_clip_identities
from identity_vehicle_migration import ensure_vehicle_subjects
from identity_models import LocalModels
class Vehicles(Photos):
 def test_unknown_vehicle_assignment_and_no_named_profiles(self):
  with connect(self.db) as c:c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) VALUES(1,'1.mp4','1.mp4','2026-10-05',1,'pir')")
  r=self.api.put('/api/v1/clips/catalog/1/identities',json={'subject_type':'Vehicle','identity_id':None});self.assertEqual(r.status_code,200);self.assertEqual(r.json()[0]['subject_type'],'Vehicle');self.assertIsNone(r.json()[0]['identity_id'])
  self.assertEqual(self.api.post('/api/v1/identities',json={'name':'Car','subject_type':'Vehicle'}).status_code,422)
  self.assertEqual(self.api.put('/api/v1/clips/catalog/1/identities',json={'subject_type':'Vehicle','identity_id':self.i}).status_code,422)
  self.assertEqual(LocalModels.__new__(LocalModels).embedding(None,'Vehicle'),(None,None,0))
 def test_legacy_migration_preserves_links_and_triggers(self):
  target=Path(self.tmp.name)/'legacy.db'
  schema=((Path(__file__).parent if (Path(__file__).parent/'sql').is_dir() else Path(__file__).parent.parent)/'sql/catalog_schema_v1.sql').read_text()
  with connect(self.db) as source:
   definitions={n:source.execute('SELECT sql FROM sqlite_master WHERE name=?',(n,)).fetchone()[0] for n in ('identities','clip_detections','identity_samples','identity_evidence')}
  with closing(sqlite3.connect(target)) as c:
   c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.executescript(schema)
   for sql in definitions.values():c.execute(sql.replace(",'Vehicle'",'').replace(",'Unknown'",''))
   c.execute("INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) VALUES(1,'1.mp4','1.mp4','2026-10-05',1,'pir')")
   c.execute("INSERT INTO identities(identity_id,name,subject_type) VALUES(1,'Original','Person')")
   c.execute("INSERT INTO clip_detections(detection_id,catalog_id,identity_id,subject_type,confirmed) VALUES(5,1,1,'Person',1)")
   c.execute("INSERT INTO identity_samples(sample_id,identity_id,detection_id,embedding) VALUES(7,1,5,'[1,0]')")
   c.execute("INSERT INTO identity_evidence(detection_id,catalog_id,detection_key,subject_type,crop_jpeg) VALUES(5,1,'original','Person',?)",(b'preserved',))
   c.executescript('CREATE TABLE migration_counter(value INTEGER);INSERT INTO migration_counter VALUES(0);CREATE TRIGGER old_subject_trigger AFTER UPDATE ON clip_detections BEGIN UPDATE migration_counter SET value=value+1; END;CREATE INDEX old_subject_index ON clip_detections(catalog_id);')
   ensure_vehicle_subjects(c);ensure_vehicle_subjects(c)
   self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1);self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
   self.assertEqual(c.execute('SELECT detection_id FROM identity_samples WHERE sample_id=7').fetchone()[0],5)
   self.assertEqual(c.execute('SELECT crop_jpeg FROM identity_evidence').fetchone()[0],b'preserved')
   c.execute("UPDATE clip_detections SET subject_type='Vehicle',identity_id=NULL WHERE detection_id=5")
   self.assertEqual(c.execute('SELECT value FROM migration_counter').fetchone()[0],1)
 def test_detector_vehicle_categories(self):
  import cv2,numpy as np
  from types import SimpleNamespace
  from contextlib import nullcontext
  model=LocalModels.__new__(LocalModels);model.cv2=cv2;model.torch=SimpleNamespace(inference_mode=nullcontext);model.detector_transform=lambda image:image
  model.detector=lambda images:[{'labels':[3,4,6,8],'scores':[.9]*4,'boxes':[np.array([i*45,0,i*45+40,40]) for i in range(4)]}]
  detections=model.detect(np.zeros((200,200,3),dtype=np.uint8))
  self.assertEqual(len(detections),4);self.assertTrue(all(d['subject_type']=='Vehicle' for d in detections))
if __name__=='__main__':unittest.main()
