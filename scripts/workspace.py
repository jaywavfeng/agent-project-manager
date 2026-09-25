"""Opt-in artifact housekeeping. No project-wide crawling or inferred deletion."""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath


PROTECTED = {".git", ".agent-project-manager", ".venv", "venv", "node_modules", "src", "source",
             "data", "raw", "inputs", "evidence", "deliverables", "secrets", ".ssh"}


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load(api, runtime):
    path = runtime / "WORKSPACE.json"
    if not path.exists():
        return {"schema_version": 1, "interval_days": 7, "quarantine_days": 7,
                "last_checked": None, "artifacts": []}
    value = api.read_json(path)
    if (value.get("schema_version") != 1 or value.get("interval_days") != 7
            or value.get("quarantine_days") != 7 or not isinstance(value.get("artifacts"), list)):
        raise api.StateError("Invalid WORKSPACE.json")
    if value.get("last_checked") is not None and not api.check_timestamp(value["last_checked"]):
        raise api.StateError("Invalid workspace check timestamp")
    seen = set()
    for item in value["artifacts"]:
        if not isinstance(item, dict) or not {"path", "producer", "revision", "kind", "reproduce", "movable", "released", "evidence", "state"} <= set(item):
            raise api.StateError("Invalid artifact registration")
        if item["path"] in seen or item["kind"] not in {"temporary", "intermediate", "deliverable", "evidence"}:
            raise api.StateError("Duplicate path or invalid artifact kind")
        seen.add(item["path"])
        if item["state"] not in {"current", "quarantined", "archived", "deleted"}:
            raise api.StateError("Invalid artifact lifecycle")
        if type(item["revision"]) is not int or item["revision"] < 1:
            raise api.StateError("Invalid artifact revision")
        if type(item["movable"]) is not bool or type(item["released"]) is not bool:
            raise api.StateError("Invalid artifact declaration")
    return value


def due(value):
    return value["last_checked"] is None or now() - parse_time(value["last_checked"]) >= timedelta(days=7)


def checked_path(api, root, relative, *, storage=False):
    pure = PurePosixPath(relative)
    if (not relative or pure.is_absolute() or relative != pure.as_posix()
            or any(p in {"..", "."} or ":" in p or "\\" in p for p in pure.parts)):
        raise api.StateError("Artifact path must be normalized and project-relative")
    if not pure.parts:
        raise api.StateError("Project root is not an artifact")
    if not storage and any(part.lower() in PROTECTED for part in pure.parts):
        raise api.StateError("Protected source/data/runtime directory")
    if storage and (len(pure.parts) < 4 or pure.parts[:2] != (".agent-project-manager", "storage")
                    or not re.fullmatch(r"[0-9a-f]{32}", pure.parts[2]) or pure.parts[3] != "content"):
        raise api.StateError("Invalid storage location")
    current = root.resolve()
    for part in pure.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise api.StateError("Symlinks and reparse points are not managed artifacts")
    try:
        current.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise api.StateError("Artifact escapes project") from exc
    return current


def snapshot(api, root, relative, *, storage=False):
    path = checked_path(api, root, relative, storage=storage)
    if not path.is_dir():
        raise api.StateError("Registered artifact directory is missing")
    result = []
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in [".", *dirs, *files]:
            child = Path(directory) if name == "." else Path(directory) / name
            rel = child.relative_to(root).as_posix()
            checked_path(api, root, rel, storage=storage)
            info = child.lstat()
            if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                raise api.StateError("Special filesystem entries cannot be cleaned")
            result.append([child.relative_to(path).as_posix(), info.st_size if child.is_file() else 0,
                           info.st_mtime_ns, info.st_ino])
            if len(result) > 10000:
                raise api.StateError("Artifact exceeds bounded 10000-entry inspection; split registration")
    return sorted(result)


def tracked(api, root, relative):
    if not (root / ".git").exists():
        return False
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", relative],
                                capture_output=True, check=False)
    except OSError as exc:
        raise api.StateError("Cannot establish tracked-file protection") from exc
    if result.returncode:
        raise api.StateError("Cannot establish tracked-file protection")
    return bool(result.stdout)


