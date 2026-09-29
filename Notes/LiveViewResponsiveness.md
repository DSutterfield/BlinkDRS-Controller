# Live View responsiveness — 2026-09-29

## Evidence

Recent Windows Live View logs showed an unexplained interval outside the older Pi startup timer. In the September 29 session, Windows measured 9.788 seconds to the start response, whereas the Pi's narrower interval was 2.839 seconds; Windows then needed another 1.592 seconds to present its first image. These are different measurement scopes, not synchronized machine timestamps. The earlier Pi timer excluded waiting for the recording lock and finishing an existing recording, and could not measure event-loop starvation before the request was dispatched.

Inspection found two concrete shared-event-loop blockers: the installed BlinkPy downloader calls synchronous time.sleep(delay) after each new download, and the controller scans archive files/sidecars and runs retention synchronously. Approximately 12,490 recordings were present during the initial measurement.

A read-only comparison executed the actual old and new polling scan functions against the archive, with cloud requests and writes replaced by controlled stubs. Old scan: 3.876 seconds, longest event-loop scheduling gap 3.886 seconds. New scan: 3.592 seconds, longest gap 0.048 seconds. This demonstrates removal of a multi-second local blocker; it does not measure the complete camera startup or prove the origin of every historical delay.

## Changes

- Controller-specific ResponsiveBlink downloader preserves filtering, naming, existing-file skips, and download pacing, using asyncio.sleep instead of the installed library's blocking sleep. The shared third-party library is not modified. Responses are released even on read/write failure. The override follows the installed BlinkPy private download hook; review it when upgrading BlinkPy.
- Bulk sidecar reads, file enumerations, changed-sidecar writes, catalog sync, and retention cleanup run outside the API event loop. Cached-thumbnail loops yield periodically. Archive/playback locks remain held until worker disk operations finish, even after repeated cancellation.
- Startup diagnostics begin in HTTP middleware and include handler entry, recording-lock acquisition, recording completion, bridge startup, and API response readiness. The response-ready marker appears in controller logs; earlier markers are included in the response consumed by Windows. Waiting before middleware dispatch is still outside these markers.
- Windows audio/video startup, buffers, calibration, and camera connection initialization are unchanged. Earlier video display and connection pre-initialization remain separate candidates, to assess after this change is tested.

## Validation

42 checks passed on the Pi against an isolated temporary candidate: 9 responsiveness/poll/HTTP timing tests, 3 polling/playback coordination tests, 11 playback preparation tests, 10 archive maintenance tests, 2 retention setting tests, and 7 existing startup fail-fast checks. The tests cover cooperative pacing, filtering, actual metadata updates, cancellation retaining archive locks, error propagation, full HTTP startup, safe deletion/retention, range playback, and startup producer failures. Test media and archive mutations are confined to temporary fixtures.

Deployed with source/hash checks, idle-state checks, backup, and rollback handling. Controller health and deployed hashes verified. Backup: /home/dan/BlinkDRS-Controller/backups/live-startup-20260929-120338.

Live verification: Pergola and Living Room both delivered JPEG and audio data and stopped cleanly; neither session was recorded. The first session after restart took 10.090 seconds to the HTTP start response, including 4.984 seconds initializing the Blink connection. The next session took 2.641 seconds. These are controller-response times, not Windows first-picture measurements; different cameras and connection states prevent treating them as an old/new speed comparison. Normal Windows use remains the acceptance test.
