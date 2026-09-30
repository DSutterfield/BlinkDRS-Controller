# Outdoor Night Vision and IR Intensity

Added to Outdoor Camera Settings September 29, 2026:
- Night vision: Auto, On, Off. Outdoor native values are 2, 1, 0 respectively.
- IR intensity: Low, Medium, High, using native values 1, 4, 7.

The existing Windows settings window displays these controls and hides IR intensity when confirmed night vision is Off. Reopen Camera Settings to load the additions. No Windows binary update is required.

Changes validate fresh camera configuration, send only the selected field, and require readback confirmation. Unknown fields are omitted without blocking other settings. Same-value changes only read configuration. Writes are never automatically repeated.

All 47 camera-settings tests passed, covering mode conversion, isolated writes, unknown fields, invalid choices, readback failures, and existing camera settings.

Live verification on Driveway: Night Vision changed to Off and restored to Auto; IR Intensity changed to Low and restored to Medium. Fresh readback confirmed both changes and restoration, with other exposed settings unchanged. Physical IR appearance was not visually inspected.
