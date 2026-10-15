"""Transactional incremental clip sync. Latest revision per ID also retains deletions."""
import sqlite3
import uuid
from contextlib import closing

# A controller restart intentionally asks clients for a fresh baseline.
INSTANCE = uuid.uuid4().hex


def ensure_clip_sync(conn):
    def current():
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='sync_clips_insert'").fetchone()
        return row is not None and 'ON CONFLICT(catalog_id) DO UPDATE' in row[0]
    if current():return
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    if current():
        conn.commit()
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS clip_sync_state (id INTEGER PRIMARY KEY CHECK(id=1),
            epoch TEXT NOT NULL, revision INTEGER NOT NULL)""")
    conn.execute('CREATE TABLE IF NOT EXISTS clip_sync_changes (catalog_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS clip_sync_revision ON clip_sync_changes(revision)')
    conn.execute('INSERT OR IGNORE INTO clip_sync_state VALUES(1,?,0)', (uuid.uuid4().hex,))

    def install(table, id_expr, event, when='', select=None):
        body = select or f'SELECT {id_expr}, revision FROM clip_sync_state WHERE id=1'
        notify = "UPDATE catalog_notifications SET revision=revision+1 WHERE id=1;" if table in ('identity_analysis_jobs','identity_evidence') else ''
        conn.execute(f'DROP TRIGGER IF EXISTS sync_{table}_{event.lower()}')
        conn.execute(f"""CREATE TRIGGER sync_{table}_{event.lower()} AFTER {event} ON {table}
            {when} BEGIN
            {notify}
            UPDATE clip_sync_state SET revision=revision+1 WHERE id=1;
            INSERT INTO clip_sync_changes(catalog_id,revision) {body}
            ON CONFLICT(catalog_id) DO UPDATE SET revision=excluded.revision;
            END""")

    fields = ('blink_media_id','filename','device_name_snapshot','system_name_snapshot',
              'captured_at','blink_updated_at','watched','source','media_type','trigger_type',
              'cv_detection_json','duration_ms','time_zone','thumbnail_path','local_present')
    for event in ('INSERT','UPDATE','DELETE'):
        row = 'OLD' if event == 'DELETE' else 'NEW'
        when = 'WHEN ' + ' OR '.join(f'OLD.{f} IS NOT NEW.{f}' for f in fields) if event == 'UPDATE' else ''
        install('clips', f'{row}.id', event, when)
        for table in ('clip_validation','clip_detections','clip_unknown_motion',
                      'identity_analysis_jobs','identity_evidence'):
            # Updates may change an association: invalidate both old and new IDs.
            select = ("SELECT OLD.catalog_id, revision FROM clip_sync_state WHERE id=1 UNION "
                      "SELECT NEW.catalog_id, revision FROM clip_sync_state WHERE id=1") if event == 'UPDATE' else None
            install(table, f'{row}.catalog_id', event, select=select)
        # Profile names are joined into every associated clip's displayed identities.
        install('identities', None, event, select=f"""SELECT DISTINCT d.catalog_id,s.revision
            FROM clip_detections d CROSS JOIN clip_sync_state s
            WHERE d.identity_id={row}.identity_id AND s.id=1""")
    conn.commit()


def sync_clips(db_path, token=None):
    from catalog_store import open_catalog_notifications, list_clips
    conn = sqlite3.connect(db_path)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='clip_sync_state'").fetchone():
        conn.close()
        conn = open_catalog_notifications(db_path)
    with closing(conn):
        conn.row_factory = sqlite3.Row
        # No schema initialization/commits after this point: payload and cursor share one snapshot.
        conn.execute('BEGIN')
        state = conn.execute('SELECT epoch,revision FROM clip_sync_state WHERE id=1').fetchone()
        epoch = INSTANCE + '.' + state['epoch']
        revision = state['revision']
        since = None
        if token:
            try:
                prior_epoch, prior = token.rsplit(':', 1)
                prior = int(prior)
                if prior_epoch == epoch and 0 <= prior <= revision:
                    since = prior
            except (ValueError, AttributeError):
                pass
        result = list_clips(db_path, limit=2147483647, _conn=conn, _since=since)
        returned = {clip['catalog_id'] for clip in result['clips']}
        changed = [] if since is None else [row[0] for row in conn.execute(
            'SELECT catalog_id FROM clip_sync_changes WHERE revision>?', (since,))]
        result.update(is_full=since is None, sync_token=f'{epoch}:{revision}',
                      removed_ids=[key for key in changed if key not in returned])
        return result
