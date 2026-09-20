# Escalation and Review

Clear in-scope Owner corrections stay local when they preserve the goal, architecture and acceptance. Otherwise preserve exact Owner words in an inbox event and pause conflicting work; notify the authorized Lead once. The Owner does not need to copy any context. Read-only questions on completed projects create no events.

Escalate repeating failures without new evidence, scope conflicts, unknown intent, architecture changes and missing external-action authority. Keep BLOCKER.md to the observable failure, attempts and lessons, safe state, required decision and independent work that can continue. Do not paste full command history. Continue distinct evidence-based hypotheses; no fixed retry-count ceremony.

Lead resolves the cause and may directly diagnose or implement. control-worker resume/cancel/takeover requires confirmed stopped writers and the current revision. Never infer that blocked or unresponsive means stopped. Takeover preserves scope and partial results; a new revision protects against late messages. A suitable completed/stopped Worker is reused for subsequent work.

Skip separate review for low-impact work with strong validation. Use balanced review when an independent pass improves correctness; use strong only for justified algorithmic, architectural, security or integration risk. Reviewer checks actual evidence and writes a bounded report; it does not silently redesign the project.

Review status completed means review finished. verdict approved means acceptance; changes-requested means repair is needed. Lead can cancel an unfinished review after reviewer quiescence and resume execution without waiving the review requirement. Revised implementation invalidates old approval; assign a fresh review. Reviewer writes use --assignment-revision, including when reusing the same reviewer ID.

Use [host dispatch](host-dispatch.md) for durable result callbacks. Missing bindings/tools yields one manual Lead continuation, not a loop. Resolve ambiguous Owner events once, retain durable resulting directives, and let [housekeeping](workspace-housekeeping.md) remove resolved events from the active inbox.
