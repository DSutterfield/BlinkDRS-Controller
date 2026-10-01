# Pi resource baseline — October 1, 2026

Raspberry Pi 4 Model B Rev 1.5, four CPU cores. Well House (Mini 2).
Sampled every two seconds: 20 seconds idle, startup, 30 seconds Live View,
150 seconds recording, 15 seconds Live View after recording, 20 seconds cooldown.
The measurement client drained JPEG frames and audio through Pi loopback. This
represents decoder/recorder workload and camera ingress, but excludes Windows
playback/decoding and Pi-to-Windows video egress. Background cloud polling and
other system traffic continued; phase differences are not a controlled estimate
of recording-only overhead. Sampling itself adds small overhead.

| Phase | Mean total CPU | Peak sampled total CPU | Peak temperature | Minimum available memory |
| --- | ---: | ---: | ---: | ---: |
| Idle | 9.0% | 25.3% | 44.3 C | 7184 MiB |
| Live View | 23.1% | 27.3% | 47.7 C | 7121 MiB |
| Live View + recording | 27.5% | 46.1% | 51.6 C | 7105 MiB |
| Live View after recording | 24.4% | 29.3% | 50.6 C | 7128 MiB |
| Cooldown after stop | 2.2% | 16.9% | 49.2 C | 7234 MiB |

Total CPU percentages are normalized across all four cores. During recording,
bridge FFmpeg averaged 78.1% of one core; recorder FFmpeg 1.2% of one core;
controller 27.4% of one core. No swap usage. Every throttling read was 0x0
(no current or recorded throttling/undervoltage flags). Cooldown samples showed
no FFmpeg processes, and controller use dropped to about 1% of one core.

Mean aggregate non-loopback received traffic was 73.6 KiB/s during Live View,
129.7 KiB/s while recording. Idle traffic averaged 168.3 KiB/s, illustrating
background traffic; these are whole-Pi measurements, not isolated camera rates.

Automatic cutoff occurred at 150.001 wall seconds and produced a 149.76-second
recording with audio and thumbnail. Live View continued for 15 seconds, then the
test stopped it. The recording is retained in Recorded Clips:
live-59fb48929fcb4c979d8eeb99f4131752-2026-10-01t21-36-08-00-00.mp4.

Result: no resource-pressure evidence in this single bounded Mini 2 run. Initial
resource characterization is complete; this is not a long-duration soak test,
leak assessment, cross-model benchmark or measurement of Windows network egress.
Optional next validation is a longer session or repeated sessions on Outdoor.
Raw samples retained in the October 1 Codex workspace work/pi-resource-results.json
and on the Pi in /tmp/blink-resource-test-20261001/result.json (temporary storage).
