"""Lead controls for stopped assignments; statectl owns persistence and validation."""
from __future__ import annotations

import functools
import json
import re


def check_control_revision(api, runtime, args, entry):
    from relay import worker
    expected = getattr(args, "assignment_revision", None)
    if "executor" in entry and expected is None:
        raise api.StateError("Current --assignment-revision is required for controlled assignments")
    if expected is not None and worker(api, runtime, entry["id"])[3] != expected:
        raise api.StateError("Stale assignment revision")


def require_quiescence(api, args):
    return api.require_nonempty(getattr(args, "quiescence_evidence", None) or "",
                                "quiescence-evidence (confirmed stopped writer, not a timeout)")


def command_control_worker(args, api):
    from relay import worker
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state, entry, task, revision, status = worker(api, runtime, args.worker_id)
    if state["status"] == "complete":
        raise api.StateError("Completed project is frozen; reopen it first")
    if args.assignment_revision != revision:
        raise api.StateError("Stale assignment revision")
    reason = api.require_nonempty(args.reason, "reason")
    evidence = require_quiescence(api, args)
    if status["status"] == "completed":
        raise api.StateError("Use reassign-worker for a completed assignment")
    if state["review"]["reviewer_id"] is not None:
        raise api.StateError("Cancel or finish the assigned review before controlling execution")
    if args.action == "resume" and status["status"] not in {"blocked", "waiting-owner", "inactive"} and entry.get("executor") != "lead":
        raise api.StateError("Resume requires resolved blocked/stopped work or a Lead handback")
    if args.action == "cancel":
        for other in state["workers"]:
            if args.worker_id in other["depends_on"] and worker(api, runtime, other["id"])[4]["status"] == "active":
                raise api.StateError("Stop active dependents before cancelling their input")
    directory = api.checked_relative_path(runtime, entry["task_path"], "task").parent
    old_files = {name: (directory / name).read_text(encoding="utf-8")
                 for name in ("TASK.md", "STATUS.json", "BLOCKER.md")}
    heading = f"## Assignment revision\n\n{revision + 1}"
    if re.search(r"(?m)^## Assignment revision\s+[1-9][0-9]*\s*$", task):
        task = re.sub(r"(?m)^## Assignment revision\s+[1-9][0-9]*\s*$", heading + "\n", task)
    else:
        task += "\n\n" + heading + "\n"
    task = task.split("\n## Latest control\n", 1)[0].rstrip()
    task += f"\n\n## Latest control\n\n{args.action}: {reason}\nStopped writer: {evidence}\n"
    status.update(status="inactive" if args.action == "cancel" else "ready",
                  next_action=reason, last_updated=api.utc_now())
    marker = {"schema_version": 1, "worker_id": args.worker_id,
              "archived_revision": revision, "new_revision": revision + 1,
              "old_files": old_files,
              "new_files": {"TASK.md": task, "STATUS.json": json.dumps(status, indent=2) + "\n",
                            "BLOCKER.md": old_files["BLOCKER.md"]},
              "write_scope": entry["write_scope"], "depends_on": entry["depends_on"],
              "milestone": state["current_milestone"], "invalidate_review": False,
              "executor": "lead" if args.action == "takeover" else "worker", "reason": reason}
    # Validate before publishing a recovery marker as well as during recovery.
    proposed = json.loads(json.dumps(state))
    next(w for w in proposed["workers"] if w["id"] == args.worker_id)["executor"] = marker["executor"]
    changes = {f"workers/{args.worker_id}/{name}": text for name, text in marker["new_files"].items()}
    changes["STATE.json"] = json.dumps(proposed)
    api.validate_candidate(runtime, changes)
    path = directory / api.REASSIGNMENT_MARKER
    api.atomic_write_json(path, marker)
    api.recover_reassignment(runtime, path)
    print(f"{args.action}: {args.worker_id} revision {revision + 1}, executor {marker['executor']}")
    return 0


def recover_control_review(api, runtime):
    path = runtime / "review" / ".cancel-review.json"
    if not path.exists():
        return
    marker = api.read_json(path)
    if set(marker) != {"old", "new", "archive_revision"}:
        raise api.StateError("Invalid review cancellation marker")
    allowed = {"STATE.json", "review/TASK.md", "review/STATUS.json", "review/REPORT.md"}
    if set(marker["old"]) != allowed or set(marker["new"]) != allowed:
        raise api.StateError("Invalid review cancellation paths")
    for name in allowed:
        if (runtime / name).read_text(encoding="utf-8") not in {marker["old"][name], marker["new"][name]}:
            raise api.StateError("Review cancellation conflicts with newer content")
    api.validate_candidate(runtime, marker["new"])
    api.write_review_archive(runtime, marker["archive_revision"],
                             {name: marker["old"]["review/" + name]
                              for name in ("TASK.md", "STATUS.json", "REPORT.md")})
    for name, content in marker["new"].items():
        api.atomic_write_text(runtime / name, content)
    api.assert_valid(runtime)
    path.unlink()


def command_cancel_review(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    state = api.load_state(runtime)
    if state["status"] == "complete" or state["review"]["reviewer_id"] is None:
        raise api.StateError("No cancellable review in an active project")
    status = api.read_json(runtime / "review/STATUS.json")
    if args.assignment_revision != status.get("revision", 1):
        raise api.StateError("Stale review revision")
    reason = api.require_nonempty(args.reason, "reason")
    evidence = require_quiescence(api, args)
    old = {name: (runtime / name).read_text(encoding="utf-8")
           for name in ("STATE.json", "review/TASK.md", "review/STATUS.json", "review/REPORT.md")}
    state["review"]["reviewer_id"] = None
    state.update(phase="execution", status="active", last_updated=api.utc_now(),
                 next_action={"actor": "project-lead", "instruction": reason})
    status.update(reviewer_id=None, status="not-requested", verdict=None,
                  revision=status.get("revision", 1) + 1, summary=f"{reason}; stopped reviewer: {evidence}", last_updated=api.utc_now())
    new = dict(old, **{"STATE.json": json.dumps(state, indent=2) + "\n",
                      "review/STATUS.json": json.dumps(status, indent=2) + "\n"})
    api.validate_candidate(runtime, new)
    api.atomic_write_json(runtime / "review/.cancel-review.json",
                          {"old": old, "new": new,
                           "archive_revision": max(api.review_history_revisions(runtime), default=0) + 1})
    recover_control_review(api, runtime)
    print("Review cancelled; execution resumed and review requirement retained")
    return 0


def add_commands(subparsers, api):
    for name, function in (("control-worker", command_control_worker), ("cancel-review", command_cancel_review)):
        p = subparsers.add_parser(name, help="Lead: safely change stopped execution")
        api.common_project_root(p)
        p.add_argument("--assignment-revision", required=True, type=int)
        p.add_argument("--reason", required=True)
        p.add_argument("--quiescence-evidence", required=True)
        if name == "control-worker":
            p.add_argument("--worker-id", required=True)
            p.add_argument("--action", required=True, choices=["resume", "cancel", "takeover"])
        p.set_defaults(func=functools.partial(function, api=api))
