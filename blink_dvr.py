"""
Blink DVR - polls your Blink account for new motion clips and downloads
them to local folders organized by camera name.
"""
import asyncio
import configparser
import contextlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import ClientSession
from blinkpy.auth import Auth, BlinkTwoFARequiredError
from blinkpy.blinkpy import Blink

import uvicorn
from controller_api import create_app
from catalog_store import sync_clip
from clip_retention import load_clip_retention
from archive_maintenance import cleanup_expired_clips, reconcile_local_presence
from fault_log import FaultLog, BlinkErrorHandler, error_code

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "settings.ini"
LOCAL_CONFIG_PATH = ROOT / "config" / "settings.local.ini"
CREDS_PATH = ROOT / "config" / "credentials.json"

config = configparser.ConfigParser()
config.read([CONFIG_PATH, LOCAL_CONFIG_PATH])

OUTPUT_DIR = Path(config["download"]["output_dir"])
ARCHIVE_ROOT = OUTPUT_DIR.parent
CATALOG_DB = ARCHIVE_ROOT / "blink_catalog.db"

# 2026-08-13 - Dan/Sage:
# Recorded-clip thumbnails are cached locally by the DVR poller so
# Dashboard playback never has to wait for a Blink thumbnail request.
CLIP_THUMBS_DIR = OUTPUT_DIR.parent / "clip_thumbs"
CLIP_THUMBS_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL = int(config["download"]["poll_interval_seconds"])
DELETE_AFTER_DAYS = int(config["download"]["delete_after_days"])
LOG_DIR = Path(config["logging"]["log_dir"])

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_handler = RotatingFileHandler(
    LOG_DIR / "blink_dvr.log", maxBytes=5_000_000, backupCount=5
)
logging.basicConfig(
    level=config["logging"]["level"],
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[log_handler, logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("blink_dvr")


def load_creds():
    if CREDS_PATH.exists():
        with open(CREDS_PATH, "r") as f:
            return json.load(f)
    return None


def save_creds(data):
    with open(CREDS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def sanitize(name):
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()

def sync_catalog_clip(mp4):
    """
    Refresh one clip in the SQLite catalog from its trusted sidecar.
    """
    from metadata_helper import read_sidecar

    metadata = read_sidecar(mp4)
    if not metadata:
        return False

    try:
        sync_clip(
            db_path=CATALOG_DB,
            archive_root=ARCHIVE_ROOT,
            thumbs_dir=CLIP_THUMBS_DIR,
            mp4_path=mp4,
            metadata=metadata,
        )
        return True

    except ValueError as e:
        log.warning(
            f"Catalog metadata not trusted for {mp4.name}: {e}"
        )
        return False

    except Exception as e:
        log.warning(
            f"Catalog sync failed for {mp4.name}: {e}"
        )
        return False


async def setup_blink(session):
    saved = load_creds()
    if not saved:
        log.error("No credentials.json found. Run first_login.py first.")
        sys.exit(1)

    log.info("Loading saved credentials")
    blink = Blink(session=session)
    blink.auth = Auth(saved, no_prompt=True, session=session)

    try:
        await blink.start()
    except BlinkTwoFARequiredError:
        log.error("Saved token expired - re-run first_login.py to renew")
        sys.exit(1)

    return blink

async def cache_clip_thumbnail(blink, mp4, metadata):
    """Cache one recorded-clip thumbnail locally if it is missing."""

    thumbnail_path = metadata.get("thumbnail")
    if not thumbnail_path:
        return False

    cache_path = CLIP_THUMBS_DIR / f"{mp4.stem}.jpg"

    # Nothing to do if this thumbnail has already been cached.
    if cache_path.exists():
        return False

    if thumbnail_path.startswith("http"):
        url = thumbnail_path
    else:
        url = f"{blink.urls.base_url}{thumbnail_path}"

    response = await blink.auth.query(
        url=url,
        headers=dict(blink.auth.header),
        reqtype="get",
        json_resp=False,
    )

    if response is None:
        return False

    try:
        if response.status != 200:
            return False

        image_bytes = await response.read()

        if not image_bytes:
            return False

        cache_path.write_bytes(image_bytes)
        return True
    finally:
        response.release()

async def download_new_clips(blink):
    from datetime import datetime, timedelta, timezone
    from metadata_helper import (
        metadata_path_for,
        read_sidecar,
        write_sidecar,
        match_event_to_file,
    )

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y/%m/%d %H:%M:%S"
    )
    log.info(f"Polling for clips since {since} UTC")

    events = await blink.get_videos_metadata(since=since, stop=10)

    # 2026-08-10 - Dan/Sage:
    # Refresh mutable Blink review status in existing sidecars.
    # Match by Blink event ID so we do not rely on filename timestamps.
    events_by_id = {
        event.get("id"): event
        for event in events
        if event.get("id") is not None
    }

    status_updates = 0
    catalog_refresh = set()

    # 2026-08-13 - Dan/Sage:
    # Fill the recorded-clip thumbnail cache gradually so background
    # Blink traffic never interferes with Dashboard clip playback.
    thumbnail_cache_limit = 5
    thumbnails_cached = 0

    for mp4 in sorted(
        OUTPUT_DIR.glob("*.mp4"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        sidecar = read_sidecar(mp4)
        if not sidecar:
            continue

        event = events_by_id.get(sidecar.get("id"))

        # Refresh metadata when Blink still returns this event.
        if event:
            changed = False

            for field in (
                "network_id",
                "network_name",
                "thumbnail",
            ):
                if field in event and sidecar.get(field) != event.get(field):
                    sidecar[field] = event.get(field)
                    changed = True

            if (
                "watched" in event
                and sidecar.get("watched") != event.get("watched")
            ):
                sidecar["watched"] = event.get("watched")
                changed = True

            if (
                "updated_at" in event
                and sidecar.get("updated_at") != event.get("updated_at")
            ):
                sidecar["updated_at"] = event.get("updated_at")
                changed = True

            if changed:
                metadata_path_for(mp4).write_text(
                    json.dumps(sidecar, indent=2)
                )
                status_updates += 1
                catalog_refresh.add(mp4)

        # Cache from the sidecar itself.  The Blink event does not have
        # to still be present in the current metadata response.
        if thumbnails_cached < thumbnail_cache_limit:
            try:
                if await cache_clip_thumbnail(blink, mp4, sidecar):
                    thumbnails_cached += 1
                    catalog_refresh.add(mp4)
            except Exception as e:
                log.warning(
                    f"Thumbnail cache failed for {mp4.name}: {e}"
                )

    # 2026-08-19 - Dan/Sage:
    # Retry metadata association for archived MP4s that do not yet have
    # a sidecar. A transient Blink metadata failure must not leave a clip
    # permanently without metadata. Exact matching prevents neighboring
    # rapid-motion clips from being confused with one another.
    missing_sidecars = [
        mp4
        for mp4 in OUTPUT_DIR.glob("*.mp4")
        if not metadata_path_for(mp4).exists()
    ]

    recovered_sidecars = 0

    if missing_sidecars:
        for event in events:
            matched = match_event_to_file(event, missing_sidecars)

            if matched and not metadata_path_for(matched).exists():
                write_sidecar(matched, event)
                recovered_sidecars += 1
                catalog_refresh.add(matched)

        if recovered_sidecars:
            log.info(
                f"Recovered metadata sidecars for "
                f"{recovered_sidecars} archived clip(s)"
            )

    if status_updates:
        log.info(
            f"Refreshed metadata in {status_updates} sidecar(s)"
        )

    if thumbnails_cached:
        log.info(
            f"Cached {thumbnails_cached} recorded-clip thumbnail(s)"
        )

    before = set(OUTPUT_DIR.rglob("*.mp4"))

    await blink.download_videos(
        path=str(OUTPUT_DIR),
        since=since,
        camera="all",
        stop=10,
        delay=1,
    )

    after = set(OUTPUT_DIR.rglob("*.mp4"))
    new_files = list(after - before)

    # If we got new clips, fetch their metadata and write sidecars
    if new_files:
        log.info(f"Downloaded {len(new_files)} new clip(s); fetching metadata")
        try:
            api_since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%S+0000"
            )
            url = (
                f"{blink.urls.base_url}/api/v1/accounts/{blink.account_id}"
                f"/media/changed?since={api_since}&page=1"
            )
            resp = await blink.auth.query(
                url=url, headers=blink.auth.header, reqtype="get", json_resp=True
            )
            events = resp.get("media", []) if isinstance(resp, dict) else []

            sidecar_count = 0
            for event in events:
                matched = match_event_to_file(event, new_files)
                if matched and not metadata_path_for(matched).exists():
                    write_sidecar(matched, event)
                    sidecar_count += 1
                    catalog_refresh.add(matched)
            if sidecar_count:
                log.info(f"Wrote {sidecar_count} metadata sidecar(s)")
        except Exception as e:
            log.warning(f"Metadata fetch failed (clips still saved): {e}")

    # 2026-08-15 - Dan/Sage:
    # Give newly downloaded clips their thumbnails promptly so the
    # Dashboard does not have to wait for the gradual background cache.
    # Limit attempts so a first-run backlog does not hammer Blink.
    immediate_thumbnail_limit = 10
    immediate_thumbnail_attempts = 0
    immediate_thumbnails_cached = 0

    for mp4 in sorted(
        new_files,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        if immediate_thumbnail_attempts >= immediate_thumbnail_limit:
            break

        sidecar = read_sidecar(mp4)
        if not sidecar:
            continue

        immediate_thumbnail_attempts += 1

        try:
            if await cache_clip_thumbnail(blink, mp4, sidecar):
                immediate_thumbnails_cached += 1
        except Exception as e:
            log.warning(
                f"Immediate thumbnail cache failed for {mp4.name}: {e}"
            )

    if immediate_thumbnails_cached:
        log.info(
            f"Cached {immediate_thumbnails_cached} new clip thumbnail(s)"
        )

    # 2026-08-19 - Dan/Sage:
    # Refresh the SQLite catalog once per changed clip after all sidecar
    # and thumbnail work for this poll cycle is complete. Catalog failures
    # must never prevent Blink clips from being downloaded and archived.
    if catalog_refresh:
        catalog_synced = 0

        for mp4 in sorted(catalog_refresh):
            if sync_catalog_clip(mp4):
                catalog_synced += 1

        if catalog_synced:
            log.info(
                f"Refreshed SQLite catalog for "
                f"{catalog_synced} clip(s)"
            )

    return len(new_files)

def cleanup_old_clips():
    return cleanup_expired_clips(CATALOG_DB, OUTPUT_DIR, load_clip_retention(LOCAL_CONFIG_PATH))

class EmbeddedUvicornServer(uvicorn.Server):
    """Uvicorn server embedded inside the Blink Controller process."""

    @contextlib.contextmanager
    def capture_signals(self):
        # systemd owns SIGTERM/SIGINT handling for the controller process.
        yield

class BlinkController:
    """Long-lived Blink controller shared by DVR and API services."""

    def __init__(self):
        self.faults = FaultLog(LOG_DIR, LOCAL_CONFIG_PATH)
        if not config.has_section('fault_log'):
            self.faults.save_days(self.faults.days)
        self.faults.append('STARTED', 'Pi controller', '', 'Controller started. Events during downtime may not have been observed.')
        self.blink_errors = BlinkErrorHandler(self.faults)
        logging.getLogger('blinkpy').addHandler(self.blink_errors)
        self.session = None
        self.blink = None
        self.api_server = None
        self.archive_dir = OUTPUT_DIR
        self.clip_thumbs_dir = CLIP_THUMBS_DIR
        self.catalog_db_path = CATALOG_DB
        self.archive_root = ARCHIVE_ROOT
        self.archive_lock = asyncio.Lock()
        self.status_refresh_lock = asyncio.Lock()
        self.status_changed = asyncio.Condition()
        self.status_revision = 0
        self.status_signature = None
        self.last_status_refresh_monotonic = 0.0
        self.blink_cloud_reachable = None
        self.last_poll_success_at = None
        self.last_poll_error = None

    async def start(self):
        """Create the HTTP session and connect to Blink."""
        self.session = ClientSession()
        try:
            self.blink = await setup_blink(self.session)
        except (Exception, SystemExit) as exc:
            self.faults.safe_observe('blink-login', 'Blink login', False, error_code(exc), 'Blink connection could not start. Check login and connectivity.')
            raise
        self.faults.safe_observe('blink-login', 'Blink login', True)
        self.faults.devices(self.blink)

        log.info(
            f"Connected. Found {len(self.blink.cameras)} cameras: "
            f"{list(self.blink.cameras.keys())}"
        )

    async def stop(self):
        """Shut down the controller cleanly."""
        if self.session is not None:
            await self.session.close()
            self.session = None

        self.blink = None

    async def poll_once(self):
        """Perform one DVR polling and cleanup cycle."""

        async with self.archive_lock:

            status_ok = await self.refresh_blink_status()

            downloaded = await download_new_clips(
                self.blink
            )

            if downloaded:
                log.info(
                    f"Downloaded {downloaded} new clip(s)"
                )

            cleanup_old_clips()
            return status_ok

    async def refresh_blink_status(self, minimum_age_seconds=10):
        """Refresh Blink system and camera state, with a short shared throttle."""

        if self.blink is None:
            return False

        async with self.status_refresh_lock:
            age = time.monotonic() - self.last_status_refresh_monotonic
            if age < minimum_age_seconds:
                return True

            revision = self.blink_errors.revision
            try:
                refreshed = await self.blink.refresh(force=True)
            except Exception as exc:
                self.faults.safe_observe('blink-status', 'Blink status connection', False, error_code(exc), 'Status refresh failed.')
                raise

            # BlinkPy's ordinary refresh updates existing objects, but it does
            # not rebuild a Sync Module or its cameras when the controller was
            # started while that module was offline. Re-run setup whenever a
            # system remains unavailable so a returning module and its camera
            # inventory can be discovered without restarting this service.
            if any(
                not bool(system.online and system.available)
                for system in self.blink.sync.values()
            ):
                refreshed = await self.blink.setup_post_verify()

            trustworthy = bool(refreshed) and revision == self.blink_errors.revision
            self.faults.safe_observe('blink-status', 'Blink status connection', trustworthy,
                                    'STATUS_REFRESH_FAILED', 'Blink status refresh result.')
            if trustworthy:
                self.faults.devices(self.blink)
            await self.publish_status_if_changed()
            self.last_status_refresh_monotonic = time.monotonic()
            return trustworthy

    async def publish_status_if_changed(self):
        """Notify API event subscribers when authoritative Blink state changes."""

        systems = sorted(
            (
                str(system.network_id),
                bool(system.online and system.available),
                system.arm,
            )
            for system in self.blink.sync.values()
        )
        cameras = sorted(
            (
                str(camera.camera_id),
                str(camera.sync.network_id),
                bool(camera.online and camera.sync.available),
                camera.motion_enabled,
            )
            for camera in self.blink.cameras.values()
        )
        signature = json.dumps(
            {"systems": systems, "cameras": cameras},
            sort_keys=True,
            default=str,
        )

        if signature == self.status_signature:
            return False

        self.status_signature = signature
        async with self.status_changed:
            self.status_revision += 1
            self.status_changed.notify_all()
        return True

    async def run_api(self):
        """Run the Controller API in the same process and event loop."""

        app = create_app(self)

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
        )

        self.api_server = EmbeddedUvicornServer(config)
        await self.api_server.serve()

    async def run(self):
        """Run the Blink controller, DVR poller, and API continuously."""

        reconcile_local_presence(CATALOG_DB, OUTPUT_DIR)
        await self.start()

        api_task = asyncio.create_task(self.run_api())

        try:
            while True:
                try:
                    revision = self.blink_errors.revision
                    status_ok = await self.poll_once()
                    clean_poll = status_ok and revision == self.blink_errors.revision
                    self.faults.safe_observe('poll', 'Blink polling connection', clean_poll,
                                            'BLINK_LIBRARY_ERROR', 'Blink polling completed with a library warning or error.' if not clean_poll else 'Blink polling resumed.')
                    if clean_poll:
                        self.blink_errors.recovered()

                    self.blink_cloud_reachable = True
                    self.last_poll_success_at = (
                        datetime.now(timezone.utc).isoformat()
                    )
                    self.last_poll_error = None

                    log.info(
                        f"Poll cycle complete; next poll in "
                        f"{POLL_INTERVAL} seconds"
                    )
                except Exception as e:
                    self.blink_cloud_reachable = False
                    self.last_poll_error = str(e)
                    self.faults.safe_observe('poll', 'Blink polling connection', False, error_code(e), 'Blink polling failed.')

                    log.exception(f"Error in poll cycle: {e}")

                await asyncio.sleep(POLL_INTERVAL)

        finally:
            if self.api_server is not None:
                self.api_server.should_exit = True

            await api_task
            await self.stop()

async def main():
    log.info("Blink DVR starting")

    controller = BlinkController()
    await controller.run()

if __name__ == "__main__":
    print("Blink DVR starting up...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
