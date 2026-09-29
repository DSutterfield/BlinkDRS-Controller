"""Run bulk disk work off-loop without releasing archive locks before it finishes."""
import asyncio
import contextlib


async def archive_io(function, *args):
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A Python thread cannot be cancelled. The caller must retain its locks
        # until disk mutation finishes, including after repeated cancellation.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        with contextlib.suppress(BaseException):
            worker.result()
        raise


def read_archive_sidecars(folder):
    from metadata_helper import read_sidecar
    files = sorted(folder.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True)
    return [(path, read_sidecar(path)) for path in files]


def missing_sidecars(folder):
    from metadata_helper import metadata_path_for
    return [path for path in folder.glob('*.mp4') if not metadata_path_for(path).exists()]
