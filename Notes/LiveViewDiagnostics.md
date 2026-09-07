# Live View startup measurements

The bridge still produces five JPEG frames per second. Startup analysis,
Windows pacing, and the initial audio reserve have been tuned using these
measurements; see the 2026-09-07 checkpoint below.

## Capture a baseline

Open one camera in BlinkDRS, leave Live View running for about 20 seconds,
then stop it. Repeat twice with the same camera and note the camera and order.
The first session after a controller restart may need to establish the bridge's
Blink connection; subsequent sessions reuse that connection. They do not prove
the camera itself was cold or warm. Compare another camera afterward if useful.

Windows writes `%LOCALAPPDATA%\BlinkDRS\logs\liveview-diagnostics.jsonl`.
The file rotates at 1 MiB, keeping one previous file. Each attempt gets a local
session ID; successful starts include the controller's session ID and timings.
The controller logs `LiveView startup` records through its normal log handlers.
Diagnostics are also returned in the existing start response and status endpoint.
No image, audio, credential, or Blink stream URL is included in these records.

## Read the measurements

All stage values are cumulative milliseconds from a monotonic clock. Subtract
two stage values **within the same machine** to get that stage's duration.
Do not subtract a Windows value from a Pi value: their clocks and origins differ.

Pi origin: entry into the Live View start API handler (or the direct bridge call).

| Stage | Meaning |
| --- | --- |
| previous_stream_stopped | Previous stream cleanup and request queueing finished |
| blink_connection_ready | Persistent Blink connection available |
| blink_command_requested | About to request Live View from Blink |
| blink_command_accepted | Blink returned its stream configuration |
| local_listener_ready | BlinkPy local TCP listener started |
| ffmpeg_started | FFmpeg subprocess created |
| first_transport_payload | First valid MPEG-TS payload received from Blink |
| first_decoded_jpeg | First complete JPEG emitted by FFmpeg |
| start_response_ready | Controller ready to return startup success |

Windows origin: start preparation after loading the placeholder image.

| Stage | Meaning |
| --- | --- |
| start_requested | Windows preparing the start request |
| start_response_received | Successful controller response received |
| audio_start_requested | Windows starts audio setup |
| audio_ready | Audio player reports startup ready |
| first_frame_response_received | First successful JPEG response received |
| first_image_assigned | Decoded bitmap assigned to the WPF Image control |

`first_image_assigned` measures application presentation readiness, not the
physical display's scan-out time. The stopped record includes image assignment
count and mean interval. Repeated JPEGs count as assignments: this is **not**
source-camera FPS, unique-frame FPS, dropped-frame count, or motion latency.
Missing stages in failed attempts mean that stage was not reached.

## Next decisions

Use the baseline to quantify Blink negotiation, decoder startup, and the Windows
audio wait. Then test allowing video and audio startup to proceed concurrently.
Measure incoming stream cadence and unique decoded frames separately before
choosing a smoother transport or changing FFmpeg settings. Compare each change
against the baseline for startup, smoothness, audio, CPU use, and clean shutdown.

## 2026-09-07 playback checkpoint

The Pi bounds FFmpeg input analysis to 1,000,000 microseconds / 1 MiB. The
single-video-decoder-thread experiment was inconclusive and was reverted.
The custom TCP-safe receiver now handles APPLICATION_DATA_AFTER_CLOSE_NOTIFY
like BlinkPy while propagating other SSL errors and cancellation. New milestones
record stream_connection_requested, stream_auth_sent, and first_protocol_header.
The auth marker means the authentication header was sent, not server acceptance.
Startup timeout messages distinguish connection setup, no valid media, and
no decoded first frame. Blink command-done failures remain unresolved.

Delivery summaries measure camera_payload, transport_forwarded, decoded_video,
and decoded_audio gaps. Each stage stores at most 128 gaps >=500 ms, retaining
total gap counts and maximum intervals. Use the playback_stopping log for
playback analysis: later status snapshots can include teardown activity, and
their current_age_ms continues to increase after stop. The start response is
only a startup snapshot and does not describe the rest of playback.

Windows adds liveaudio-diagnostics.jsonl in the same log directory, with the
same Windows session ID as liveview-diagnostics.jsonl. Audio times are relative
to audio task creation, not Start or the first picture. Its UTC origin is
audio_started_utc. Numeric per-second summaries are capped at 180 buckets and
the file rotates at 1 MiB. No audio samples are saved.

Decoded/output signal markers use a peak threshold of 328 on signed 16-bit PCM.
Output means samples requested by WaveOut, not proof of audible speaker output.
Peaks are before device gain. MinOutputVolume records application gain/mute,
not OS/device mute. LowBufferReads is a pre-read observation and can race with
an incoming buffer append; it is not an exact underrun counter. Unsupported
formats are explicitly flagged. Log records are written when audio stops.

The initial reserve is now 1.5 seconds (buffer capacity remains 2 seconds).
Windows still waits for audio readiness before starting its image loop, so this
reserve can delay the first picture. Latest verified user run: 6.798-second
first picture, zero low-buffer observations, no audio error, maximum 1.742-second
inter-decode gap; user reported smooth playback. Confirm longer playback,
another camera, and audio/video alignment before declaring the work complete.
