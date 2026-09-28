# Camera settings adapters — 2026-09-28

The controller owns camera-type support and validation. `device_settings.py` registers separate profiles by Blink product type. `owl` (original Mini), `sedona` (Outdoor), and `tulip` (Video Doorbell) have separate adapters. `hawk` (Mini 2) and unknown types remain unsupported; inherited library methods alone do not imply support.

The Devices response includes `camera_settings.available` and `camera_settings.profile`. Supported device cards show Camera Settings. The Windows dialog renders choice-setting descriptors returned by the controller, keeping camera-family behavior out of UI event handlers. Additional setting kinds can add renderers as needed.

GET `/api/v1/devices/{device_id}/settings?system_id={system_id}` reads settings on demand. PUT `/api/v1/devices/{device_id}/settings/{key}?system_id={system_id}` applies an explicit value and reads it back. Original Mini currently exposes `night_vision` with `auto`, `on`, and `off` choices. Outdoor exposes `motion_sensitivity` with string choices `1` through `9`; the adapter sends an integer to Blink. The request model restricts the combined choice vocabulary, and each adapter validates its own keys and values before contacting Blink.

Discovery does not contact camera configuration endpoints. Operations serialize per device, time out after 25 seconds, validate system identity and online status, and prevent network recovery while a settings operation is active. A write is sent once; up to three fresh reads confirm the value. Missing or unknown values are errors, never defaulted to Off. The UI disables editing during requests and requires Refresh after an uncertain failure.

To extend: add a camera-family adapter, validate its supported keys and value ranges, register its exact product type, add mocked read/write/unsupported/failed-confirmation tests, and verify actual model responses before enabling its capability. Preserve the original Mini adapter when adding a different family.

Original Mini readback prefers `illuminator_enable_v2` when its key is present. Observed legacy `illuminator_enable` reports auto for both Auto and Off. Only fall back to legacy strings/numbers when the v2 key is absent; unknown or null v2 values remain errors. Controller errors contain the reason; the Windows dialog supplies recovery instructions once.

Validation: 13 controller settings tests passed, including all three current modes, observed legacy-auto/v2-off responses, legacy-only values, unknown-v2 rejection, serialization, validation, and failed confirmation. Five existing fault/API lifecycle tests, eight WPF harness checks, a clean Windows Release build, and rendered layout inspection also passed. Automated tests used simulated camera writes.

Live validation: a controlled Living Room Auto/Off comparison confirmed the field mapping and restored Living Room to Off. Corrected reads reported Bed Room On and Kitchen/Living Room/Piano Room Off. A Living Room Off request returned HTTP 200 with confirmed Off. The user confirmed changes in the official Blink app and verified the Off correction in BlinkDRS. Temporary diagnostics were removed.

Deployment: installed on the Pi and Windows on 2026-09-28; service health and deployed bytes verified. Initial Pi backup: /home/dan/BlinkDRS-Controller/backups/night-vision-20260928-094642. Off correction backup: /home/dan/BlinkDRS-Controller/backups/night-vision-off-fix-20260928-100758. The installed Windows DLL matches the tested build; the final readback correction required only a controller update.

## Outdoor motion sensitivity — 2026-09-28

A separate `sedona` adapter reads `/network/{network}/camera/{camera}/config` and posts only `motion_sensitivity` to the corresponding `/update` route. BlinkPy calls this shared route `catalina`; live Driveway and Back Gate reads verified that it also returns sedona configuration, with `camera[0].motion_sensitivity` equal to 9. Validate the returned camera ID and the exact integer/string range; never substitute a default when data is missing or malformed.

The existing Windows choice renderer displays 1–9 with low/high labels. Original Mini retains night vision only. No Windows executable change is required. A write must return an accepted config_set command and a matching fresh read before success is reported. Retries are reads only; uncertain results require Refresh.

Validation: all 20 controller settings tests passed, including the original Mini regressions, Outdoor payload isolation, range/identity checks, command rejection, delayed readback and mismatch handling. Eight simulated-controller WPF checks passed with the existing Windows build, and the rendered layout was inspected. Live reads passed for all seven Outdoor cameras and all four original Minis. Catio and Picnic Table/Play Area returned 6, Back Door 5, and Pergola, Water Bowl, Back Gate and Driveway 9. Reapplying Back Gate's existing value 9 returned HTTP 200 with confirmed 9, followed by an unchanged fresh read. The user subsequently confirmed that Outdoor motion sensitivity changes are synchronized with the official Blink app.

