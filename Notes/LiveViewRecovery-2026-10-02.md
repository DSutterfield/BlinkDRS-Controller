# Live View recovery — October 2, 2026

Established Live View sessions now monitor the Blink feed and decoded video producer. Producer completion cleans up the stream, FFmpeg, audio/frame tasks and recording relay. A cleanup lock serializes this against Stop and a subsequent Start. The status API publishes ended_session with session ID, camera, reason and timestamp before cleanup finishes. Normal Stop clears it; a new start uses a new session ID.

The actual Windows player schedules recovery after the frame loop returns, avoiding a self-await deadlock. It checks the ended session, runs normal Stop cleanup, replaces the stale image with the camera thumbnail when available, reads final recording status and enables Start Live View. Cancellation, Close and a different buffer prevent stale recovery work. Controller cleanup failures retain the existing Stop retry path. No automatic restart occurs.

Recording retains the independent 150-second cutoff. Stop cleanup distinguishes an already-ended incoming stream from manual stopping so saved recordings can report that they were shortened by Live View ending. Each reconnect starts a separate session; recordings are not joined across viewing gaps.

## Validation

- 20 Python tests cover startup responsiveness, normal/failed producer ending, relay and producer cleanup, concurrent Stop, stale-session protection and real Blink poll command-done cleanup. Passed on Windows and Pi.
- Isolated Pi FFmpeg/catalog integration checks pass: cutoff, manual/natural ending, MP4 audio/video, thumbnails, catalog/importer preservation and invalid media exclusion. Production catalog is not used by these isolated tests.
- An STA/Dispatcher harness opens the actual Release LiveViewWindow with Well House. Audio uses the actual decoder/output with per-instance mute; saved user settings remain unchanged.
- First recording: 150.001 seconds wall time, 149.76 seconds media, duration_limit, audio and thumbnail. Live View continues.
- Incoming stream ends without a test Stop near six minutes. Automatic player cleanup enables Start and reports an interruption. The active recording saves 82.43 seconds of media with audio/thumbnail and stream_ended; the UI identifies it as shortened.
- Same-window manual restart uses a different session ID and displays new frames. A subsequent 10.37-second recording saves with audio/thumbnail. Normal Stop and Close both release the session.
- Final status-publication refinement is covered by the concurrent-cleanup regression. A second real-player test injects failure only into its frame receiver endpoint. It reports connection interruption, cleans up, manually reconnects and remains stopped after explicit Stop.
- The first failure-test launch coincided with Controller startup and could not connect; it opened no session. It passed after the Controller health/device APIs were ready.
- Windows private memory in sampled initial-session playback/recording was about 139–168 MiB, with 17–25 threads. This is a short recovery test, not the previously planned 30-minute soak or an all-model resource baseline.

## Automatic reconnection

Deferred. The installed BlinkPy poll loop reports generic terminal command state, while transport closure can also appear as EOF. Neither proves a Blink-imposed session limit rather than a network/camera failure. No fixed six-minute timer or blind retries were added. Future work must establish reliable expiry evidence, bounded retries, user Stop/Close cancellation and visible gaps.

Initial Pi backup: backups/live-recovery-20261002-130244. Final status-refinement backup: backups/live-recovery-20261002-131142. The initial backup preserves the pre-recovery implementation. Raw Windows test events and harness source are retained in the October 2 Codex workspace work/recovery-validation directory.

## User acceptance — completed October 2

Dan reported that Pergola returned online and passed several Live View tests with no lockups or blank screens. The functional recovery test round is complete, including the automated Well House recording, natural ending, manual reconnect, interruption and Stop/Close checks above.

The requested recording-button refinement is implemented: Stop immediately restores "Start Recording" before cleanup begins. The Release build passed with no warnings or errors; a separate visual retest of this final label change was not recorded.

Optional automatic reconnection and extended resource/soak testing remain separate follow-ups.
