# Delegation Policy

A lightweight rule set, not a workflow engine. Decide per task whether delegation pays. Record the choice in `PLAN.md` under `## Delegation` when it is not obvious.

## Rule of thumb

Delegate a task when **all** of these hold:

1. The task is describable with a bounded objective and explicit acceptance criteria.
2. Its write scope does not overlap anything else active.
3. The result is independently verifiable from evidence, without re-deriving the whole design.
4. The round trip is cheaper than doing it inline.

Otherwise keep it. Delegating a task you cannot describe is how coordination cost exceeds the work.

## Delegation classes

| Class | Examples | Who does it |
|---|---|---|
| **Delegate** | experiment execution, parameter sweeps, data organisation, result statistics, literature search, chart generation, routine code implementation, data cleaning, bulk mechanical refactors | `worker` |
| **Keep unless clearly cheaper** | integration of returned results, refactoring across the whole codebase, debugging with an unclear root cause, anything requiring repeated back-and-forth | current Agent first; delegate only with a written justification |
| **Never delegate** | final user-facing writing, core research conclusions and their interpretation, final architecture decisions, user-intent interpretation, acceptance and final judgment, anything requiring external-action authority | current Agent only |

## Recording a refusal

When you keep a task that a reader might expect to be delegated, note the reason in one line in `PLAN.md`. Do not write an essay. `"Kept: root cause unknown, needs interactive debugging"` is enough.

## Cost

Every delegation costs a context handoff in both directions. The coordination / actual-task token ratio must stay under 10%. If adding a Worker pushes it over, do the work inline.
