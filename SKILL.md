---
name: tao
description: Coordinate multi-stage engineering work with cost-aware Lead execution, reusable independent Workers, repository-backed handoffs, and bounded workspace housekeeping. Activate only for an explicit $tao command; maintenance of this skill never activates orchestration.
license: Apache-2.0
metadata:
  author: "jaywavfeng"
  version: "0.7.0"
---

# Tiered Agent Orchestrator

> Correct completion first; minimize the total cost of execution, coordination, waiting and rework.

Use the cheapest model likely to finish correctly. Roles serve that goal: a strong Lead may implement, diagnose, test, integrate and accept directly when delegation costs more. Reuse an economy Worker when delegation helps; balanced review is optional where it adds confidence. Do not write a cost essay for ordinary choices.

## Start or resume

Only an explicit `$tao` request activates orchestration. Maintaining TAO, pasted examples and existing runtime files never activates it. Complete simple tasks directly without initializing state or inventing a Worker.

For durable work, initialize once and keep goals/criteria in `PLAN.md`. Use one reusable Worker by default. Add roles only for independent useful work. Existing `$tao continue worker-N` or `reviewer-N` resumes that role; `$tao continue lead` reads the current Lead context. The repository, not chat history, carries the handoff.

Invoke the helper in a terminal with a verified Python 3.9+ interpreter and this skill's absolute script path:

```powershell
& "<absolute path to python.exe>" "<absolute skill directory>\scripts\statectl.py" context --project-root "<project directory>" --role lead
```

Replace placeholders. Never launch a bare `.py`, `code`, `Invoke-Item` or a file-opening tool for a state operation. Do not open it in VS Code to execute it. Use subcommand `--help` for unfamiliar arguments; `context` returns reusable executable/script arguments.

## Execute and accept

Lead: use [coordination](references/orchestration-protocol.md) for initial planning/direction changes and [state ownership](references/runtime-state.md) before state changes. Select one [OpenAI](profiles/openai-codex.md) or [generic](profiles/generic.md) profile when routing. Persist bounded objectives, allowed_scope and acceptance before delegation. Continue authorized work after verification without repeatedly seeking permission.

Worker: read `context --role worker-N`, current task/status, relevant directives and dependencies. Respect write scopes and exclusions; validate actual results. Record meaningful transitions with `set-worker-status`. Relay and controlled assignments use `--assignment-revision`; only the current executor writes results. Lead-owned work uses `--actor lead`. Never validate or gate reasoning in an Owner-created conversation.

A stopped/blocked assignment can be resumed, cancelled or taken over through `control-worker`; record the reason and confirmed writer/process quiescence. Silence, timeout and blocked status are not stopping evidence. Takeover preserves scope and partial results, increments revision and fences old updates. Reassign stopped work without pretending it completed. Use [escalation](references/escalation-and-review.md) for real blockers or ambiguous Owner intent.

Reviewer: inspect the assigned scope and evidence. Record both review completion and `--verdict approved|changes-requested` with its revision. Lead can `cancel-review`, fix the issue, and request a fresh review; required review is retained. A finished review is not necessarily approval.

Complete only against actual criteria and current evidence. Completed projects stay frozen for read-only questions; actionable changes use `reopen-project`. No role bypasses host permissions or expands external-action authority.

## Communicate only when useful

Read [host dispatch](references/host-dispatch.md) when binding or messaging. Prefer existing independent conversations. With existing Owner authority, automatically create an independent conversation only if the host supports explicit model and reasoning selection. Missing effective metadata means unverified routing, not automatic refusal; contradictory metadata requires correction. If creation is unavailable, provide one manual setup instruction. Do not use automatic subagents: requested routing is not proof of correct token billing.

Use `prepare-message`, the host send tool, then `record-message`. Lead dispatches ready assignments; Workers/Reviewers report result transitions. Preserve per-role receipts, ignore stale events, and reconcile uncertain sends once without blind retries. Host messages wake roles to read canonical state; delivery is not acceptance. Passive waits end on one unchanged timeout; no polling daemon.

## Keep the working set small

Ordinary continuation uses one role context, not repeated protocol/history reads. Context pages identify omitted active roles/events; inspect relevant pages before acceptance. Write facts once at assignment, control, blocker, direction or completion transitions. Keep `HANDOFF.md` current and short; `OWNER_STATUS.md` is optional human presentation.

Use [workspace housekeeping](references/workspace-housekeeping.md) when declaring output directories or tidying. Lead runs it at milestone completion, before project completion, or when `housekeeping_due` is true during substantive continuation (7 days). Never mutate on a status-only request. Register task/run directories in batches; keep evidence and deliverables. Archive resolved feedback and released intermediate artifacts; only explicitly released reproducible temporary artifacts enter 7-day quarantine. Unknown files remain untouched. Normal searches exclude history/storage/generated outputs unless the task needs them.

Target **coordination tokens / actual-task tokens <= 10%**. Substantive Lead work counts as task work. Without purpose-attributed telemetry, report only measured context/action proxies; use [benchmark rules](benchmarks/README.md) when measuring. No daily ledger, background cleanup or routine whole-project audit.

Prefer the simplest mechanism reliable for actual failures. Read-only commands never repair state; use explicit `recover` after a reported interrupted update. Advance the task once the necessary checks pass.
