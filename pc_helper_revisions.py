"""Cheap transaction fences. Counters trigger rechecks, never accept stale inputs."""
def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS pc_helper_input_revisions (
        id INTEGER PRIMARY KEY CHECK(id=1), type_revision INTEGER NOT NULL DEFAULT 0,
        identity_revision INTEGER NOT NULL DEFAULT 0)''')
    if conn.execute('SELECT 1 FROM pc_helper_input_revisions WHERE id=1').fetchone() is None:
        conn.execute('INSERT OR IGNORE INTO pc_helper_input_revisions(id) VALUES(1)')
    conn.execute('''CREATE TABLE IF NOT EXISTS pc_helper_clip_revisions (
        catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL DEFAULT 0)''')
    bump_type='UPDATE pc_helper_input_revisions SET type_revision=type_revision+1 WHERE id=1;'
    bump_identity='UPDATE pc_helper_input_revisions SET identity_revision=identity_revision+1 WHERE id=1;'
    def clip(cid):
        return f'''INSERT INTO pc_helper_clip_revisions(catalog_id,revision)
            SELECT {cid},1 WHERE EXISTS(SELECT 1 FROM clips WHERE id={cid})
            ON CONFLICT(catalog_id) DO UPDATE SET revision=revision+1;'''
    def trigger(name,table,event,body,when='1'):
        conn.execute(f'CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table} WHEN {when} BEGIN {body} END')
    for event in ('INSERT','UPDATE','DELETE'):
        ref='OLD' if event=='DELETE' else 'NEW';suffix=event.lower()
        for table in ('subject_type_samples','evaluation_holdout_clips','subject_analysis_exclusions'):
            trigger('pc_ref_'+table+'_'+suffix,table,event,bump_type+(bump_identity if table!='subject_type_samples' else ''))
        for table in ('identities','identity_samples'):
            trigger('pc_name_'+table+'_'+suffix,table,event,bump_identity)
        for table in ('clip_unknown_motion','identity_rejections'):
            trigger('pc_clip_'+table+'_'+suffix,table,event,clip(ref+'.catalog_id'))
        # Same-clip human decisions always fence work, including name corrections.
        cond=f'{ref}.confirmed=1'+(' OR OLD.confirmed=1' if event=='UPDATE' else '')
        body=clip(ref+'.catalog_id')+bump_identity
        if event=='UPDATE':body+=clip('OLD.catalog_id')
        trigger('pc_clip_detections_'+suffix,'clip_detections',event,body,cond)
        # Names do not affect Type. A changed Type/confirmation does.
        if event=='UPDATE':
            condition='(NEW.confirmed IS NOT OLD.confirmed OR NEW.subject_type IS NOT OLD.subject_type OR NEW.catalog_id IS NOT OLD.catalog_id) AND NEW.detection_id IN (SELECT detection_id FROM subject_type_samples)'
            trigger('pc_ref_detection_update','clip_detections',event,bump_type,condition)
        for table in ('identity_evidence','identity_track_views'):
            did=ref+'.detection_id';known=f'EXISTS(SELECT 1 FROM clip_detections d WHERE d.detection_id={did} AND d.confirmed=1)'
            cid=f'(SELECT catalog_id FROM clip_detections WHERE detection_id={did})'
            trigger('pc_clip_'+table+'_'+suffix,table,event,clip(cid)+bump_identity,known)
        # Crop pixels can feed Type variants; identity descriptors cannot.
        when=f'{ref}.detection_id IN (SELECT detection_id FROM subject_type_samples)'
        ev='UPDATE OF crop_jpeg' if event=='UPDATE' else event
        trigger('pc_ref_image_'+suffix,'identity_evidence',ev,bump_type,when)
        if event!='UPDATE':
            trigger('pc_ref_clip_'+suffix,'clips',event,bump_type)
            trigger('pc_target_clip_'+suffix,'clips',event,clip(ref+'.id'))
    trigger('pc_ref_clip_metadata_update','clips','UPDATE OF device_id,captured_at',bump_type,
            'NEW.device_id IS NOT OLD.device_id OR NEW.captured_at IS NOT OLD.captured_at')
    trigger('pc_target_clip_metadata_update','clips','UPDATE OF video_path,local_present,device_id,captured_at',clip('NEW.id'),
            'NEW.video_path IS NOT OLD.video_path OR NEW.local_present IS NOT OLD.local_present OR NEW.device_id IS NOT OLD.device_id OR NEW.captured_at IS NOT OLD.captured_at')
    # A restored Blink ID can change holdout membership without changing date/device.
    trigger('pc_ref_clip_media_id_update_v2','clips','UPDATE OF blink_media_id',bump_type,
            'NEW.blink_media_id IS NOT OLD.blink_media_id')

def inference_revision(conn,cid):
    row=conn.execute('SELECT type_revision FROM pc_helper_input_revisions WHERE id=1').fetchone()
    own=conn.execute('SELECT revision FROM pc_helper_clip_revisions WHERE catalog_id=?',(cid,)).fetchone()
    return row[0],own[0] if own else 0

def identity_revision(conn):
    return conn.execute('SELECT identity_revision FROM pc_helper_input_revisions WHERE id=1').fetchone()[0]
