# Camera settings adapters — 2026-09-28

The controller owns camera-type support and validation. `device_settings.py` registers separate profiles by Blink product type. Only `owl` (original Mini) is enabled initially. `hawk` (Mini 2), `sedona` (Outdoor), `tulip` (Doorbell), and unknown types are unsupported until their own adapters are implemented and tested; inherited library methods alone do not imply support.

The Devices response includes `camera_settings.available` and `camera_settings.profile`. Supported device cards show Camera Settings. The Windows dialog renders choice-setting descriptors returned by the controller, keeping camera-family behavior out of UI event handlers. Additional setting kinds can add renderers as needed.

GET `/api/v1/devices/{device_id}/settings?system_id={system_id}` reads settings on demand. PUT `/api/v1/devices/{device_id}/settings/{key}?system_id={system_id}` applies an explicit value and reads it back. Original Mini currently exposes `night_vision` with `auto`, `on`, and `off` choices. The current request model validates those values; extend validation alongside each future setting's adapter.

Discovery does not contact camera configuration endpoints. Operations serialize per device, time out after 25 seconds, validate system identity and online status, and prevent network recovery while a settings operation is active. A write is sent once; up to three fresh reads confirm the value. Missing or unknown values are errors, never defaulted to Off. The UI disables editing during requests and requires Refresh after an uncertain failure.

To extend: add a camera-family adapter, validate its supported keys and value ranges, register its exact product type, add mocked read/write/unsupported/failed-confirmation tests, and verify actual model responses before enabling its capability. Preserve the original Mini adapter when adding a different family.

Original Mini readback prefers `illuminator_enable_v2` when its key is present. Observed legacy `illuminator_enable` reports auto for both Auto and Off. Only fall back to legacy strings/numbers when the v2 key is absent; unknown or null v2 values remain errors. Controller errors contain the reason; the Windows dialog supplies recovery instructions once.

Validation: 13 controller settings tests passed, including all three current modes, observed legacy-auto/v2-off responses, legacy-only values, unknown-v2 rejection, serialization, validation, and failed confirmation. Five existing fault/API lifecycle tests, eight WPF harness checks, a clean Windows Release build, and rendered layout inspection also passed. Automated tests used simulated camera writes.

Live validation: a controlled Living Room Auto/Off comparison confirmed the field mapping and restored Living Room to Off. Corrected reads reported Bed Room On and Kitchen/Living Room/Piano Room Off. A Living Room Off request returned HTTP 200 with confirmed Off. The user confirmed changes in the official Blink app and verified the Off correction in BlinkDRS. Temporary diagnostics were removed.

Deployment: installed on the Pi and Windows on 2026-09-28; service health and deployed bytes verified. Initial Pi backup: /home/dan/BlinkDRS-Controller/backups/night-vision-20260928-094642. Off correction backup: /home/dan/BlinkDRS-Controller/backups/night-vision-off-fix-20260928-100758. The installed Windows DLL matches the tested build; the final readback correction required only a controller update.
