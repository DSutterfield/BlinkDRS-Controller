# Recorded Clips retention and complete deletion — 2026-09-18

Settings now includes Pi-owned local Recorded Clips retention, 0–3650 days; 0 keeps recordings until manual deletion. The existing 30-day setting is unchanged until saved by the user. Saves preserve other settings, persist in config/settings.local.ini, and apply on the next cleanup cycle without restarting. Retention continues to measure local file modification time, independently of Blink cloud retention.

Cleanup removes missing/deleted clip catalog rows, JSON sidecars, thumbnails, normalized playback copies, and validation records. It also clears historical orphan artifacts. Manual coordinated deletion now stages playback copies and enables foreign-key cascades; rollback restores those files if cloud deletion is rejected. Playback preparation shares the archive lock with cleanup to avoid recreating caches during deletion.

The earlier availability-only repair is superseded: deleted clip history is no longer retained in the active catalog. Database backups remain separate recovery backups. The recovered Back Gate copy saved to Windows is unaffected.

Validation: Windows Release build and rendered Settings layout; fourteen isolated retention, API persistence/validation, artifact cleanup, rollback, catalog notification, and damaged-clip filtering tests. Deployed to the authorized Pi at 192.168.0.127. All fourteen tests also passed on the Pi; live API reports 30 days, no missing-file catalog rows or orphan validation rows remain, and both oldest clips returned HTTP 206 playback data. Backup: /home/dan/BlinkDRS-Controller/backups/retention-update-20260918-140906. The deployment SSH command timed out after file installation/cleanup; controller restart and deployed-file equality were verified afterward.
