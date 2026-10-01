# Live Recording — September 11, 2026

The Pi and Windows test build now support Record Clip / Stop Recording from an active Live View. Completed clips are saved beside motion clips in the Pi archive and SQLite catalog, labeled **Recorded Live**, without a Blink Cloud media ID. Recording copies incoming AAC audio when available. Review and deletion of these clips stay local.

Settings has a system-wide **Safety duration limit (minutes)** field. The Pi stores it in `live_recording_settings.json` under the archive root; initial value **150 seconds / 2.5 minutes**, accepted range 5–1800 seconds. Each recording snapshots the current limit. At that limit recording finalizes while Live View continues. Stopping Live View, switching its camera, or shutting down the controller also finalizes the active recording. A lost Windows connection does not disable the Pi timer.

Partial MP4s remain in `.live_recording_pending` and are never listed as valid clips. Completed files are probed, atomically published to `clips`, and cataloged under the archive lock. Their sidecars support idempotent catalog recovery on service startup and archive import. Failed/incomplete files remain private for diagnosis; they are not presented as successful recordings.

Blink's original live stream was not independently decodable by reliable late subscribers in the real-camera tests. The bridge therefore creates a regular-keyframe H.264 recording stream while copying source audio. This adds encoding work during Live View. The recorder remuxes that stream into MP4. A sampled Living Room run used approximately 80% of one CPU core for the bridge FFmpeg and 4% for the recorder; this is one observation, not a load guarantee across cameras. The Windows Live View audio calibration remains unchanged and is not baked into recordings.

Validation: Windows build zero warnings/errors; WPF minimum-width layout and local-clip review controls checked. Isolated Pi tests passed for API registration, settings validation/persistence, repeated start, stale-session rejection, automatic cutoff, manual finalization, natural stream end, audio/video MP4, catalog source/trigger, review preservation, local deletion while Blink is disconnected, importer compatibility, missing-row recovery, and exclusion of invalid recordings. Existing startup and TCP/TLS cleanup checks also passed.

Real Living Room tests produced an 11.78-second clip on manual recording stop and a 6.74-second clip when Live View stopped. Both contain audio and appear as Recorded Live. The first was fully decoded by FFmpeg without errors; Live View continued after manual recording stop. Earlier successful testing also left a 12.18-second clip. Failed test files were not cataloged. The production setting remains 150 seconds. Automatic cutoff was tested at a shorter limit in a temporary synthetic archive; a full 150-second real-camera cutoff was not run.

Use `Start-BlinkDRS.cmd` (the older validated launcher is updated too). User confirmed expected playback on a couple of cameras and accepted the teal Recorded Live border/label. Broader camera-model coverage remains to validate. The existing intermittent Live View startup issue and Mini-versus-Outdoor audio-offset investigation remain open.

Pi backups: initial feature `/home/dan/.blinkdrs/live-recording-20260911-125747`; latest stream correction `/home/dan/.blinkdrs/recording-relay-20260911-131211`.

Playback correction: normalization previously produced 96 kHz AAC, outside Windows decoder support. Output is now explicitly 48 kHz AAC with loudnorm-v2 cache names. The existing Front Door clip passed the playback endpoint and full decode checks; its original archive hash was unchanged. Recorded Clips mute was also enabled during diagnosis. Deployed backup: `/home/dan/.blinkdrs/playback-audio-20260911-132808`.


## October 1 duration-limit and thumbnail checks

Dan's Pergola limit test produced a playable 128.381-second MP4 under the 150-second wall-clock safety limit. The same Live View session reported repeated payload gaps (largest 10.350 seconds) and repeated FFmpeg audio/video timestamp discontinuities of about 10.5 seconds. These are consistent with media duration diverging from wall-clock capture time; the exact contribution and stop reason cannot be reconstructed because the old manifest did not retain capture time or stop reason. Do not mark full-duration reliability complete. The 150-second safety timer remains unchanged.

