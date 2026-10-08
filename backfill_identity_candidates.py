"""Bounded possible-match indexing with short per-sighting transactions."""
import argparse,json
from identity_store import connect
from identity_candidates import rank_candidates

def backfill(db,limit=1000):
 with connect(db) as conn:
  rows=list(conn.execute('''SELECT d.detection_id,d.catalog_id,d.subject_type,e.embedding,e.embedding_model FROM clip_detections d JOIN identity_evidence e USING(detection_id) JOIN clips c ON c.id=d.catalog_id WHERE d.confirmed=0 AND c.local_present=1 AND d.subject_type IN ('Cat','Dog') AND e.embedding_model LIKE 'dinov2-small-cls-v1:%' AND e.embedding IS NOT NULL ORDER BY datetime(c.captured_at) DESC LIMIT ?''',(min(limit,1000),)))
  indexed=skipped=0
  for r in rows:
   ranks=rank_candidates(conn,r['subject_type'],r['embedding_model'],json.loads(r['embedding']),r['catalog_id'])
   conn.execute('BEGIN IMMEDIATE')
   current=conn.execute('SELECT d.*,e.embedding,e.embedding_model FROM clip_detections d JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=?',(r['detection_id'],)).fetchone()
   if current is None or current['confirmed'] or current['embedding']!=r['embedding'] or current['embedding_model']!=r['embedding_model'] or current['subject_type']!=r['subject_type']:
    conn.rollback();skipped+=1;continue
   before=tuple(current);conn.execute('DELETE FROM identity_candidates WHERE detection_id=?',(r['detection_id'],))
   for index,item in enumerate(ranks[:3]):conn.execute('INSERT INTO identity_candidates VALUES(?,?,?,?,?)',(r['detection_id'],item['identity_id'],index+1,item['score'],'household-ridge-v1-possible'))
   after=conn.execute('SELECT d.*,e.embedding,e.embedding_model FROM clip_detections d JOIN identity_evidence e USING(detection_id) WHERE d.detection_id=?',(r['detection_id'],)).fetchone()
   assert before==tuple(after),'Label preservation failed; current transaction will roll back'
   conn.commit();indexed+=bool(ranks)
  return dict(examined=len(rows),indexed=indexed,skipped_changed=skipped,labels_preserved=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--limit',type=int,default=1000);a=p.parse_args();print(json.dumps(backfill(a.db,a.limit)))
