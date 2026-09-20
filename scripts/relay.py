"""Local relay packets; the host Agent, never this module, sends messages."""
from __future__ import annotations

import functools
import contextlib
import io
import json
import re
import sys
from pathlib import Path


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def worker(api, runtime, role):
    state = api.load_state(runtime)
    entry = next((w for w in state["workers"] if w["id"] == role), None)
    if entry is None:
        raise api.StateError(f"Unknown Worker: {role}")
    task = api.checked_relative_path(runtime, entry["task_path"], "task_path")
    content = task.read_text(encoding="utf-8")
    match = re.search(r"(?m)^## Assignment revision\s+([1-9][0-9]*)\s*$", content)
    if match is None and "## Assignment revision" in content:
        raise api.StateError("Malformed assignment revision")
    revision = int(match.group(1)) if match else 1  # pre-revision schema-v1 tasks
    status = api.read_json(api.checked_relative_path(runtime, entry["status_path"], "status_path"))
    return state, entry, content, revision, status


def load_transport(api, runtime):
    path = runtime / "TRANSPORT.json"
    if not path.exists():
        return {"schema_version": 1, "project_id": api.load_state(runtime)["project_id"],
                "bindings": {}, "dispatches": {}}
    value = api.read_json(path)
    if (set(value) != {"schema_version", "project_id", "bindings", "dispatches"}
            or value["schema_version"] != 1
            or value["project_id"] != api.load_state(runtime)["project_id"]
            or not isinstance(value["bindings"], dict)
            or not isinstance(value["dispatches"], dict)):
        raise api.StateError("Invalid TRANSPORT.json")
    for role, binding in value["bindings"].items():
        if role != "lead" and not api.WORKER_ID_RE.fullmatch(role) and not api.REVIEWER_ID_RE.fullmatch(role):
            raise api.StateError("Invalid transport role")
        if not isinstance(binding, dict) or set(binding) != {
                "thread_id", "host_id", "cwd", "route_source", "evidence"}:
            raise api.StateError("Invalid thread binding")
        if any(not isinstance(binding[k], str) or not binding[k].strip()
               for k in ("thread_id", "host_id", "cwd")):
            raise api.StateError("Incomplete thread binding")
        if not isinstance(binding["route_source"], str) or binding["route_source"] not in {"owner", "attested", "requested"}:
            raise api.StateError("Invalid route source")
        if binding["route_source"] == "owner" and (
                not isinstance(binding["evidence"], str) or not binding["evidence"].strip()):
            raise api.StateError("Owner binding requires selection/authorization evidence")
    for role, receipt in value["dispatches"].items():
        if (role not in value["bindings"] or not isinstance(receipt, dict)
                or set(receipt) != {"revision", "result", "evidence"}
                or type(receipt["revision"]) is not int or receipt["revision"] < 1
                or not isinstance(receipt["result"], str)
                or receipt["result"] not in {"pending", "sent", "unknown", "not-sent"}
                or not isinstance(receipt["evidence"], str)):
            raise api.StateError("Invalid dispatch receipt")
    return value


def check_attestation(api, receipt, binding):
    """Validate a normalized host receipt, not echoed requested overrides."""
    if not isinstance(receipt, dict):
        raise api.StateError("Missing actual/effective host receipt")
    if receipt.get("runtime_kind", "independent-thread") != "independent-thread":
        raise api.StateError("Automatic subagents are not enabled")
    required = {"thread_id", "host_id", "requested_model", "requested_reasoning",
                "effective_model", "effective_reasoning", "source"}
    if not required <= receipt.keys() or any(
            not isinstance(receipt[k], str) or not receipt[k].strip() for k in required):
        raise api.StateError("Missing actual/effective model or reasoning evidence")
    if (receipt["thread_id"] != binding["thread_id"]
            or receipt["host_id"] != binding["host_id"]
            or receipt["requested_model"] != receipt["effective_model"]
            or receipt["requested_reasoning"] != receipt["effective_reasoning"]):
        raise api.StateError("Contradictory actual/effective route evidence")


