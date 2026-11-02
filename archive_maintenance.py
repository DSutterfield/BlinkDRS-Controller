"""Local video cleanup, preserving confirmed learning; no cloud deletions."""
import logging
import re
import sqlite3
import stat
import time
from contextlib import closing, nullcontext
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def clip_artifact_paths(archive_root, filename, sidecar_path=None, thumbnail_path=None):
    root = Path(archive_root).resolve()
    if Path(filename).name != filename or '/' in filename or '\\' in filename:
        raise ValueError('Invalid archive filename')
    stem = Path(filename).stem
    paths = [root/'clips'/filename, root/'clips'/(stem+'.json'), root/'clip_thumbs'/(stem+'.jpg')]
    for relative in (sidecar_path, thumbnail_path):
        if relative:
            paths.append(root/relative)
    cache = root/'playback_cache'
    if cache.is_dir():
        paths.extend(p for p in cache.iterdir() if p.name.startswith(stem+'.loudnorm') and p.suffix=='.mp4')
    unique = []
    for path in paths:
        path = path.resolve()
        if root not in path.parents or path.parent not in (root/'clips', root/'clip_thumbs', root/'playback_cache'):
            raise ValueError('Clip artifact path escapes archive directories')
        if path not in unique:
            unique.append(path)
    return unique


def reconcile_local_presence(db_path, archive_dir, mutation_guard=None):
    """Hide missing clips, preserving confirmed evidence before row cleanup.

    Scan first: directory/I/O failures abort before any changes. If artifact
    removal fails, keep the row hidden so the next cleanup can retry it.
    """
    archive_dir = Path(archive_dir)
    guard = mutation_guard or nullcontext
    if not archive_dir.is_dir() or not Path(db_path).is_file():
        raise FileNotFoundError('Archive directory or catalog unavailable')
    missing, restored = [], []
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        rows = conn.execute('SELECT id,filename,local_present,sidecar_path,thumbnail_path FROM clips').fetchall()
        for catalog_id, filename, present, sidecar, thumbnail in rows:
            if Path(filename).name != filename or "/" in filename or "\\" in filename:
                raise ValueError("Invalid archive filename")
            try:
                exists = stat.S_ISREG((archive_dir/filename).stat().st_mode)
            except FileNotFoundError:
                exists = False
            if not exists:
                from learning_archive import verified_stub
                if not present and verified_stub(conn, catalog_id):
                    continue
                missing.append((catalog_id,clip_artifact_paths(archive_dir.parent, filename, sidecar, thumbnail)))
            elif not present:
                restored.append((catalog_id,))
        with conn:
            conn.executemany('UPDATE clips SET local_present=0 WHERE id=?',[(i,) for i,_ in missing])
            conn.executemany('UPDATE clips SET local_present=1 WHERE id=?',restored)
        for catalog_id, paths in missing:
            from learning_archive import preserve, verified_stub
            with guard():
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    keep = verified_stub(conn, catalog_id) or preserve(conn, archive_dir.parent, catalog_id)
                if keep:
                    continue
                for path in paths:
                    path.unlink(missing_ok=True)
                with conn:
                    conn.execute('DELETE FROM clips WHERE id=?',(catalog_id,))
        # Older manual deletions did not enable SQLite foreign-key cascades.
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='clip_validation'").fetchone():
            with conn:
                conn.execute('DELETE FROM clip_validation WHERE catalog_id NOT IN (SELECT id FROM clips)')
    # Clear leftovers from historical deletions whose catalog rows are gone.
    root = archive_dir.parent
    candidates = [(p, p.stem) for p in archive_dir.glob('*.json')]
    candidates += [(p, p.stem) for p in (root/'clip_thumbs').glob('*.jpg')]
    for path in (root/'playback_cache').glob('*.mp4'):
        match = re.fullmatch(r'(.+)\.loudnorm(?:-v\d+)?(?:\.tmp)?\.mp4', path.name)
        if match:
            candidates.append((path, match.group(1)))
    for path, stem in candidates:
        try:
            (archive_dir/(stem+'.mp4')).stat()
        except FileNotFoundError:
            # Reuse containment checks before deleting any derived artifact.
            allowed = clip_artifact_paths(root, stem+'.mp4')
            if path.resolve() in allowed:
                with guard():
                    path.unlink(missing_ok=True)
    if missing:
        log.info('Removed %s missing clip entries and their local artifacts',len(missing))
    return len(missing)+len(restored)


def recording_timestamp(captured_at):
    """Catalog capture time is UTC when legacy values omit an offset.

    An absent/invalid date is not permission to delete an original. File copy,
    restore and playback times must never determine recording retention.
    """
    try:
        stamp = datetime.fromisoformat(captured_at.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return None


def cleanup_expired_clips(db_path, archive_dir, retention_days, now=None, max_delete=200, mutation_guard=None):
    started = time.monotonic()
    log.info('Local retention cleanup started (%s days from recording date); playback protected per file', retention_days)
    guard = mutation_guard or nullcontext
    reconcile_local_presence(db_path, archive_dir, mutation_guard=guard)
    if retention_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    count = 0
    try:
        with closing(sqlite3.connect(db_path)) as reader:
            dates = {name: recording_timestamp(date) for name, date in
                     reader.execute('SELECT filename,captured_at FROM clips')}
        candidates = []
        unknown_dates = 0
        for video in Path(archive_dir).glob('*.mp4'):
            recorded = dates.get(video.name)
            if recorded is None:
                unknown_dates += 1
            elif recorded < cutoff:
                candidates.append((recorded, video))
        if unknown_dates:
            log.warning('Retention kept %s videos without a valid catalog recording date', unknown_dates)
        for recorded, video in sorted(candidates, key=lambda item: (item[0], item[1].name)):
            if count >= max_delete:
                break
            # Validate all paths before removing the original.
            artifacts = clip_artifact_paths(Path(archive_dir).parent, video.name)
            with guard(), closing(sqlite3.connect(db_path, timeout=30)) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    row = conn.execute('SELECT id,captured_at FROM clips WHERE filename=?', (video.name,)).fetchone()
                    # Recheck under the same lock as preservation/removal: a
                    # corrected recording date or removed row wins over scan.
                    current = recording_timestamp(row[1]) if row else None
                    if current is None or current >= cutoff:
                        continue
                    if row:
                        jobs = conn.execute("SELECT 1 FROM sqlite_master WHERE name='identity_analysis_jobs'").fetchone()
                        if jobs and conn.execute("SELECT 1 FROM identity_analysis_jobs WHERE catalog_id=? AND state='running'", (row[0],)).fetchone():
                            continue
                        from learning_archive import preserve
                        preserve(conn, Path(archive_dir).parent, row[0], video)
                    # No unlink is reachable until the independent learning
                    # archive was committed and verified. Lock human edits until
                    # the marker and hidden catalog stub are safely committed.
                    video.unlink()
                    if row:
                        conn.execute('UPDATE clips SET local_present=0 WHERE id=?', (row[0],))
                count += 1
                for artifact in artifacts:
                    artifact.unlink(missing_ok=True)
    finally:
        reconcile_local_presence(db_path, archive_dir, mutation_guard=guard)
    if count:
        log.info('Local retention (%s days from recording date) removed %s expired videos in %.3fs; confirmed learning preserved',retention_days,count,time.monotonic()-started)
    return count
