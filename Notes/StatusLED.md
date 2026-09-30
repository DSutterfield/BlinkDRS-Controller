# Camera Status LED

Added September 29, 2026 to Camera Settings.

| Camera | Available modes |
| --- | --- |
| Original Mini and Mini 2 | on, off, recording |
| Outdoor | off, recording |
| Doorbell | off, recording (restricted to the modes reported by its configuration) |

`on` means Always On. `off` means Always Off. `recording` lights the status LED during recording or Live View. This control changes `led_state` only; the Doorbell's independent `button_led_mode` is unchanged.

The existing Windows settings renderer displays these controls from the controller's descriptors. Refresh Systems & Devices to pick up the new Mini 2 settings capability, and reopen Camera Settings to load current values.

Each change validates current camera support and sends one field, then reads fresh configuration up to three times. It never repeats a write automatically. An already-current value requires only a fresh read. Unknown or missing LED data does not block other settings; Mini 2 exposes only its validated LED control. Blink can reject rapid consecutive commands while a preceding command finishes; Refresh reads the actual current state before another attempt.

Validation: 38 camera-settings regression tests passed, including per-family choices, single-field writes, rejected values, failed commands, readback mismatches, and preservation of other controls.

Reference: [Blink Status LED options](https://support.blinkforhome.com/en_GB/can-i-disable-the-blue-recording-led-on-the-camera).

Live verification: changed and read back one camera of each family (Living Room Camera, Well House, Driveway, Front Door), then restored and confirmed each original mode. Physical LED appearance was not visually inspected.
