# Recorded Clips automatic refresh — September 12, 2026

Implemented and deployed: SQLite triggers increment a persistent catalog revision
in the same transaction as new clips, deletions, reviews, visible metadata changes,
and validation exclusions. The Pi status stream checks that small revision every
two seconds and sends an event only on changes (or initial connection). It does
not query Blink Cloud or scan video files for this check. Events follow catalog
commit; cloud acquisition still follows the existing download polling schedule.
Routine catalog timestamp updates do not trigger a list reload. A reconnect sends
the latest revision so Windows catches up after a disconnected interval.

Windows refreshes Recorded Clips on notifications or navigation to the tab.
Refresh preserves active filters, search text/caret, selection when present,
scroll offset, and does not explicitly take keyboard focus. Requests coalesce;
refreshes during playback are deferred until the player closes. Critical-operation
completion catches up any event skipped while review/delete controls were busy.

Playback raises a clip-reviewed event only after a successful controller response.
The parent list updates its review state and decrements its unreviewed summary
immediately. Close-time reconciliation checks the state and avoids counting again.
Playback's navigation list remains a snapshot for the duration of that player.

Validated: clean Windows build; WPF integration with a local fake controller
(new clip refresh, search/caret/focus/selection preservation, review count change
before player close, no second decrement); SQLite regression for commit/rollback,
quiet metadata updates, review, deletion, validation events. Pi SSE endpoint
verified live after deployment. Real camera arrival still merits user confirmation.

Checkpoint: audio testing deferred until the user's environment is suitable.
Door Bell battery exhaustion was correctly reported on Dashboard and
Systems/Devices, confirmed by the user.

User acceptance confirmed September 12: completed playback review decremented
the unreviewed counter once; closing playback did not change it again. At least
three new trigger events during playback appeared and updated the count on close.
Recorded Clips automatic refresh and immediate review counts are complete.