def eligible(api, runtime, item):
    from relay import worker
    if item["kind"] in {"deliverable", "evidence"}:
        return "protected deliverable/evidence"
    if not item["released"] or not item["evidence"]:
        return "producer/process release is not confirmed"
    if item["kind"] == "temporary" and not item["reproduce"].strip():
        return "no regeneration method"
    if item["kind"] == "intermediate" and not item["movable"]:
        return "moving is not authorized"
    if item["producer"] != "lead":
        try:
            _, _, _, revision, status = worker(api, runtime, item["producer"])
        except api.StateError:
            return "producer not found"
        if revision < item["revision"]:
            return "producer revision not reached"
        if revision == item["revision"] and status["status"] not in {"completed", "inactive"}:
            return "producer is active"
    state = api.load_state(runtime)
    for entry in state["workers"]:
        _, _, task, _, status = worker(api, runtime, entry["id"])
        if any(item["path"] in evidence for evidence in status["verification"]):
            return "current verification reference"
        if status["status"] not in api.ACTIVE_WORKER_STATUSES:
            continue
        if any(api.scopes_overlap(item["path"] + "/**", scope) for scope in entry["write_scope"]):
            return "active write scope"
        # Read dependencies and exclusions are ordinary Markdown list entries.
        for line in task.splitlines():
            if line.startswith("- ") and api.scopes_overlap(item["path"] + "/**", line[2:].strip("` ")):
                return "active task dependency"
        if item["path"] in task:
            return "active task reference"
    for name in ("PLAN.md", "HANDOFF.md", "OWNER_DIRECTIVES.md"):
        if item["path"] in (runtime / name).read_text(encoding="utf-8"):
            return "current canonical reference"
    if state["review"]["reviewer_id"] is not None:
        review_status = api.read_json(runtime / "review/STATUS.json")
        if review_status["status"] != "completed":
            return "review is active"
        if any(item["path"] in evidence for evidence in review_status["verification"]):
            return "current review verification reference"
        for name in ("TASK.md", "REPORT.md"):
            if item["path"] in (runtime / "review" / name).read_text(encoding="utf-8"):
                return "current review reference"
    if api.pending_owner_events(runtime):
        return "unresolved Owner feedback"
    for path in [runtime / "DELIVERY.json", runtime / "review/DELIVERY.json",
                 *runtime.glob("workers/*/DELIVERY.json")]:
        if path.exists() and any(x.get("result") in {"pending", "unknown"}
                                 for x in api.read_json(path).get("messages", {}).values()):
            return "uncertain delivery"
    return None


def command_register(args, api):
    from relay import worker
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    if api.load_state(runtime)["status"] == "complete":
        raise api.StateError("Completed project is frozen")
    root = runtime.parent
    checked_path(api, root, args.path)
    if not (root / args.path).is_dir():
        raise api.StateError("Register an existing task/run directory")
    if args.producer != "lead" and worker(api, runtime, args.producer)[3] != args.assignment_revision:
        raise api.StateError("Stale producer revision")
    if args.assignment_revision < 1:
        raise api.StateError("Positive assignment revision required")
    if args.kind == "temporary" and not (args.reproduce or "").strip():
        raise api.StateError("Temporary artifacts require --reproduce")
    if args.released and not (args.quiescence_evidence or "").strip():
        raise api.StateError("Released artifacts require confirmed process/consumer quiescence")
    value = load(api, runtime)
    item = {"path": args.path, "producer": args.producer, "revision": args.assignment_revision,
            "kind": args.kind, "reproduce": args.reproduce or "", "movable": args.movable,
            "released": args.released, "evidence": args.quiescence_evidence or "", "state": "current"}
    for existing in value["artifacts"]:
        if existing["path"] == args.path:
            if existing["state"] != "current":
                raise api.StateError("Restore the existing artifact before changing its registration")
            if existing["kind"] in {"deliverable", "evidence"} and item["kind"] not in {"deliverable", "evidence"}:
                raise api.StateError("Cannot downgrade protected evidence/deliverables")
            existing.update(item)
            break
        if api.scopes_overlap(existing["path"], args.path):
            raise api.StateError("Overlapping artifact registrations")
    else:
        value["artifacts"].append(item)
    api.atomic_write_json(runtime / "WORKSPACE.json", value)
    print(json.dumps(item, ensure_ascii=False))
    return 0


def archive_feedback(api, runtime):
    moved = 0
    for path in sorted((runtime / "inbox/owner").glob("*.md")):
        content = path.read_text(encoding="utf-8")
        if api.owner_event_frontmatter(content, path)[1] != "resolved":
            continue
        destination = path.parent / "history" / path.name
        if destination.exists():
            if destination.read_text(encoding="utf-8") != content:
                raise api.StateError("Archived feedback conflicts with current content")
            path.unlink()
        else:
            destination.parent.mkdir(exist_ok=True)
            path.rename(destination)
        moved += 1
    return moved


