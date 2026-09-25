# agent-project-manager

[English](README.md) | [简体中文](README.zh-CN.md)

**Finish the task correctly first, then keep execution, coordination, waiting and rework cheap.**

Status: **v1.0.0 · Apache-2.0 · Benchmark pending**

A Codex skill invoked as `$apm`. One agent handles the work by default; multiple agents are an optional capability, not a requirement. All state lives in the repository, so any agent can pick the project up — the handoff is never buried in chat history.

## Why this exists

1. **Project memory outlives the agent.** Long-term user intent, decisions, constraints, lessons and rejected directions are stored as compressed entries in `.agent-project-manager/memory.jsonl` — conclusions, never transcripts. Swap the model, keep the project.
2. **Read only what this task needs.** The Context Compiler (`context --role <role> --task "<current task>"`) returns the minimum necessary context instead of the whole project. Coordination should stay under **10%** of task tokens.
3. **Delegation is opt-in.** The default `standalone` mode has no Workers at all. Switch to `leader` only when a separate, genuinely independent unit of work justifies the overhead — see the [delegation policy](references/delegation-policy.md).
4. **A clean, recoverable workspace.** Durable state files, atomic writes, crash recovery, and bounded housekeeping that quarantines reproducible temporary output while keeping evidence and deliverables intact.

## Install and use

Install GitHub repository `jaywavfeng/agent-project-manager`, path `.`, as `agent-project-manager` using Codex's skill installer. Invocation is `$apm`. Python 3.9+ and its standard library are sufficient. A release does not automatically replace an existing global installation.

```text
$apm Finish this project. Use standalone execution unless a reusable independent Worker genuinely costs less overall. Record my long-term requirements so a later agent does not lose them. Keep the workspace tidy before you declare the project complete.
```

`$apm` only activates on an explicit request. Maintaining this skill, pasting the example above, or discovering an existing `.agent-project-manager` directory never starts it on its own.

| Request | Action |
|---|---|
| `$apm <task>` | Start or continue a project |
| `$apm continue lead` | Restore current goal, evidence, blockers and next action |
| `$apm continue worker-1` | Continue the existing scoped assignment |
| `$apm continue reviewer-1` | Continue the currently assigned review |
| `$apm status` | Read `PROJECT_STATUS.md` and state without cleanup or any other mutation |

Write the state helper in a terminal with a verified Python interpreter and this skill's absolute script path:

```powershell
& "<absolute path to python.exe>" "<absolute skill directory>\scripts\statectl.py" context --project-root "<project directory>" --role lead --task "<current task>"
```

Always invoke it through the interpreter. Never launch a bare `.py` file, and never open VS Code (or any file-opening tool) to perform a state operation. Request a subcommand's `--help` instead of rereading the implementation.

## Modes and roles

A project starts in `standalone`: one agent reads its context, does the work, verifies it against the acceptance criteria, updates state and memory, and stops. No roles are created for simple work.

`set-project --mode leader` opts into working with others. Only then do `worker` and `reviewer` exist:

- **lead** — owns direction, assignments and `TRANSPORT.json`; keeps final user-facing writing, architecture decisions and acceptance.
- **worker** — owns a bounded objective, `allowed_scope` and its own results; writes only through `--assignment-revision`.
- **reviewer** — records both review completion and a `--verdict approved|changes-requested`, so a finished review is never mistaken for approval.

A stopped or blocked assignment can be resumed, cancelled or taken over with `control-worker`, which preserves scope and partial results, advances the revision and fences stale updates. `reassign-worker` reuses a confirmed stopped assignment; `reopen-project` preserves a completion snapshot before new actionable work. Host messages are reserved with `prepare-message`, sent by the host, then closed with `record-message` — each role keeps its own `DELIVERY.json` receipts. Details: [state contract](references/runtime-state.md), [delegation and roles](references/delegation-and-roles.md), [host dispatch](references/host-dispatch.md), [worked example](examples/one-worker-flow.md).

`PROJECT_STATUS.md` is the single human-facing page: concise, bilingual by default, and rendered from state (`status-refresh`) rather than written as a log.

## Workspace housekeeping

Register output directories per task or run with `workspace-register`. `housekeep` previews without mutating anything; `housekeep --apply` performs only authorized work, and `--if-due` skips checks within seven days. `workspace-restore` brings released output back without overwriting current files.

Source, raw input, evidence, deliverables and unknown directories are always preserved. Reproducible temporary artifacts enter a seven-day quarantine. There is no periodic whole-project scan, no age-based evidence deletion and no background timer. Policy and examples: [workspace housekeeping](references/workspace-housekeeping.md).

## Evidence and validation

Target: **coordination tokens / actual-task tokens <= 10%**. Substantive lead execution and acceptance count as task work. Missing purpose attribution means *unmeasured*, not zero overhead — optional `purpose_usage` and its accounting rules are described in the [benchmark guide](benchmarks/README.md). No daily ledger is required, and context words or tool-call counts are proxies, not proven token or credit savings.

```console
python -m unittest discover -s tests -v
python scripts/benchmark.py --help
```

From this repository checkout you can also drive the helper directly:

```console
python scripts/statectl.py init --project-root /path/to/project --project-id my-project
python scripts/statectl.py context --project-root /path/to/project --role lead --task "fix the parser"
python scripts/statectl.py memory-add --project-root /path/to/project --kind constraint --text "No new runtime dependencies"
python scripts/statectl.py validate --project-root /path/to/project
```

CI covers Windows/Ubuntu × Python 3.9/3.13. Tests use isolated projects and synthetic host receipts; they do not prove live desktop messaging or billed usage. See the [v1.0.0 validation](benchmarks/v1.0.0-validation.md). Scenario definitions are not a claim of independent Agent execution. Publication, deployment and unrelated global changes still require Owner authority.
