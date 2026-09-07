# 2026-09-07 - Live View measurements and startup cleanup

- Added startup and bounded media-delivery gap diagnostics.
- Bounded FFmpeg input analysis; measured payload-to-JPEG delay fell from about
  6.2 seconds to about 2.2 seconds in Pergola tests.
- Restored expected TLS-close handling in the TCP-safe receiver and added
  connection milestones and descriptive startup timeout messages.
- Syntax, receiver regression tests, deployment health, and two consecutive
  start/stop tests passed. Playback gaps and Blink command-done errors remain
  under investigation. Single-thread decoding was tested and reverted.

# 2026-09-07 - Controller project rename

- Adopted BlinkDRS-Controller for the repository, solution, and checkout folders.
- Updated Pi service paths, Python launchers, Windows shortcuts, and BlinkDRS SSH configuration.
- Preserved controller behavior, archive storage, and existing local changes.

# Change Log

## 2026-07-24 — Initial project documentation

Added:

- `Developer_Notes.md`
- `ChangeLog.md`
- `Local_Modifications.md`

No source-code changes were made during this documentation step.

## 2026-07-29 — Capability reporting and login setup

Added:

- `capability_probe.py`
- `capability_matrix.py`

Updated:

- `first_login.py`
  - Added input validation.
  - Added required 2FA handling.
  - Save credentials only after successful Blink setup and camera discovery.

Repository maintenance:

- Added `.gitignore` rules for local environments, Visual Studio files,
  credentials, generated reports, and user-specific project settings.

## 2026-09-04 — Device control and event-driven health reporting

Added and verified:

- Extended system and camera inventory fields for the BlinkDRS Systems/Devices screen.
- Confirmed camera motion and system armed-setting endpoints.
- Fresh camera snapshot retrieval for thumbnail refresh.
- Expanded controller health reporting for internet, Blink Cloud, archive mount, disk capacity, catalog, and poll failures.
- Authoritative Blink refresh before status reporting.
- Offline Sync Module handling that also marks its cameras unavailable.
- Automatic topology reinitialization when a Sync Module returns after starting offline.
- Revisioned server-sent status events with keepalive messages for BlinkDRS.
- Explicit no-cache headers for controller API responses.

Hardware tests completed:

- Sync Module unplug and recovery without restarting BlinkDRS or the Pi service.
- Pi power loss, full reboot, controller restart, event-stream reconnection, and normal service recovery.
