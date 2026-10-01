# Actual Windows player soak attempt — October 1, 2026

Purpose: automate normal single-user Windows/Pi validation without user reaction-time dependence. A temporary STA/Dispatcher harness references the actual Release BlinkDRS assembly, opens LiveViewWindow, runs its Loaded/startup path, triggers its real Record Clip button event, monitors displayed frame count and process resources, and runs its normal Stop/Close cleanup. Audio is decoded/output through the actual player with per-instance mute; persisted settings are unchanged. This tests the actual player component, not the full MainWindow/list-navigation workflow. Build passed without warnings/errors.

Short verification passed on Well House: displayed frames, a manually stopped 6.72-second recording with audio/thumbnail, and confirmed cleanup.

Planned full schedule: 15 minutes each on Well House and Pergola, three 150-second recordings per camera, then cooldown. The full run stopped early on Well House after about six minutes because the second recording ended with stop_reason=stream_ended. Pergola and the intended full cooldown were not run. This is an incomplete/failing sustained-session test, not a completed 30-minute validation.

- First recording: automatic limit, 150.001 wall seconds, 149.76 seconds of media, audio and thumbnail.
- Second recording: stream ended, 35.785 wall seconds, 26.88 seconds of media, audio and thumbnail preserved.
- Incoming payload ended around 360.676 seconds after session request. Payload cadence before ending had no measured gaps over 0.302 seconds. FFmpeg subsequently finished with about 359.4 seconds of media. The old stream was stopped/closed and no active Live View remained.
- Pi (182 active samples): 25.6% mean total CPU, 47.3% peak sampled CPU, 52.1 C peak temperature, minimum available memory 7087.9 MiB, throttling 0x0 throughout.
- Windows: private memory ranged 131.1–170.9 MiB; mean process CPU 17.4% of one core. Actual Pi-to-Windows traffic was measured, with one observed outbound sample about 654 KiB/s. Whole-interface traffic includes other activity.
- No resource exhaustion established. The observed incoming stream ending is the immediate limit; its exact upstream cause/policy remains unconfirmed. Blink documents finite standard Live View and separate Extended Live View, but the installed BlinkPy request does not establish a six-minute guarantee.

Next work: investigate the incoming session end, verify clear player terminal-state reporting and recording finalization, and choose manual restart versus an explicitly designed reconnect policy before any multiuser expansion. A shared session/ownership/concurrency policy remains untested. Repeat a full soak with a schedule compatible with verified session behavior; do not silently call deliberate session restarts continuous viewing.

Sources: https://support.blinkforhome.com/en_GB/how-to-access-live-view and https://support.blinkforhome.com/en_GB/using-mini/extended-live-view. Raw events/measurements and the reusable harness remain in the October 1 Codex workspace under work/windows-soak and work/soak-harness.