def bound(api, runtime, transport, role, *, require_current=True):
    binding = transport["bindings"].get(role)
    if binding is None:
        raise api.StateError(f"No binding for {role}; use the manual handoff")
    if api.WORKER_ID_RE.fullmatch(role):
        worker(api, runtime, role)
    elif require_current and role != "lead" and api.load_state(runtime)["review"]["reviewer_id"] != role:
        raise api.StateError("Reviewer binding is not current")
    if Path(binding["cwd"]).resolve() != runtime.parent.resolve():
        raise api.StateError("Bound thread uses a different project directory")
    if binding["route_source"] == "attested":
        check_attestation(api, binding["evidence"], binding)
    elif binding["route_source"] == "requested":
        check_requested(api, binding["evidence"], binding)
    return binding


def check_observation(api, binding, observation):
    # The Agent extracts these values from a fresh read_thread host response.
    if any(observation.get(k) != binding[k] for k in ("thread_id", "host_id")):
        raise api.StateError("Observed thread does not match binding")
    if not isinstance(observation.get("cwd"), str) or (
            Path(observation["cwd"]).resolve() != Path(binding["cwd"]).resolve()):
        raise api.StateError("Observed thread uses a different project directory")
    if observation.get("status") != "idle":
        raise api.StateError("Target is not idle/available; do not duplicate or interrupt work")


