"""
controller_api.py

Blink Management System - Pi Controller API

Provides the network interface between the Raspberry Pi Blink Controller
and the Windows Management Console.

API Version: 1
"""

import asyncio
import time
import json
import os
import shutil
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pathlib import Path
from fastapi.responses import FileResponse, Response
from fastapi.responses import StreamingResponse
from liveview_bridge import LiveViewBridge
from live_recording import install_recording_api
from catalog_store import (
    delete_clip_by_catalog_id,
    get_clip_by_catalog_id,
    get_clip_by_media_id,
    list_clips,
    set_clip_watched,
)
from clip_delete import (
    finalize_staged_clip,
    restore_staged_clip,
    set_stage_state,
    stage_clip_for_delete,
)
from aiohttp import ClientTimeout

app = FastAPI(
    title="Blink Controller API",
    version="1.0",
)


@app.get("/api/v1/health")
async def health():
    """Return basic Pi Controller API health information."""
    return {
        "status": "ok",
        "controller": "blink-controller",
        "api_version": "1",
    }
"""
controller_api.py

Blink Management System - Pi Controller API

Provides the network interface between the Raspberry Pi Blink Controller
and the Windows Management Console.

API Version: 1
"""

from fastapi import FastAPI

class MotionRequest(BaseModel):
    enabled: bool

class SystemArmRequest(BaseModel):
    armed: bool

class LiveViewStartRequest(BaseModel):
    name: str

async def delete_blink_cloud_media(controller, media_id):
    """
    Send one media-delete request to Blink.

    Returns the HTTP status and response text so the caller can
    distinguish success, definite rejection, and uncertain outcomes.
    """

    blink = controller.blink
    auth = blink.auth

    # Match BlinkPy's normal token-refresh behavior before making
    # the request directly through the authenticated session.
    if auth.need_refresh():
        await auth.refresh_tokens(refresh=True)

    media_id_text = str(media_id)

    blink_media_id = (
        int(media_id_text)
        if media_id_text.isdigit()
        else media_id_text
    )

    url = (
        f"{blink.urls.base_url}"
        f"/api/v1/accounts/{blink.account_id}"
        f"/media/delete"
    )

    headers = dict(auth.header)
    headers["Content-Type"] = "application/json"

    body = {
        "media_list": [blink_media_id]
    }

    async with controller.session.post(
        url,
        json=body,
        headers=headers,
        timeout=ClientTimeout(total=10),
    ) as response:

        response_text = await response.text()

        return response.status, response_text

async def coordinate_clip_delete(controller, catalog_id):
    """
    Coordinate deletion of one Blink clip.

    Local files are quarantined first. They are restored unless Blink
    deletion is positively confirmed with a successful HTTP response.
    """

    async with controller.archive_lock:

        clip = get_clip_by_catalog_id(
            db_path=controller.catalog_db_path,
            catalog_id=catalog_id,
        )

        if clip is None:
            raise LookupError(
                "Clip was not found in the local catalog"
            )

        blink_media_id = clip.get(
            "blink_media_id"
        )

        if not blink_media_id:
            if clip.get("source") != "recorded_live":
                raise ValueError("Clip has no Blink media ID")
            stage = stage_clip_for_delete(controller.archive_root, clip)
            try:
                if not delete_clip_by_catalog_id(controller.catalog_db_path, catalog_id):
                    raise RuntimeError("Local catalog row could not be deleted")
            except Exception:
                restore_staged_clip(stage)
                raise
            set_stage_state(stage, "catalog_deleted")
            finalize_staged_clip(stage)
            return {"deleted": True, "catalog_id": catalog_id, "filename": clip["filename"], "blink_media_id": None}

        if controller.blink is None:
            raise RuntimeError("Blink controller is not connected")

        stage = stage_clip_for_delete(
            controller.archive_root,
            clip,
        )

        try:
            set_stage_state(
                stage,
                "cloud_delete_pending",
            )

        except Exception:

            restore_staged_clip(stage)
            raise

        try:
            status, response_text = (
                await delete_blink_cloud_media(
                    controller,
                    blink_media_id,
                )
            )

        except Exception as exc:

            try:
                restore_staged_clip(stage)

            except Exception as restore_exc:
                raise RuntimeError(
                    "Blink deletion could not be confirmed "
                    "and local restore also failed"
                ) from restore_exc

            raise RuntimeError(
                "Blink deletion could not be confirmed; "
                "local files were restored"
            ) from exc

        if not 200 <= status < 300:

            try:
                restore_staged_clip(stage)

            except Exception as restore_exc:
                raise RuntimeError(
                    f"Blink rejected deletion with HTTP "
                    f"{status}, and local restore failed"
                ) from restore_exc

            response_summary = (
                response_text[:500]
                if response_text
                else ""
            )

            raise RuntimeError(
                f"Blink rejected deletion with HTTP "
                f"{status}: {response_summary}"
            )

        # From this point forward the Blink deletion is irreversible.
        # Local failures must be recovered/retried, not rolled back.
        set_stage_state(
            stage,
            "cloud_deleted",
        )

        if not delete_clip_by_catalog_id(
            db_path=controller.catalog_db_path,
            catalog_id=catalog_id,
        ):
            raise RuntimeError(
                "Blink deleted the clip, but the local "
                "catalog row could not be deleted"
            )

        set_stage_state(
            stage,
            "catalog_deleted",
        )

        finalize_staged_clip(stage)

        return {
            "deleted": True,
            "catalog_id": catalog_id,
            "blink_media_id": str(
                blink_media_id
            ),
            "filename": clip["filename"],
            "blink_status": status,
        }

