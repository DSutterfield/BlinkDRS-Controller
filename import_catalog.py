"""
Import the BlinkDVR archive into SQLite.

Catalog identity is the physical MP4 file.

Blink metadata is attached only when a Blink media event can be
matched exactly to an MP4 by camera filename prefix and created_at.
Unmatched MP4s are preserved as metadata_status='local_only'.
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_ROOT = Path("/home/dan/BlinkDVR")
CLIPS_DIR = ARCHIVE_ROOT / "clips"
THUMBS_DIR = ARCHIVE_ROOT / "clip_thumbs"
DEFAULT_DB = ARCHIVE_ROOT / "blink_catalog.db"

NAME_RE = re.compile(
    r"^(.*)-(20\d{2}-\d{2}-\d{2}t\d{2}-\d{2}-\d{2}-00-00)$"
)

DEVICE_RE = re.compile(
    r"/networks/([^/]+)/([^/]+)/([^/]+)/"
)


def relative_to_archive(path):
    return str(path.relative_to(ARCHIVE_ROOT))


def parse_archive_name(path):
    match = NAME_RE.match(path.stem)
    if not match:
        raise ValueError(f"Unrecognized archive filename: {path.name}")

    prefix, stamp = match.groups()

    captured = datetime.strptime(
        stamp,
        "%Y-%m-%dt%H-%M-%S-00-00",
    ).replace(tzinfo=timezone.utc)

    return prefix, stamp, captured


def event_stamp(created_at):
    dt = datetime.fromisoformat(created_at)
    return dt.strftime("%Y-%m-%dt%H-%M-%S-00-00").lower()


def trigger_type(data):
    detections = data.get("cv_detection") or []

    if "person" in detections:
        return "person"

    if "vehicle" in detections:
        return "vehicle"

    if detections:
        return str(detections[0])

    return "motion"


def local_recording_metadata(path):
    sidecar = path.with_suffix(".json")
    if path.name.startswith("live-") and sidecar.is_file():
        data = json.loads(sidecar.read_text())
        if data.get("local_recording") == 1:
            return data
    return None


def build_recovery_maps():
    mp4_by_key = {}
    prefix_info = {}
    events = {}

    for mp4_path in CLIPS_DIR.glob("*.mp4"):
        if local_recording_metadata(mp4_path):
            continue
        prefix, stamp, _ = parse_archive_name(mp4_path)

        key = (prefix, stamp)
        if key in mp4_by_key:
            raise ValueError(f"Duplicate MP4 archive key: {key}")

        mp4_by_key[key] = mp4_path

    for sidecar_path in CLIPS_DIR.glob("*.json"):
        if local_recording_metadata(sidecar_path):
            continue
        data = json.loads(sidecar_path.read_text())

        prefix, _, _ = parse_archive_name(sidecar_path)

        match = DEVICE_RE.search(data.get("thumbnail", ""))
        if not match:
            raise ValueError(
                f"Cannot extract device identity from {sidecar_path.name}"
            )

        url_network_id, url_device_type, blink_device_id = match.groups()

        if url_network_id != str(data["network_id"]):
            raise ValueError(
                f"Network mismatch in {sidecar_path.name}"
            )

        if url_device_type != str(data["device_type"]):
            raise ValueError(
                f"Device-type mismatch in {sidecar_path.name}"
            )

        identity = {
            "blink_system_id": str(data["network_id"]),
            "system_name": data["network_name"],
            "blink_device_id": str(blink_device_id),
            "device_name": data["device_name"],
            "device_type": data["device_type"],
        }

        previous_identity = prefix_info.get(prefix)
        if previous_identity and previous_identity != identity:
            raise ValueError(
                f"Ambiguous device identity for filename prefix {prefix}"
            )

        prefix_info[prefix] = identity

        media_id = str(data["id"])

        if media_id not in events:
            events[media_id] = {
                "data": data,
                "source_sidecar": sidecar_path,
                "prefix": prefix,
            }

    matched_by_mp4 = {}

    for media_id, event in events.items():
        data = event["data"]
        prefix = event["prefix"]

        key = (prefix, event_stamp(data["created_at"]))
        mp4_path = mp4_by_key.get(key)

        if mp4_path is None:
            raise ValueError(
                f"No exact MP4 match for Blink media ID {media_id}"
            )

        if mp4_path in matched_by_mp4:
            other_id = matched_by_mp4[mp4_path]["media_id"]
            raise ValueError(
                f"MP4 {mp4_path.name} matches both "
                f"{other_id} and {media_id}"
            )

        matched_by_mp4[mp4_path] = {
            "media_id": media_id,
            "data": data,
            "source_sidecar": event["source_sidecar"],
        }

    return mp4_by_key, prefix_info, matched_by_mp4


def survey():
    mp4_by_key, prefix_info, matched_by_mp4 = build_recovery_maps()

    physical_mp4s = len(mp4_by_key)
    matched = len(matched_by_mp4)
    local_only = physical_mp4s - matched

    print(f"Physical MP4 files:       {physical_mp4s}")
    print(f"Blink-matched MP4s:       {matched}")
    print(f"Local-only MP4s:          {local_only}")
    print(f"Filename device prefixes: {len(prefix_info)}")


def upsert_system(conn, identity, seen_at):
    blink_system_id = identity["blink_system_id"]
    name = identity["system_name"]

    conn.execute(
        """
        INSERT INTO systems (
            blink_system_id,
            name,
            raw_name,
            active,
            first_seen_at,
            last_seen_at
        )
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(blink_system_id) DO UPDATE SET
            name = excluded.name,
            raw_name = excluded.raw_name,
            active = 1,
            first_seen_at = CASE
                WHEN systems.first_seen_at IS NULL
                  OR excluded.first_seen_at < systems.first_seen_at
                THEN excluded.first_seen_at
                ELSE systems.first_seen_at
            END,
            last_seen_at = CASE
                WHEN systems.last_seen_at IS NULL
                  OR excluded.last_seen_at > systems.last_seen_at
                THEN excluded.last_seen_at
                ELSE systems.last_seen_at
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            blink_system_id,
            name,
            name,
            seen_at,
            seen_at,
        ),
    )

    row = conn.execute(
        "SELECT id FROM systems WHERE blink_system_id = ?",
        (blink_system_id,),
    ).fetchone()

    return row[0]


