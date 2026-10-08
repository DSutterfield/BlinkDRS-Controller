import sqlite3,tempfile,unittest,json
from pathlib import Path
from identity_store import connect
from species_replay import prepare,presence_comparison
class ReplayTests(unittest.TestCase):
 def test_batch_neighbors_duplicates_and_live_labels_are_isolated(self):
  with tempfile.TemporaryDirectory() as folder:
   db=Path(folder)/'snapshot.db';out=Path(folder)/'results'
   with sqlite3.connect(db) as c:c.executescript((Path(__file__).parent.parent/'sql/catalog_schema_v1.sql').read_text())
   with connect(db) as c:
    c.execute("INSERT INTO systems(id,blink_system_id,name) VALUES(1,'sys','Fixture')")
    c.execute("INSERT INTO devices(id,blink_device_id,system_id,name) VALUES(1,'cam',1,'Fixture camera')")
    for cid,date in [(1,'2026-10-08T10:00:00+00:00'),(2,'2026-10-08T12:00:00+00:00'),(3,'2026-10-08T10:03:00+00:00'),(4,'2026-10-08T15:00:00+00:00'),(5,'2026-10-08T17:00:00+00:00')]:
     c.execute('INSERT INTO clips(id,filename,video_path,captured_at,local_present,source) VALUES(?,?,?,?,1,?)',(cid,f'{cid}.mp4',f'{cid}.mp4',date,'pir'))
     c.execute('UPDATE clips SET device_id=1,system_id=1 WHERE id=?',(cid,))
     c.execute("INSERT INTO clip_detections(detection_id,catalog_id,subject_type,confirmed) VALUES(?,?,'Dog',1)",(cid,cid))
     c.execute("INSERT INTO identity_evidence(detection_id,catalog_id,detection_key,subject_type,crop_jpeg) VALUES(?,?,?,'Dog',?)",(cid,cid,str(cid),b'one' if cid in (1,4) else str(cid).encode()))
     import hashlib
     h=hashlib.sha256(b'one' if cid in (1,4) else str(cid).encode()).hexdigest()
     c.execute("INSERT INTO subject_type_samples VALUES(?,'Dog','[1,0]','test',.8,?)",(cid,h))
   before=db.read_bytes();manifest=prepare(db,out,[1,2]);self.assertEqual(db.read_bytes(),before)
   self.assertEqual(manifest['excluded_clip_ids'],[1,2,3])
   with sqlite3.connect(out/'inference.db') as c:
    self.assertEqual(c.execute('SELECT detection_id FROM subject_type_samples').fetchall(),[(5,)])
    self.assertEqual(c.execute('SELECT count(*) FROM clip_detections WHERE catalog_id IN (1,2)').fetchone()[0],0)
    self.assertEqual(c.execute('SELECT count(*) FROM identity_samples').fetchone()[0],0)
   answers=json.loads((out/'answer-key.json').read_text());self.assertEqual(len(answers),2)
   self.assertTrue((out/answers[0]['confirmed_labels'][0]['image']).is_file())
 def test_comparison_does_not_treat_incomplete_review_as_ground_truth_absence(self):
  r=presence_comparison(['Dog'],[dict(subject_type='Person'),dict(subject_type='Unknown')])
  self.assertEqual(r['expected_types_not_observed'],['Dog']);self.assertEqual(r['additional_types_to_review'],['Person']);self.assertEqual(r['unknown_appearances'],1)
  self.assertNotIn('accuracy',r);self.assertNotIn('false_positives',r)
if __name__=='__main__':unittest.main()
