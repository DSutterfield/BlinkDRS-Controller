"""Refresh a bounded set of unnamed dog appearances; human confirmations are untouched."""
import argparse
import hashlib
import json
from identity_store import connect
from dog_identity import recommend_identity


def _gallery_token(conn):
    profiles=list(conn.execute("SELECT identity_id,name,active FROM identities WHERE subject_type='Dog' ORDER BY identity_id"))
    samples=list(conn.execute('''SELECT s.*,d.catalog_id,d.confirmed,d.subject_type,d.identity_id AS current_identity
        FROM identity_samples s JOIN identities i USING(identity_id)
        LEFT JOIN clip_detections d USING(detection_id) WHERE i.subject_type='Dog' ORDER BY s.sample_id'''))
    unknowns=list(conn.execute('''SELECT d.detection_id,d.catalog_id,e.embedding,e.embedding_model,e.quality_score
        FROM clip_detections d JOIN identity_evidence e USING(detection_id)
        WHERE d.subject_type='Dog' AND e.subject_type='Dog' AND d.confirmed=1 AND d.identity_id IS NULL
        ORDER BY d.detection_id'''))
    return hashlib.sha256(repr([[tuple(r) for r in rows] for rows in (profiles,samples,unknowns)]).encode()).hexdigest()


def _evidence(conn, did):
    row=conn.execute('''SELECT d.detection_id,d.catalog_id,d.identity_id,d.confirmed,d.subject_type,
        e.embedding,e.embedding_model,e.quality_score,e.crop_jpeg,e.subject_type AS evidence_type,
        c.local_present FROM clip_detections d JOIN identity_evidence e USING(detection_id)
        JOIN clips c ON c.id=d.catalog_id WHERE d.detection_id=?''',(did,)).fetchone()
    if row is None:return None,None
    views=list(conn.execute('''SELECT seconds,embedding,embedding_model,quality_score,crop_jpeg
        FROM identity_track_views WHERE detection_id=? ORDER BY seconds,view_id''',(did,)))
    token=hashlib.sha256(repr((tuple(row),[tuple(v) for v in views])).encode()).hexdigest()
    item=dict(row)
    item['embedding']=json.loads(row['embedding']) if row['embedding'] else None
    item['views']=[dict(v,embedding=json.loads(v['embedding'])) for v in views]
    return item,token


def refresh_suggestions(db,identity_id,limit=100,dry_run=True):
    if not 1<=limit<=500:raise ValueError('Limit must be between 1 and 500.')
    with connect(db) as conn:
        identity=conn.execute("SELECT name FROM identities WHERE identity_id=? AND active=1 AND subject_type='Dog'",(identity_id,)).fetchone()
        if identity is None:raise ValueError('Choose an active dog identity.')
        rows=list(conn.execute('''SELECT d.detection_id FROM clip_detections d
            JOIN identity_evidence e USING(detection_id) JOIN clips c ON c.id=d.catalog_id
            WHERE d.subject_type='Dog' AND e.subject_type='Dog' AND d.confirmed=0
            AND d.identity_id IS NULL AND c.local_present=1 AND e.embedding IS NOT NULL
            ORDER BY datetime(c.captured_at) DESC,d.detection_id LIMIT ?''',(limit,)))
        result=dict(identity_id=identity_id,name=identity['name'],examined=len(rows),eligible=[],updated=[],skipped_changed=0,dry_run=dry_run)
        for row in rows:
            item,token=_evidence(conn,row['detection_id'])
            if item is None:continue
            gallery=_gallery_token(conn)
            predicted=recommend_identity(conn,item,item['catalog_id'])
            if predicted!=identity_id:continue
            conn.execute('BEGIN IMMEDIATE')
            current,current_token=_evidence(conn,row['detection_id'])
            if current_token!=token or _gallery_token(conn)!=gallery:
                conn.rollback();result['skipped_changed']+=1;continue
            entry=dict(catalog_id=item['catalog_id'],detection_id=item['detection_id'])
            result['eligible'].append(entry)
            if dry_run:
                conn.rollback();continue
            changed=conn.execute('''UPDATE clip_detections SET identity_id=?
                WHERE detection_id=? AND confirmed=0 AND identity_id IS NULL AND subject_type='Dog' ''',
                (identity_id,item['detection_id'])).rowcount
            if changed:
                conn.execute('UPDATE identity_evidence SET similarity=NULL WHERE detection_id=?',(item['detection_id'],))
                result['updated'].append(entry)
            conn.commit()
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True);parser.add_argument('--identity-id',type=int,required=True)
    parser.add_argument('--limit',type=int,default=100);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(refresh_suggestions(args.db,args.identity_id,args.limit,not args.apply),indent=2))
