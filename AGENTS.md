# BlinkDRS standing instructions

Dan authorized these standing instructions on October 5, 2026. They apply to requested BlinkDRS development and debugging until Dan changes or revokes them.

## Authorized scope

- Work on the Windows BlinkDRS app in `C:/Users/DanSu/source/repos/BlinkDRS` and its Pi controller in `C:/Users/DanSu/source/repos/BlinkDRS-Controller` and `/home/dan/BlinkDRS-Controller` on Dan's configured Pi.
- Inspect and edit project source, configuration and project documentation; build, test, diagnose, fix and verify changes needed for the requested work.
- Use the existing BlinkDRS Pi connection and stored credentials through the project's connection helper. Verify the saved SSH host-key fingerprint. Do not display passwords or secrets.
- Transfer BlinkDRS controller files, regression tests and necessary project assets between Dan's Windows computer and that Pi. The configured, fingerprint-verified Pi is an authorized destination for this project material.
- Stage, test, deploy and verify controller updates; restart the BlinkDRS controller or AI worker when needed for the requested work. Routine actions within this scope do not require asking Dan for approval again.
- Build and publish the matching Windows app locally. Install or update it in the existing project deployment location when needed for the requested change, checking for a running app first.

## Working agreements

- Preserve recordings, catalogs, identity profiles and human-confirmed crops. Do not delete or reset them without explicit task-specific authorization.
- Back up affected deployed files before replacing them. Use isolated catalogs and temporary video fixtures for regression tests.
- Before a service restart, check for active Live View or recording/saving. Wait for it to finish; do not interrupt it without explicit authorization.
- Verify staged file hashes and source syntax, run relevant regressions, then check service health and affected endpoints after deployment. Restore the previous files if verification fails.
- Stay within BlinkDRS and Dan's configured Pi. Ask before unrelated device changes, credential changes, public publication, or destructive actions outside the requested task.
- Give concise progress and outcome updates. When permission is genuinely blocked by the platform, explain the specific rejected action and reason; do not repeat permission questions for already authorized routine work.

These instructions express Dan's authorization. They do not disable or bypass Codex sandbox, automatic review, managed permissions, or other platform controls. They authorize work within requested tasks, not scheduled or unsolicited background work.

## Release information

For every BlinkDRS or Pi controller change, update the affected component's version and date updated before deployment. Keep About BlinkDRS accurate: Windows version comes from BlinkDRS.vbproj, Windows date from ReleaseInfo in AboutBlinkDrsView.vb; controller version/date come from release_info.py through /api/v1/about. Verify the About tab against the installed app and running Pi after deployment. Do not substitute API protocol version for controller release version.

