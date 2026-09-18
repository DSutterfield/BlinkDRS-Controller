import json
from pathlib import Path
import shutil
import uuid
from archive_maintenance import clip_artifact_paths

DELETE_MANIFEST_NAME = "delete-stage.json"

def _write_stage_manifest(stage):
    """Persist delete-stage recovery information atomically."""

    quarantine_dir = Path(stage["quarantine_dir"])

    manifest_path = (
        quarantine_dir / DELETE_MANIFEST_NAME
    )

    temp_path = (
        quarantine_dir / f"{DELETE_MANIFEST_NAME}.tmp"
    )

    temp_path.write_text(
        json.dumps(stage, indent=2) + "\n",
        encoding="utf-8",
    )

    temp_path.replace(manifest_path)


def set_stage_state(stage, state):
    """Persist the current coordinated-delete state."""

    allowed_states = {
        "staged",
        "cloud_delete_pending",
        "cloud_deleted",
        "catalog_deleted",
    }

    if state not in allowed_states:
        raise ValueError(
            f"Invalid delete stage state: {state}"
        )

    stage["state"] = state
    _write_stage_manifest(stage)


def load_stage_manifest(quarantine_dir):
    """Load a persisted delete-stage manifest."""

    manifest_path = (
        Path(quarantine_dir) / DELETE_MANIFEST_NAME
    )

    return json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

def stage_clip_for_delete(archive_root, clip):
    """
    Move a clip's local files into a private quarantine directory.

    Returns a dictionary describing the staged files so they can either
    be restored or permanently removed later.
    """

    archive_root = Path(archive_root).resolve()

    quarantine_root = archive_root / ".delete_quarantine"
    quarantine_root.mkdir(parents=True, exist_ok=True)

    quarantine_dir = (
        quarantine_root
        / f"{clip['id']}-{uuid.uuid4().hex}"
    )

    quarantine_dir.mkdir()

    staged_files = []

    video_name = clip.get("filename")

    candidates = [("artifact", str(path.relative_to(archive_root)))
                  for path in clip_artifact_paths(archive_root, video_name,
                      clip.get("sidecar_path"), clip.get("thumbnail_path"))]

    if not video_name:
        raise ValueError("Clip has no video filename")

    video_source = (
        archive_root / "clips" / video_name
    ).resolve()

    if not video_source.is_file():
        raise FileNotFoundError(
            f"Clip video is missing: {video_name}"
        )

    try:
        for file_type, relative_name in candidates:

            if not relative_name:
                continue

            source = (archive_root / relative_name).resolve()

            # Safety: every source must remain beneath archive_root.
            if archive_root not in source.parents:
                raise ValueError(
                    f"{file_type} path escapes archive root: {relative_name}"
                )

            if not source.is_file():
                continue

            destination = quarantine_dir / source.name

            shutil.move(str(source), str(destination))

            staged_files.append(
                {
                    "type": file_type,
                    "source": str(source),
                    "quarantine": str(destination),
                }
            )

        stage = {
            "catalog_id": clip["id"],
            "blink_media_id": clip.get("blink_media_id"),
            "quarantine_dir": str(quarantine_dir),
            "files": staged_files,
            "state": "staged",
        }

        _write_stage_manifest(stage)

        return stage

    except Exception:

        # Compensating rollback if staging itself fails partway through.
        for staged in reversed(staged_files):

            quarantine_path = Path(staged["quarantine"])
            source_path = Path(staged["source"])

            if quarantine_path.is_file():
                source_path.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                shutil.move(
                    str(quarantine_path),
                    str(source_path),
                )

        if quarantine_dir.exists():
            shutil.rmtree(
                quarantine_dir,
                ignore_errors=True,
            )

        raise

def restore_staged_clip(stage):
    """
    Restore all quarantined files to their original locations.

    Raises an exception rather than overwriting an unexpected file.
    """

    staged_files = stage.get("files", [])

    # Verify the entire restore can succeed before moving any file.
    for staged in staged_files:

        quarantine_path = Path(staged["quarantine"])
        source_path = Path(staged["source"])

        if quarantine_path.is_file() and source_path.exists():
            raise FileExistsError(
                f"Cannot restore because destination already exists: "
                f"{source_path}"
            )

    for staged in reversed(staged_files):

        quarantine_path = Path(staged["quarantine"])
        source_path = Path(staged["source"])

        if not quarantine_path.is_file():
            continue

        source_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.move(
            str(quarantine_path),
            str(source_path),
        )

    quarantine_dir = Path(stage["quarantine_dir"])

    if quarantine_dir.exists():
        shutil.rmtree(quarantine_dir)

    return True

def finalize_staged_clip(stage):
    """
    Permanently remove files from a completed delete quarantine.

    Unexpected files are never removed. If anything unexpected exists,
    finalization stops while the recovery manifest is still intact.
    """

    quarantine_dir = Path(
        stage["quarantine_dir"]
    ).resolve()

    staged_files = stage.get("files", [])

    quarantine_paths = []

    # Safety: every staged file must belong to this quarantine directory.
    for staged in staged_files:

        quarantine_path = Path(
            staged["quarantine"]
        ).resolve()

        if quarantine_dir not in quarantine_path.parents:
            raise ValueError(
                f"Quarantine path escapes delete directory: "
                f"{quarantine_path}"
            )

        quarantine_paths.append(
            quarantine_path
        )

    manifest_path = (
        quarantine_dir / DELETE_MANIFEST_NAME
    ).resolve()

    # Before deleting anything, verify that the directory contains
    # only files belonging to this operation plus its manifest.
    expected_paths = set(quarantine_paths)
    expected_paths.add(manifest_path)

    if quarantine_dir.exists():

        for entry in quarantine_dir.iterdir():

            if entry.resolve() not in expected_paths:
                raise OSError(
                    f"Unexpected item in quarantine: {entry}"
                )

    # Permanently remove only this operation's staged files.
    for quarantine_path in quarantine_paths:

        if not quarantine_path.exists():
            continue

        if not quarantine_path.is_file():
            raise OSError(
                f"Unexpected non-file in quarantine: "
                f"{quarantine_path}"
            )

        quarantine_path.unlink()

    # Keep the recovery manifest until all staged files are gone.
    if manifest_path.is_file():
        manifest_path.unlink()

    # This should now be empty. rmdir still refuses to erase
    # anything unexpected that might somehow remain.
    if quarantine_dir.exists():
        quarantine_dir.rmdir()

    return True