def upsert_device(conn, identity, system_id, seen_at):
    blink_device_id = identity["blink_device_id"]
    name = identity["device_name"]
    device_type = identity["device_type"]

    conn.execute(
        """
        INSERT INTO devices (
            blink_device_id,
            system_id,
            entity_type,
            name,
            raw_name,
            device_type,
            raw_type,
            active,
            first_seen_at,
            last_seen_at
        )
        VALUES (?, ?, 'camera', ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(blink_device_id, entity_type) DO UPDATE SET
            system_id = excluded.system_id,
            name = excluded.name,
            raw_name = excluded.raw_name,
            device_type = excluded.device_type,
            raw_type = excluded.raw_type,
            active = 1,
            first_seen_at = CASE
                WHEN devices.first_seen_at IS NULL
                  OR excluded.first_seen_at < devices.first_seen_at
                THEN excluded.first_seen_at
                ELSE devices.first_seen_at
            END,
            last_seen_at = CASE
                WHEN devices.last_seen_at IS NULL
                  OR excluded.last_seen_at > devices.last_seen_at
                THEN excluded.last_seen_at
                ELSE devices.last_seen_at
            END,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            blink_device_id,
            system_id,
            name,
            name,
            device_type,
            device_type,
            seen_at,
            seen_at,
        ),
    )

    row = conn.execute(
        """
        SELECT id
        FROM devices
        WHERE blink_device_id = ?
          AND entity_type = 'camera'
        """,
        (blink_device_id,),
    ).fetchone()

    return row[0]


def import_mp4(conn, mp4_path, identity, matched_event):
    prefix, _, filename_time = parse_archive_name(mp4_path)

    system_name = identity["system_name"]
    device_name = identity["device_name"]

    physical_sidecar = mp4_path.with_suffix(".json")
    sidecar_path = (
        relative_to_archive(physical_sidecar)
        if physical_sidecar.exists()
        else None
    )

    thumb_path = THUMBS_DIR / f"{mp4_path.stem}.jpg"
    local_thumb = (
        relative_to_archive(thumb_path)
        if thumb_path.exists()
        else None
    )

    if matched_event is not None:
        data = matched_event["data"]

        metadata_status = "matched"
        blink_media_id = matched_event["media_id"]

        metadata_source_path = relative_to_archive(
            matched_event["source_sidecar"]
        )

        captured_at = data["created_at"]
        blink_updated_at = data.get("updated_at")
        time_zone = data.get("time_zone")

        watched = data.get("watched")
        if watched is not None:
            watched = 1 if watched else 0

        source = data.get("source")
        media_type = data.get("type")
        trigger = trigger_type(data)

        cv_detection = data.get("cv_detection")
        cv_detection_json = json.dumps(
            cv_detection if cv_detection is not None else []
        )

        duration_ms = data.get("duration_ms")
        thumbnail_cloud_url = data.get("thumbnail")

    else:
        # This is a real archived recording, but its neighboring
        # historical sidecar cannot be trusted as Blink metadata.
        metadata_status = "local_only"
        blink_media_id = None
        metadata_source_path = None

        captured_at = filename_time.isoformat()
        blink_updated_at = None
        time_zone = None
        watched = None
        source = None
        media_type = "video"
        trigger = None
        cv_detection_json = None
        duration_ms = None
        thumbnail_cloud_url = None

    system_id = upsert_system(
        conn,
        identity,
        captured_at,
    )

    device_id = upsert_device(
        conn,
        identity,
        system_id,
        captured_at,
    )

    conn.execute(
        """
        INSERT INTO clips (
            blink_media_id,
            metadata_status,
            device_id,
            system_id,
            device_name_snapshot,
            system_name_snapshot,
            filename,
            video_path,
            sidecar_path,
            metadata_source_path,
            thumbnail_path,
            thumbnail_cloud_url,
            file_size_bytes,
            captured_at,
            blink_updated_at,
            time_zone,
            watched,
            source,
            media_type,
            trigger_type,
            cv_detection_json,
            duration_ms,
            local_present,
            cloud_present
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(video_path) DO UPDATE SET
            blink_media_id = excluded.blink_media_id,
            metadata_status = excluded.metadata_status,
            device_id = excluded.device_id,
            system_id = excluded.system_id,
            device_name_snapshot = excluded.device_name_snapshot,
            system_name_snapshot = excluded.system_name_snapshot,
            filename = excluded.filename,
            sidecar_path = excluded.sidecar_path,
            metadata_source_path = excluded.metadata_source_path,
            thumbnail_path = excluded.thumbnail_path,
            thumbnail_cloud_url = excluded.thumbnail_cloud_url,
            file_size_bytes = excluded.file_size_bytes,
            captured_at = excluded.captured_at,
            blink_updated_at = excluded.blink_updated_at,
            time_zone = excluded.time_zone,
            watched = excluded.watched,
            source = excluded.source,
            media_type = excluded.media_type,
            trigger_type = excluded.trigger_type,
            cv_detection_json = excluded.cv_detection_json,
            duration_ms = excluded.duration_ms,
            local_present = 1,
            cloud_present = excluded.cloud_present,
            catalog_updated_at = CURRENT_TIMESTAMP
        """,
        (
            blink_media_id,
            metadata_status,
            device_id,
            system_id,
            device_name,
            system_name,
            mp4_path.name,
            relative_to_archive(mp4_path),
            sidecar_path,
            metadata_source_path,
            local_thumb,
            thumbnail_cloud_url,
            mp4_path.stat().st_size,
            captured_at,
            blink_updated_at,
            time_zone,
            watched,
            source,
            media_type,
            trigger,
            cv_detection_json,
            duration_ms,
            1,
            None,
        ),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Import BlinkDVR archive into SQLite catalog"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    args = parser.parse_args()

    mp4_by_key, prefix_info, matched_by_mp4 = build_recovery_maps()
    mp4_paths = sorted(mp4_by_key.values())

    print(f"Database:       {args.db}")
    print(f"Archive root:   {ARCHIVE_ROOT}")
    print(f"MP4 files:      {len(mp4_paths)}")
    print(f"Blink matched:  {len(matched_by_mp4)}")
    print(f"Local only:     {len(mp4_paths) - len(matched_by_mp4)}")
    print()

    conn = sqlite3.connect(args.db)

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        with conn:
            for number, mp4_path in enumerate(mp4_paths, start=1):
                prefix, _, _ = parse_archive_name(mp4_path)

                identity = prefix_info.get(prefix)
                if identity is None:
                    raise ValueError(
                        f"No device identity for {mp4_path.name}"
                    )

                matched_event = matched_by_mp4.get(mp4_path)

                import_mp4(
                    conn,
                    mp4_path,
                    identity,
                    matched_event,
                )

                if number % 250 == 0:
                    print(f"Imported {number} clips...")

        from live_recording import catalog_recording
        for video in CLIPS_DIR.glob("live-*.mp4"):
            metadata = local_recording_metadata(video)
            if metadata:
                catalog_recording(args.db, ARCHIVE_ROOT, video, metadata)

        systems = conn.execute(
            "SELECT COUNT(*) FROM systems"
        ).fetchone()[0]

        devices = conn.execute(
            "SELECT COUNT(*) FROM devices"
        ).fetchone()[0]

        clips = conn.execute(
            "SELECT COUNT(*) FROM clips"
        ).fetchone()[0]

        matched = conn.execute(
            """
            SELECT COUNT(*)
            FROM clips
            WHERE metadata_status = 'matched'
            """
        ).fetchone()[0]

        local_only = conn.execute(
            """
            SELECT COUNT(*)
            FROM clips
            WHERE metadata_status = 'local_only'
            """
        ).fetchone()[0]

        thumbnails = conn.execute(
            """
            SELECT COUNT(*)
            FROM clips
            WHERE thumbnail_path IS NOT NULL
            """
        ).fetchone()[0]

        print()
        print("Import complete.")
        print(f"Systems:              {systems}")
        print(f"Devices:              {devices}")
        print(f"Clips:                {clips}")
        print(f"Blink matched:        {matched}")
        print(f"Local only:           {local_only}")
        print(f"Local thumbnails:     {thumbnails}")
        print(f"Missing thumbnails:   {clips - thumbnails}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
