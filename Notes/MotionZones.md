# Motion Zones — September 30, 2026

Camera Settings provides a Motion Zones editor with a current photo, clickable exclusion cells, Update Photo, Reload Zones, Apply and Cancel. Dark cells are excluded. An amber Basic cell contains a mix of included/excluded Advanced cells. Changing the displayed grid preserves fine selections; editing a Basic cell changes its entire block. Only Apply saves. Changes are confirmed with fresh Blink readback. A stale revision refuses the save until reloaded. Offline cameras/Sync Modules refuse zone access. Privacy zones are preserved and are not edited.

## Supported formats

- Original Mini (owl, zone_version v1): verified 5×5 Basic grid. Upper-left maps to bit 0, upper-right to bit 4. Basic saves set the 0x40000000 mode flag and update only motion_regions. Its Advanced array remains untouched. Advanced editing is unavailable because its subdivisions and mode encoding could not be verified through oBa. A saved Advanced or unknown format is rejected instead of being reinterpreted.
- Mini 2 (hawk), Outdoor (sedona), Doorbell (tulip): verified v2 zones endpoints, camera-reported 8×8 Basic / 16×16 Advanced dimensions. Mini 2 upper-left Basic exclusion maps to indices 0, 1, 16, 17 of the 256-cell row-major mask. Both grid views use the same fine selections. The complete current configuration is preserved when changing the mask; analytics follows whether motion exclusions exist.

## Controller routes

GET/PUT /api/v1/devices/{device_id}/motion-zones?system_id=...

PUT requires revision, mode (basic/advanced) and cells (strict binary integers). These routes share the existing per-camera settings/light lock and deadline. Only the selected camera/system identity can be changed. Revision covers motion-zone configuration and privacy data to detect external edits.

## Validation

- 64 current controller tests passed, including invalid masks, strict types, stale revisions, rejected/unconfirmed commands, privacy preservation, legacy mode flags, and existing settings/light behavior.
- Windows Release build passed without warnings/errors.
- UI checks verified Basic block clicks, Advanced single-cell clicks, grid switching without selection loss, and layout rendering.
- Unchanged v2 saves/readbacks verified on Mini 2, Outdoor, Doorbell; Original Mini Basic save/readback and a single Outdoor fine-cell cloud save/readback verified. Original masks were restored.
- Dan reported Driveway offline during the investigation. Outdoor checks established stored cloud configuration only, not physical camera application. No further Driveway tests were performed after that report.
- Living Room and Well House calibration changes were restored to their original all-included masks; Mini 2 analytics returned to false. Doorbell's existing 40 excluded fine cells were retained.

The Pi's old test_device_settings.py has an outdated assertion that newer supported families must be unsupported. The current source-repository tests used for the 64-test run reflect the installed family support. That old test was not changed by this feature.

## Original Mini Advanced zones — investigation closed October 2, 2026

Blink's official Activity Zones documentation lists Advanced support for Mini 2, Outdoor 4, Wired Floodlight, Indoor/Outdoor (3rd Gen) and XT2, excluding Original Mini. Dan also confirms that oBa offers no Advanced option for Original Mini. Classify Original Mini Advanced zones as unsupported by Blink and close the implementation investigation. Original Mini retains verified Basic editing; Mini 2 retains Advanced support. Legacy advanced_motion_regions fields alone do not establish a supported feature. Earlier unverified-format wording above describes the historical investigation.

Source: https://support.blinkforhome.com/541917
