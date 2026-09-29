# Live View connection preparation — September 29, 2026

Outcome: the candidate passed isolated tests but failed production live verification and was rolled back. The previously accepted half-second decoder analysis setting is retained.

## Candidate behavior — rolled back

The controller prepares Live View's separate persistent Blink account connection once in the background when the API starts. This performs the connection/discovery work without starting a camera stream. The API startup handler schedules the work and returns immediately.

A user click during preparation waits for the same fully initialized connection through an asynchronous lock. The bridge publishes the connection only after setup succeeds. Preparation has a 15-second deadline; a failed or timed-out preparation closes its partial HTTP session and lets the next user request retry normally. Shutdown cancels and awaits any preparation before closing the bridge. Repeated scheduling does not create repeated logins.

This removes connection setup from the first Live View's critical path when preparation has finished. It does not reduce Blink's camera-start delay or speed already-warm sessions. A click immediately after restart can still wait for setup. No periodic warm-up or automatic camera streaming was added.

## Verification

Eight new tests passed: connection reuse without camera streaming, click during preparation, failure/retry, timeout/retry, shutdown during preparation, repeated scheduling, no preparation after shutdown, and cancellation during foreground setup. Six existing shutdown checks and seven startup fail-fast checks also passed.

In the isolated real-account test, scheduling returned immediately; preparation finished in 5.064 seconds with the bridge inactive. The subsequent Mini session reached connection-ready in 0.859 milliseconds and first-image/start-response-ready in 2.745 seconds. JPEG and fresh audio were verified, with 31 frames and 99 decoded audio chunks over the short test. The stream stopped cleanly. This is an individual timing sample, not a guaranteed startup time.

## Decoder experiments not installed

Two additional input options were tested against the installed 500,000-microsecond analysis budget: slice-only video decoder threading and a two-frame frame-rate probe. FFmpeg documents that frame threading can add decoding latency, and that fpsprobesize sets the number of frames used to probe frame rate: [codec documentation](https://ffmpeg.org/ffmpeg-codecs.html), [format documentation](https://ffmpeg.org/ffmpeg-formats.html).

| Camera / run | Baseline payload-to-JPEG | Slice-only | Two-frame probe |
|---|---:|---:|---:|
| Mini | 0.934 s | 0.962 s | 0.999 s |
| Pergola initial | 0.999 s | 0.777 s | 1.000 s |
| Pergola reversed order | 0.999 s | 3.002 s | Not repeated |
| Doorbell | 1.366 s | Stream ended before startup completed | Not tested |

The Mini showed no benefit; the outdoor threading result was inconsistent. The Doorbell failure was a Blink stream ending, not proof that the decoder option caused it. Neither option was retained. Nine experimental decoder sessions produced image/audio successfully; one failed before startup completed. No camera media was saved by these experiments.

The existing analysis budget, playback reserves, audio processing, audio/video calibration, and recording output configuration remain intact. The candidate only changed the Pi; the Windows application was not modified.

## Deployment and rollback

Installed with verified file hashes and healthy service restart. Previous-source backup: /home/dan/BlinkDRS-Controller/backups/live-prepare-20260929-125814. The production service log confirmed background connection preparation completed in 5.149 seconds after startup.

The first production outdoor verification failed after 30 seconds with no first transport payload. Connection-ready was reached at 3.055 ms, authentication at 319.045 ms, and the first protocol header at 481.927 ms. Cleanup completed at 30.329 seconds; the API remained healthy and recording was idle. This confirms the connection preparation was reused, but does not establish the cause of the missing camera media. Similar no-media failures occurred in the preceding decoder experiments before this change.

A second production verification, on the Mini, failed before blink_command_accepted (connection-ready at 2.026 ms; failed API response at 10.139 seconds). The candidate was therefore rolled back; all previous production file hashes and API health were verified. The local repository source was never replaced with the candidate.

After rollback, a Mini session passed JPEG, audio, stop, and health checks. Its first post-restart connection took 5.107 seconds and total HTTP startup took 7.673 seconds. These samples do not prove preparation caused the failures, but they do not support retaining it in production. Candidate code and eight tests remain in the workspace for further investigation. No additional optimization from this round remains installed; the earlier accepted changes remain in place.
