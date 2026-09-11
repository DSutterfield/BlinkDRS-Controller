"""
SQLite catalog maintenance for the Blink DVR.

The physical MP4 is the durable archive object.
Blink metadata is trusted only when device name and created_at
exactly match the MP4 filename.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEVICE_RE = re.compile(
    r"/networks/([^/]+)/([^/]+)/([^/]+)/"
)

NAME_RE = re.compile(
    r"^(.*)-(20\d{2}-\d{2}-\d{2}t\d{2}-\d{2}-\d{2}-00-00)$"
)


def device_slug(name):
    return re.sub(
        r"[^a-z0-9]+",
        "-",
        str(name).lower(),
    ).strip("-")


def parse_archive_name(path):
    match = NAME_RE.match(path.stem)
    if not match:
        raise ValueError(
            f"Unrecognized archive filename: {path.name}"
        )

    prefix, stamp = match.groups()

    captured = datetime.strptime(
        stamp,
        "%Y-%m-%dt%H-%M-%S-00-00",
    ).replace(tzinfo=timezone.utc)

    return prefix, stamp, captured


def metadata_matches_mp4(mp4_path, metadata):
    created_at = metadata.get("created_at")
    device_name = metadata.get("device_name")

    if not created_at or not device_name:
        return False

    try:
        created = datetime.fromisoformat(created_at)
    except ValueError:
        return False

    expected_stem = (
        f"{device_slug(device_name)}-"
        f"{created.strftime('%Y-%m-%dt%H-%M-%S')}"
        "-00-00"
    )

    return mp4_path.stem.lower() == expected_stem


def trigger_type(metadata):
    detections = metadata.get("cv_detection") or []

    if "person" in detections:
        return "person"

    if "vehicle" in detections:
        return "vehicle"

    if detections:
        return str(detections[0])

    return "motion"


def identity_from_metadata(metadata):
    match = DEVICE_RE.search(metadata.get("thumbnail", ""))
    if not match:
        raise ValueError(
            "Cannot extract Blink device ID from thumbnail URL"
        )

    url_network_id, url_device_type, blink_device_id = (
        match.groups()
    )

    if url_network_id != str(metadata["network_id"]):
        raise ValueError(
            "Thumbnail network ID does not match sidecar"
        )

    if url_device_type != str(metadata["device_type"]):
        raise ValueError(
            "Thumbnail device type does not match sidecar"
        )

    return {
        "blink_system_id": str(metadata["network_id"]),
        "system_name": metadata["network_name"],
        "blink_device_id": str(blink_device_id),
        "device_name": metadata["device_name"],
        "device_type": metadata["device_type"],
    }


def relative_to_archive(path, archive_root):
    return str(path.relative_to(archive_root))


def upsert_system(conn, identity, seen_at):
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
            identity["blink_system_id"],
            identity["system_name"],
            identity["system_name"],
            seen_at,
            seen_at,
        ),
    )

    return conn.execute(
        "SELECT id FROM systems WHERE blink_system_id = ?",
        (identity["blink_system_id"],),
    ).fetchone()[0]


def upsert_device(conn, identity, system_id, seen_at):
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
            identity["blink_device_id"],
            system_id,
            identity["device_name"],
            identity["device_name"],
            identity["device_type"],
            identity["device_type"],
            seen_at,
            seen_at,
        ),
    )

    return conn.execute(
        """
        SELECT id
        FROM devices
        WHERE blink_device_id = ?
          AND entity_type = 'camera'
        """,
        (identity["blink_device_id"],),
    ).fetchone()[0]


def sync_clip(
    db_path,
    archive_root,
    thumbs_dir,
    mp4_path,
    metadata=None,
    identity=None,
):
    """
    Insert or refresh one physical MP4 in the SQLite catalog.

    If metadata exactly matches the MP4 filename, it is trusted and the
    clip is stored as metadata_status='matched'.

    Otherwise the MP4 is preserved as metadata_status='local_only'.
    """

    if metadata and metadata.get("local_recording") == 1:
        from live_recording import catalog_recording
        return catalog_recording(db_path, archive_root, mp4_path, metadata)

    archive_root = Path(archive_root)
    thumbs_dir = Path(thumbs_dir)
    mp4_path = Path(mp4_path)

    prefix, _, filename_time = parse_archive_name(mp4_path)

    physical_sidecar = mp4_path.with_suffix(".json")
    sidecar_path = (
        relative_to_archive(physical_sidecar, archive_root)
        if physical_sidecar.exists()
        else None
    )

    thumb_path = thumbs_dir / f"{mp4_path.stem}.jpg"
    thumbnail_path = (
        relative_to_archive(thumb_path, archive_root)
        if thumb_path.exists()
        else None
    )

    trusted_metadata = (
        metadata is not None
        and metadata_matches_mp4(mp4_path, metadata)
    )

    if trusted_metadata:
        identity = identity_from_metadata(metadata)

        metadata_status = "matched"
        blink_media_id = str(metadata["id"])

        metadata_source_path = (
            relative_to_archive(physical_sidecar, archive_root)
            if physical_sidecar.exists()
            else None
        )

        captured_at = metadata["created_at"]
        blink_updated_at = metadata.get("updated_at")
        time_zone = metadata.get("time_zone")

        watched = metadata.get("watched")
        if watched is not None:
            watched = 1 if watched else 0

        source = metadata.get("source")
        media_type = metadata.get("type")
        trigger = trigger_type(metadata)

        detections = metadata.get("cv_detection")
        cv_detection_json = json.dumps(
            detections if detections is not None else []
        )

        duration_ms = metadata.get("duration_ms")
        thumbnail_cloud_url = metadata.get("thumbnail")

    else:
        if identity is None:
            raise ValueError(
                f"No trusted metadata or device identity for {mp4_path.name}"
            )

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

    conn = sqlite3.connect(db_path)

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        with conn:
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
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL
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
                    identity["device_name"],
                    identity["system_name"],
                    mp4_path.name,
                    relative_to_archive(mp4_path, archive_root),
                    sidecar_path,
                    metadata_source_path,
                    thumbnail_path,
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
                ),
            )

    finally:
        conn.close()


def list_clips(db_path, limit=100, offset=0):
    """Return locally available recorded clips from the SQLite catalog."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM clips
            WHERE local_present = 1
            """
        ).fetchone()[0]

        unreviewed_total = conn.execute(
            """
            SELECT COUNT(*)
            FROM clips
            WHERE local_present = 1
              AND COALESCE(watched, 0) = 0
            """
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT
                id,
                blink_media_id,
                filename,
                device_name_snapshot,
                system_name_snapshot,
                captured_at,
                blink_updated_at,
                watched,
                source,
                media_type,
                trigger_type,
                cv_detection_json,
                duration_ms,
                time_zone,
                thumbnail_path
            FROM clips
            WHERE local_present = 1
            ORDER BY captured_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

        clips = []

        for row in rows:
            detections = []

            if row["cv_detection_json"]:
                try:
                    detections = json.loads(
                        row["cv_detection_json"]
                    )
                except json.JSONDecodeError:
                    detections = []

            clips.append(
                {
                    "catalog_id": row["id"],
                    "id": row["blink_media_id"],
                    "filename": row["filename"],
                    "device_name": row["device_name_snapshot"],
                    "system_name": row["system_name_snapshot"],
                    "created_at": row["captured_at"],
                    "updated_at": row["blink_updated_at"],
                    "watched": bool(row["watched"]),
                    "source": row["source"],
                    "type": row["media_type"],
                    "trigger_type": row["trigger_type"],
                    "cv_detection": detections,
                    "duration_ms": row["duration_ms"],
                    "time_zone": row["time_zone"],
                    "thumbnail_available": (
                        row["thumbnail_path"] is not None
                    ),
                }
            )

        return {
            "total": total,
            "unreviewed_total": unreviewed_total,
            "offset": offset,
            "limit": limit,
            "count": len(clips),
            "clips": clips,
        }

    finally:
        conn.close()


def get_clip_by_media_id(db_path, media_id):
    """Return one locally archived clip identified by its Blink media ID."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT
                id,
                blink_media_id,
                filename,
                sidecar_path,
                watched
            FROM clips
            WHERE blink_media_id = ?
              AND local_present = 1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (str(media_id),),
        ).fetchone()

        return dict(row) if row is not None else None

    finally:
        conn.close()


def set_clip_watched(db_path, catalog_id, watched):
    """Update the local watched/reviewed state for one catalog clip."""

    conn = sqlite3.connect(db_path)

    try:
        with conn:
            cursor = conn.execute(
                """
                UPDATE clips
                SET watched = ?,
                    catalog_updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    1 if watched else 0,
                    catalog_id,
                ),
            )

        return cursor.rowcount == 1

    finally:
        conn.close()

def get_clip_by_catalog_id(db_path, catalog_id):
    """Return one locally archived clip identified by its catalog row ID."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT
                id,
                blink_media_id,
                source,
                filename,
                sidecar_path,
                thumbnail_path,
                watched
            FROM clips
            WHERE id = ?
              AND local_present = 1
            LIMIT 1
            """,
            (catalog_id,),
        ).fetchone()

        return dict(row) if row is not None else None

    finally:
        conn.close()

def delete_clip_by_catalog_id(db_path, catalog_id):
    """Delete one clip row from the local SQLite catalog."""

    conn = sqlite3.connect(db_path)

    try:
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM clips
                WHERE id = ?
                """,
                (catalog_id,),
            )

        return cursor.rowcount == 1

    finally:
        conn.close()
