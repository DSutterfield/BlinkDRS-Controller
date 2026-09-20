"""Prepare playback audio without retaining work for disconnected viewers."""
import asyncio
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


def _current(source, cached):
    try:
        original = source.stat()
        prepared = cached.stat()
        return prepared.st_size > 0 and prepared.st_mtime_ns >= original.st_mtime_ns
    except FileNotFoundError:
        return False


async def _convert(source, cached):
    temporary = cached.with_name(cached.stem + ".tmp.mp4")
    temporary.unlink(missing_ok=True)
    process = None
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source), "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000",
        "-movflags", "+faststart", str(temporary),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    ))
    try:
        # If the viewer leaves during process creation, still obtain and reap it.
        process = await asyncio.shield(spawn)
        try:
            _, errors = await asyncio.wait_for(process.communicate(), timeout=60)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Playback preparation timed out") from exc
        if process.returncode:
            detail = errors.decode("utf-8", errors="replace").strip()
            raise HTTPException(500, "The playback copy could not be created. " + detail)
        temporary.replace(cached)
    finally:
        try:
            if process is None:
                process = await spawn
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()
        finally:
            temporary.unlink(missing_ok=True)


async def _while_connected(request, operation):
    async def disconnected():
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)

    worker = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((worker, watcher), return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            return await worker
        # No response can reach the viewer; stop lock waits and ffmpeg promptly.
        raise HTTPException(499, "Playback request cancelled")
    finally:
        for task in (worker, watcher):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def clip_video_response(archive_dir, archive_lock, filename, request):
    if ("/" in filename or "\\" in filename or Path(filename).name != filename
            or Path(filename).suffix.lower() != ".mp4"):
        raise HTTPException(400, "Invalid clip filename")
    source = Path(archive_dir) / filename
    cached = source.parent.parent / "playback_cache" / (source.stem + ".loudnorm-v2.mp4")

    async def prepare():
        # Retain the existing deletion/retention interlock for all cache writes.
        async with archive_lock:
            if not source.is_file():
                raise HTTPException(404, "Clip video was not found")
            if not _current(source, cached):
                cached.parent.mkdir(parents=True, exist_ok=True)
                await _convert(source, cached)

    # A read of an existing copy must not queue behind archive downloads or
    # another clip's audio conversion. FileResponse keeps HTTP range support.
    if not _current(source, cached):
        await _while_connected(request, prepare())
    return FileResponse(cached, media_type="video/mp4", filename=filename,
                        content_disposition_type="inline")
