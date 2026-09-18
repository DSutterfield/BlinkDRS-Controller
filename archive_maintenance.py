"""Local archive cleanup. Does not issue Blink cloud deletion requests."""
import logging
import re
import sqlite3
import stat
import time
from contextlib import closing
from pathlib import Path

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


def reconcile_local_presence(db_path, archive_dir):
    """Remove missing clips and their saved references, under the archive lock.

    Scan first: directory/I/O failures abort before any changes. If artifact
    removal fails, keep the row hidden so the next cleanup can retry it.
    """
    archive_dir = Path(archive_dir)
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
                missing.append((catalog_id,clip_artifact_paths(archive_dir.parent, filename, sidecar, thumbnail)))
            elif not present:
                restored.append((catalog_id,))
        with conn:
            conn.executemany('UPDATE clips SET local_present=0 WHERE id=?',[(i,) for i,_ in missing])
            conn.executemany('UPDATE clips SET local_present=1 WHERE id=?',restored)
        for catalog_id, paths in missing:
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
                path.unlink(missing_ok=True)
    if missing:
        log.info('Removed %s missing clip entries and their local artifacts',len(missing))
    return len(missing)+len(restored)


def cleanup_expired_clips(db_path, archive_dir, retention_days, now=None):
    reconcile_local_presence(db_path, archive_dir)
    if retention_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    count = 0
    try:
        for video in Path(archive_dir).glob('*.mp4'):
            if video.stat().st_mtime >= cutoff:
                continue
            # Validate all paths before removing the original.
            artifacts = clip_artifact_paths(Path(archive_dir).parent, video.name)
            video.unlink()
            count += 1
            for artifact in artifacts:
                artifact.unlink(missing_ok=True)
    finally:
        reconcile_local_presence(db_path, archive_dir)
    if count:
        log.info('Local retention (%s days) removed %s expired clips and references',retention_days,count)
    return count
