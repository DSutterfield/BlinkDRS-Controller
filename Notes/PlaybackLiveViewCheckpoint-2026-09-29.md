# Playback and Live View checkpoint — September 28–29, 2026

## September 29: completed changes

- Recorded playback now distinguishes downloading/preparing a clip from opening it in the Windows media engine. Media opening has a 15-second deadline; failed or timed-out media is released so Play can retry without closing the window. Asynchronous, rotating stage diagnostics aid future investigation. Subsequent user testing encountered no hangs; the exact original incident was not captured.
- The Pi keeps bulk archive work and download pacing from blocking the API event loop. Worker cancellation retains archive locks until disk work finishes. Startup diagnostics now include request handling and recording-lock waits.
- Live View shutdown closes transport promptly, wakes sleeping feed tasks, and awaits Blink command completion and decoder cleanup. Windows coalesces Stop requests, protects Close during cleanup, and releases confirmed sessions after startup errors.
- Windows displays a fresh, session-checked still image while synchronized playback buffers. The Pi decoder's input analysis budget is reduced from one second to half a second. The clearest repeated measured saving was about half a second on the Mini; other cameras varied. The user reported a substantial improvement in perceived startup.
- Replace the large Live View wait animation with a 48-pixel conventional circular spinner, a thin ring, one rotation every 1.2 seconds, and a single rotation center.
- Audio processing, playback reserves, Living Room audio calibration, and recording output configuration remain intact.

## September 28: previously committed work included in this push

Both repositories had two local commits ahead of their tracking branches before this checkpoint.

Windows branch `main`:

- `b5259d1` — retain loaded clips and selection when refresh fails, serialize refreshes, improve catalog/review request deadlines, and add verified original Mini night-vision controls.
- `f26d23c` — hide IR intensity when confirmed Night Vision is Off while preserving its stored value.

Controller branch `pi-controller-api-v1`:

- `5047ce8` — separate playback preparation from polling, maintain archive/playback lock ordering, and add serialized camera settings with verified Mini night-vision readback.
- `2419286` — add Outdoor and Doorbell motion, clip, and IR settings.

These existing commits retain their original history and are pushed together with the new September 29 checkpoint commits.

## Validation and limits

The final Windows spinner build completed with zero warnings/errors and the installed DLL hash matched it. Earlier tests of the included behavioral changes passed 15 recorded-playback WPF checks, the loading-control regression harness, and 12 Live View WPF checks. Controller responsiveness, archive/playback/retention, six shutdown checks, and seven startup fail-fast checks passed during implementation. Live camera checks verified image/audio delivery and cleanup. See the individual implementation notes for measurements and test scope.

Two additional decoder options were not retained because gains were inconsistent. Background connection preparation passed isolated tests but failed production live verification; it was rolled back. The prior controller hashes were restored, and a Mini session subsequently passed picture, audio, stop, and health checks. Neither experimental decoder options nor background preparation is included in the committed implementation. The investigation notes preserve these results.

An unrelated pre-existing Windows `ROADMAP.md` edit is deliberately left local and unchanged; it is not part of this checkpoint.