def recover_moves(api, runtime, value):
    """Reconcile only recorded moves; ambiguous content is left untouched."""
    for batch in sorted((runtime / "storage").glob("*/operation.json")):
        op = api.read_json(batch)
        if op.get("result") == "restoring":
            target = checked_path(api, runtime.parent, op["path"])
            source = checked_path(api, runtime.parent, op["destination"], storage=True)
            if target.exists() and not source.exists():
                item = next(i for i in value["artifacts"] if i["path"] == op["path"])
                item.update(state="current", released=False, evidence="")
                item.pop("location", None)
                item.pop("stored_at", None)
                api.atomic_write_json(runtime / "WORKSPACE.json", value)
                op["result"] = "restored"
                api.atomic_write_json(batch, op)
            elif source.exists() and not target.exists():
                op["result"] = "moved"
                api.atomic_write_json(batch, op)
            else:
                raise api.StateError("Ambiguous interrupted restore; inspect both locations")
            continue
        if op.get("result") == "deleting":
            source = checked_path(api, runtime.parent, op["destination"], storage=True)
            if not source.exists():
                item = next(i for i in value["artifacts"] if i["path"] == op["path"])
                item["state"] = "deleted"
                api.atomic_write_json(runtime / "WORKSPACE.json", value)
                op["result"] = "deleted"
                api.atomic_write_json(batch, op)
            continue
        if op.get("result") != "pending":
            continue
        source = checked_path(api, runtime.parent, op["path"])
        destination = checked_path(api, runtime.parent, op["destination"], storage=True)
        if source.exists() and destination.exists():
            raise api.StateError("Both sides of a pending housekeeping move exist; inspect before recovery")
        if destination.exists() and not source.exists():
            item = next((i for i in value["artifacts"] if i["path"] == op["path"]), None)
            if item is None:
                raise api.StateError("Pending move lost its registration")
            item.update(state=op["state"], location=op["destination"], stored_at=op["time"])
            op["result"] = "moved"
            api.atomic_write_json(runtime / "WORKSPACE.json", value)
        elif source.exists():
            op["result"] = "not-moved"
        else:
            raise api.StateError("Both sides of a pending housekeeping move are missing")
        api.atomic_write_json(batch, op)


