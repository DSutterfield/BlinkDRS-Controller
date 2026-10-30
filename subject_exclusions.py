"""Explicit human confirmation of an unusable subject crop; never a teacher."""
def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS subject_analysis_exclusions (
        detection_id INTEGER PRIMARY KEY REFERENCES clip_detections(detection_id) ON DELETE CASCADE,
        crop_sha256 TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT 'no-identifiable-subject' CHECK(reason='no-identifiable-subject'),
        confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    for table in ('identity_samples','subject_type_samples'):
        for event in ('INSERT','UPDATE'):
            conn.execute(f'''CREATE TRIGGER IF NOT EXISTS unusable_{table}_{event.lower()}
                BEFORE {event} ON {table} WHEN NEW.detection_id IN
                (SELECT detection_id FROM subject_analysis_exclusions)
                BEGIN SELECT RAISE(IGNORE); END''')

def blocked(conn,did):
    return conn.execute('SELECT 1 FROM subject_analysis_exclusions WHERE detection_id=?',(did,)).fetchone() is not None

def mark(db_path,cid,did):
    from identity_store import connect,require_clip,clip_identities
    with connect(db_path) as conn:
        require_clip(conn,cid)
        row=conn.execute('''SELECT d.detection_id,e.crop_jpeg FROM clip_detections d JOIN identity_evidence e USING(detection_id)
            WHERE d.catalog_id=? AND d.detection_id=? AND e.crop_jpeg IS NOT NULL''',(cid,did)).fetchone()
        if row is None:raise LookupError('Select an existing subject crop.')
        import hashlib
        conn.execute('INSERT OR IGNORE INTO subject_analysis_exclusions(detection_id,crop_sha256) VALUES(?,?)',(did,hashlib.sha256(row['crop_jpeg']).hexdigest()))
        conn.execute('UPDATE clip_detections SET identity_id=NULL,confirmed=1,confirmed_at=CURRENT_TIMESTAMP WHERE detection_id=?',(did,))
        for table in ('identity_samples','subject_type_samples','identity_candidates','cat_review_cache','dog_review_cache'):
            conn.execute('DELETE FROM '+table+' WHERE detection_id=?',(did,))
        return clip_identities(conn,cid)
