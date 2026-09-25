# OpenAI Codex Profile

agent-project-manager v1.0.0 preserves the model mapping. Routing capabilities are checked against current host tools.

| Role | Model | Reasoning | Use |
|---|---|---|---|
| PROJECT_LEAD | `gpt-5.6-sol` | `xhigh` | Intent, decisions, direct execution when cheaper, acceptance |
| WORKER | `gpt-5.6-luna` | `xhigh` | Sustained bounded implementation and validation |
| First escalation / REVIEWER | `gpt-5.6-terra` | `high` or `xhigh` | Capability gaps and useful independent review |
| SOL escalation | `gpt-5.6-sol` | `xhigh` | High-risk decisions or insufficient Terra capability |

Use [host dispatch](../references/host-dispatch.md). Independent-conversation creation requests both model and reasoning explicitly (host tools may name the latter thinking). Missing effective metadata is unverified; contradictions require correction. Do not silently inherit Lead settings. Automatic subagents are disabled; routing parameters are not billed-usage evidence.

Owner-selected conversations preserve their settings. Check only a clearly visible model family; unavailable selectors continue. Never gate manual reasoning. For display, 极高 = xhigh and 高 = high. Model recommendations do not authorize new conversations or unrelated external actions. Real token/credit savings require attributable telemetry.
