import sqlite3,unittest
from unittest.mock import patch
import test_identity_analysis
from identity_analysis import publish_detections,crop_image
from identity_store import connect,get_clip_identities,assign_identity,create_identity
from identity_unknown_migration import ensure_unknown_subjects
from identity_api import Assignment,ManualCrop
from subject_type_learning import pending
class UnknownTypeTests(unittest.TestCase):
 setUp=test_identity_analysis.AnalysisTests.setUp
 observation=test_identity_analysis.AnalysisTests.observation
 def test_unknown_keeps_crop_without_matching_or_training(self):
  with patch('identity_analysis.match_identity',side_effect=AssertionError('Unknown must not match identities')):
   publish_detections(self.db,1,'unknown',[self.observation(kind='Unknown')])
  row=get_clip_identities(self.db,1)[0];did=row['detection_id']
  self.assertEqual(row['subject_type'],'Unknown');self.assertIsNone(row['identity_id']);self.assertFalse(row['confirmed'])
  self.assertEqual(crop_image(self.db,1,did),b'jpeg')
  assign_identity(self.db,1,None,'Unknown',did)
  self.assertEqual(pending(self.db,'fixture'),[])
  with connect(self.db) as c:
   self.assertEqual(c.execute('SELECT count(*) FROM identity_samples').fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM identity_candidates').fetchone()[0],0)
  publish_detections(self.db,1,'retry',[])
  self.assertEqual(crop_image(self.db,1,did),b'jpeg')
 def test_unknown_assignment_api_and_named_profile_guards(self):
  self.assertEqual(Assignment(subject_type='Unknown').subject_type,'Unknown')
  self.assertEqual(ManualCrop(subject_type='Unknown',seconds=0,x=0,y=0,width=20,height=20).subject_type,'Unknown')
  iid=create_identity(self.db,'Trish','Person')['identity_id']
  with self.assertRaises(ValueError):assign_identity(self.db,1,iid,'Unknown')
  with self.assertRaises(ValueError):create_identity(self.db,'Anything','Unknown')
 def test_migration_preserves_rows_links_crops_and_triggers(self):
  c=sqlite3.connect(':memory:');self.addCleanup(c.close);c.execute('PRAGMA foreign_keys=ON')
  c.executescript("""CREATE TABLE clips(id INTEGER PRIMARY KEY);INSERT INTO clips VALUES(1);
  CREATE TABLE clip_detections(detection_id INTEGER PRIMARY KEY,catalog_id INTEGER REFERENCES clips(id),subject_type TEXT CHECK(subject_type IN ('Person','Cat','Dog','Vehicle')),confirmed INTEGER);
  INSERT INTO clip_detections VALUES(5,1,'Person',1);
  CREATE TABLE evidence(detection_id INTEGER REFERENCES clip_detections(detection_id),crop BLOB);INSERT INTO evidence VALUES(5,X'010203');
  CREATE TABLE counter(value INTEGER);INSERT INTO counter VALUES(0);
  CREATE INDEX original_idx ON clip_detections(catalog_id);
  CREATE TRIGGER original_trigger AFTER UPDATE ON clip_detections BEGIN UPDATE counter SET value=value+1; END;""")
  before=c.execute('SELECT * FROM clip_detections').fetchall()
  ensure_unknown_subjects(c);ensure_unknown_subjects(c)
  self.assertEqual(c.execute('SELECT * FROM clip_detections').fetchall(),before)
  self.assertEqual(c.execute('SELECT * FROM evidence').fetchone(),(5,b'\x01\x02\x03'))
  self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0],1);self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
  c.execute("UPDATE clip_detections SET subject_type='Unknown' WHERE detection_id=5")
  self.assertEqual(c.execute('SELECT value FROM counter').fetchone()[0],1)
if __name__=='__main__':unittest.main()
