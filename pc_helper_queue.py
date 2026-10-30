"""Controller-owned helper leases, retryable input changes and fenced publication."""
import json,sqlite3,time,uuid,shutil
from contextlib import contextmanager,closing
from pathlib import Path
from pc_helper_wire import canonical,sha,decode
from identity_store import connect
class Conflict(RuntimeError):pass
class Paused(RuntimeError):pass
class ReferenceChanged(Conflict):pass
class ReviewChanged(ReferenceChanged):pass
from pc_helper_inputs import context,context_parts,publication_context
from pc_helper_revisions import inference_revision,identity_revision
def path_in(root,relative):
    path=(Path(root)/relative).resolve();root=Path(root).resolve()
    if not path.is_relative_to(root) or path==root:raise ValueError('Path escapes configured root')
    return path
@contextmanager
def transaction(db):
    conn=sqlite3.connect(db,timeout=30);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
    try:
        conn.execute('BEGIN IMMEDIATE');yield conn;conn.commit()
    except BaseException:conn.rollback();raise
    finally:conn.close()
def expire(conn,now):
    expired=list(conn.execute("SELECT token,catalog_id FROM pc_helper_leases WHERE status='leased' AND expires<=?",(now,)))
    for row in expired:
        conn.execute("UPDATE pc_helper_leases SET status='expired' WHERE token=?",(row['token'],))
        attempts=conn.execute("SELECT COUNT(*) FROM pc_helper_leases WHERE catalog_id=? AND status IN ('leased','expired','released')",(row['catalog_id'],)).fetchone()[0]
        conn.execute("UPDATE identity_analysis_jobs SET state=?,message='PC helper lease expired',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=? AND state='running'",('failed' if attempts>=3 else 'queued',row['catalog_id']))
    return len(expired)
def recover_local_jobs(db):
    # A local supervisor restart/shutdown has no ownership of PC work.
    with connect(db):pass
    with transaction(db) as conn:
        expire(conn,time.time())
        conn.execute("""UPDATE identity_analysis_jobs SET state='failed',message='Worker interrupted; retry analysis',updated_at=CURRENT_TIMESTAMP
            WHERE state='running' AND NOT EXISTS(SELECT 1 FROM pc_helper_leases h WHERE h.catalog_id=identity_analysis_jobs.catalog_id AND h.status='leased')""")
