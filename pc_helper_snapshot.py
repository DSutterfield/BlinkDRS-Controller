"""Build reference-only SQLite directly; never clone/delete a full catalog."""
import sqlite3
from contextlib import closing
from pc_helper_queue import context,Conflict,ReferenceChanged
def export_references(db,snapshot,cid,expected_context,expected_revision=None):
    with closing(sqlite3.connect(db,timeout=30)) as source,closing(sqlite3.connect(snapshot)) as target:
        from pc_helper_revisions import inference_revision
        source.row_factory=sqlite3.Row
        epoch=inference_revision(source,cid)
        if expected_revision is None or epoch!=tuple(expected_revision):
            if context(source,cid)!=expected_context:raise ReferenceChanged('Analysis inputs changed before snapshot')
        selected_ids=[r[0] for r in source.execute("SELECT d.detection_id FROM clip_detections d WHERE d.catalog_id NOT IN (SELECT catalog_id FROM evaluation_excluded_clips) AND d.detection_id NOT IN (SELECT detection_id FROM subject_analysis_exclusions) AND (d.detection_id IN (SELECT detection_id FROM subject_type_samples) OR (d.catalog_id=? AND d.confirmed=1 AND d.subject_type='Cat' AND d.model_version='manual-crop:v1'))",(cid,))]
        source.execute('BEGIN')
        if inference_revision(source,cid)!=epoch:raise ReferenceChanged('Analysis inputs changed before snapshot copy')
        definitions=list(source.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid"))
        for row in definitions:
            if row['type']=='table':target.execute(row['sql'])
        # Keep every clip's camera/time metadata so exclusion views retain the
        # exact production meaning, including nearby held-out recordings.
        for table in ['systems','devices','clips','identities','evaluation_holdout_clips']:
            rows=source.execute('SELECT * FROM '+table+' ORDER BY 1');count=len(rows.description);target.executemany('INSERT INTO '+table+' VALUES('+','.join('?'*count)+')',(tuple(row) for row in rows))
        for table in ['clip_detections','identity_evidence','subject_type_samples']:
            for offset in range(0,len(selected_ids),900):
                batch=selected_ids[offset:offset+900]
                rows=source.execute('SELECT * FROM '+table+' WHERE detection_id IN ('+','.join('?' for _ in batch)+') ORDER BY 1',batch);count=len(rows.description)
                target.executemany('INSERT INTO '+table+' VALUES('+','.join('?'*count)+')',(tuple(row) for row in rows))
        # Carry the exclusion policy and parent metadata, never excluded crop pixels/features.
        for table,query in [('clip_detections','SELECT d.* FROM clip_detections d JOIN subject_analysis_exclusions x USING(detection_id)'),
                            ('subject_analysis_exclusions','SELECT * FROM subject_analysis_exclusions')]:
            rows=source.execute(query);count=len(rows.description)
            target.executemany('INSERT INTO '+table+' VALUES('+','.join('?'*count)+')',(tuple(row) for row in rows))
        # No identity samples, answer labels, jobs, candidates, or historical
        # auto detections are ever copied. Install triggers after bulk inserts.
        for kind in ['view','index','trigger']:
            for row in definitions:
                if row['type']==kind:target.execute(row['sql'])
        from pc_helper_revisions import ensure
        ensure(target)
        target.commit()
        return epoch
