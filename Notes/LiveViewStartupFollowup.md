# Live View shutdown and early preview — 2026-09-29

## Findings and changes

The latest user sessions took 4.985–8.474 seconds to moving video. Several waited 0.9–3.5 seconds for the shared recording/Live View lock. That lock also protects stopping a previous Live View; it does not prove the user was recording.

The controller previously terminated FFmpeg before closing the stream, then waited for Blink's send/poll tasks. The installed poll routine sleeps between command checks and sends command-done in its finally block. Cleanup now closes the transport immediately, wakes the feed tasks by cancellation, and overlaps decoder shutdown with Blink completion. The controller owns and awaits every feed task so command-done can finish before shutdown returns. Simply cancelling BlinkPy's original feed was rejected by testing because its gather could return while command-done was still running. Existing decoder kill/reap and completion grace periods remain.

Windows displays a fresh, session-validated still image while audio buffers, with “Camera image received; preparing playback...” status. This independent preview does not consume the synchronized video queue or change audio reserve, video reserve, playback cadence, or Living Room's audio calibration. Preview has a 500 ms deadline; failure falls back to ordinary playback. Diagnostics record preview separately from first moving-frame assignment.

Concurrent Stop requests now share a single task. Repeated Close cannot bypass a pending stop. A confirmed controller session is stopped even if audio startup fails or is cancelled, and startup errors remain visible after cleanup.

## Verification

Clean Windows Release build, zero warnings/errors. Twelve isolated WPF checks passed: early preview, image freezing/status, separate timing, untouched playback queue, stop coalescing, repeated close, cancelled startup cleanup, stale-session rejection, preview deadline and cancellation, and preserved startup errors. Six controller shutdown tests passed against the installed Blink polling code and controlled transport, including awaited command-done, sibling cleanup after transport failure, auth cancellation, decoder kill/reap, and idempotence. Seven existing startup fail-fast checks passed.

Live tests used brief, muted Pergola and Living Room sessions without recording. Both produced the early image and at least 13 moving-frame assignments, then confirmed controller shutdown. User audio/video alignment still requires normal viewing; buffer/calibration settings were not altered.

| Measurement | Observed seconds |
|---|---:|
| Pergola stop before change | 5.770 |
| Pergola stop after change | 0.405 |
| Living Room stop after change | 0.220 |
| Living Room early still preview | 2.675 |
| Living Room synchronized playback | 4.127 |
| Pergola early preview, first connection after controller restart | 11.144 |
| Pergola synchronized playback, first connection after restart | 12.619 |

These are individual samples, not guaranteed startup times or a controlled camera-start benchmark. The first post-restart connection still has Blink initialization cost. No connection pre-initialization or reduced decoder analysis was introduced in this change.

Pi deployment backup: /home/dan/BlinkDRS-Controller/backups/live-followup-20260929-122416. Deployment verified file hashes and service health. Changes remain uncommitted and unpushed.