References: [Blink sensitivity scale](https://support.blinkforhome.com/camera-settings-cat/adjusting-sensitivity-for-your-camera), [observed configuration update protocol](https://github.com/adrian-dobre/BlinkWebService/blob/master/BlinkForHomeApiDocumentation.md). The older unofficial protocol notes were checked against current live read responses and the installed BlinkPy implementation.

Deployment: controller healthy and installed module bytes verified. Backup: /home/dan/BlinkDRS-Controller/backups/motion-sensitivity-20260928-103928. No camera sensitivity values were changed during automated or live validation.

## Outdoor clip controls — 2026-09-28

The sedona adapter now exposes Motion clip length (seconds), Retrigger time (seconds), and End clip early if motion stops, alongside motion sensitivity. Clip length maps to `video_length` (5–60 seconds, further limited by the camera-reported `clip_max_length`); retrigger time maps to `alert_interval` (10–60 seconds). End early maps On/Off to boolean `early_termination` and is exposed only when `early_termination_supported` is true. Original Mini and other camera families are unchanged.

Each Apply sends only its own field. Clip length and end-early writes first validate current model limits/support. All four current settings are read in one configuration request and returned after a confirmed change, keeping the existing Windows dialog's rows visible. Confirmation matches both the requested key and value; another setting with the same value cannot produce false success. Wire values remain strings for the existing choice renderer, while Blink receives integer durations and boolean end-early values.

Validation: 27 controller settings tests and 11 isolated WPF checks passed. Coverage includes duration boundaries, invalid/unknown values, camera-specific limits, unsupported end-early controls, payload isolation, full-list preservation, confirmation failures and existing Mini/sensitivity regressions. The four-row Windows layout was rendered and inspected. Live read-only discovery verified Driveway (60-second clip, 60-second retrigger, end early Off) and Back Gate (30, 10, On). No Windows executable update is required.

Reference: [Blink device settings and clip-control ranges](https://support.blinkforhome.com/en_GB/new-device-layout-settings).

Deployment verified: controller healthy, module bytes match tested source, backup /home/dan/BlinkDRS-Controller/backups/clip-controls-20260928-105242. All seven Outdoor cameras returned the new controls; all four Mini night-vision reads succeeded. Reapplying Back Gate's existing clip length 30, retrigger time 10, and end early On each returned HTTP 200 with all four settings preserved. Final fresh read was unchanged. The user subsequently reported that the Outdoor clip controls appear to work.

## Doorbell read-only discovery — 2026-09-28

The user requested inspection of the Doorbell's settings after reporting that Outdoor clip controls work. Front Door identifies as `tulip`. A GET through the controller's active Blink session to `/api/v1/accounts/{account}/networks/{network}/doorbells/{camera}/config` succeeded. The response is a flat object, unlike the Outdoor camera-list envelope.

Observed fields: `clip_length_max=30`, `clip_length=10`, `retrigger_time=10`, `motion_sensitivity=9`, `early_termination=true`, and `early_termination_supported=true`. The Doorbell uses clip_length/retrigger_time instead of Outdoor video_length/alert_interval. It also reports `early_notification=true` with `early_notification_compatible=false`; do not infer this notification option is editable.

During this initial discovery, no Doorbell settings were changed or enabled in the UI. These findings established the separate route, field mappings, and 30-second clip limit used by the tulip adapter described below. The temporary localhost-only diagnostic was removed, original module bytes restored, and controller health confirmed.

## Doorbell controls and IR — 2026-09-28

The separate tulip adapter exposes motion sensitivity (1–9), clip length (5–30 seconds, bounded by clip_length_max), retrigger time (10–60 seconds), supported end-early control, night vision (Auto/On/Off), and IR intensity (Low/Medium/High). It uses the Doorbell account/network config endpoint and the Doorbell's flat response. Numeric values are validated before converting from choice strings; missing or unknown values fail instead of becoming defaults. Early notification remains outside the supported controls.

Live comparison with the user's oBa selections established illuminator_intensity Low=1, Medium=4, High=7. Night vision reads illuminator_enable_v2 and writes illuminator_enable. Posting to the readback field was ignored in live testing; the write field is deliberately separate. A direct same-session POST restoring Low returned HTTP 200 with a config_set command; fresh read confirmed 1. Writes require this observed command response and matching fresh readback. Each request posts only the requested field and returns all supported controls after confirmation. No automatic write retries are added.

Validation: all 33 controller tests passed, including Doorbell route/limits, all six one-field payloads, exact intensity mapping, unknown IR rejection, unsupported settings, failed/busy commands, mismatched confirmation, and existing Mini/Outdoor regressions. Thirteen WPF checks passed using the existing Windows executable; the six-control layout was rendered and inspected. No Windows executable update is required.

Doorbell no-op handling: reapplying the current sensitivity returned HTTP 200 with id/network_id/state, omitting the config_set command marker. The adapter now confirms an already-current value from its fresh pre-write read and avoids sending a redundant command. A dedicated regression verifies one read and no write. Actual changes still require command acceptance and matching readback.

Doorbell command timing: rapid follow-up configuration commands intermittently failed during live testing. BlinkPy's generic command-completion helper was tested but did not reliably confirm these Doorbell commands, including a case where configuration had already changed. That helper is not used in the final adapter. Success means a matching fresh Doorbell configuration read, not proof of physical command completion. The UI retains its existing failure/Refresh behavior and the adapter never retries a write automatically.

Final live verification: with ten seconds between commands, sensitivity 9→8→9, clip length 10→15→10, retrigger 10→20→10, end early On→Off→On, and intensity Low→Medium→Low→High→Low all returned HTTP 200 and matching complete settings. After correcting the night-vision write field, Auto→Off→Auto and Auto→On→Auto also returned HTTP 200 with matching v2 readback. All six original values were restored: sensitivity 9, clip length 10, retrigger 10, end early On, night vision Auto, intensity Low. Rapid successive writes can still be rejected by Blink; the app reports failure and requires Refresh instead of assuming success.

Final deployment: controller healthy and module bytes verified; backup /home/dan/BlinkDRS-Controller/backups/doorbell-settings-20260928-114357. All temporary diagnostics were removed. Existing Windows executable supports the six descriptors. User acceptance: the user confirmed Doorbell settings are synchronized with the official Blink app. They also noted that IR intensity is hidden when Night Vision is Off; the Windows shared renderer now follows that confirmed-mode visibility rule without altering intensity.

IR visibility follow-up: the Windows shared renderer hides IR intensity only when confirmed Night Vision is Off and restores it for On or Auto without changing its stored value. Clean Release build and 16 simulated-controller WPF checks passed; the installed DLL matched the tested build.
