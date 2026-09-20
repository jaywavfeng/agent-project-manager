# Workspace Housekeeping

Housekeeping reduces the active working set and manages explicitly released artifacts. Disk space and prompt tokens are different measurements. Do not have a model reread every file to decide what to delete.

## Declare outputs once per run

Prefer existing project output conventions. Otherwise use outputs/<role>/<revision>/<run>/ for generated task artifacts. Keep source, original inputs, datasets, dependencies, final deliverables and validation evidence separate. Ordinary search starts in the assigned source/dependency paths and excludes history, storage and generated output directories unless needed.

workspace-register --path RELATIVE_DIRECTORY --producer lead|worker-N --assignment-revision N --kind temporary|intermediate|deliverable|evidence records an existing task/run directory in optional WORKSPACE.json. Registration is an explicit classification, not a filename heuristic. Do not register raw data or source as temporary. Unknown existing files remain unmanaged. Nested/overlapping registrations and protected paths are rejected; Git-tracked content is not moved or deleted.

Temporary artifacts require --reproduce with a real regeneration command or method. Intermediate artifacts require --movable before archiving. --released --quiescence-evidence TEXT is a declaration that producers and consumers, including background processes, have stopped and there are no external references that require the old path. Register without --released while work is running, then repeat registration to release the directory after checking actual processes. Worker producers must be terminal or have moved to a later revision. Lead producers explicitly confirm completion in the release evidence.

The helper cannot discover every external process or semantic dependency. It additionally blocks current task scope/read references, canonical plan/handoff/directive references, unfinished review or current verification references, unresolved Owner feedback, and uncertain delivery. If any use is unclear, leave released=false. Do not downgrade evidence/deliverables to temporary.

## Trigger only during useful work

Lead runs housekeeping at milestone completion, before project completion freeze, or when context reports housekeeping_due during a substantive continuation. The default interval is 7 days. No background timer and no idle-project wakeup. Skip it on status-only requests. A skip is not a task blocker.

- housekeep: read-only candidate preview, bounded output and counts; no registration or state mutation.
- housekeep --apply: inspect, recheck and perform authorized operations; replace the current summary only when actions occurred.
- housekeep --apply --if-due: same, but skip if checked within 7 days. At milestones omit --if-due.
- workspace-restore --path ORIGINAL_RELATIVE_DIRECTORY: restore quarantined/archived material without overwriting current content; reset released=false.

All commands use explicit Python plus the absolute installed script path. No additional per-run Owner approval is needed within previously authorized registrations. Unknown directories are reported only as a shallow count and up to five examples, never recursively inventoried.

## Preserve evidence and recoverability

Released intermediate directories move to .tiered-agent/storage/<batch>/content. Released reproducible temporary directories first move there as quarantined. Only a subsequent normal housekeeping pass at least 7 days later can permanently delete unchanged quarantined content. Evidence, deliverables and historical task/review/completion records have no age-based deletion policy.

Each batch records the original path, destination, reason, metadata inventory and operation outcome before moving. Changed content, unsafe paths, source/target conflicts and failed moves are retained for inspection. Interrupted moves/restores reconcile on the next explicit apply/restore; conflicting contents are never overwritten. Quarantined files changed after the move are not deleted. The retained regeneration method remains available after deletion.

Before a move/delete, recheck current state, registration, tracked files and directory metadata. Inspect only registered trees, bounded to 10000 entries per directory; larger candidates are skipped for narrower registration. Reject path escape, symlinks, Windows junction/reparse points and special filesystem entries. This assumes the confirmed writers remain stopped; filesystem checks are not a sandbox against a malicious concurrent writer.

Resolved Owner events move to inbox/owner/history and remain addressable by event ID. Pending events and recovery files are never cleaned. Ordinary context reads only pending events, paginated active roles and pointers; explicit history validation remains available. Do not move active evidence merely to make a directory look tidy.

WORKSPACE.json holds policy, registration lifecycle and last_checked. HOUSEKEEPING.json holds one current summary. Batch records stay with cold storage. Unchanged runs create no new summary/archive; only the last-check timestamp may advance. A completed project is read-only, including housekeeping apply; perform the last tidy before freezing completion.
