"""Dog-cache epoch only. Helper publication retains its separate global fences."""

def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS dog_review_source_revisions(catalog_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,revision INTEGER NOT NULL DEFAULT 0)')
    conn.execute('CREATE TABLE IF NOT EXISTS dog_review_state(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL DEFAULT 0)')
    if conn.execute('SELECT 1 FROM dog_review_state WHERE id=1').fetchone() is None:
        conn.execute('INSERT INTO dog_review_state(id,revision) VALUES(1,0)')
        # Old cache numbers belong to the global epoch. Only currently valid
        # results can be carried into this new epoch; stale results need scoring.
        from pc_helper_revisions import identity_revision
        prior=identity_revision(conn)
        conn.execute('DELETE FROM dog_review_cache WHERE gallery_revision<>?',(prior,))
        conn.execute('UPDATE dog_review_cache SET gallery_revision=0')
        conn.execute('DROP TRIGGER IF EXISTS dog_hint_notify_update')
    bump='UPDATE dog_review_state SET revision=revision+1 WHERE id=1;'
    def trigger(name,table,event,when,scopes=None):
        body=bump
        if scopes is not None:
            body=''
            for scope,condition in scopes:
                body+=f"UPDATE dog_review_state SET revision=revision+1 WHERE id=1 AND ({condition});"
                body+=f"INSERT INTO dog_review_source_revisions(catalog_id,revision) SELECT {scope},1 WHERE ({condition}) AND EXISTS(SELECT 1 FROM clips WHERE id={scope}) ON CONFLICT(catalog_id) DO UPDATE SET revision=revision+1;"
        conn.execute(f'CREATE TRIGGER IF NOT EXISTS dog_epoch_{name} AFTER {event} ON {table} WHEN {when} BEGIN {body} END')
    def profile(ref):return f"EXISTS(SELECT 1 FROM identities i WHERE i.identity_id={ref}.identity_id AND i.subject_type='Dog')"
    def confirmed(did):return f"EXISTS(SELECT 1 FROM clip_detections d WHERE d.detection_id={did} AND d.subject_type='Dog' AND d.confirmed=1)"
    for event in ('INSERT','UPDATE','DELETE'):
        refs=['OLD'] if event=='DELETE' else ['NEW'] if event=='INSERT' else ['OLD','NEW'];suffix=event.lower()
        role='('+' OR '.join(r+".subject_type='Dog'" for r in refs)+')'
        if event=='UPDATE':role+=' AND (NEW.name IS NOT OLD.name OR NEW.subject_type IS NOT OLD.subject_type OR NEW.active IS NOT OLD.active OR NEW.identity_id IS NOT OLD.identity_id)'
        trigger('profile_'+suffix,'identities',event,role)
        trigger('sample_'+suffix,'identity_samples',event,' OR '.join(profile(r) for r in refs),[(f'(SELECT catalog_id FROM clip_detections WHERE detection_id={r}.detection_id)',profile(r)) for r in refs])
        for table in ('identity_evidence','subject_analysis_exclusions'):
            trigger(table+'_'+suffix,table,event,' OR '.join(confirmed(r+'.detection_id') for r in refs),[(r+'.catalog_id' if table=='identity_evidence' else f'(SELECT catalog_id FROM clip_detections WHERE detection_id={r}.detection_id)',confirmed(r+'.detection_id')) for r in refs])
        relevant=' OR '.join('('+r+".subject_type='Dog' AND "+r+'.confirmed=1)' for r in refs)
        if event=='UPDATE':relevant='('+relevant+') AND (NEW.identity_id IS NOT OLD.identity_id OR NEW.subject_type IS NOT OLD.subject_type OR NEW.confirmed IS NOT OLD.confirmed OR NEW.catalog_id IS NOT OLD.catalog_id)'
        trigger('label_'+suffix,'clip_detections',event,relevant,[(r+'.catalog_id',r+".subject_type='Dog' AND "+r+'.confirmed=1') for r in refs])
        # Holdout edits can change same-camera neighbour membership.
        trigger('holdout_'+suffix,'evaluation_holdout_clips',event,'1')
    trigger('clip_metadata','clips','UPDATE OF device_id,captured_at,blink_media_id',"(NEW.device_id IS NOT OLD.device_id OR NEW.captured_at IS NOT OLD.captured_at OR NEW.blink_media_id IS NOT OLD.blink_media_id) AND EXISTS(SELECT 1 FROM clip_detections d WHERE d.catalog_id=NEW.id AND d.subject_type='Dog' AND d.confirmed=1)",[('NEW.id','1')])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_notifications'").fetchone():
        conn.execute('CREATE TRIGGER IF NOT EXISTS dog_epoch_notify AFTER UPDATE ON dog_review_state WHEN NEW.revision<>OLD.revision AND EXISTS(SELECT 1 FROM dog_review_cache) BEGIN UPDATE catalog_notifications SET revision=revision+1 WHERE id=1; END')

def revision(conn,catalog_id=None):
    total=conn.execute('SELECT revision FROM dog_review_state WHERE id=1').fetchone()[0]
    if catalog_id is None:return total
    own=conn.execute('SELECT revision FROM dog_review_source_revisions WHERE catalog_id=?',(catalog_id,)).fetchone()
    return total-(own[0] if own else 0)
