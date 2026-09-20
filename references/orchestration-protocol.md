# Lead Coordination

Optimize correct completion and total cost, including waiting and rework. Lead can directly implement, diagnose, test and integrate when that is cheaper than delegation. No extra justification document is required. Reuse one economy Worker for sustained bounded work; add independent roles only when useful. Ordinary review is balanced; strong review needs a concrete risk justification.

Initialize durable state only when a handoff helps. Keep stable goals, constraints and acceptance in PLAN.md. Use context --role lead for continuation, then relevant directives, active tasks/blockers and pending events. Its pagination reports active totals; do not silently ignore relevant omitted work. Read history only for a particular discrepancy. Refresh HANDOFF.md at a meaningful change, not after each command; OWNER_STATUS.md is optional.

Assignments precede messages. Use the selected profile and [host dispatch](host-dispatch.md). Existing authorization persists. After an actual result, verify criteria and continue the authorized next step. Do not ask the Owner to perform routine handoffs when automatic messages work.

When a Worker stalls, resolve the actual cause. control-worker can resume, cancel or take over after confirmed writer/process quiescence; a timeout is not proof of that. Takeover retains the task and write scope while fencing the old revision. Reassign completed or stopped work honestly. Do not release an input still required by active dependents. Completed historical dependencies do not follow a reused role into a new assignment.

Review completion and approval are separate. cancel-review preserves the requirement, lets execution resume, and archives the prior state. New implementation invalidates old review; only a current approved verdict permits required-review completion.

At milestone completion, before completion freeze, or a due substantive continuation, use [housekeeping](workspace-housekeeping.md). This is bounded artifact maintenance, not another audit gate. Skip ambiguous/active files and keep useful work moving. Status-only requests remain read-only. For completed projects, use reopen-project only for actionable changes.

Use [state ownership](runtime-state.md) for controls and [escalation](escalation-and-review.md) for unresolved intent. Coordination / actual-task tokens targets 10%; remove repeated reads, reports and polling before adding machinery. Route declarations and document reductions do not prove billing savings.