class Coordinator:
    def __init__(self,db,archive,contract,zones,ready=lambda:False,clock=time.time):
        self.db=Path(db);self.archive=Path(archive);self.contract=contract;self.zones=zones;self.ready=ready;self.clock=clock
        if not self.db.is_file() or not self.archive.is_dir():raise ValueError('Existing catalog and archive required')
        with connect(db):pass
    def claim(self,owner,contract,ttl=900):
        if not self.ready():raise Paused('Controller is busy or unavailable')
        if contract!=self.contract:raise Conflict('Analysis contract mismatch')
        if not isinstance(owner,str) or not 1<=len(owner)<=100 or type(ttl) is not int or not 30<=ttl<=1800:raise ValueError('Invalid helper lease')
        with transaction(self.db) as conn:expire(conn,self.clock())
        # Slow fingerprints and recording reads must never reserve the writer.
        with closing(sqlite3.connect(self.db,timeout=30)) as conn:
            conn.row_factory=sqlite3.Row
            if conn.execute("SELECT COUNT(*) FROM pc_helper_leases WHERE status='leased'").fetchone()[0]>=2 or conn.execute("SELECT 1 FROM pc_helper_leases WHERE status='leased' AND owner=?",(owner,)).fetchone():return None
            row=conn.execute("SELECT j.catalog_id,c.video_path FROM identity_analysis_jobs j JOIN clips c ON c.id=j.catalog_id WHERE j.state='queued' AND c.local_present=1 ORDER BY j.updated_at,j.catalog_id LIMIT 1").fetchone()
            if row is None:return None
            cid=row['catalog_id'];relative=row['video_path'];epoch=inference_revision(conn,cid)
            try:path=path_in(self.archive,relative);data=path.read_bytes()
            except (FileNotFoundError,ValueError):
                with transaction(self.db) as writer:writer.execute("UPDATE identity_analysis_jobs SET state='failed',message='PC helper input is missing or outside archive',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=? AND state='queued'",(cid,))
                return None
            parts=context_parts(conn,cid)
            if inference_revision(conn,cid)!=epoch:return None
        if not self.ready():raise Paused('Controller is busy or unavailable')
        with transaction(self.db) as conn:
            if conn.execute("SELECT COUNT(*) FROM pc_helper_leases WHERE status='leased'").fetchone()[0]>=2 or conn.execute("SELECT 1 FROM pc_helper_leases WHERE status='leased' AND owner=?",(owner,)).fetchone():return None
            head=conn.execute("SELECT j.catalog_id FROM identity_analysis_jobs j JOIN clips c ON c.id=j.catalog_id WHERE j.state='queued' AND c.local_present=1 ORDER BY j.updated_at,j.catalog_id LIMIT 1").fetchone()
            if head is None or head[0]!=cid or inference_revision(conn,cid)!=epoch:return None
            zones=self.zones(cid)
            from identity_motion_zones import MotionZones
            MotionZones(zones)
            token=uuid.uuid4().hex;manifest=dict(schema=1,token=token,catalog_id=cid,video_sha256=sha(data),video_bytes=len(data),zones=zones,contract=contract,input_revision=list(epoch),**parts)
            conn.execute("INSERT INTO pc_helper_leases(token,catalog_id,owner,expires,status,manifest) VALUES(?,?,?,?,'leased',?)",(token,cid,owner,self.clock()+ttl,canonical(manifest).decode()))
            conn.execute("UPDATE identity_analysis_jobs SET state='running',message='PC helper analyzing',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?",(cid,));return manifest
    def _lease(self,conn,token):
        row=conn.execute('SELECT * FROM pc_helper_leases WHERE token=?',(token,)).fetchone()
        if row is None or row['status']!='leased' or row['expires']<=self.clock():raise Conflict('Helper no longer owns this job')
        job=conn.execute('SELECT state FROM identity_analysis_jobs WHERE catalog_id=?',(row['catalog_id'],)).fetchone()
        if job is None or job['state']!='running':raise Conflict('Job was canceled or replaced')
        return row,json.loads(row['manifest'])
    def heartbeat(self,token,ttl=900):
        if type(ttl) is not int or not 30<=ttl<=1800:raise ValueError('Invalid lease duration')
        with transaction(self.db) as conn:
            row,manifest=self._lease(conn,token);conn.execute('UPDATE pc_helper_leases SET expires=? WHERE token=?',(self.clock()+ttl,token));return dict(status='leased',expires=self.clock()+ttl)
    def release(self,token,terminal=False):
        with transaction(self.db) as conn:
            existing=conn.execute('SELECT status FROM pc_helper_leases WHERE token=?',(token,)).fetchone()
            if existing and existing[0]=='reference_changed':return  # Client cleanup is idempotent.
            row,manifest=self._lease(conn,token);conn.execute("UPDATE pc_helper_leases SET status='released' WHERE token=?",(token,));attempts=conn.execute("SELECT COUNT(*) FROM pc_helper_leases WHERE catalog_id=? AND status IN ('leased','expired','released')",(row['catalog_id'],)).fetchone()[0]
            conn.execute("UPDATE identity_analysis_jobs SET state=?,message='PC helper returned job',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?",('failed' if terminal or attempts>=3 else 'queued',row['catalog_id']))
    def retry_reference_change(self,token,reason):
        # Only controller-detected input conflicts take this path; callers cannot
        # excuse arbitrary inference failures by supplying a release flag.
        with transaction(self.db) as conn:
            row,manifest=self._lease(conn,token)
            conn.execute("UPDATE pc_helper_leases SET status='reference_changed' WHERE token=?",(token,))
            conn.execute("UPDATE identity_analysis_jobs SET state='queued',message=?,updated_at=CURRENT_TIMESTAMP WHERE catalog_id=? AND state='running'",
                         ('Reference/review inputs changed; retrying: '+str(reason)[:750],row['catalog_id']))

    def _verify_inputs(self,conn,cid,manifest):
        path=path_in(self.archive,conn.execute('SELECT video_path FROM clips WHERE id=?',(cid,)).fetchone()[0])
        if sha(path.read_bytes())!=manifest['video_sha256']:raise Conflict('Recording changed during analysis')
        epoch=inference_revision(conn,cid)
        cache=(manifest['context_sha256'],epoch)
        if epoch!=tuple(manifest['input_revision']) and getattr(self,'_verified_input',None)!=cache:
            current=context_parts(conn,cid)
            if current['clip_context_sha256']!=manifest['clip_context_sha256']:raise ReviewChanged('This clip was reviewed or edited during analysis')
            if current['reference_context_sha256']!=manifest['reference_context_sha256']:raise ReferenceChanged('Type references or exclusion policy changed during analysis')
            if inference_revision(conn,cid)!=epoch:raise ReferenceChanged('Inputs changed while checking the result; retry with fresh inputs')
        self._verified_input=cache
        if self.zones(cid)!=manifest['zones']:raise ReviewChanged('Motion zones changed during analysis')
        return epoch

    def cancel(self,token):
        with transaction(self.db) as conn:
            row,manifest=self._lease(conn,token);conn.execute("UPDATE pc_helper_leases SET status='canceled' WHERE token=?",(token,));conn.execute("UPDATE identity_analysis_jobs SET state='failed',message='PC helper job canceled',updated_at=CURRENT_TIMESTAMP WHERE catalog_id=?",(row['catalog_id'],))
    def export(self,token,destination):
        folder=Path(destination);folder.mkdir(parents=True,exist_ok=False);snapshot=folder/'references.sqlite'
        with closing(sqlite3.connect(self.db,timeout=30)) as conn:
            conn.row_factory=sqlite3.Row;row,manifest=self._lease(conn,token)
        from pc_helper_snapshot import export_references
        snapshot_epoch=export_references(self.db,snapshot,manifest['catalog_id'],manifest['context_sha256'],manifest['input_revision'])
        self._verified_input=(manifest['context_sha256'],snapshot_epoch)
        with closing(sqlite3.connect(self.db,timeout=30)) as conn:
            conn.row_factory=sqlite3.Row;row,current=self._lease(conn,token)
            epoch=self._verify_inputs(conn,manifest['catalog_id'],manifest)
            video=path_in(self.archive,conn.execute('SELECT video_path FROM clips WHERE id=?',(manifest['catalog_id'],)).fetchone()[0])
        shutil.copyfile(video,folder/'video.mp4')
        if sha((folder/'video.mp4').read_bytes())!=manifest['video_sha256']:raise Conflict('Recording changed during export')
        manifest['references_sha256']=sha(snapshot.read_bytes());manifest['references_bytes']=snapshot.stat().st_size
        with transaction(self.db) as conn:
            row,current=self._lease(conn,token)
            if inference_revision(conn,manifest['catalog_id'])!=epoch:raise ReferenceChanged('Inputs changed during snapshot export')
            conn.execute('UPDATE pc_helper_leases SET manifest=? WHERE token=?',(canonical(manifest).decode(),token))
        (folder/'manifest.json').write_bytes(canonical(manifest));return manifest
    def commit(self,data):
        packet,observations=decode(data);digest=sha(data)
        def duplicate(conn):
            previous=conn.execute('SELECT status,result_sha256 FROM pc_helper_leases WHERE token=?',(packet['token'],)).fetchone()
            if previous and previous['status']=='accepted':
                if previous['result_sha256']!=digest:raise Conflict('Different result for accepted attempt')
                return dict(status='accepted',duplicate=True)
        # Identity edits need only fresh Pi-side scoring, never repeated PC video
        # inference. Fence that scoring separately under the short write lock.
        for attempt in range(3):
            with closing(sqlite3.connect(self.db,timeout=30)) as conn:
                conn.row_factory=sqlite3.Row
                accepted=duplicate(conn)
                if accepted:return accepted
                if not self.ready():raise Paused('Controller is busy or unavailable')
                row,manifest=self._lease(conn,packet['token']);cid=row['catalog_id']
                if 'references_sha256' not in manifest or packet['manifest_sha256']!=sha(canonical(manifest)) or packet['contract']!=self.contract or packet['video_sha256']!=manifest['video_sha256']:raise Conflict('Evidence does not match leased inputs')
                epoch=self._verify_inputs(conn,cid,manifest)
                scoring_revision=identity_revision(conn)
                from identity_analysis import prepare_detections,publish_prepared
                prepared=prepare_detections(conn,cid,observations)
                if inference_revision(conn,cid)!=epoch or identity_revision(conn)!=scoring_revision:continue
            if not self.ready():raise Paused('Controller is busy or unavailable')
            with transaction(self.db) as conn:
                accepted=duplicate(conn)
                if accepted:return accepted
                row,current=self._lease(conn,packet['token'])
                if current!=manifest:raise Conflict('Helper manifest changed during scoring')
                if inference_revision(conn,cid)!=epoch or identity_revision(conn)!=scoring_revision:continue
                path=path_in(self.archive,conn.execute('SELECT video_path FROM clips WHERE id=?',(cid,)).fetchone()[0])
                if sha(path.read_bytes())!=manifest['video_sha256']:raise Conflict('Recording changed during scoring')
                if self.zones(cid)!=manifest['zones']:raise ReviewChanged('Motion zones changed during scoring')
                publish_prepared(conn,cid,manifest['video_sha256'],observations,prepared)
                conn.execute("UPDATE pc_helper_leases SET status='accepted',result_sha256=?,accepted_at=? WHERE token=?",(digest,self.clock(),packet['token']))
                return dict(status='accepted',duplicate=False)
        raise ReferenceChanged('Identity references kept changing during Pi scoring; retry with fresh inputs')
