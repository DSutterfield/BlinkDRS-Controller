# Mini and Mini 2 Camera Settings

Both models now expose these supported settings in BlinkDRS:

| Setting | Choices |
| --- | --- |
| Night vision | Auto, On, Off |
| Motion sensitivity | 1–9 |
| Motion clip length | 5–30 seconds, capped by the camera's reported maximum |
| Retrigger time | 10–60 seconds |
| End clip early if motion stops | On, Off, when the camera reports support |
| IR intensity | Low, Medium, High |
| Status LED | On, Off, Recording |

The original Mini retains its existing night-vision behavior. Mini 2 uses the verified shared owl configuration endpoint for all these controls; BlinkPy's generic hawk path is not used. The fresh illuminator_enable_v2 field confirms night vision, avoiding the stale legacy value sometimes returned when night vision is Off.

The four motion/clip controls now match the existing Outdoor controls, with the documented 30-second Mini limit instead of the Outdoor's 60-second limit. IR intensity matches the existing Doorbell values: Low 1, Medium 4, High 7. IR intensity remains hidden when confirmed night vision is Off.

Before each new control change, the controller reads current capabilities and validates the requested value. It writes one field and checks fresh readback, never automatically repeating the write. Missing or unfamiliar optional fields are omitted without blocking other controls. Rapid changes can be rejected while Blink finishes a previous command; use Refresh before retrying.

No Windows binary change is required: reopen Camera Settings to obtain the expanded list. This update covers the verified motion, clip, and infrared controls, not every setting in the official app (such as zones, audio, video quality, or spotlight controls).

Validation: 43 settings regression tests passed. Tests cover model limits, unsupported fields, isolated writes, Mini 2 night vision routing/readback, rejected commands, and confirmation failures.

Sources: [Blink device settings](https://support.blinkforhome.com/en_US/new-device-layout-settings), plus selected fields read directly from the user's cameras. No claim of a complete comparison with the user's mobile app screens.

Live verification passed on Living Room Camera (original Mini) and Well House (Mini 2). Every added control was changed, confirmed through fresh configuration, and restored; unrelated exposed settings remained unchanged. Physical image/IR behavior was not visually inspected.