def command_bind(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    if api.load_state(runtime)["status"] == "complete":
        raise api.StateError("Completed project is frozen; reopen before binding")
    if api.WORKER_ID_RE.fullmatch(args.role):
        worker(api, runtime, args.role)
    elif args.role != "lead" and (not api.REVIEWER_ID_RE.fullmatch(args.role) or api.load_state(runtime)["review"]["reviewer_id"] != args.role):
        raise api.StateError("Unknown role or unassigned reviewer")
    if Path(args.cwd).resolve() != runtime.parent.resolve():
        raise api.StateError("Binding must use the same project directory")
    binding = {"thread_id": api.require_nonempty(args.thread_id, "thread-id"),
               "host_id": api.require_nonempty(args.host_id, "host-id"),
               "cwd": str(Path(args.cwd).resolve()), "route_source": args.route_source,
               "evidence": api.require_nonempty(args.evidence, "evidence")}
    if args.route_source == "attested":
        if not args.receipt:
            raise api.StateError("Attested binding requires --receipt from the host")
        binding["evidence"] = api.read_json(Path(args.receipt))
        check_attestation(api, binding["evidence"], binding)
    if args.route_source == "requested":
        if not args.receipt:
            raise api.StateError("Requested route requires independent-thread creation receipt")
        binding["evidence"] = api.read_json(Path(args.receipt))
        check_requested(api, binding["evidence"], binding)
    transport = load_transport(api, runtime)
    for role, other in transport["bindings"].items():
        if role != args.role and (other["host_id"], other["thread_id"]) == (
                binding["host_id"], binding["thread_id"]):
            raise api.StateError("A thread cannot represent two roles")
    old = transport["bindings"].get(args.role)
    if old and old != binding:
        if not args.replace:
            raise api.StateError("Binding exists; verify replacement then use --replace")
        last = transport["dispatches"].get(args.role)
        if last and last["result"] in {"pending", "unknown"}:
            raise api.StateError("Resolve uncertain delivery before replacing binding")
        if api.WORKER_ID_RE.fullmatch(args.role) and worker(api, runtime, args.role)[4]["status"] == "active":
            raise api.StateError("Cannot replace a running Worker's binding")
        if api.REVIEWER_ID_RE.fullmatch(args.role) and api.read_json(runtime / "review/STATUS.json")["status"] == "active":
            raise api.StateError("Cannot replace a running Reviewer's binding")
        for file in [runtime / "DELIVERY.json", runtime / "review/DELIVERY.json", *runtime.glob("workers/*/DELIVERY.json")]:
            if file.exists() and any(r["result"] in {"pending", "unknown"} and
                                     (r["sender"] == args.role or r["recipient"] == args.role)
                                     for r in api.read_json(file).get("messages", {}).values()):
                raise api.StateError("Resolve uncertain delivery before replacing binding")
    transport["bindings"][args.role] = binding
    api.atomic_write_json(runtime / "TRANSPORT.json", transport)
    emit({"bound": args.role, "thread_id": args.thread_id})
    return 0


def command_context(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state = api.load_state(runtime)
    result = {"project_root": str(runtime.parent), "phase": state["phase"],
              "project_status": state["status"], "next_action": state["next_action"],
              "command_argv": [sys.executable, str(api.SKILL_ROOT / "scripts" / "statectl.py")],
              "directives_path": str(runtime / "OWNER_DIRECTIVES.md")}
    if args.role == "lead":
        snapshot = api.status_snapshot(runtime, validate=False)
        active = [w for w in snapshot["workers"] if w["status"] in api.ACTIVE_WORKER_STATUSES]
        terminal = len(snapshot["workers"]) - len(active)
        offset, limit = args.offset, args.limit
        if offset < 0 or limit < 1 or limit > 100:
            raise api.StateError("Use offset >= 0 and limit 1..100")
        for w in active:
            w.pop("verification", None)
            w["summary"] = w["summary"][:400]
            entry = next(e for e in state["workers"] if e["id"] == w["id"])
            w["executor"] = entry.get("executor", "worker")
            w["task_path"] = entry["task_path"]
        snapshot["workers"] = active[offset:offset + limit]
        result["status"] = snapshot
        result["worker_page"] = {"active_total": len(active), "terminal_total": terminal,
                                 "offset": offset, "next_offset": offset + limit if offset + limit < len(active) else None}
        result["handoff"] = (runtime / "HANDOFF.md").read_text(encoding="utf-8")
        result["plan_path"] = str(runtime / "PLAN.md")
        events = api.pending_owner_events(runtime)
        result["pending_events"] = [str(p) for p in events[offset:offset + limit]]
        result["event_page"] = {"total": len(events), "next_offset": offset + limit if offset + limit < len(events) else None}
        from workspace import load, due
        result["housekeeping_due"] = due(load(api, runtime)) if state["status"] != "complete" else False
    elif args.role.startswith("reviewer-"):
        if not state["review"]["required"] or state["review"]["reviewer_id"] != args.role:
            raise api.StateError("Reviewer is not assigned")
        result["task"] = (runtime / "review" / "TASK.md").read_text(encoding="utf-8")
        result["status"] = api.read_json(runtime / "review" / "STATUS.json")
        revision = result["status"].get("revision", 1)
        if args.assignment_revision is not None and args.assignment_revision != revision:
            raise api.StateError("Stale review message")
        result["assignment_revision"] = revision
    else:
        _, entry, task, revision, status = worker(api, runtime, args.role)
        if args.assignment_revision is not None and args.assignment_revision != revision:
            raise api.StateError("Stale assignment message; do not execute it")
        result.update(task=task, assignment_revision=revision, status=status)
        result["executor"] = entry.get("executor", "worker")
        result["dependencies"] = {
            role: worker(api, runtime, role)[4]["status"] for role in entry["depends_on"]}
        if status["status"] in {"blocked", "waiting-owner"}:
            path = api.checked_relative_path(runtime, entry["task_path"], "task_path").parent
            result["blocker"] = (path / "BLOCKER.md").read_text(encoding="utf-8")
    emit(result)
    return 0


def dispatch_packet(api, runtime, role, observation):
    api.assert_valid(runtime)
    state, entry, _, revision, status = worker(api, runtime, role)
    if state["status"] != "active" or state["phase"] != "execution":
        raise api.StateError("Dispatch requires active execution; resolve project decisions first")
    if status["status"] != "ready":
        raise api.StateError("Dispatch requires a ready Worker; running/blocked work is not resent")
    if entry.get("executor", "worker") != "worker":
        raise api.StateError("Lead-owned assignment cannot be dispatched to Worker")
    if api.pending_owner_events(runtime):
        raise api.StateError("Resolve pending Owner feedback before dispatch")
    for dependency in entry["depends_on"]:
        if worker(api, runtime, dependency)[4]["status"] != "completed":
            raise api.StateError(f"Dependency not completed: {dependency}")
    transport = load_transport(api, runtime)
    target = bound(api, runtime, transport, role)
    lead = bound(api, runtime, transport, "lead")
    check_observation(api, target, observation)
    previous = transport["dispatches"].get(role)
    if previous and previous["revision"] == revision and previous["result"] != "not-sent":
        raise api.StateError("Assignment already sent or uncertain; reconcile once, do not resend")
    delivery = load_delivery(api, runtime, "lead")["messages"].get(role)
    if delivery and delivery["revision"] == revision and delivery["result"] != "not-sent":
        raise api.StateError("Assignment already sent or uncertain")
    return {"worker_id": role, "assignment_revision": revision,
            "thread_id": target["thread_id"], "host_id": target["host_id"],
            "prompt": (f"$tao continue {role}\nProject: {runtime.parent}\n"
                       f"CLI executable/script argv: {json.dumps([sys.executable, str(api.SKILL_ROOT / 'scripts' / 'statectl.py')])}\n"
                       f"Assignment revision: {revision}. Use context --assignment-revision {revision} "
                       "through the explicit Python interpreter before acting; ignore stale messages. "
                       "Read the current TASK.md and directives, execute within scope, validate, "
                       "and update only Worker-owned state. At a completion/blocker transition, use "
                       "notification-context and send its packet once to the bound Lead. "
                       f"Lead task: {lead['thread_id']} (host {lead['host_id']}).")}


def command_dispatch_context(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    emit(dispatch_packet(api, runtime, args.worker_id, api.read_json(Path(args.observation))))
    return 0


def command_record_dispatch(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state, _, _, revision, _ = worker(api, runtime, args.worker_id)
    if state["status"] == "complete":
        raise api.StateError("Completed project is frozen")
    if args.assignment_revision != revision:
        raise api.StateError("Stale assignment receipt")
    transport = load_transport(api, runtime)
    binding = bound(api, runtime, transport, args.worker_id)
    if args.thread_id != binding["thread_id"]:
        raise api.StateError("Receipt target differs from bound thread")
    last = transport["dispatches"].get(args.worker_id)
    if args.result == "pending":
        if not args.observation:
            raise api.StateError("Reserve delivery with a fresh --observation")
        dispatch_packet(api, runtime, args.worker_id, api.read_json(Path(args.observation)))
    elif not last or last["revision"] != revision:
        raise api.StateError("Reserve pending delivery before recording its result")
    elif last["result"] == "sent" and args.result != "sent":
        raise api.StateError("Confirmed delivery cannot be reset or retried")
    evidence = api.require_nonempty(args.evidence, "evidence")
    transport["dispatches"][args.worker_id] = {
        "revision": revision, "result": args.result, "evidence": evidence}
    api.atomic_write_json(runtime / "TRANSPORT.json", transport)
    emit({"worker_id": args.worker_id, "assignment_revision": revision, "result": args.result})
    return 0


def command_notification_context(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state, _, _, revision, status = worker(api, runtime, args.worker_id)
    if revision != args.assignment_revision:
        raise api.StateError("Stale assignment notification")
    if state["status"] == "complete" or status["status"] not in {"completed", "blocked", "waiting-owner"}:
        raise api.StateError("No actionable Worker transition to notify")
    target = bound(api, runtime, load_transport(api, runtime), "lead")
    event = f"{args.worker_id}:{revision}:{status['status']}:{status['last_updated']}"
    emit({"thread_id": target["thread_id"], "host_id": target["host_id"], "event_id": event,
          "prompt": (f"$tao continue lead\nProject: {runtime.parent}\nWorker event: {event}\n"
                     "Read current repository status. Ignore an older assignment revision or a "
                     "transition already handled; a notification is not acceptance evidence. "
                     "Resolve the blocker or verify completion, then reuse the Worker when suitable.")})
    return 0


def add_commands(subparsers, api):
    def parser(name, function, help_text):
        value = subparsers.add_parser(name, help=help_text)
        api.common_project_root(value)
        value.set_defaults(func=functools.partial(function, api=api))
        return value

    p = parser("bind-thread", command_bind, "PROJECT_LEAD: bind an explicitly selected host task")
    p.add_argument("--role", required=True)
    p.add_argument("--thread-id", required=True)
    p.add_argument("--host-id", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--route-source", choices=["owner", "attested", "requested"], required=True)
    p.add_argument("--evidence", required=True, help="Owner selection/authorization or host receipt source")
    p.add_argument("--receipt", help="Normalized actual/effective host receipt JSON")
    p.add_argument("--replace", action="store_true")
    p = parser("context", command_context, "Read a compact role packet without history or editor UI")
    p.add_argument("--role", required=True)
    p.add_argument("--assignment-revision", type=int)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=20)
    p = parser("dispatch-context", command_dispatch_context, "Read a validated short Worker message")
    p.add_argument("--worker-id", required=True)
    p.add_argument("--observation", required=True, help="Fresh normalized host thread observation JSON")
    p = parser("record-dispatch", command_record_dispatch, "PROJECT_LEAD: reserve/record one delivery")
    p.add_argument("--worker-id", required=True)
    p.add_argument("--thread-id", required=True)
    p.add_argument("--assignment-revision", type=int, required=True)
    p.add_argument("--result", choices=["pending", "sent", "unknown", "not-sent"], required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--observation")
    p = parser("prepare-message", command_prepare_message, "Reserve and emit one role message")
    p.add_argument("--sender", required=True)
    p.add_argument("--recipient", required=True)
    p.add_argument("--assignment-revision", required=True, type=int)
    p.add_argument("--observation", help="Fresh host target observation JSON")
    p.add_argument("--status", choices=["completed", "blocked", "waiting-owner"], help="Persist callback result in this same call")
    p.add_argument("--summary")
    p.add_argument("--verification", action="append")
    p.add_argument("--files-changed", action="append")
    p.add_argument("--next-action", default="")
    p.add_argument("--verdict", choices=["approved", "changes-requested"])
    p = parser("record-message", command_record_message, "Record a reserved host delivery outcome")
    p.add_argument("--sender", required=True)
    p.add_argument("--recipient", required=True)
    p.add_argument("--event-id", required=True)
    p.add_argument("--result", required=True, choices=["sent", "unknown", "not-sent"])
    p.add_argument("--evidence", required=True)
    p = parser("notification-context", command_notification_context, "Read a Lead notification; no state writes")
    p.add_argument("--worker-id", required=True)
    p.add_argument("--assignment-revision", type=int, required=True)


def check_requested(api, receipt, binding):
    if not isinstance(receipt, dict) or receipt.get("runtime_kind") != "independent-thread":
        raise api.StateError("Automatic subagents are not enabled; independent-thread receipt required")
    for key in ("thread_id", "host_id", "requested_model", "requested_reasoning", "source"):
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise api.StateError("Missing explicit model/reasoning creation evidence")
    if any(receipt[k] != binding[k] for k in ("thread_id", "host_id")):
        raise api.StateError("Creation receipt identity mismatch")
    for field in ("model", "reasoning"):
        effective = receipt.get("effective_" + field)
        if effective is not None and effective != receipt["requested_" + field]:
            raise api.StateError("Contradictory actual/effective route evidence")


def delivery_path(api, runtime, sender):
    if sender == "lead":
        return runtime / "DELIVERY.json"
    if api.WORKER_ID_RE.fullmatch(sender):
        worker(api, runtime, sender)
        return runtime / "workers" / sender / "DELIVERY.json"
    if api.REVIEWER_ID_RE.fullmatch(sender) and api.load_state(runtime)["review"]["reviewer_id"] == sender:
        return runtime / "review/DELIVERY.json"
    raise api.StateError("Unknown or unassigned sender")


def load_delivery(api, runtime, sender):
    path = delivery_path(api, runtime, sender)
    if not path.exists():
        return {"schema_version": 1, "messages": {}}
    value = api.read_json(path)
    if value.get("schema_version") != 1 or not isinstance(value.get("messages"), dict):
        raise api.StateError("Invalid delivery records")
    for target, record in value["messages"].items():
        if (not isinstance(record, dict) or not {"event_id", "sender", "recipient", "revision", "result", "thread_id", "host_id", "evidence"} <= set(record)
                or record["result"] not in {"pending", "unknown", "sent", "not-sent"}
                or record["recipient"] != target or type(record["revision"]) is not int):
            raise api.StateError("Invalid message receipt")
    return value


def role_status(api, runtime, role):
    if api.WORKER_ID_RE.fullmatch(role):
        _, entry, _, revision, status = worker(api, runtime, role)
        return revision, status, entry.get("executor", "worker")
    if api.REVIEWER_ID_RE.fullmatch(role) and api.load_state(runtime)["review"]["reviewer_id"] == role:
        status = api.read_json(runtime / "review/STATUS.json")
        return status.get("revision", 1), status, "reviewer"
    raise api.StateError("Unknown or unassigned role")


def command_prepare_message(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state = api.load_state(runtime)
    if state["status"] == "complete":
        raise api.StateError("Completed project is frozen")
    if args.sender == args.recipient or (args.sender != "lead" and args.recipient != "lead"):
        raise api.StateError("Messages are between Lead and one assigned role")
    role = args.recipient if args.sender == "lead" else args.sender
    revision, status, executor = role_status(api, runtime, role)
    if revision != args.assignment_revision:
        raise api.StateError("Stale assignment message")
    if executor == "lead":
        raise api.StateError("Lead executes this assignment directly; no Worker round trip")
    transport = load_transport(api, runtime)
    bound(api, runtime, transport, args.sender)
    target = bound(api, runtime, transport, args.recipient)
    if args.status:
        if args.sender == "lead" or args.recipient != "lead" or not args.summary:
            raise api.StateError("Combined status is a result callback with a nonempty --summary")
        # Reuse the owned status APIs. A later host failure never undoes a real result.
        with contextlib.redirect_stdout(io.StringIO()):
            if api.WORKER_ID_RE.fullmatch(role):
                args.worker_id, args.actor = role, "worker"
                api.command_set_worker_status(args)
            else:
                if args.status == "waiting-owner":
                    raise api.StateError("Reviewers use blocked for missing Owner input")
                args.reviewer_id = role
                api.command_set_review_status(args)
        revision, status, executor = role_status(api, runtime, role)
    if args.sender == "lead":
        if state["status"] != "active" or status["status"] != "ready" or api.pending_owner_events(runtime):
            raise api.StateError("Dispatch needs ready work, active project and resolved Owner decisions")
        if api.WORKER_ID_RE.fullmatch(role):
            if state["phase"] != "execution":
                raise api.StateError("Worker dispatch requires execution phase")
            entry = worker(api, runtime, role)[1]
            if any(worker(api, runtime, d)[4]["status"] != "completed" for d in entry["depends_on"]):
                raise api.StateError("Dependency not completed")
        elif state["phase"] != "review":
            raise api.StateError("Reviewer dispatch requires review phase")
        if not args.observation:
            raise api.StateError("Fresh --observation required for assignment delivery")
        check_observation(api, target, api.read_json(Path(args.observation)))
        legacy = transport["dispatches"].get(role)
        if legacy and legacy["revision"] == revision and legacy["result"] != "not-sent":
            raise api.StateError("Assignment already sent or uncertain in legacy receipt")
    elif status["status"] not in {"completed", "blocked", "waiting-owner"}:
        raise api.StateError("No actionable result transition")
    event = f"{role}:{revision}:{status['status']}:{status['last_updated']}"
    value = load_delivery(api, runtime, args.sender)
    previous = value["messages"].get(args.recipient)
    if previous and previous["revision"] == revision:
        if previous["result"] in {"pending", "unknown"} or (previous["event_id"] == event and previous["result"] == "sent"):
            raise api.StateError("Message already sent or uncertain; reconcile once, do not resend")
    receipt = {"event_id": event, "sender": args.sender, "recipient": args.recipient,
               "revision": revision, "result": "pending", "thread_id": target["thread_id"],
               "host_id": target["host_id"], "evidence": "Reserved before host call"}
    value["messages"][args.recipient] = receipt
    api.atomic_write_json(delivery_path(api, runtime, args.sender), value)
    prompt = (f"$tao continue {args.recipient}\nProject: {runtime.parent}\nEvent: {event}\n"
              f"CLI argv: {json.dumps([sys.executable, str(api.SKILL_ROOT / 'scripts/statectl.py')])}\n")
    if args.sender == "lead":
        prompt += (f"Use context --role {role} --assignment-revision {revision}; ignore stale work. "
                   "Execute the current assignment, validate, then prepare-message to lead once on a result transition.")
    else:
        prompt += "Read current state; ignore stale or handled events. Verify the result or resolve the blocker, then continue authorized work."
    verified = target["route_source"] == "attested" or (
        target["route_source"] == "requested" and
        all(target["evidence"].get("effective_" + field) for field in ("model", "reasoning")))
    emit(dict(receipt, prompt=prompt, route_verified=bool(verified)))
    return 0


def command_record_message(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    if api.load_state(runtime)["status"] == "complete":
        raise api.StateError("Completed project is frozen")
    value = load_delivery(api, runtime, args.sender)
    receipt = value["messages"].get(args.recipient)
    if not receipt or receipt["event_id"] != args.event_id or receipt["sender"] != args.sender:
        raise api.StateError("No matching reserved message")
    role = args.recipient if args.sender == "lead" else args.sender
    if role_status(api, runtime, role)[0] != receipt["revision"]:
        raise api.StateError("Stale delivery receipt")
    if receipt["result"] == "sent" and args.result != "sent":
        raise api.StateError("Confirmed delivery cannot be reset")
    receipt.update(result=args.result, evidence=api.require_nonempty(args.evidence, "evidence"))
    api.atomic_write_json(delivery_path(api, runtime, args.sender), value)
    emit(receipt)
    return 0
