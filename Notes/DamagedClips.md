# Damaged clips — September 12, 2026

Cause: requires further investigation. Fresh Blink downloads for Back Door
(September 11, 3:27:47 PM, Person; catalog 9766) and Pergola (September 11,
8:02:24 PM, Motion; catalog 9814) match the archived originals byte for byte.
Both originals report zero duration in Blink metadata and produce decode errors.
Re-downloading cannot restore the missing footage.

Both are Outdoor cameras powered by third-party solar panels with internal
batteries. Manufacturer instructions require removing the cameras' AA batteries.
Panel brand/model is pending. Back Door receives limited direct sun. A power
interruption is a hypothesis, not a confirmed cause. Reported camera battery
voltage must not be interpreted as a measurement of the solar battery.

Confirmed damaged clips have an independent SQLite clip_validation record:
status, reason, source SHA-256, and UTC check time. MP4s, sidecars, thumbnails,
review status, and original catalog rows remain intact. Catalog synchronization
preserves the validation record. Unknown/zero duration alone does not hide files.

The default /api/v1/clips list excludes status=damaged, including totals,
unreviewed totals, and pagination. The existing Windows app uses this list for
Recorded Clips and playback navigation; refresh it after deployment.
Research: GET /api/v1/clips?include_damaged=true includes preserved clips and
returns validation_status. Direct file and catalog-ID access remain available.
No automatic scan or duration-based exclusion was added.

To restore one clip to the normal list, run set_clip_validation(db_path,
catalog_id, 'unchecked', 'Restored for investigation', existing_sha256) from
catalog_store on the Pi. After repairing/replacing media, validate it again and
record the new hash before restoring it. This feature does not change existing
archive-retention policy or explicit deletion controls.

Validation: catalog regression covers counts, pagination, zero/unknown-duration
visibility, metadata updates, preservation, research inclusion, and restoration.
