# Live Recording — September 11, 2026

The Pi and Windows test build now support Record Clip / Stop Recording from an active Live View. Completed clips are saved beside motion clips in the Pi archive and SQLite catalog, labeled **Recorded Live**, without a Blink Cloud media ID. Recording copies incoming AAC audio when available. Review and deletion of these clips stay local.

Settings has a system-wide **Safety duration limit (minutes)** field. The Pi stores it in `live_recording_settings.json` under the archive root; initial value **150 seconds / 2.5 minutes**, accepted range 5–1800 seconds. Each recording snapshots the current limit. At that limit recording finalizes while Live View continues. Stopping Live View, switching its camera, or shutting down the controller also finalizes the active recording. A lost Windows connection does not disable the Pi timer.

Partial MP4s remain in `.live_recording_pending` and are never listed as valid clips. Completed files are probed, atomically published to `clips`, and cataloged under the archive lock. Their sidecars support idempotent catalog recovery on service startup and archive import. Failed/incomplete files remain private for diagnosis; they are not presented as successful recordings.

Blink's original live stream was not independently decodable by reliable late subscribers in the real-camera tests. The bridge therefore creates a regular-keyframe H.264 recording stream while copying source audio. This adds encoding work during Live View. The recorder remuxes that stream into MP4. A sampled Living Room run used approximately 80% of one CPU core for the bridge FFmpeg and 4% for the recorder; this is one observation, not a load guarantee across cameras. The Windows Live View audio calibration remains unchanged and is not baked into recordings.

Validation: Windows build zero warnings/errors; WPF minimum-width layout and local-clip review controls checked. Isolated Pi tests passed for API registration, settings validation/persistence, repeated start, stale-session rejection, automatic cutoff, manual finalization, natural stream end, audio/video MP4, catalog source/trigger, review preservation, local deletion while Blink is disconnected, importer compatibility, missing-row recovery, and exclusion of invalid recordings. Existing startup and TCP/TLS cleanup checks also passed.

Real Living Room tests produced an 11.78-second clip on manual recording stop and a 6.74-second clip when Live View stopped. Both contain audio and appear as Recorded Live. The first was fully decoded by FFmpeg without errors; Live View continued after manual recording stop. Earlier successful testing also left a 12.18-second clip. Failed test files were not cataloged. The production setting remains 150 seconds. Automatic cutoff was tested at a shorter limit in a temporary synthetic archive; a full 150-second real-camera cutoff was not run.

Use `Start-BlinkDRS.cmd` (the older validated launcher is updated too). User playback/visual acceptance and other-camera recording quality remain to validate. The existing intermittent Live View startup issue and Mini-versus-Outdoor audio-offset investigation remain open.

Pi backups: initial feature `/home/dan/.blinkdrs/live-recording-20260911-125747`; latest stream correction `/home/dan/.blinkdrs/recording-relay-20260911-131211`.
