# tiered-agent-orchestrator

[English](README.md) | [简体中文](README.zh-CN.md)

**Correct completion first; minimize execution, coordination, waiting and rework.**

Project status: **v0.7.0 · Apache-2.0 · Benchmark pending**

TAO is a Codex skill invoked as `$tao`. A strong Lead decides whether direct execution or a reusable economy Worker costs less overall. Repository state makes handoffs independent of chat history. Simple work needs no orchestration.

## v0.7.0

- Lead can implement, diagnose, test, integrate and safely take over stopped work. `control-worker` preserves scope and evidence while advancing revision; `reassign-worker` can reuse confirmed stopped assignments.
- Completed historical dependencies no longer prevent role reuse. Review completion and approval are separate; `cancel-review` permits repairs without waiving required review.
- `prepare-message` and `record-message` support both Lead assignments and Worker/Reviewer callbacks with durable per-role receipts. Duplicate, stale and uncertain events are handled explicitly.
- Independent conversation creation may use explicit model/reasoning selection with unverified effective metadata. Contradictions require correction. Automatic subagents are disabled; requested routing is not billing proof.
- Registered task artifacts receive bounded housekeeping at milestones or after seven days on the next substantive continuation. Reproducible temporary output enters seven-day quarantine; evidence and unknown files are retained. There is no background timer.
- Compact Lead context pages active work instead of expanding terminal verification histories. Resolved feedback leaves the active inbox but remains retrievable by event ID.

## Install and use

Install GitHub repository `jaywavfeng/tiered-agent-orchestrator`, path `.`, as `tiered-agent-orchestrator` using Codex's skill installer. Invocation remains `tao`. Python 3.9+ and its standard library are sufficient. A release does not automatically replace an existing global installation.

```text
$tao Complete this project. Choose direct execution or a reusable independent Worker by total cost. Automatically send assignments and result callbacks between the selected conversations. If needed and supported, create an independent gpt-5.6-luna conversation with xhigh in this current project directory; otherwise give me one setup instruction. Do not use automatic subagents. Keep the gpt-5.6-sol Lead and use gpt-5.6-terra first for escalation.
```

Existing authorization persists. Owner-selected conversations preserve their settings; unavailable model selectors and manual reasoning do not create proof loops. Respect actual host permissions and creation capabilities. Bind final task IDs and the same resolved project directory; another worktree is not the same runtime.

| Request | Action |
|---|---|
| `$tao continue lead` | Restore current goal, evidence, blockers and next action |
| `$tao continue worker-1` | Continue the existing scoped assignment |
| `$tao continue reviewer-1` | Continue the currently assigned review |
| `$tao status` | Read status without cleanup or other mutation |

Maintaining TAO or finding `.tiered-agent` does not implicitly activate orchestration.

## CLI and state

From this repository:

```console
python scripts/statectl.py --help
python scripts/statectl.py init --project-root /path/to/project --project-id my-project
python scripts/statectl.py context --project-root /path/to/project --role lead
python scripts/statectl.py control-worker --help
python scripts/statectl.py prepare-message --help
python scripts/statectl.py housekeep --project-root /path/to/project
python scripts/statectl.py validate --project-root /path/to/project
```

Installed Windows usage:

```powershell
& "<absolute path to python.exe>" "<absolute skill directory>\scripts\statectl.py" context --project-root "<project directory>" --role lead
```

Use real quoted paths. Do not launch a bare `.py` file or open VS Code to perform a state operation. Request the subcommand's `--help` instead of rereading implementation.

Lead owns global direction, assignments and `TRANSPORT.json`. Current executors own task results; each sender owns its `DELIVERY.json`. `HANDOFF.md` is the bounded takeover packet, and `OWNER_STATUS.md` is optional human presentation. `reassign-worker` archives actual prior state; `reopen-project` preserves completion before actionable new work. Existing schema-v1 projects remain readable without bulk migration. Controlled tasks and new reviews require revision-specific writes. Legacy required reviews need an explicit approved verdict before a new completion decision.

Details: [state contract](references/runtime-state.md), [host messages](references/host-dispatch.md), [example flow](examples/one-worker-flow.md).

## Workspace housekeeping

Register existing output directories by task/run through `workspace-register`. Temporary output needs a regeneration method; moving intermediate output needs explicit movable classification. Release requires evidence that producers, background processes and consumers have stopped. The helper also checks current references, scope, Git-tracked content and unsafe paths; it cannot infer every external dependency.

`housekeep` previews without mutation. `housekeep --apply` performs authorized work; `--if-due` skips checks within seven days. `workspace-restore` restores stored output without overwrite. Source, raw input, evidence, deliverables and unknown directories are preserved. No periodic whole-project content scan, age-based evidence deletion or automatic cleanup of unrelated global backups occurs. Final housekeeping runs before project completion freezes state.

Full policy and examples: [workspace housekeeping](references/workspace-housekeeping.md).

## Evidence and validation

Target **coordination tokens / actual-task tokens <= 10%**. Substantive Lead execution and acceptance are task work. Missing purpose attribution means unmeasured, not zero overhead. Optional `purpose_usage` and its accounting rules are described in [benchmarks](benchmarks/README.md). No daily ledger is required. Context words and tool-call counts are proxies, not measured token/credit savings.

```console
python -m unittest discover -s tests -v
python scripts/benchmark.py --help
```

CI covers Windows/Ubuntu × Python 3.9/3.13. Tests use isolated projects and synthetic host receipts; they do not prove live desktop messaging or billed usage. See [v0.7.0 validation](benchmarks/v0.7.0-validation.md). Scenario definitions are not a claim of independent Agent execution. Publication, deployment and unrelated global changes still require Owner authority.
