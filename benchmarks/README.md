# Benchmark Protocol

Status: **Benchmark pending.** No measured savings claim belongs in the README without attributable evidence. Ordinary Workers do not maintain a token ledger.

## Existing paired comparison

Compare the same task under `strong-only` and `tiered` strategies, with the same completion criteria. Record one JSON object per completed run using `schemas/benchmark-run.schema.json`. Existing schema-v2 records remain valid. Keep task success, test pass rate, total/per-tier tokens, duration, model switches, Worker conversations, escalations and Owner interventions. Monetary cost and credits are optional/null when unavailable.

`measurement_source` must identify host per-model telemetry or documented per-conversation usage, with a nonempty evidence reference. Account-wide usage, requested models, selector screenshots or guessed counts are not attribution. Publish raw records and measurement notes with any real savings comparison.

```console
python scripts/benchmark.py validate benchmarks/runs.jsonl
python scripts/benchmark.py aggregate benchmarks/runs.jsonl --output benchmarks/summary.json
```

## Optional purpose attribution and the 10% target

One completed run may include `purpose_usage`. This does not require a paired strong-only run to inspect:

```json
{
  "coordination": 10,
  "task": 100,
  "unclassified": 0,
  "source": "documented-purpose-attribution",
  "evidence": "Synthetic example only; not a real measurement",
  "synthetic": true
}
```

The three counts sum to the run's `tokens.total`. Include the same host-reported token categories throughout: input, output and reported reasoning/cache categories without double counting. Document that accounting convention with the source. Missing token categories or mixed work that cannot be classified belong in `unclassified`; if total usage itself is unavailable, do not manufacture a benchmark run.

- `coordination`: agent-project-manager protocol loading, bookkeeping, bindings, dispatch/callbacks, unnecessary polling and repeated management context.
- `task`: intent analysis, architecture, implementation, debugging, experiments, tests and substantive acceptance. Lead work is not automatically coordination; Worker bookkeeping is not automatically task work.
- `unclassified`: measured tokens whose purpose cannot be reliably separated.
- `source`: `host-purpose-telemetry` or `documented-purpose-attribution`. Per-model/per-conversation totals alone do not establish purpose.
- `evidence`: source location, attribution method and accounting coverage; no private chain-of-thought or secrets. Use visible turns/tool operations with measured usage or host purpose telemetry. Ambiguous mixed turns are unclassified.
- `synthetic`: true for fixtures, examples and simulated measurements. Never mark generated numbers as nonsynthetic.

```console
python scripts/benchmark.py overhead benchmarks/runs.jsonl
```

The ratio is **coordination / task**, with a target <= 0.10 for the same successfully completed task. This is not coordination / total and not strong / economy. `overhead` and `aggregate` report individual runs, so averaging cannot hide an over-budget task. Zero denominator, incomplete attribution, missing usage or unsuccessful work gives `unmeasured` with null ratio/target decision. Synthetic runs are labeled `synthetic` and do not establish a measured ratio or savings.

If measured coordination exceeds 10%, combine small assignments, reduce repeated protocol/context reads and eliminate duplicate status reports. Preserve necessary scope, recovery and acceptance checks. Do not rerun tasks merely to improve published numbers. Without suitable telemetry, report document size/read/action proxies and leave the numeric target unverified.
