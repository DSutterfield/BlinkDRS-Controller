# Next session: Motion Zones

Requested by Dan on September 29, 2026. Investigate and implement in a future session, not as part of this note-taking task.

## Dan's description to preserve

"The only setting I currently have in mind is the Motion Zone. It involved a Basic Zone and an Advanced Zone. A current photo is captured with a grid (finer grid for Advanced). Clicking each square in the grid darkens it meaning it is excluded from motion detection. Clicking it again returns it to normal and includes it motion detection. If that is exposed to us, keep my description so that we can deal with it next session."

## Intended behavior

- Capture a current camera image as the editing background.
- Offer Basic and Advanced motion-zone grids; Advanced uses a finer grid.
- Click a cell to darken it and exclude that area from motion detection.
- Click an excluded cell again to restore its normal appearance and include it in motion detection.
- Load the camera's saved selections, provide explicit Apply/Cancel behavior, and confirm saved changes with fresh readback.
- Verify grid dimensions, coordinate orientation, exclusion encoding, and Basic/Advanced conversion for each supported model before writing. Preserve existing zones during investigation.
- These are motion-detection exclusion zones, not privacy zones that obscure recorded video.

## Read-only findings

Selected configuration reads from one camera of each family exposed zone_version:

| Camera family | Sample | zone_version |
| --- | --- | --- |
| Original Mini (owl) | Living Room Camera | v1 |
| Mini 2 (hawk) | Well House | v2 |
| Outdoor (sedona) | Driveway | v2 |
| Doorbell (tulip) | Front Door | v2 |

The tested configuration responses did not include zone cell selections under keys containing zone or mask, and the installed BlinkPy camera source scan found no motion_zone/activity_zone helper. Zone-version metadata is evidence of different zone formats, not confirmation that readable/writable motion-zone grids are available through our integration. Locate and verify the actual zone endpoints and encoding next session before promising editing support. Privacy-zone compatibility fields are separate and do not establish motion-zone support.

No zone settings or camera images were changed/captured during this inspection. Inspection script: work/inspect-motion-zones.sh in the September 29 Codex workspace.

## Other pending user check

Dan will visually test the Mini 2 light tonight; the wellhouse was too bright to judge it today. The installed toggle stops Live View, confirms the light change, and resumes. Controller tests confirmed On/Off and stream restart, but physical light appearance remains for Dan to check. This is a note, not a scheduled reminder.