Closing Live View after about 10 seconds saved a playable 10.369-second MP4 with audio but no thumbnail. Re-extraction succeeded. The thumbnail was repaired and its catalog path updated. Generation now bounds decoder/filter/encoder threads, scales to 320 pixels wide, retries once, atomically publishes a nonempty JPEG, and logs extraction failures. Recordings now retain stop reason, capture wall seconds, maximum seconds and thumbnail availability in their manifest, plus finalization/FFmpeg diagnostics in the log.

Isolated recording integration checks passed for settings bounds/persistence, retried start, stale stop, cutoff, manual stop, natural end, audio/video, local deletion, recovery and invalid-output exclusion. The old test controller stub needed its newer faults.settings_path supplied. Actual short-clip thumbnail extraction, existing-thumbnail preservation and invalid-input cleanup also passed. Deployed after verifying Live View idle and the prior source hash; service active. Backup: backups/recording-thumbnail-20261001-155058. Source hash: 937bf882ebeba212828432aaed4bad813fc9699295a0b105c0a6e3df7090c1d6.

Next: repeat the 150-second camera test and compare wall time, saved media duration, stop reason and timestamp diagnostics; repeat close-window recording to verify thumbnail generation under live load.


### Repeated user tests, October 1

The repeated Pergola limit test stopped automatically with stop_reason=duration_limit after 150.001 wall seconds and saved 150.017 seconds of media (catalog 18865), with audio and a thumbnail. This validates real-camera cutoff in this run, not sustained reliability across models.

Closing Live View during the second recording stopped it manually after 10.479 wall seconds and saved 8.912 seconds of media (catalog 18866), with audio and a thumbnail. Live View status was inactive without an error; retained decoder messages showed no timestamp-discontinuity errors. The 1.567-second wall/media difference remains to quantify, including recorder connection/keyframe startup and delivery timing; it is not evidence of thumbnail or publication failure. Broader camera coverage and resource testing remain open.


### Mini 2 cross-model test and thumbnail cleanup race

Dan repeated both tests on Well House (Mini 2). Cutoff was duration_limit at 150.001 wall seconds; original MP4 duration 149.825 seconds and normalized playback copy 149.900 seconds. Dan clarified that 148 seconds was a recalled estimate of a value slightly below 150 seconds, consistent with the measured duration; no playback-display discrepancy is established. Close-window recording was manual at 10.984 wall seconds with 10.698 seconds of media and audio.

The short clip manifest logged thumbnail success, but the physical JPEG and catalog thumbnail reference were missing. Archive reconciliation removes JPEGs whose matching published MP4 does not yet exist. Thumbnail extraction occurred before acquiring archive_lock, exposing the JPEG to cleanup during publication. Generation now stays in .live_recording_pending; MP4 and thumbnail publish under archive_lock before catalog insertion. The regression forces reconciliation immediately after extraction: old source loses its JPEG and fails; fixed source preserves it and passes the recording integration suite. The Well House thumbnail was repaired.


### User audio and stop-path confirmation, October 1

Dan confirmed that Pergola's Stop Live View button and Stop Recording button each produced a clip. Sound was consistent between Live View and recorded playback; Stop Recording did not interrupt Live View sound. The latest short Well House close-window test produced an approximately 11-second clip with thumbnail, confirming normal-use publication after the cleanup race fix. These confirm the tested stop paths, audio continuity and thumbnail behavior. Visible clap alignment/drift testing and sustained Pi resource measurements remain unverified.


October 1 deferral requested by Dan: put the visible sound/clap audio-video alignment test on hold because the living room is in use and there is no convenient test setup. Resume when a suitable environment is available. Today's successful cutoff, stop/save, thumbnail and audio-continuity checks remain recorded; precise alignment is not yet verified. No reminder is scheduled.


### Resource baseline, October 1

Completed a bounded Well House (Mini 2) idle/Live View/150-second recording/cooldown measurement. No swap or throttling; recording averaged 27.5% total CPU and peaked at 51.6 C with over 7 GB memory available. The cutoff saved 149.76 seconds with audio and thumbnail; shutdown released FFmpeg processes. See Notes/PiResources-2026-10-01.md for sampling scope, background traffic, metrics and limits. Longer soak/cross-model and Windows-egress measurements remain optional follow-ups.
