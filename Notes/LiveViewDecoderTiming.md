# Live View decoder timing — September 29, 2026

The Pi decoder's FFmpeg input analysis budget was reduced from 1,000,000 to 500,000 microseconds. This is an analysis budget, not a guaranteed fixed delay. The value is now named FFMPEG_ANALYZE_DURATION_US in liveview_bridge.py.

## Isolated camera comparisons

Tests used a separate bridge with the same code as the controller, changing only the analysis budget. They did not record clips. Each successful session verified a JPEG, fresh audio, and continuing decoded video/audio for six seconds. The outdoor camera and Mini were also tested with the setting order reversed.

The measurement below starts when the first transport payload arrives and ends at the first decoded JPEG. Whole-session startup times are not a fair comparison here: the first session in each test also initializes its Blink connection.

| Camera / comparison | Previous 1-second budget | New half-second budget | Observed reduction |
|---|---:|---:|---:|
| Pergola, initial | 1.386 s | 1.337 s | 0.050 s |
| Pergola, reversed order | 1.388 s | 1.010 s | 0.378 s |
| Living Room Mini, initial | 1.479 s | 0.980 s | 0.499 s |
| Living Room Mini, reversed order | 1.561 s | 1.003 s | 0.558 s |
| Front Door | 2.180 s | 2.043 s | 0.137 s |

Ten sessions completed successfully with both video and audio. One additional outdoor attempt timed out because no valid media payload arrived; it yielded no decoder comparison. Its retry passed. These are small samples, not guaranteed savings. The Mini showed the clearest repeatable benefit. Doorbell delivery gaps of about 0.9 seconds occurred under both settings.

Six existing shutdown checks and seven startup fail-fast checks passed against the candidate, including cancellation, missing frames, decoder exit, and cleanup. Probe byte limit, playback buffers, audio processing, audio/video calibration, frame rate, and recording outputs were unchanged.

The change is Pi-side; no Windows rebuild is required. The controller deployment checks source hashes, refuses an active Live View/recording session, saves the previous source, and checks service health with rollback on restart failure.

Installed backup: /home/dan/BlinkDRS-Controller/backups/live-decoder-20260929-124057. Controller health and deployed file hash verified after restart.

Post-install verification through the production HTTP API passed on Living Room Camera, Front Door, and Pergola: session-matched JPEG and audio, confirmed stop after each session, and final health OK. HTTP startup was 12.131 seconds for the first Mini session after restart (10.084 seconds spent establishing the Blink connection), then 4.517 seconds for Front Door and 3.433 seconds for Pergola. These are controller response times, not Windows moving-playback measurements. The post-install first-payload-to-JPEG times were 0.991, 1.313, and 0.999 seconds respectively. Audio/video alignment still relies on normal viewing; this test verifies data delivery, not perceived synchronization.
