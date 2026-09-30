# Mini 2 lighting

Camera Settings includes:
- Timeout after manual activation: 30, 60, 180, 300, or 600 seconds, limited to the options the camera reports.
- Motion activation: On/Off, using the built-in motion sensor to activate the light.

The Live View header has a Mini 2-only Light toggle showing the last confirmed on/off state. Changing it briefly stops Live View, changes and confirms the light, then resumes the stream. Blink rejected light commands during active streams (busy code 307), so the user selected this stop/start behavior. We have not established how the official app handles this.

The toggle is disabled during clip recording/finalization and during a request. Recording status is checked again before stopping. A failed stop prevents the light command; a rejected light change still attempts to resume the stream. Closing waits for the operation and suppresses a restart that has not begun. The light is not automatically turned off on close; Blink's manual timeout still applies.

Light state refreshes every 10 seconds during Live View to follow automatic shutoff or changes from another app. Unknown state displays Check light. The UI never marks a change successful before confirmation, and light commands are not automatically retried. Neither the manual toggle nor restarting Live View changes the motion-light preference or manual timeout.

Controller GET/PUT /api/v1/devices/{id}/light?system_id=... validates camera/system identity, Mini 2 type, spotlight support, and strict boolean enabled requests. Settings and light commands share a camera lock and bounded request deadline.

Validation: 53 controller tests passed. Windows Release build passed without warnings or errors. Simulated UI checks covered Mini 2-only visibility, confirmed state display, rejected commands, operation order, failed stop, light-command failure, closing, and failed restart. Live stop/change/restart passed for both On (3.38 seconds) and Off (3.77 seconds), measured at the controller; laptop playback buffering is additional. Test streams were stopped and the original light Off state restored. Both lighting settings were changed and restored earlier. Current settings remain timeout 30 seconds and motion activation Off.