def create_app(controller):
    """Create the Controller API around an existing BlinkController."""

    app = FastAPI(
        title="Blink Controller API",
        version="1.0",
    )

    @app.middleware("http")
    async def disable_api_caching(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
        return response

    liveview_bridge = LiveViewBridge()

    recorder, recording_lock = install_recording_api(app, controller, liveview_bridge)
    async def shutdown_liveview():
        await recorder.stop()
        await asyncio.to_thread(liveview_bridge.shutdown)
    app.router.add_event_handler("shutdown", shutdown_liveview)

    @app.get("/api/v1/health")
    async def health():
        """Return Pi Controller, storage, and Blink connection health."""

        connected = controller.blink is not None

        if connected:
            camera_count = len(controller.blink.cameras)
        else:
            camera_count = 0

        archive_available = controller.archive_dir.is_dir()
        catalog_available = controller.catalog_db_path.is_file()

        archive_mount_present = os.path.ismount(
            controller.archive_root
        )

        disk_total_bytes = None
        disk_used_bytes = None
        disk_free_bytes = None

        if controller.archive_root.exists():
            try:
                disk_usage = shutil.disk_usage(
                    controller.archive_root
                )
                disk_total_bytes = disk_usage.total
                disk_used_bytes = disk_usage.used
                disk_free_bytes = disk_usage.free
            except OSError:
                pass

        internet_reachable = False

        if controller.session is not None:
            try:
                async with controller.session.get(
                    "https://connectivitycheck.gstatic.com/generate_204",
                    timeout=ClientTimeout(total=3),
                ) as response:
                    internet_reachable = response.status in {
                        200,
                        204,
                    }
            except Exception:
                internet_reachable = False

        return {
            "status": (
                "ok"
                if archive_available and catalog_available
                else "degraded"
            ),
            "controller": "blink-controller",
            "api_version": "1",
            "blink_connected": connected,
            "internet_reachable": internet_reachable,
            "blink_cloud_reachable": (
                controller.blink_cloud_reachable
            ),
            "last_poll_success_at": (
                controller.last_poll_success_at
            ),
            "last_poll_error": controller.last_poll_error,
            "archive_available": archive_available,
            "archive_mount_present": archive_mount_present,
            "archive_path": str(controller.archive_root),
            "disk_total_bytes": disk_total_bytes,
            "disk_used_bytes": disk_used_bytes,
            "disk_free_bytes": disk_free_bytes,
            "catalog_available": catalog_available,
            "camera_count": camera_count,
        }

    @app.get("/api/v1/devices")
    async def devices():
        """Return devices known to the Pi Controller."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        result = []

        for dictionary_key, camera in controller.blink.cameras.items():
            result.append(
                {
                    "id": str(camera.camera_id),
                    "name": camera.name or dictionary_key,
                    "system_id": str(camera.sync.network_id),
                    "system_name": camera.sync.name,
                    "device_type": camera.product_type,
                    "class_name": camera.__class__.__name__,
                    "serial": camera.serial,
                    "firmware_version": camera.version,
                    "motion_enabled": camera.motion_enabled,
                    "online": bool(camera.online and camera.sync.available),
                    "battery": camera.battery,
                    "battery_level": camera.battery_level,
                    "battery_voltage": camera.battery_voltage,
                    "temperature_f": camera.temperature,
                    "temperature_c": camera.temperature_c,
                    "wifi_signal": camera.wifi_strength,
                    "sync_signal": camera.sync_signal_strength,
                }
            )

        return {
            "count": len(result),
            "devices": sorted(
                result,
                key=lambda device: (
                    device["system_name"].lower(),
                    device["name"].lower(),
                ),
            ),
        }

    @app.get("/api/v1/devices/{device_id}/thumbnail")
    async def device_thumbnail(
        device_id: str,
        refresh: bool = Query(default=False),
    ):
        """Return the cached thumbnail for one Blink camera."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        camera = None

        for candidate in controller.blink.cameras.values():
            if str(candidate.camera_id) == device_id:
                camera = candidate
                break

        if camera is None:
            raise HTTPException(
                status_code=404,
                detail="Camera was not found",
            )

        thumbnail_dir = (
            controller.archive_root / "camera_thumbs"
        )

        thumbnail_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        thumbnail_path = (
            thumbnail_dir / f"{device_id}.jpg"
        )

        if refresh or not thumbnail_path.is_file():

            try:
                if refresh:
                    await camera.snap_picture()

                    image_bytes = camera.image_from_cache
                    if image_bytes:
                        await asyncio.to_thread(
                            thumbnail_path.write_bytes,
                            image_bytes,
                        )
                    else:
                        await camera.image_to_file(
                            str(thumbnail_path)
                        )
                else:
                    await camera.image_to_file(
                        str(thumbnail_path)
                    )

            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Blink camera thumbnail request failed: "
                        f"{exc}"
                    ),
                ) from exc

        if not thumbnail_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Camera thumbnail was not available",
            )

        return FileResponse(
            path=thumbnail_path,
            media_type="image/jpeg",
            filename=f"{device_id}.jpg",
            content_disposition_type="inline",
        )


    @app.post("/api/v1/liveview/start")
    async def liveview_start(
        request: LiveViewStartRequest,
    ):
        """Start live view and wait for the first decoded frame."""
        async with recording_lock:
            await recorder.stop()
            request_started_at = time.monotonic()

            camera_name = request.name.strip()

            if not camera_name:
                raise HTTPException(
                    status_code=400,
                    detail="A camera name is required",
                )

            try:
                return await asyncio.to_thread(
                    liveview_bridge.start,
                    camera_name,
                    request_started_at,
                )

            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Live View start failed: {exc}",
                ) from exc

    @app.get("/api/v1/liveview/frame")
    async def liveview_frame(after: int | None = Query(default=None, ge=0), session_id: str | None = None):
        """Return the latest decoded Live View JPEG frame."""

        if after is not None:
            if not session_id:
                raise HTTPException(status_code=422, detail="session_id is required with after")
            try:
                item = await asyncio.to_thread(liveview_bridge.next_frame, after, session_id, 1.0)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if item is None:
                return Response(status_code=204, headers={"Cache-Control": "no-store"})
            number, frame = item
            return Response(content=frame, media_type="image/jpeg", headers={
                "Cache-Control": "no-store", "X-Frame-Number": str(number),
                "X-LiveView-Session": session_id,
            })


        status = liveview_bridge.status()

        if not status["active"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    status["error"]
                    or "Live View is not active"
                ),
            )

        frame = await asyncio.to_thread(
            liveview_bridge.latest_frame,
            1.0,
        )

        if frame is None:
            raise HTTPException(
                status_code=503,
                detail="No Live View frame is available",
            )

        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={
                "Cache-Control": (
                    "no-store, no-cache, "
                    "must-revalidate, max-age=0"
                ),
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/v1/liveview/audio")
    async def liveview_audio(session_id: str | None = None):
        """Stream the active Live View camera microphone audio."""

        status = liveview_bridge.status()

        if not status["active"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    status["error"]
                    or "Live View is not active"
                ),
            )

        # Start listening at the current moment rather than
        # playing audio accumulated since Live View started.
        try:
            video_cursor, audio_session = liveview_bridge.clear_audio(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def audio_stream():

            while True:

                chunk = await asyncio.to_thread(
                    liveview_bridge.next_audio_chunk,
                    1.0,
                )

                if chunk:
                    yield chunk
                    continue

                status = liveview_bridge.status()

                if not status["active"]:
                    break

        return StreamingResponse(
            audio_stream(),
            media_type="audio/mpeg",
            headers={
                "X-Video-Start-After": str(video_cursor),
                "X-LiveView-Session": audio_session,
                "Cache-Control": (
                    "no-store, no-cache, "
                    "must-revalidate, max-age=0"
                ),
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/v1/liveview/status")
    async def liveview_status():
        """Return the current Live View state."""

        return liveview_bridge.status()

    @app.post("/api/v1/liveview/stop")
    async def liveview_stop():
        async with recording_lock:
            await recorder.stop()
            """Stop the active Live View session."""

            try:
                await asyncio.to_thread(
                    liveview_bridge.stop,
                )

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Live View stop failed: {exc}",
                ) from exc

            return {
                "ok": True,
                "active": False,
            }

    @app.get("/api/v1/systems")
    async def systems(refresh: bool = Query(default=False)):
        """Return Blink systems known to the Pi Controller."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        if refresh:
            try:
                await controller.refresh_blink_status()
            except Exception as exc:
                controller.blink_cloud_reachable = False
                controller.last_poll_error = str(exc)

        result = []

        for dictionary_key, system in controller.blink.sync.items():
            system_id = str(system.network_id)

            camera_count = sum(
                1
                for camera in controller.blink.cameras.values()
                if str(camera.sync.network_id) == system_id
            )

            result.append(
                {
                    "id": system_id,
                    "name": (system.name or dictionary_key).strip(),
                    "armed": system.arm,
                    "online": bool(system.online and system.available),
                    "camera_count": camera_count,
                }
            )

        return {
            "count": len(result),
            "systems": sorted(
                result,
                key=lambda system: system["name"].lower(),
            ),
        }

    @app.get("/api/v1/status/events")
    async def status_events():
        """Stream revisioned Blink status-change notifications."""

        async def event_stream():
            last_revision = -1

            while True:
                revision = controller.status_revision
                if revision != last_revision:
                    last_revision = revision
                    payload = json.dumps({"revision": revision})
                    yield f"event: status\ndata: {payload}\n\n"

                try:
                    async with controller.status_changed:
                        await asyncio.wait_for(
                            controller.status_changed.wait_for(
                                lambda: controller.status_revision != last_revision
                            ),
                            timeout=20,
                        )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.put("/api/v1/systems/{system_id}/arm")
    async def set_system_arm(system_id: str, request: SystemArmRequest):
        """Arm or disarm one Blink system."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        system = next(
            (
                system
                for system in controller.blink.sync.values()
                if str(system.network_id) == system_id
            ),
            None,
        )

        if system is None:
            raise HTTPException(
                status_code=404,
                detail=f"System {system_id} was not found",
            )

        previous_state = system.arm

        try:
            response = await system.async_arm(request.armed)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Blink system arm command failed: {exc}",
            ) from exc

        if response is None:
            raise HTTPException(
                status_code=502,
                detail="Blink did not return a response",
            )

        # Refresh this system's network information so the returned
        # state comes back from Blink rather than from our request.
        await system.get_network_info()

        confirmed_state = system.arm
        if confirmed_state is None or bool(confirmed_state) != request.armed:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Blink did not confirm the requested Armed setting. "
                    "The Sync Module may be offline."
                ),
            )

        return {
            "id": str(system.network_id),
            "name": system.name.strip(),
            "previous_armed": previous_state,
            "armed": confirmed_state,
        }

    @app.put("/api/v1/devices/{device_id}/motion")
    async def set_device_motion(device_id: str, request: MotionRequest):
        """Enable or disable motion detection for one camera."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        camera = next(
            (
                camera
                for camera in controller.blink.cameras.values()
                if str(camera.camera_id) == device_id
            ),
            None,
        )

        if camera is None:
            raise HTTPException(
                status_code=404,
                detail=f"Device {device_id} was not found",
            )

        previous_state = camera.motion_enabled

        try:
            response = await camera.async_arm(request.enabled)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Blink motion command failed: {exc}",
            ) from exc

        if response is None:
            raise HTTPException(
                status_code=502,
                detail="Blink did not return a response",
            )

        # BlinkPy sends the command but does not update this cached field.
        # Keep our controller state synchronized with the accepted request.
        camera.motion_enabled = request.enabled

        return {
            "id": str(camera.camera_id),
            "name": camera.name.strip(),
            "previous_motion_enabled": previous_state,
            "motion_enabled": camera.motion_enabled,
        }

    @app.get("/api/v1/clips")
    def clips(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        """Return locally archived recorded clips from SQLite."""

        if not controller.catalog_db_path.is_file():
            raise HTTPException(
                status_code=503,
                detail="Local clip catalog is unavailable",
            )

        try:
            return list_clips(
                db_path=controller.catalog_db_path,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Clip catalog query failed: {exc}",
            ) from exc

    @app.put("/api/v1/clips/{media_id}/review")
    async def review_clip(media_id: str):
        """Mark one locally archived Blink clip as reviewed."""

        if controller.blink is None:
            raise HTTPException(
                status_code=503,
                detail="Blink controller is not connected",
            )

        clip = get_clip_by_media_id(
            db_path=controller.catalog_db_path,
            media_id=media_id,
        )

        if clip is None:
            raise HTTPException(
                status_code=404,
                detail="Clip was not found in the local catalog",
            )

        # Already reviewed is a successful no-op.
        if clip["watched"]:
            return {
                "id": media_id,
                "filename": clip["filename"],
                "watched": True,
                "already_reviewed": True,
            }

        url = (
            f"{controller.blink.urls.base_url}"
            f"/api/v4/accounts/{controller.blink.account_id}"
            f"/media/mark_as_viewed"
        )

        headers = dict(controller.blink.auth.header)
        headers["Content-Type"] = "application/json"

        blink_media_id = (
            int(media_id)
            if media_id.isdigit()
            else media_id
        )

        body = {
            "media_list": [blink_media_id]
        }

        try:
            await controller.blink.auth.query(
                url=url,
                data=json.dumps(body),
                headers=headers,
                reqtype="post",
                json_resp=False,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Blink review command failed: {exc}",
            ) from exc

        if not set_clip_watched(
            db_path=controller.catalog_db_path,
            catalog_id=clip["id"],
            watched=True,
        ):
            raise HTTPException(
                status_code=500,
                detail="Blink accepted the review command, "
                "but the local catalog was not updated",
            )

        sidecar_updated = False
        sidecar_name = clip.get("sidecar_path")

        if sidecar_name:
            sidecar_path = controller.archive_root / sidecar_name

            if sidecar_path.is_file():
                try:
                    metadata = json.loads(
                        sidecar_path.read_text(encoding="utf-8")
                    )
                    metadata["watched"] = True

                    sidecar_path.write_text(
                        json.dumps(metadata, indent=2) + "\n",
                        encoding="utf-8",
                    )

                    sidecar_updated = True

                except (OSError, json.JSONDecodeError):
                    sidecar_updated = False

        return {
            "id": media_id,
            "filename": clip["filename"],
            "watched": True,
            "already_reviewed": False,
            "sidecar_updated": sidecar_updated,
        }

    @app.delete("/api/v1/clips/catalog/{catalog_id}")
    async def delete_recorded_clip(catalog_id: int):
        """Delete one recorded clip from Blink and the local archive."""

        try:
            return await coordinate_clip_delete(
                controller,
                catalog_id,
            )

        except LookupError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        except RuntimeError as exc:

            message = str(exc)

            if (
                message.startswith(
                    "Blink rejected deletion"
                )
                or message.startswith(
                    "Blink deletion could not be confirmed"
                )
            ):
                raise HTTPException(
                    status_code=502,
                    detail=message,
                ) from exc

            raise HTTPException(
                status_code=500,
                detail=message,
            ) from exc

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Clip deletion failed: {exc}",
            ) from exc

    @app.put("/api/v1/clips/catalog/{catalog_id}/review")
    async def review_clip_by_catalog_id(catalog_id: int):
        """Mark one locally archived clip as reviewed by catalog row ID."""

        clip = get_clip_by_catalog_id(
            db_path=controller.catalog_db_path,
            catalog_id=catalog_id,
        )

        if clip is None:
            raise HTTPException(
                status_code=404,
                detail="Clip was not found in the local catalog",
            )

        blink_media_id = clip["blink_media_id"]

        if blink_media_id:

            if controller.blink is None:
                raise HTTPException(
                    status_code=503,
                    detail="Blink controller is not connected",
                )

            url = (
                f"{controller.blink.urls.base_url}"
                f"/api/v4/accounts/{controller.blink.account_id}"
                f"/media/mark_as_viewed"
            )

            headers = dict(controller.blink.auth.header)
            headers["Content-Type"] = "application/json"

            media_id_text = str(blink_media_id)

            blink_id = (
                int(media_id_text)
                if media_id_text.isdigit()
                else media_id_text
            )

            body = {
                "media_list": [blink_id]
            }

            try:
                await controller.blink.auth.query(
                    url=url,
                    data=json.dumps(body),
                    headers=headers,
                    reqtype="post",
                    json_resp=False,
                )

            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Blink review command failed: {exc}",
                ) from exc

        was_already_reviewed = bool(clip["watched"])

        if not was_already_reviewed:

            if not set_clip_watched(
                db_path=controller.catalog_db_path,
                catalog_id=catalog_id,
                watched=True,
            ):
                raise HTTPException(
                    status_code=500,
                    detail="Blink accepted the review command, "
                    "but the local catalog was not updated",
                )

        sidecar_updated = False
        sidecar_name = clip.get("sidecar_path")

        if sidecar_name:
            sidecar_path = controller.archive_root / sidecar_name

            if sidecar_path.is_file():
                try:
                    metadata = json.loads(
                        sidecar_path.read_text(encoding="utf-8")
                    )

                    metadata["watched"] = True

                    sidecar_path.write_text(
                        json.dumps(metadata, indent=2) + "\n",
                        encoding="utf-8",
                    )

                    sidecar_updated = True

                except (OSError, json.JSONDecodeError):
                    sidecar_updated = False

        return {
            "catalog_id": catalog_id,
            "id": blink_media_id,
            "filename": clip["filename"],
            "watched": True,
            "already_reviewed": was_already_reviewed,
            "sidecar_updated": sidecar_updated,
        }

    @app.get("/api/v1/clips/{filename}/video")
    async def clip_video(filename: str):
        """Return one locally archived MP4 clip with playback-normalized audio."""

        if (
            "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
            or Path(filename).suffix.lower() != ".mp4"
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid clip filename",
            )

        video_path = controller.archive_dir / filename

        if not video_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Clip video was not found",
            )

        # Keep playback copies outside the archive clips directory so they
        # cannot be mistaken for archived Blink recordings.
        playback_cache_dir = (
            controller.archive_dir.parent / "playback_cache"
        )

        playback_cache_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        playback_path = (
            playback_cache_dir
            / f"{video_path.stem}.loudnorm-v2.mp4"
        )

        # Rebuild the cached playback file if it does not exist or if the
        # archived source has changed since the cache was created.
        rebuild_playback = (
            not playback_path.is_file()
            or playback_path.stat().st_mtime
            < video_path.stat().st_mtime
        )

        if rebuild_playback:

            temp_path = (
                playback_cache_dir
                / f"{video_path.stem}.loudnorm-v2.tmp.mp4"
            )

            if temp_path.exists():
                temp_path.unlink()

            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                str(video_path),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11",
                # loudnorm upsamples internally; Windows AAC playback requires <=48 kHz.
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(temp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_data, stderr_data = await process.communicate()

            if process.returncode != 0:

                if temp_path.exists():
                    temp_path.unlink()

                error_text = stderr_data.decode(
                    "utf-8",
                    errors="replace",
                ).strip()

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "The playback copy could not be created."
                        + (
                            f" {error_text}"
                            if error_text
                            else ""
                        )
                    ),
                )

            temp_path.replace(playback_path)

        return FileResponse(
            path=playback_path,
            media_type="video/mp4",
            filename=filename,
            content_disposition_type="inline",
    )

    @app.get("/api/v1/clips/{filename}/thumbnail")
    async def clip_thumbnail(filename: str):
        """Return the cached thumbnail for one locally archived clip."""

        if (
            "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
            or Path(filename).suffix.lower() != ".mp4"
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid clip filename",
            )

        thumbnail_name = f"{Path(filename).stem}.jpg"
        thumbnail_path = controller.clip_thumbs_dir / thumbnail_name

        if not thumbnail_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Clip thumbnail was not found",
            )

        return FileResponse(
            path=thumbnail_path,
            media_type="image/jpeg",
            filename=thumbnail_name,
            content_disposition_type="inline",
        )

    return app
