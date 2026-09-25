# Runtime State Contract

Read once before changing runtime state; ordinary continuation uses role context.

| Files | Writer and purpose |
|---|---|
| STATE.json, PLAN.md, OWNER_DIRECTIVES.md | Lead: lifecycle, stable criteria, current Owner direction |
| PROJECT_STATUS.md, optional OWNER_STATUS.md | Lead: bilingual human-facing status page; never machine authority |
| memory.jsonl, memory-archive.md | Lead: compressed durable memory and its consolidated archive |
| TRANSPORT.json | Lead: bindings and legacy receipts; never lifecycle authority |
| workers/worker-N/TASK.md | Lead: current assignment and revision |
| Worker STATUS.json and BLOCKER.md | Current executor: results, evidence and next action |
| review/TASK.md | Lead: current review criteria |
| review/STATUS.json and REPORT.md | Assigned reviewer: revision, progress and verdict |
| Per-role DELIVERY.json | Sending role: current receipt per destination |
| WORKSPACE.json, HOUSEKEEPING.json | Lead/helper: registered directories, schedule and latest cleanup summary |
| inbox/owner/*.md | Originating Worker: verbatim feedback; Lead resolves and archives |

Default Worker executor is worker. Optional executor=lead records a takeover without changing role identity or releasing scope. Controlled assignments require --assignment-revision and matching --actor worker|lead for status writes. These checks coordinate trusted agents; they are not OS authentication. Workers do not rewrite global state, bindings or another role's files.

## Transitions

Worker states remain ready, active, blocked, waiting-owner, completed and inactive. control-worker --action resume|cancel|takeover requires revision, reason and quiescence-evidence. All three archive the actual old files and advance revision; resume produces ready/worker, takeover ready/lead, cancel inactive/worker. Dependencies still constrain execution and scope overlap is forbidden. Completed tasks use reassign-worker. Reassigning other stopped statuses requires reason and quiescence evidence; previously controlled roles also require revision.

History records truthful prior states, including blocked or cancelled work. Terminal historical dependencies do not track a role's next assignment. Nonterminal dependents still prevent upstream reassignment; inactive never satisfies an input dependency. Assignment revisions and existing history directory names remain stable across releases.

assign-review gives each new review a revision. set-review-status passes --assignment-revision; a completed review has verdict approved or changes-requested. An absent legacy verdict never implies approval: inspect the report and record it. cancel-review requires revision, reason and stopped reviewer evidence, archives the review, detaches it and returns to execution while retaining review.required. Old reviewer writes fail. Subsequent implementation invalidates completed review as before.

Completion rejects unfinished non-inactive Workers, unresolved feedback and required review without current approval. Existing completed legacy snapshots remain readable; their historical state is not rewritten. reopen-project snapshots completion before new work.

## Updates and reads

Candidate multi-file state is validated before writes; existing operation markers remain until resulting state validates. An interrupted update requires explicit recover. Newer conflicting content is protected. Ordinary commands do not scan historical bodies; validate explicitly checks history. Status, context and previews never repair files.

context --role lead pages active Workers and pending events with --offset and --limit (default 20, maximum 100), reports terminal counts, and omits old verification bodies. status --json retains the complete compatibility interface. Worker/reviewer packets include their task, current revision and required evidence pointers. Read historical files by a specific role/revision/event, not a full-tree scan.

Owner-event frontmatter, not quoted message text, determines pending/resolved state. Housekeeping moves resolved events to inbox/owner/history; resolving by ID works in either location and is idempotent. Unresolved events never enter cold storage. Human summaries do not override machine state.

Details: [host messages](host-dispatch.md), [artifact housekeeping](workspace-housekeeping.md), [roles](delegation-and-roles.md).
