# One Reusable Worker

1. Lead chooses direct execution or a durable assignment based on total cost. For sustained work, initialize state, write stable criteria, add worker-1 and enter execution.
2. Bind independent Lead/Worker conversations once. If creation is unavailable, give the Owner one setup instruction. Do not substitute an automatic subagent.
3. Use prepare-message, host send and record-message. Worker reads revision-specific context, executes in scope and records actual validation.
4. Worker sends a result transition through the same prepare/send/record workflow. Lead verifies the result and proceeds autonomously within existing authority.
5. If blocked, Lead resolves the cause and confirms execution/process quiescence, then uses control-worker resume or takeover. No fake completion is needed. A stopped assignment can also be cancelled and reassigned.
6. At the milestone, register/release appropriate generated run directories and apply housekeeping. Evidence stays usable; temporary artifacts enter quarantine. Reassign the same completed Worker for M2.
7. If useful review is required, bind reviewer-1 and use the same message loop. Only a current approved verdict satisfies required review. Cancel a blocked review to fix implementation, then reassign it.
8. Verify final criteria, perform the final tidy, update the bounded handoff and complete the project. Future read-only questions leave it frozen; actionable changes use reopen-project.

From the repository:

```console
python scripts/statectl.py context --project-root /path/to/project --role lead
python scripts/statectl.py control-worker --help
python scripts/statectl.py prepare-message --help
python scripts/statectl.py workspace-register --help
python scripts/statectl.py housekeep --project-root /path/to/project
```

Installed usage follows the explicit interpreter and absolute script path in [SKILL.md](../SKILL.md). Details: [state](../references/runtime-state.md), [messages](../references/host-dispatch.md), [housekeeping](../references/workspace-housekeeping.md).
