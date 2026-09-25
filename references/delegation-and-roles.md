# Delegation and Roles

Roles are optional. Default to `standalone`: the current Agent understands the project, reads the memory it needs, completes the task, updates state, and stops. Do not create a Worker to make simple work look organized.

## When delegation pays

Delegate only when a separate conversation is cheaper or more reliable than doing the work inline. If you cannot name the task, its scope and its acceptance criteria, do not delegate it yet.

See [delegation policy](delegation-policy.md) for which task types may be delegated and which must stay with the current Agent.

## Roles

**standalone (default).** One Agent, one task. Runs with no Workers and no required review. Use for ordinary work, small edits, single-file fixes and anything you can finish correctly now.

**leader.** Enabled explicitly via `set-project --mode leader`. Understands the goal, decomposes work, decides what to keep and what to delegate, assigns Worker and profile, validates returned results, owns key decisions and final integration. A leader must not delegate everything: keep high-level decisions, key design, user-intent interpretation, final judgment and important writing.

**worker.** Executes the assigned task only. Reads the minimum context it needs, does the work, returns a structured result, updates its own artifacts, and hands back. A Worker must not redesign the project.

**reviewer.** Optional independent check when correctness risk justifies it. Checks actual evidence in scope, writes a bounded report. Balanced review is the default; strong review needs a concrete risk justification (algorithmic, architectural, security, integration).

## Abstract profiles

Route by abstract tier, never by vendor model name:

| Tier | Typical role | Use when |
|---|---|---|
| `strong` | leader | judgement, design, integration, final writing, acceptance |
| `balanced` | reviewer | an independent correctness check is worth its cost |
| `economy` | worker | bounded, well-specified, independently verifiable work |

Concrete model names live only in the selected profile ([generic](../profiles/generic.md) or provider-specific). Keep them out of this protocol so the skill stays agent-agnostic.

## Coordination

Optimize correct completion and total cost, including waiting and rework. A strong leader can directly implement, diagnose, test and integrate when that is cheaper than delegation. No extra justification document is required.

Initialize durable state only when a handoff helps. Keep stable goals, constraints and acceptance in `PLAN.md`, and durable user requirements in project memory. Use `context --role lead` for continuation, then relevant directives, active tasks/blockers and pending events. Its pagination reports active totals; do not silently ignore relevant omitted work. Read history only for a particular discrepancy. A cold start reads state, memory and the plan directly; there is no handoff file to keep in sync.

Assignments precede messages. Use the selected profile and [host dispatch](host-dispatch.md). Existing authorization persists. After an actual result, verify criteria and continue the authorized next step. Do not ask the Owner to perform routine handoffs when automatic messages work.

When a Worker stalls, resolve the actual cause. `control-worker` can resume, cancel or take over after confirmed writer/process quiescence; a timeout is not proof of that. Takeover retains the task and write scope while fencing the old revision. Reassign completed or stopped work honestly. Do not release an input still required by active dependents. Completed historical dependencies do not follow a reused role into a new assignment.

## Escalation

Clear in-scope Owner corrections stay local when they preserve the goal, architecture and acceptance. Otherwise preserve exact Owner words in an inbox event and pause conflicting work; notify the authorized leader once. The Owner does not need to copy any context. Read-only questions on completed projects create no events.

Escalate repeating failures without new evidence, scope conflicts, unknown intent, architecture changes and missing external-action authority. Keep `BLOCKER.md` to the observable failure, attempts and lessons, safe state, required decision and independent work that can continue. Do not paste full command history. Continue distinct evidence-based hypotheses; no fixed retry-count ceremony.

The leader resolves the cause and may directly diagnose or implement. `control-worker` resume/cancel/takeover requires confirmed stopped writers and the current revision. Never infer that blocked or unresponsive means stopped.

## Review discipline

Skip separate review for low-impact work with strong validation. A Reviewer checks actual evidence and writes a bounded report; it does not silently redesign the project.

Review status `completed` means the review finished. `verdict approved` means acceptance; `changes-requested` means repair is needed. The leader can `cancel-review` after reviewer quiescence and resume execution without waiving the review requirement. Revised implementation invalidates old approval; assign a fresh review. Reviewer writes use `--assignment-revision`.

Use [host dispatch](host-dispatch.md) for durable result callbacks. Missing bindings/tools yields one manual continuation, not a loop. Resolve ambiguous Owner events once, retain durable resulting directives, and let [housekeeping](workspace-housekeeping.md) remove resolved events from the active inbox.

## Cost

Keep coordination tokens / actual-task tokens under 10%. Remove repeated reads, reports and polling before adding machinery. Route declarations and document reductions do not prove billing savings.
