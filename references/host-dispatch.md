# Host Dispatch

Read only for independent-conversation creation, binding or messaging. The helper builds packets and records receipts; the Agent uses available host tools. No Python daemon, background poller or private desktop API is involved.

## Routing and authorization

Reuse an Owner-selected independent Worker/Reviewer conversation. Preserve its settings and check only a clearly visible model family. Missing selectors do not require proof or command resend; never gate manual reasoning. An explicit wrong-family indicator receives one correction on the same conversation.

Record existing Owner authority once in OWNER_DIRECTIVES.md. Bidirectional messaging authority covers assignments, resume instructions and result callbacks between selected roles. It does not authorize unrelated messages, deployment or publication. Respect host requirements for explicit new-conversation requests and model selection.

Automatic creation is permitted when the host actually offers independent conversations and accepts explicit model and reasoning selection. Check current tool capabilities, not a static claim about a desktop release. Use the selected profile and the actual project directory, not an isolated default worktree. If the capability is missing, give one manual setup instruction with model, reasoning, directory and `$tao continue worker-N`; continue independent useful work. Do not recursively ask for selector screenshots.

Creation acceptance without effective metadata is unverified routing, not refusal. If actual/effective metadata is present, check it; contradictory values require correction before work. Neither requested nor effective routing proves billed tokens. Automatic subagents are disabled in v0.7.0: they are not independent conversations and do not establish correct billing.

## Bind once

Invoke all commands through explicit Python and the installed absolute script path from [SKILL.md](../SKILL.md).

bind-thread accepts --role lead|worker-N|reviewer-N, --thread-id, --host-id, --cwd, --route-source, --evidence and --project-root. Use actual final IDs and matching resolved project directories. Never infer identity from a title or setup-only clientThreadId. Binding a reviewer requires a current assignment. --replace requires a deliberately verified replacement; active execution and uncertain messages prevent unsafe replacement.

Route sources:

- owner: --evidence identifies Owner selection and existing messaging authority.
- requested: --receipt points to normalized creation evidence with runtime_kind=independent-thread, thread_id, host_id, requested_model, requested_reasoning and source. Optional effective_model/effective_reasoning must match when supplied. Missing effective values remain unverified.
- attested: legacy format requires thread_id, host_id, requested_model, requested_reasoning, effective_model, effective_reasoning and source. Both effective values must match. This existing route remains supported for independent conversations; it never licenses automatic subagents.

The helper validates consistency, not the authenticity of a fabricated receipt. Normalize actual host output; never invent fields. Exclude credentials. Unassigned historical reviewer bindings may remain stored but are not dispatchable.

## One event, one delivery

1. Persist an assignment or actual result transition. For dispatch, the project must be active and the recipient ready, with resolved dependencies and Owner decisions.
2. Read the recipient host state once for a new assignment. Store a short observation JSON containing thread_id, host_id, cwd and status=idle. Do not infer idle from missing data. Result callbacks may wake an idle Lead or queue for an active Lead.
3. prepare-message --sender ROLE --recipient ROLE --assignment-revision N emits target IDs, event_id and prompt and reserves pending delivery in one call. Lead dispatch additionally passes --observation FILE. Each sender owns its DELIVERY.json; receipts retain only the current event per destination. A pending/unknown receipt blocks another event for that revision until reconciled.
4. Send using the host tool and the returned target/prompt. For Codex task tools use threadId, hostId and prompt; omit model/thinking overrides on existing conversations.
5. record-message --sender ROLE --recipient ROLE --event-id ID --result sent|unknown|not-sent --evidence TEXT records confirmed acceptance, uncertainty or definite pre-delivery rejection. Never report sent without host confirmation. A confirmed sent event cannot be reset to resend.

Lead can dispatch to Workers or Reviewers; each returns completed, blocked or waiting-owner transitions to Lead. For fewer calls, prepare-message callbacks accept --status, --summary, --verification and (for completed review) --verdict: this persists the real result and reserves its notification together. Repeating an unchanged result does not create another event. The separate set-worker-status and set-review-status interfaces also use the current revision. Lead-executed assignments need no artificial round trip. After a blocker is actually resolved and the writer is stopped, control-worker resume creates a fresh ready revision that can be dispatched.

On uncertainty, inspect once. Exact event/revision presence establishes delivery; absence from a partial history window does not establish nondelivery. If still ambiguous, retain the receipt and return the manual continuation. Do not make a retry loop. Older notifications cannot reopen or accept newer work: inspect current repository state and next action first. New review/assignment revisions reject stale writes.

Use passive host waits with cursors, at most 60 seconds per call. One unchanged timeout ends the wait. Result callbacks resume Lead work; do not create periodic polling tasks. Lead verifies actual evidence and continues authorized work without routine Owner approval.

Legacy dispatch-context / record-dispatch / notification-context remain available. New assignment sends check both old and new receipts. Prefer prepare-message / record-message because callbacks then have durable receipts too. Missing tools or bindings retains `$tao continue worker-N`, `$tao continue reviewer-N` and `$tao continue lead` without pretending automation ran.