def command_housekeep(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    value = load(api, runtime)
    if args.apply and api.load_state(runtime)["status"] == "complete":
        raise api.StateError("Completed project is frozen; housekeeping runs before completion")
    if args.apply:
        recover_moves(api, runtime, value)
    if args.if_due and not due(value):
        print(json.dumps({"due": False, "candidates": []}))
        return 0
    candidates, skipped = [], []
    for item in value["artifacts"]:
        if item["state"] not in {"current", "quarantined"}:
            continue
        reason = eligible(api, runtime, item)
        if reason:
            skipped.append({"path": item["path"], "reason": reason})
            continue
        if item["state"] == "quarantined" and now() - parse_time(item["stored_at"]) < timedelta(days=7):
            continue
        relative = item.get("location", item["path"])
        try:
            observed = snapshot(api, runtime.parent, relative, storage=item["state"] != "current")
            if item["state"] == "quarantined":
                op = api.read_json((runtime.parent / relative).parent / "operation.json")
                if op.get("inventory") != observed:
                    raise api.StateError("Quarantined content changed; retained for inspection")
            if tracked(api, runtime.parent, item["path"]):
                raise api.StateError("Git-tracked files are protected")
            candidates.append({"path": item["path"], "action": "delete" if item["state"] == "quarantined"
                               else ("quarantine" if item["kind"] == "temporary" else "archive"),
                               "entries": len(observed), "observed": observed, "registration": dict(item)})
        except (api.StateError, OSError) as exc:
            skipped.append({"path": item["path"], "reason": str(exc)})
    applied = []
    if args.apply:
        for candidate in candidates:
            # Reload canonical state and registration immediately before each operation.
            value = load(api, runtime)
            item = next(i for i in value["artifacts"] if i["path"] == candidate["path"])
            relative = item.get("location", item["path"])
            try:
                if item != candidate["registration"]:
                    raise api.StateError("Registration changed after inspection")
                if eligible(api, runtime, item) or tracked(api, runtime.parent, item["path"]):
                    raise api.StateError("Eligibility changed")
                if snapshot(api, runtime.parent, relative, storage=item["state"] != "current") != candidate["observed"]:
                    raise api.StateError("Directory changed after inspection")
                source = checked_path(api, runtime.parent, relative, storage=item["state"] != "current")
                if candidate["action"] == "delete":
                    if item["state"] != "quarantined" or now() - parse_time(item["stored_at"]) < timedelta(days=7):
                        raise api.StateError("Quarantine retention not satisfied")
                    # Deletion is confined to the already validated quarantine directory.
                    record = source.parent / "operation.json"
                    op = api.read_json(record)
                    op.update(result="deleting", delete_started=api.utc_now())
                    api.atomic_write_json(record, op)
                    shutil.rmtree(source)
                    item["state"] = "deleted"
                    api.atomic_write_json(runtime / "WORKSPACE.json", value)
                    op["result"] = "deleted"
                    api.atomic_write_json(record, op)
                else:
                    batch = runtime / "storage" / uuid.uuid4().hex
                    destination = batch / "content"
                    location = destination.relative_to(runtime.parent).as_posix()
                    checked_path(api, runtime.parent, location, storage=True)
                    state = "quarantined" if candidate["action"] == "quarantine" else "archived"
                    stamp = api.utc_now()
                    op = {"path": item["path"], "destination": location, "state": state,
                          "time": stamp, "result": "pending", "inventory": candidate["observed"],
                          "reason": "released registered " + item["kind"]}
                    api.atomic_write_json(batch / "operation.json", op)
                    source.rename(destination)
                    item.update(state=state, location=location, stored_at=stamp)
                    api.atomic_write_json(runtime / "WORKSPACE.json", value)
                    op["result"] = "moved"
                    api.atomic_write_json(batch / "operation.json", op)
                applied.append({k: candidate[k] for k in ("path", "action")})
            except (api.StateError, OSError) as exc:
                skipped.append({"path": item["path"], "reason": str(exc)})
        moved = archive_feedback(api, runtime)
        value = load(api, runtime)
        value["last_checked"] = api.utc_now()
        api.atomic_write_json(runtime / "WORKSPACE.json", value)
        if applied or moved:
            api.atomic_write_json(runtime / "HOUSEKEEPING.json",
                                  {"time": api.utc_now(), "actions": applied, "feedback_archived": moved})
    else:
        moved = 0
    registered_roots = {PurePosixPath(i["path"]).parts[0] for i in value["artifacts"]}
    unknown = sorted(p.name for p in runtime.parent.iterdir()
                     if p.is_dir() and p.name not in registered_roots and p.name not in PROTECTED)
    print(json.dumps({"due": due(value), "applied": args.apply,
                      "candidates": [{k: v for k, v in c.items() if k not in {"observed", "registration"}} for c in candidates][:20],
                      "candidate_count": len(candidates), "actions": applied[:20],
                      "skipped": skipped[:20], "skipped_count": len(skipped),
                      "feedback_archived": moved,
                      "unclassified_directory_count": len(unknown), "unclassified_examples": unknown[:5]}, ensure_ascii=False))
    return 0


def command_restore(args, api):
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    if api.load_state(runtime)["status"] == "complete":
        raise api.StateError("Completed project is frozen")
    value = load(api, runtime)
    recover_moves(api, runtime, value)
    item = next((i for i in value["artifacts"] if i["path"] == args.path), None)
    if item is None or item["state"] not in {"quarantined", "archived"}:
        raise api.StateError("No stored artifact at that registered path")
    target = checked_path(api, runtime.parent, item["path"])
    source = checked_path(api, runtime.parent, item["location"], storage=True)
    snapshot(api, runtime.parent, item["location"], storage=True)
    if target.exists():
        raise api.StateError("Restore would overwrite current project content")
    op = api.read_json(source.parent / "operation.json")
    op["result"] = "restoring"
    api.atomic_write_json(source.parent / "operation.json", op)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    item.update(state="current", released=False, evidence="")
    item.pop("location", None)
    item.pop("stored_at", None)
    api.atomic_write_json(runtime / "WORKSPACE.json", value)
    op["result"] = "restored"
    api.atomic_write_json(source.parent / "operation.json", op)
    print("Restored; release declaration reset")
    return 0


def add_commands(subparsers, api):
    for name, function in (("workspace-register", command_register), ("housekeep", command_housekeep),
                           ("workspace-restore", command_restore)):
        p = subparsers.add_parser(name, help="Manage explicitly registered task artifacts")
        api.common_project_root(p)
        p.set_defaults(func=functools.partial(function, api=api))
        if name == "workspace-register":
            p.add_argument("--path", required=True)
            p.add_argument("--producer", required=True)
            p.add_argument("--assignment-revision", type=int, required=True)
            p.add_argument("--kind", choices=["temporary", "intermediate", "deliverable", "evidence"], required=True)
            p.add_argument("--reproduce")
            p.add_argument("--movable", action="store_true")
            p.add_argument("--released", action="store_true")
            p.add_argument("--quiescence-evidence")
        elif name == "housekeep":
            p.add_argument("--apply", action="store_true")
            p.add_argument("--if-due", action="store_true")
        else:
            p.add_argument("--path", required=True)
