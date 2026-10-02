# Smart Detection

Smart Detection belongs to Motion Settings and exposes Person Detected, Vehicle Detected, and Other Motion as Off/On choices. Original Mini is excluded. Mini 2, Outdoor and Doorbell require all three boolean detection_modes fields before controls are offered.

Blink fields: person_detection, vehicle_detection, and motion_detection. Mini 2 uses /api/v1/accounts/{account}/networks/{network}/owls/{camera}/config; Doorbell uses the corresponding doorbells route. Outdoor uses /api/v2/accounts/{account}/networks/{network}/cameras/{camera}/config because the legacy configuration response omits detection_modes.

Writes send only detection_modes, preserving other reported modes, then verify fresh configuration. Both full config_set and verified compact id/network_id/state=done acknowledgements are handled. Unknown responses, failed commands and confirmation mismatches do not report success or retry writes. No BlinkDRS recording, motion tracking, catalog or playback logic changes.

Validation on October 2, 2026: 55 camera settings regression tests pass. Release Windows build has no warnings or errors. Blink accepted unchanged-value POSTs for one Mini 2, Outdoor and Doorbell; values remained unchanged. Actual On/Off changes were covered by mocked regressions, not live toggles. Deployed API exposes all three choices for those families and none for Original Mini. Controller is healthy with 13 cameras. Pi backup: backups/smart-detection-20261002-120134/device_settings.py.

User acceptance on October 2: Dan confirmed that Smart Detection settings work and completed the change/apply/revert/refresh cycle after the Off/On controls were changed to compact checkboxes. The feature and layout validation are complete. This user confirmation supplements the scripted validation above.
