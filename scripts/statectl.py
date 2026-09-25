#!/usr/bin/env python3
"""Deterministic runtime-state helper for agent-project-manager."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from memory import MemoryError as MemoryContentError


SCHEMA_VERSION = 1
RUNTIME_NAME = ".agent-project-manager"
SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "runtime"
PHASES = {"planning", "execution", "review", "complete"}
PROJECT_STATUSES = {"active", "blocked", "waiting-owner", "complete"}
WORKER_STATUSES = {
    "ready",
    "active",
    "blocked",
    "waiting-owner",
    "completed",
    "inactive",
}
REVIEW_STATUSES = {"not-requested", "ready", "active", "blocked", "completed"}
REVIEW_LEVELS = {"none", "balanced", "strong"}
MODES = {"standalone", "leader"}
PROJECT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
WORKER_ID_RE = re.compile(r"^worker-[1-9][0-9]*$")
REVIEWER_ID_RE = re.compile(r"^reviewer-[1-9][0-9]*$")
ASSIGNMENT_HISTORY_RE = re.compile(r"^assignment-([0-9]{4,})$")
COMPLETION_HISTORY_RE = re.compile(r"^completion-([0-9]{4,})$")
REVIEW_HISTORY_RE = re.compile(r"^review-([0-9]{4,})$")
OWNER_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ACTIVE_WORKER_STATUSES = {"ready", "active", "blocked", "waiting-owner"}
REASSIGNMENT_MARKER = ".reassign.json"
REVIEW_ASSIGNMENT_MARKER = ".assign-review.json"
ADD_WORKER_MARKER_PREFIX = ".add-worker-"


class StateError(RuntimeError):
    """Raised for an invalid or unsafe state operation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def project_root(value: str) -> Path:
    return Path(value).expanduser().resolve()


def runtime_dir(root: Path) -> Path:
    return root / RUNTIME_NAME


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateError(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateError(f"Expected a JSON object in {path}")
    return value


def read_template_text(name: str) -> str:
    path = TEMPLATE_ROOT / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StateError(f"Skill template is missing: {path}") from exc


def read_template_json(name: str) -> dict[str, Any]:
    path = TEMPLATE_ROOT / name
    return read_json(path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def write_new_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as exc:
        raise StateError(f"Refusing to overwrite existing file: {path}") from exc


def write_new_json(path: Path, value: dict[str, Any]) -> None:
    write_new_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def require_nonempty(value: str, label: str) -> str:
    result = value.strip()
    if not result:
        raise StateError(f"{label} must not be empty")
    return result


def check_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def checked_relative_path(runtime: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise StateError(f"{label} must be a non-empty relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or value != pure.as_posix():
        raise StateError(f"{label} must be a normalized path inside {RUNTIME_NAME}: {value}")
    candidate = (runtime / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(runtime.resolve())
    except ValueError as exc:
        raise StateError(f"{label} escapes {RUNTIME_NAME}: {value}") from exc
    return candidate


def state_path(runtime: Path) -> Path:
    return runtime / "STATE.json"


def load_state(runtime: Path) -> dict[str, Any]:
    return read_json(state_path(runtime))


def save_state(runtime: Path, state: dict[str, Any]) -> None:
    state["last_updated"] = utc_now()
    atomic_write_json(state_path(runtime), state)


def scope_root(value: str) -> tuple[str, bool]:
    """Return the literal prefix and whether a scope contains glob syntax."""
    normalized = value.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    literal: list[str] = []
    has_glob = False
    for part in parts:
        if any(character in part for character in "*?["):
            has_glob = True
            break
        literal.append(part)
    return "/".join(literal), has_glob


def scopes_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_root, left_glob = scope_root(left)
    right_root, right_glob = scope_root(right)
    if not (left_glob or right_glob):
        return (
            left_root == right_root
            or left_root.startswith(right_root + "/")
            or right_root.startswith(left_root + "/")
        )
    if (left_glob and not left_root) or (right_glob and not right_root):
        return True
    if not left_root or not right_root:
        return False
    return (
        left_root == right_root
        or left_root.startswith(right_root + "/")
        or right_root.startswith(left_root + "/")
    )


def validate_status_object(
    value: dict[str, Any], expected_id: str, path: Path
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "worker_id",
        "status",
        "summary",
        "files_changed",
        "verification",
        "next_action",
        "last_updated",
    }
    missing = sorted(required - value.keys())
    if missing:
        errors.append(f"{path}: missing fields: {', '.join(missing)}")
        return errors
    if value["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{path}: unsupported schema_version")
    if value["worker_id"] != expected_id:
        errors.append(f"{path}: worker_id does not match registry")
    if value["status"] not in WORKER_STATUSES:
        errors.append(f"{path}: invalid worker status {value['status']!r}")
    for key in ("summary", "next_action"):
        if not isinstance(value[key], str):
            errors.append(f"{path}: {key} must be a string")
    for key in ("files_changed", "verification"):
        if not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]):
            errors.append(f"{path}: {key} must be a list of strings")
    if not check_timestamp(value["last_updated"]):
        errors.append(f"{path}: last_updated must be an ISO-8601 UTC timestamp")
    return errors


def validate_review_status(value: dict[str, Any], path: Path) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "reviewer_id",
        "status",
        "summary",
        "verification",
        "last_updated",
    }
    missing = sorted(required - value.keys())
    if missing:
        errors.append(f"{path}: missing fields: {', '.join(missing)}")
        return errors
    if value["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{path}: unsupported schema_version")
    reviewer_id = value["reviewer_id"]
    if reviewer_id is not None and (
        not isinstance(reviewer_id, str) or not REVIEWER_ID_RE.fullmatch(reviewer_id)
    ):
        errors.append(f"{path}: invalid reviewer_id")
    if value.get("verdict") not in {None, "approved", "changes-requested"}:
        errors.append(f"{path}: invalid review verdict")
    if "revision" in value and (type(value["revision"]) is not int or value["revision"] < 1):
        errors.append(f"{path}: invalid review revision")
    if value["status"] not in REVIEW_STATUSES:
        errors.append(f"{path}: invalid review status {value['status']!r}")
    if not isinstance(value["summary"], str):
        errors.append(f"{path}: summary must be a string")
    if not isinstance(value["verification"], list) or not all(
        isinstance(item, str) for item in value["verification"]
    ):
        errors.append(f"{path}: verification must be a list of strings")
    if not check_timestamp(value["last_updated"]):
        errors.append(f"{path}: last_updated must be an ISO-8601 UTC timestamp")
    return errors


def validate_assignment_history(worker_dir: Path, worker_id: str) -> list[str]:
    history_dir = worker_dir / "history"
    if not history_dir.exists():
        return []
    if not history_dir.is_dir():
        return [f"{history_dir}: assignment history must be a directory"]

    errors: list[str] = []
    seen_revisions: set[int] = set()
    for assignment_dir in sorted(history_dir.iterdir()):
        if assignment_dir.name.startswith(".assignment-"):
            continue
        match = ASSIGNMENT_HISTORY_RE.fullmatch(assignment_dir.name)
        if not assignment_dir.is_dir() or match is None:
            errors.append(f"{assignment_dir}: invalid assignment history entry")
            continue
        revision = int(match.group(1))
        if revision < 1:
            errors.append(f"{assignment_dir}: assignment revision must be positive")
            continue
        if revision in seen_revisions:
            errors.append(f"{assignment_dir}: duplicate assignment revision {revision}")
            continue
        seen_revisions.add(revision)
        for name in ("TASK.md", "STATUS.json", "BLOCKER.md"):
            if not (assignment_dir / name).is_file():
                errors.append(f"{assignment_dir}: missing archived {name}")
        archived_status_path = assignment_dir / "STATUS.json"
        if archived_status_path.is_file():
            try:
                archived_status = read_json(archived_status_path)
            except StateError as exc:
                errors.append(str(exc))
                continue
            errors.extend(
                validate_status_object(archived_status, worker_id, archived_status_path)
            )
    return errors


def validate_review_history(review_dir: Path) -> list[str]:
    history_dir = review_dir / "history"
    if not history_dir.exists():
        return []
    if not history_dir.is_dir():
        return [f"{history_dir}: review history must be a directory"]

    errors: list[str] = []
    for entry in sorted(history_dir.iterdir()):
        if entry.name.startswith(".review-"):
            continue
        match = REVIEW_HISTORY_RE.fullmatch(entry.name)
        if not entry.is_dir() or match is None:
            errors.append(f"{entry}: invalid review history entry")
            continue
        for name in ("TASK.md", "STATUS.json", "REPORT.md"):
            if not (entry / name).is_file():
                errors.append(f"{entry}: missing archived {name}")
        status_path_value = entry / "STATUS.json"
        if status_path_value.is_file():
            try:
                status = read_json(status_path_value)
            except StateError as exc:
                errors.append(str(exc))
                continue
            errors.extend(validate_review_status(status, status_path_value))
    return errors


def validate_completion_history(runtime: Path, project_id: str) -> list[str]:
    history_dir = runtime / "history"
    if not history_dir.exists():
        return []
    if not history_dir.is_dir():
        return [f"{history_dir}: completion history must be a directory"]

    errors: list[str] = []
    for entry in sorted(history_dir.iterdir()):
        if entry.name.startswith(".completion-"):
            continue
        match = COMPLETION_HISTORY_RE.fullmatch(entry.name)
        if not entry.is_dir() or match is None:
            errors.append(f"{entry}: invalid completion history entry")
            continue
        # STATE.json and REOPEN.json are always archived; the human pages and the
        # review snapshot are present only when the project actually had them.
        for name in ("STATE.json", "REOPEN.json"):
            if not (entry / name).is_file():
                errors.append(f"{entry}: missing completion snapshot {name}")
        archived_state_path = entry / "STATE.json"
        if not archived_state_path.is_file():
            continue
        try:
            archived_state = read_json(archived_state_path)
        except StateError as exc:
            errors.append(str(exc))
            continue
        if archived_state.get("project_id") != project_id:
            errors.append(f"{archived_state_path}: project_id does not match runtime")
        if archived_state.get("phase") != "complete" or archived_state.get("status") != "complete":
            errors.append(f"{archived_state_path}: snapshot must be complete/complete")
        archived_workers = archived_state.get("workers", [])
        if not isinstance(archived_workers, list):
            errors.append(f"{archived_state_path}: workers must be a list")
            archived_workers = []
        for worker in archived_workers:
            if not isinstance(worker, dict):
                continue
            for key in ("task_path", "status_path"):
                try:
                    target = checked_relative_path(entry, worker.get(key), f"completion.{key}")
                except StateError as exc:
                    errors.append(str(exc))
                    continue
                if not target.is_file():
                    errors.append(f"{entry}: missing snapshotted {key}: {target}")
                elif key == "status_path" and isinstance(worker.get("id"), str):
                    try:
                        worker_status = read_json(target)
                    except StateError as exc:
                        errors.append(str(exc))
                    else:
                        errors.extend(
                            validate_status_object(worker_status, worker["id"], target)
                        )
                        if worker_status.get("status") not in {"completed", "inactive"}:
                            errors.append(
                                f"{target}: completion snapshot Worker must be completed or inactive"
                            )
            task_path_value = worker.get("task_path")
            if isinstance(task_path_value, str):
                try:
                    worker_dir = checked_relative_path(
                        entry, task_path_value, "completion.worker.task_path"
                    ).parent
                    if not (worker_dir / "BLOCKER.md").is_file():
                        errors.append(f"{entry}: missing snapshotted Worker BLOCKER.md")
                except StateError:
                    pass
        review = archived_state.get("review")
        # Only an assigned review has files worth snapshotting.
        if isinstance(review, dict) and review.get("required"):
            for key in ("task_path", "status_path", "report_path"):
                try:
                    target = checked_relative_path(entry, review.get(key), f"completion.review.{key}")
                except StateError as exc:
                    errors.append(str(exc))
                    continue
                if not target.is_file():
                    errors.append(f"{entry}: missing snapshotted review file: {target}")
                elif key == "status_path":
                    try:
                        archived_review_status = read_json(target)
                    except StateError as exc:
                        errors.append(str(exc))
                    else:
                        errors.extend(validate_review_status(archived_review_status, target))
        reopen_path = entry / "REOPEN.json"
        if reopen_path.is_file():
            try:
                reopen = read_json(reopen_path)
            except StateError as exc:
                errors.append(str(exc))
                continue
            if reopen.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
                errors.append(f"{reopen_path}: unsupported schema_version")
            if not isinstance(reopen.get("reason"), str) or not reopen["reason"].strip():
                errors.append(f"{reopen_path}: reason must be a non-empty string")
            if not check_timestamp(reopen.get("reopened_at")):
                errors.append(f"{reopen_path}: reopened_at must be an ISO-8601 UTC timestamp")
    return errors


def validate_runtime(runtime: Path, *, include_history: bool = True) -> list[str]:
    errors: list[str] = []
    for name in ("STATE.json", "memory.jsonl"):
        if not (runtime / name).is_file():
            errors.append(f"Missing required file: {runtime / name}")
    if errors:
        return errors

    try:
        state = load_state(runtime)
    except StateError as exc:
        return [str(exc)]

    # Conditional files and directories exist only once the feature is in use, so
    # a standalone project stays at three files. Each check reads the state that
    # was just loaded; a missing directory for an unused feature is not an error.
    if state.get("workers") and not (runtime / "workers").is_dir():
        errors.append(f"Missing required directory: {runtime / 'workers'}")
    if state.get("review", {}).get("required") and not (runtime / "review").is_dir():
        errors.append(f"Missing required directory: {runtime / 'review'}")
    for entry in state.get("workers", []):
        for key in ("task_path", "status_path"):
            relative = entry.get(key)
            if relative and not (runtime / relative).is_file():
                errors.append(f"Missing required file: {runtime / relative}")

    required = {
        "schema_version",
        "project_id",
        "mode",
        "phase",
        "status",
        "profile",
        "current_milestone",
        "workers",
        "review",
        "last_updated",
        "next_action",
    }
    missing = sorted(required - state.keys())
    if missing:
        errors.append(f"STATE.json: missing fields: {', '.join(missing)}")
        return errors

    if state["schema_version"] != SCHEMA_VERSION:
        errors.append("STATE.json: unsupported schema_version")
    if not isinstance(state["project_id"], str) or not PROJECT_ID_RE.fullmatch(state["project_id"]):
        errors.append("STATE.json: invalid project_id")
    if state["mode"] not in MODES:
        errors.append(f"STATE.json: invalid mode {state['mode']!r}")
    if state["phase"] not in PHASES:
        errors.append(f"STATE.json: invalid phase {state['phase']!r}")
    if state["status"] not in PROJECT_STATUSES:
        errors.append(f"STATE.json: invalid status {state['status']!r}")
    if (state["phase"] == "complete") != (state["status"] == "complete"):
        errors.append("STATE.json: phase and status must both be complete or both be non-complete")
    for key in ("profile", "current_milestone"):
        if not isinstance(state[key], str) or not state[key].strip():
            errors.append(f"STATE.json: {key} must be a non-empty string")
    if not check_timestamp(state["last_updated"]):
        errors.append("STATE.json: last_updated must be an ISO-8601 UTC timestamp")

    next_action = state["next_action"]
    if not isinstance(next_action, dict) or set(next_action) != {"actor", "instruction"}:
        errors.append("STATE.json: next_action must contain only actor and instruction")
    elif not all(isinstance(next_action[key], str) and next_action[key].strip() for key in next_action):
        errors.append("STATE.json: next_action values must be non-empty strings")

    workers = state["workers"]
    if not isinstance(workers, list):
        errors.append("STATE.json: workers must be a list")
        workers = []

    seen_ids: set[str] = set()
    registry: dict[str, dict[str, Any]] = {}
    worker_statuses: dict[str, str] = {}
    active_scopes: list[tuple[str, str]] = []
    for index, worker in enumerate(workers):
        prefix = f"STATE.json: workers[{index}]"
        if not isinstance(worker, dict):
            errors.append(f"{prefix} must be an object")
            continue
        worker_required = {"id", "task_path", "status_path", "write_scope", "depends_on"}
        if not worker_required <= set(worker) or set(worker) - worker_required - {"executor"}:
            errors.append(f"{prefix} must contain exactly {', '.join(sorted(worker_required))}")
            continue
        if worker.get("executor", "worker") not in {"worker", "lead"}:
            errors.append(f"{prefix}: invalid executor")
        worker_id = worker["id"]
        if not isinstance(worker_id, str) or not WORKER_ID_RE.fullmatch(worker_id):
            errors.append(f"{prefix}.id is invalid")
            continue
        if worker_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated: {worker_id}")
            continue
        seen_ids.add(worker_id)
        registry[worker_id] = worker
        for key in ("write_scope", "depends_on"):
            if not isinstance(worker[key], list) or not all(
                isinstance(item, str) and item.strip() for item in worker[key]
            ):
                errors.append(f"{prefix}.{key} must be a list of strings")

        try:
            task = checked_relative_path(runtime, worker["task_path"], f"{prefix}.task_path")
            status_file = checked_relative_path(runtime, worker["status_path"], f"{prefix}.status_path")
        except StateError as exc:
            errors.append(str(exc))
            continue
        if not task.is_file():
            errors.append(f"{prefix}: task file does not exist: {task}")
        worker_dir = task.parent
        blocker_file = worker_dir / "BLOCKER.md"
        if not blocker_file.is_file():
            errors.append(f"{prefix}: blocker file does not exist: {blocker_file}")
        if include_history:
            errors.extend(validate_assignment_history(worker_dir, worker_id))
        if not status_file.is_file():
            errors.append(f"{prefix}: status file does not exist: {status_file}")
            continue
        try:
            status_value = read_json(status_file)
        except StateError as exc:
            errors.append(str(exc))
            continue
        errors.extend(validate_status_object(status_value, worker_id, status_file))
        worker_statuses[worker_id] = status_value.get("status")
        if status_value.get("status") in ACTIVE_WORKER_STATUSES:
            for scope in worker.get("write_scope", []):
                for other_scope, other_id in active_scopes:
                    if scopes_overlap(scope, other_scope):
                        errors.append(
                            f"Active write scope {scope!r} for {worker_id} overlaps "
                            f"{other_scope!r} for {other_id}"
                        )
                active_scopes.append((scope, worker_id))

    for worker_id, worker in registry.items():
        for dependency in worker.get("depends_on", []):
            if dependency == worker_id:
                errors.append(f"{worker_id} cannot depend on itself")
            elif dependency not in registry:
                errors.append(f"{worker_id} depends on unknown Worker {dependency}")
            elif worker_statuses.get(worker_id) == "active" and worker_statuses.get(
                dependency
            ) != "completed":
                errors.append(
                    f"Active Worker {worker_id} has incomplete dependency "
                    f"{dependency} ({worker_statuses.get(dependency)})"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(worker_id: str) -> None:
        if worker_id in visiting:
            errors.append(f"Worker dependency cycle includes {worker_id}")
            return
        if worker_id in visited or worker_id not in registry or worker_statuses.get(worker_id) not in ACTIVE_WORKER_STATUSES:
            return
        visiting.add(worker_id)
        for dependency in registry[worker_id].get("depends_on", []):
            visit(dependency)
        visiting.remove(worker_id)
        visited.add(worker_id)

    for worker_id in registry:
        visit(worker_id)

    review = state["review"]
    current_review_status: Optional[dict[str, Any]] = None
    review_required_fields = {
        "required",
        "level",
        "reviewer_id",
        "task_path",
        "status_path",
        "report_path",
    }
    if not isinstance(review, dict) or set(review) != review_required_fields:
        errors.append("STATE.json: review has invalid fields")
    else:
        if not isinstance(review["required"], bool):
            errors.append("STATE.json: review.required must be boolean")
        if review["level"] not in REVIEW_LEVELS:
            errors.append("STATE.json: review.level is invalid")
        if review["required"] != (review["level"] != "none"):
            errors.append("STATE.json: review.required and review.level disagree")
        reviewer_id = review["reviewer_id"]
        if reviewer_id is not None and (
            not isinstance(reviewer_id, str) or not REVIEWER_ID_RE.fullmatch(reviewer_id)
        ):
            errors.append("STATE.json: review.reviewer_id is invalid")
        if not review["required"] and reviewer_id is not None:
            errors.append("STATE.json: optional review must not name a reviewer")
        for key in ("task_path", "status_path", "report_path"):
            try:
                target = checked_relative_path(runtime, review[key], f"review.{key}")
            except StateError as exc:
                errors.append(str(exc))
                continue
            # Review files are created by `assign-review`, so an unused review
            # legitimately has none of them on disk.
            if review["required"] and not target.is_file():
                errors.append(f"Missing review file: {target}")
        try:
            review_status_path = checked_relative_path(runtime, review["status_path"], "review.status_path")
            if review_status_path.is_file():
                current_review_status = read_json(review_status_path)
                errors.extend(validate_review_status(current_review_status, review_status_path))
                if reviewer_id is not None and reviewer_id != current_review_status.get("reviewer_id"):
                    errors.append("STATE.json and review/STATUS.json reviewer_id disagree")
        except StateError as exc:
            errors.append(str(exc))

    if state["phase"] == "review":
        if not isinstance(review, dict) or not review.get("required") or review.get("reviewer_id") is None:
            errors.append("STATE.json: review phase requires an assigned review")
    if state["phase"] == "complete":
        unfinished = sorted(
            worker_id
            for worker_id, status in worker_statuses.items()
            if status not in {"completed", "inactive"}
        )
        if unfinished:
            errors.append(
                "STATE.json: complete project has unfinished Workers: " + ", ".join(unfinished)
            )
        if isinstance(review, dict) and review.get("required"):
            if (
                review.get("reviewer_id") is None
                or current_review_status is None
                or current_review_status.get("status") != "completed"
                or current_review_status.get("reviewer_id") != review.get("reviewer_id")
                or ("verdict" in current_review_status and current_review_status["verdict"] != "approved")
            ):
                errors.append("STATE.json: complete project has unfinished required review")

    if state["mode"] == "standalone":
        if state["workers"]:
            errors.append("STATE.json: standalone mode must not register Workers")
        if state["review"]["required"]:
            errors.append("STATE.json: standalone mode must not require a review")

    if include_history:
        errors.extend(validate_review_history(runtime / "review"))
        errors.extend(validate_completion_history(runtime, state["project_id"]))

    return errors


def assert_valid(runtime: Path) -> None:
    errors = validate_runtime(runtime, include_history=False)
    if errors:
        raise StateError("Runtime validation failed:\n- " + "\n- ".join(errors))


def validate_candidate(runtime: Path, changes: dict[str, str]) -> None:
    """Validate current files plus a proposed write set, without copying history.

    A minimal standalone runtime holds only ``STATE.json``, ``memory.jsonl`` and
    ``PROJECT_STATUS.md``; every other file is created on demand. The candidate
    therefore mirrors whatever the live runtime actually has, rather than a
    fixed list of files.
    """
    with tempfile.TemporaryDirectory(prefix="apm-candidate-") as directory:
        candidate = Path(directory)
        state = load_state(runtime)
        paths = {"STATE.json", "memory.jsonl"}
        for name in ("PROJECT_STATUS.md", "PLAN.md", "OWNER_DIRECTIVES.md", "OWNER_STATUS.md",
                     "HANDOFF.md"):
            if (runtime / name).is_file():
                paths.add(name)
        if state["review"]["required"] or (runtime / "review").is_dir():
            paths.update(("review/TASK.md", "review/STATUS.json", "review/REPORT.md"))
        for entry in state["workers"]:
            paths.update((entry["task_path"], entry["status_path"],
                          str(PurePosixPath(entry["task_path"]).parent / "BLOCKER.md")))
        for name in sorted(paths | set(changes)):
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = runtime / name
            if name in changes:
                content = changes[name]
            elif source.is_file():
                content = source.read_text(encoding="utf-8")
            else:
                content = None
            if content is not None:
                target.write_text(content, encoding="utf-8")
            elif target.suffix == ".md":
                target.write_text("", encoding="utf-8")
        for name in ("workers", "inbox/owner", "review"):
            (candidate / name).mkdir(parents=True, exist_ok=True)
        assert_valid(candidate)


def command_init(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    if not PROJECT_ID_RE.fullmatch(args.project_id):
        raise StateError("project-id must use lowercase letters, digits, and single hyphens")
    profile = require_nonempty(args.profile, "profile")
    if runtime.exists():
        if state_path(runtime).is_file():
            assert_valid(runtime)
            print(f"Already initialized; no files changed: {runtime}")
            return 0
        raise StateError(f"Refusing to overwrite partial runtime directory: {runtime}")

    if not root.is_dir():
        raise StateError(f"Project root does not exist or is not a directory: {root}")
    staging = Path(tempfile.mkdtemp(prefix=f"{RUNTIME_NAME}.init-", dir=root))
    try:
        now = utc_now()
        state = read_template_json("STATE.json")
        state["project_id"] = args.project_id
        state["profile"] = profile
        state["mode"] = args.mode
        state["last_updated"] = now
        write_new_json(state_path(staging), state)
        write_new_text(staging / "memory.jsonl", "")

        # The manager must cost less than the work it saves: initialize only the
        # three files every project needs. PLAN.md, workers/, review/ and
        # inbox/owner/ are created by the command that first needs them.
        assert_valid(staging)
        # Render the human page from real state rather than shipping placeholders.
        write_new_text(
            staging / "PROJECT_STATUS.md",
            render_project_status(staging, status_snapshot(staging, validate=False)),
        )
        if runtime.exists():
            raise StateError(f"Runtime appeared during initialization: {runtime}")
        staging.rename(runtime)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(f"Initialized {runtime}")
    return 0


def markdown_list(values: Iterable[str]) -> str:
    items = list(values)
    return "\n".join(f"- {item}" for item in items) if items else "- None"


def normalize_worker_assignment(args: argparse.Namespace) -> tuple[str, list[str]]:
    objective = require_nonempty(args.objective, "objective")
    args.objective = objective
    args.allowed_scope = [
        require_nonempty(item, "allowed-scope") for item in args.allowed_scope
    ]
    args.read_dependency = [
        require_nonempty(item, "read-dependency") for item in args.read_dependency
    ]
    args.do_not_modify = [
        require_nonempty(item, "do-not-modify") for item in args.do_not_modify
    ]
    args.depends_on = [require_nonempty(item, "depends-on") for item in args.depends_on]
    args.plan_section = [
        require_nonempty(item, "plan-section") for item in args.plan_section
    ]
    args.completion_criterion = [
        require_nonempty(item, "completion-criterion")
        for item in args.completion_criterion
    ]
    if getattr(args, "coordination_justification", None) is not None:
        args.coordination_justification = require_nonempty(
            args.coordination_justification, "coordination-justification"
        )
    return objective, args.allowed_scope


def build_worker_task(args: argparse.Namespace, assignment_revision: int = 1) -> str:
    coordination = args.coordination_justification or "Not required."
    return (
        f"# Worker Task: {args.worker_id}\n\n"
        f"## Assignment revision\n\n{assignment_revision}\n\n"
        f"## Objective\n\n{args.objective.strip()}\n\n"
        f"## Allowed scope\n\n{markdown_list(args.allowed_scope)}\n\n"
        f"## Read dependencies\n\n{markdown_list(args.read_dependency)}\n\n"
        f"## Do not modify\n\n{markdown_list(args.do_not_modify)}\n\n"
        f"## Worker dependencies\n\n{markdown_list(args.depends_on)}\n\n"
        f"## Relevant plan sections\n\n{markdown_list(args.plan_section)}\n\n"
        f"## Completion criteria\n\n{markdown_list(args.completion_criterion)}\n\n"
        f"## Coordination justification\n\n{coordination.strip()}\n"
    )


def add_worker_marker_path(runtime: Path, worker_id: str) -> Path:
    return runtime / f"{ADD_WORKER_MARKER_PREFIX}{worker_id}.json"


def validate_add_worker_marker(value: dict[str, Any], path: Path) -> None:
    required = {"schema_version", "worker_id", "old_state", "new_state", "files"}
    if set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"Invalid pending Worker-add marker: {path}")
    worker_id = value.get("worker_id")
    if not isinstance(worker_id, str) or not WORKER_ID_RE.fullmatch(worker_id):
        raise StateError(f"Invalid Worker in pending add marker: {path}")
    if path.name != f"{ADD_WORKER_MARKER_PREFIX}{worker_id}.json":
        raise StateError(f"Pending Worker-add marker has the wrong filename: {path}")
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != {"TASK.md", "STATUS.json", "BLOCKER.md"}:
        raise StateError(f"Invalid files in pending Worker-add marker: {path}")
    if not all(isinstance(content, str) for content in files.values()):
        raise StateError(f"Invalid file content in pending Worker-add marker: {path}")
    try:
        old_state = json.loads(value["old_state"])
        new_state = json.loads(value["new_state"])
        worker_status = json.loads(files["STATUS.json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise StateError(f"Invalid JSON in pending Worker-add marker: {path}") from exc
    if not all(isinstance(item, dict) for item in (old_state, new_state, worker_status)):
        raise StateError(f"Invalid objects in pending Worker-add marker: {path}")
    old_workers = old_state.get("workers")
    new_workers = new_state.get("workers")
    if not isinstance(old_workers, list) or not isinstance(new_workers, list):
        raise StateError(f"Invalid registry in pending Worker-add marker: {path}")
    immutable_keys = set(old_state) - {"workers", "last_updated", "next_action"}
    if set(old_state) != set(new_state) or any(
        old_state[key] != new_state[key] for key in immutable_keys
    ):
        raise StateError(f"Pending Worker-add marker changes unrelated project state: {path}")
    if not check_timestamp(new_state.get("last_updated")) or new_state.get("next_action") != {
        "actor": "project-lead",
        "instruction": "Finish assignments, then move the project to execution.",
    }:
        raise StateError(f"Pending Worker-add marker has invalid project metadata: {path}")
    if new_workers[:-1] != old_workers or len(new_workers) != len(old_workers) + 1:
        raise StateError(f"Pending Worker-add marker does not append exactly one Worker: {path}")
    entry = new_workers[-1]
    expected_entry = {
        "id": worker_id,
        "task_path": f"workers/{worker_id}/TASK.md",
        "status_path": f"workers/{worker_id}/STATUS.json",
    }
    if not isinstance(entry, dict) or any(entry.get(key) != expected for key, expected in expected_entry.items()):
        raise StateError(f"Pending Worker-add marker has an invalid registry entry: {path}")
    if set(entry) != {"id", "task_path", "status_path", "write_scope", "depends_on"}:
        raise StateError(f"Pending Worker-add marker has invalid registry fields: {path}")
    if not isinstance(entry["write_scope"], list) or not entry["write_scope"] or not all(
        isinstance(item, str) and item.strip() for item in entry["write_scope"]
    ):
        raise StateError(f"Pending Worker-add marker has invalid write scope: {path}")
    if not isinstance(entry["depends_on"], list) or not all(
        isinstance(item, str) and WORKER_ID_RE.fullmatch(item) for item in entry["depends_on"]
    ):
        raise StateError(f"Pending Worker-add marker has invalid dependencies: {path}")
    if worker_id in {item.get("id") for item in old_workers if isinstance(item, dict)}:
        raise StateError(f"Pending Worker-add marker duplicates an existing Worker: {path}")
    if validate_status_object(worker_status, worker_id, path) or worker_status.get("status") != "ready":
        raise StateError(f"Pending Worker-add marker does not publish a ready status: {path}")


def recover_add_worker(runtime: Path, marker_path: Path) -> None:
    marker = read_json(marker_path)
    validate_add_worker_marker(marker, marker_path)
    current_state = state_path(runtime).read_text(encoding="utf-8")
    if current_state not in {marker["old_state"], marker["new_state"]}:
        raise StateError(
            "Pending Worker-add marker conflicts with newer STATE.json; refusing to overwrite it"
        )
    if current_state == marker["old_state"]:
        assert_valid(runtime)

    worker_id = marker["worker_id"]
    worker_dir = runtime / "workers" / worker_id
    if worker_dir.exists():
        if not worker_dir.is_dir() or set(path.name for path in worker_dir.iterdir()) != set(
            marker["files"]
        ):
            raise StateError(
                f"Pending Worker-add marker conflicts with newer {worker_id} content; "
                "refusing to overwrite it"
            )
        for name, content in marker["files"].items():
            if (worker_dir / name).read_text(encoding="utf-8") != content:
                raise StateError(
                    f"Pending Worker-add marker conflicts with newer {worker_id}/{name}; "
                    "refusing to overwrite it"
                )
    else:
        staging = Path(
            tempfile.mkdtemp(prefix=f".{worker_id}.add-", dir=runtime / "workers")
        )
        try:
            for name, content in marker["files"].items():
                write_new_text(staging / name, content)
            staging.rename(worker_dir)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    if current_state == marker["old_state"]:
        atomic_write_text(state_path(runtime), marker["new_state"])
    assert_valid(runtime)
    marker_path.unlink()


def recover_pending_worker_additions(runtime: Path) -> None:
    if not state_path(runtime).is_file():
        return
    for marker_path in sorted(runtime.glob(f"{ADD_WORKER_MARKER_PREFIX}*.json")):
        recover_add_worker(runtime, marker_path)


def command_add_worker(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    if not WORKER_ID_RE.fullmatch(args.worker_id):
        raise StateError("worker-id must match worker-N with N greater than zero")
    _, scopes = normalize_worker_assignment(args)
    state = load_state(runtime)
    registry = state["workers"]
    if state["status"] == "complete":
        raise StateError("Cannot add a Worker to a completed project")
    if state["mode"] == "standalone":
        raise StateError(
            "Standalone mode has no Workers; switch to leader mode first with "
            "set-project --mode leader once delegation is actually justified"
        )
    if state["phase"] == "review":
        raise StateError(
            "Cannot add a Worker during review; finish the review and return the project "
            "to execution first"
        )
    if any(item["id"] == args.worker_id for item in registry):
        raise StateError(f"Worker already exists: {args.worker_id}")
    known_ids = {item["id"] for item in registry}
    unknown = sorted(set(args.depends_on) - known_ids)
    if unknown:
        raise StateError(f"Unknown Worker dependencies: {', '.join(unknown)}")
    for item in registry:
        status_file = checked_relative_path(
            runtime, item["status_path"], f"{item['id']}.status_path"
        )
        if read_json(status_file)["status"] not in ACTIVE_WORKER_STATUSES:
            continue
        overlap = sorted(
            f"{scope} <-> {other_scope}"
            for scope in scopes
            for other_scope in item["write_scope"]
            if scopes_overlap(scope, other_scope)
        )
        if overlap:
            raise StateError(
                f"Write scope overlaps with {item['id']}: {', '.join(overlap)}"
            )

    if registry and not args.coordination_justification:
        raise StateError(
            "An additional Worker requires --coordination-justification; "
            "reuse an existing completed Worker unless a new conversation has clear value"
        )

    worker_dir = runtime / "workers" / args.worker_id
    if worker_dir.exists():
        raise StateError(f"Refusing to overwrite existing Worker directory: {worker_dir}")
    # Workers are opt-in: the directory only appears once a Worker is registered.
    (runtime / "workers").mkdir(parents=True, exist_ok=True)
    old_state_text = state_path(runtime).read_text(encoding="utf-8")
    status = read_template_json("worker-status.json")
    status["worker_id"] = args.worker_id
    status["last_updated"] = utc_now()
    registry.append(
        {
            "id": args.worker_id,
            "task_path": f"workers/{args.worker_id}/TASK.md",
            "status_path": f"workers/{args.worker_id}/STATUS.json",
            "write_scope": scopes,
            "depends_on": list(args.depends_on),
        }
    )
    state["next_action"] = {
        "actor": "project-lead",
        "instruction": "Finish assignments, then move the project to execution.",
    }
    state["last_updated"] = utc_now()
    marker = {
        "schema_version": SCHEMA_VERSION,
        "worker_id": args.worker_id,
        "old_state": old_state_text,
        "new_state": json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        "files": {
            "TASK.md": build_worker_task(args),
            "STATUS.json": json.dumps(status, indent=2, ensure_ascii=False) + "\n",
            "BLOCKER.md": read_template_text("blocker.md"),
        },
    }
    marker_path = add_worker_marker_path(runtime, args.worker_id)
    atomic_write_json(marker_path, marker)
    recover_add_worker(runtime, marker_path)
    assert_valid(runtime)
    print(f"Registered {args.worker_id}")
    return 0


def archived_assignment_revisions(worker_dir: Path) -> list[int]:
    history_dir = worker_dir / "history"
    if not history_dir.is_dir():
        return []
    revisions: list[int] = []
    for path in history_dir.iterdir():
        match = ASSIGNMENT_HISTORY_RE.fullmatch(path.name)
        if path.is_dir() and match is not None:
            revisions.append(int(match.group(1)))
    return revisions


def write_assignment_archive(
    worker_dir: Path, revision: int, files: dict[str, str]
) -> None:
    history_dir = worker_dir / "history"
    history_dir.mkdir(exist_ok=True)
    destination = history_dir / f"assignment-{revision:04d}"
    if destination.exists():
        for name, content in files.items():
            if (destination / name).read_text(encoding="utf-8") != content:
                raise StateError(f"Existing assignment archive conflicts: {destination}")
        for stale in history_dir.glob(f".assignment-{revision:04d}-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
        return
    temporary = Path(
        tempfile.mkdtemp(prefix=f".assignment-{revision:04d}-", dir=history_dir)
    )
    try:
        for name, content in files.items():
            write_new_text(temporary / name, content)
        os.replace(temporary, destination)
        for stale in history_dir.glob(f".assignment-{revision:04d}-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
    except Exception:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise


def validate_reassignment_marker(value: dict[str, Any], path: Path) -> None:
    required = {
        "schema_version",
        "worker_id",
        "archived_revision",
        "new_revision",
        "old_files",
        "new_files",
        "write_scope",
        "depends_on",
        "milestone",
        "invalidate_review",
    }
    if not required <= set(value) or set(value) - required - {"executor", "reason"} or value.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"Invalid pending reassignment marker: {path}")
    if not isinstance(value.get("worker_id"), str) or not WORKER_ID_RE.fullmatch(value["worker_id"]):
        raise StateError(f"Invalid Worker in pending reassignment: {path}")
    archived_revision = value.get("archived_revision")
    if (
        not isinstance(archived_revision, int)
        or archived_revision < 1
        or value.get("new_revision") != archived_revision + 1
    ):
        raise StateError(f"Invalid revision in pending reassignment: {path}")
    for key in ("old_files", "new_files"):
        files = value.get(key)
        if not isinstance(files, dict) or set(files) != {"TASK.md", "STATUS.json", "BLOCKER.md"}:
            raise StateError(f"Invalid {key} in pending reassignment: {path}")
        if not all(isinstance(content, str) for content in files.values()):
            raise StateError(f"Invalid {key} content in pending reassignment: {path}")
    if not isinstance(value.get("write_scope"), list) or not all(
        isinstance(item, str) and item.strip() for item in value["write_scope"]
    ):
        raise StateError(f"Invalid write scope in pending reassignment: {path}")
    if not isinstance(value.get("depends_on"), list) or not all(
        isinstance(item, str) and WORKER_ID_RE.fullmatch(item) for item in value["depends_on"]
    ):
        raise StateError(f"Invalid dependencies in pending reassignment: {path}")
    if not isinstance(value.get("milestone"), str) or not value["milestone"].strip():
        raise StateError(f"Invalid milestone in pending reassignment: {path}")
    if not isinstance(value.get("invalidate_review"), bool):
        raise StateError(f"Invalid review flag in pending reassignment: {path}")
    try:
        old_status = json.loads(value["old_files"]["STATUS.json"])
        new_status = json.loads(value["new_files"]["STATUS.json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise StateError(f"Invalid status JSON in pending reassignment: {path}") from exc
    if not isinstance(old_status, dict) or not isinstance(new_status, dict):
        raise StateError(f"Invalid status object in pending reassignment: {path}")
    if validate_status_object(old_status, value["worker_id"], path):
        raise StateError(f"Invalid archived assignment status: {path}")
    if validate_status_object(new_status, value["worker_id"], path):
        raise StateError(f"Invalid replacement assignment status: {path}")
    if value.get("executor", "worker") not in {"worker", "lead"}:
        raise StateError(f"Invalid assignment executor: {path}")


def recover_reassignment(runtime: Path, marker_path: Path) -> None:
    marker = read_json(marker_path)
    validate_reassignment_marker(marker, marker_path)
    state = load_state(runtime)
    registry = {item["id"]: item for item in state["workers"]}
    worker_id = marker["worker_id"]
    if worker_id not in registry:
        raise StateError(f"Pending reassignment names unknown Worker: {worker_id}")
    entry = registry[worker_id]
    worker_dir = checked_relative_path(runtime, entry["task_path"], "worker.task_path").parent
    if marker_path.resolve().parent != worker_dir.resolve():
        raise StateError(f"Pending reassignment marker is in the wrong Worker directory: {marker_path}")

    for name in ("TASK.md", "STATUS.json", "BLOCKER.md"):
        current = (worker_dir / name).read_text(encoding="utf-8")
        if current not in {marker["old_files"][name], marker["new_files"][name]}:
            raise StateError(
                f"Pending reassignment conflicts with newer {worker_id}/{name}; "
                "refusing to overwrite it"
            )

    entry["write_scope"] = marker["write_scope"]
    entry["depends_on"] = marker["depends_on"]
    if "executor" in marker:
        entry["executor"] = marker["executor"]
    state["phase"] = "execution"
    state["status"] = "active"
    state["current_milestone"] = marker["milestone"]
    state["next_action"] = {
        "actor": "project-lead" if entry.get("executor") == "lead" or json.loads(marker["new_files"]["STATUS.json"])["status"] == "inactive" else worker_id,
        "instruction": f"Continue {worker_id} with assignment revision {marker['new_revision']}.",
    }
    if marker["invalidate_review"]:
        state["review"]["reviewer_id"] = None
    changes = {str((worker_dir / name).relative_to(runtime).as_posix()): content
               for name, content in marker["new_files"].items()}
    changes["STATE.json"] = json.dumps(state)
    validate_candidate(runtime, changes)
    write_assignment_archive(worker_dir, marker["archived_revision"], marker["old_files"])
    for name, content in marker["new_files"].items():
        atomic_write_text(worker_dir / name, content)
    save_state(runtime, state)
    assert_valid(runtime)
    marker_path.unlink()


def recover_pending_reassignments(runtime: Path) -> None:
    workers_dir = runtime / "workers"
    if not workers_dir.is_dir() or not state_path(runtime).is_file():
        return
    for marker_path in sorted(workers_dir.glob(f"*/{REASSIGNMENT_MARKER}")):
        recover_reassignment(runtime, marker_path)


def command_reassign_worker(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    if not WORKER_ID_RE.fullmatch(args.worker_id):
        raise StateError("worker-id must match worker-N with N greater than zero")
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError("Cannot reassign a Worker in a completed project")
    registry = {item["id"]: item for item in state["workers"]}
    if args.worker_id not in registry:
        raise StateError(f"Unknown Worker: {args.worker_id}")

    entry = registry[args.worker_id]
    worker_dir = checked_relative_path(
        runtime, entry["task_path"], "worker.task_path"
    ).parent
    history_errors = validate_assignment_history(worker_dir, args.worker_id)
    if history_errors:
        raise StateError("\n".join(history_errors))
    status_path_value = checked_relative_path(
        runtime, entry["status_path"], "worker.status_path"
    )
    old_status = read_json(status_path_value)
    from lifecycle import check_control_revision, require_quiescence
    check_control_revision(sys.modules[__name__], runtime, args, entry)
    if old_status["status"] != "completed":
        if not getattr(args, "reason", None):
            raise StateError("Assignment is not completed; stopped reassignment requires --reason and --quiescence-evidence")
        require_nonempty(getattr(args, "reason", None) or "", "reason for non-completed reassignment")
        require_quiescence(sys.modules[__name__], args)

    _, scopes = normalize_worker_assignment(args)
    known_ids = set(registry)
    unknown = sorted(set(args.depends_on) - known_ids)
    if unknown:
        raise StateError(f"Unknown Worker dependencies: {', '.join(unknown)}")
    if args.worker_id in args.depends_on:
        raise StateError(f"{args.worker_id} cannot depend on itself")

    def dependency_reaches(start: str, target: str, seen: set[str]) -> bool:
        if start in seen:
            return False
        if read_json(checked_relative_path(runtime, registry[start]["status_path"], "dependency"))["status"] not in ACTIVE_WORKER_STATUSES:
            return False
        seen.add(start)
        for dependency in registry[start]["depends_on"]:
            if dependency == target or dependency_reaches(dependency, target, seen):
                return True
        return False

    for dependency in args.depends_on:
        if dependency_reaches(dependency, args.worker_id, set()):
            raise StateError(
                f"Reassigning {args.worker_id} would create a Worker dependency cycle"
            )
    for other_id, other in registry.items():
        if other_id == args.worker_id:
            continue
        other_status_path = checked_relative_path(
            runtime, other["status_path"], f"{other_id}.status_path"
        )
        other_status = read_json(other_status_path)["status"]
        if other_status in ACTIVE_WORKER_STATUSES and args.worker_id in other["depends_on"]:
            raise StateError(
                f"Cannot reassign {args.worker_id}; nonterminal dependent {other_id} "
                "still requires its completed assignment"
            )
        if other_status not in ACTIVE_WORKER_STATUSES:
            continue
        overlap = sorted(
            f"{scope} <-> {other_scope}"
            for scope in scopes
            for other_scope in other["write_scope"]
            if scopes_overlap(scope, other_scope)
        )
        if overlap:
            raise StateError(
                f"Write scope overlaps with {other_id}: {', '.join(overlap)}"
            )

    review_status_file = runtime / "review" / "STATUS.json"
    review_status = read_json(review_status_file) if review_status_file.is_file() else {}
    if (
        state["review"]["required"]
        and state["review"]["reviewer_id"] is not None
        and review_status.get("status") != "completed"
    ):
        raise StateError("Cannot return to execution while the assigned review is unfinished")

    archived_revision = max(archived_assignment_revisions(worker_dir), default=0) + 1
    new_revision = archived_revision + 1
    new_status = read_template_json("worker-status.json")
    new_status.update(
        {
            "worker_id": args.worker_id,
            "summary": f"Assignment revision {new_revision} is ready.",
            "last_updated": utc_now(),
        }
    )
    marker = {
        "schema_version": SCHEMA_VERSION,
        "worker_id": args.worker_id,
        "archived_revision": archived_revision,
        "new_revision": new_revision,
        "old_files": {
            name: (worker_dir / name).read_text(encoding="utf-8")
            for name in ("TASK.md", "STATUS.json", "BLOCKER.md")
        },
        "new_files": {
            "TASK.md": build_worker_task(args, new_revision),
            "STATUS.json": json.dumps(new_status, indent=2, ensure_ascii=False) + "\n",
            "BLOCKER.md": read_template_text("blocker.md"),
        },
        "write_scope": scopes,
        "depends_on": list(args.depends_on),
        "milestone": require_nonempty(args.milestone, "milestone"),
        "invalidate_review": bool(
            state["review"]["required"] and state["review"]["reviewer_id"] is not None
        ),
    }
    if "executor" in entry or old_status["status"] != "completed":
        marker["executor"] = "worker"
    if getattr(args, "reason", None):
        marker["reason"] = args.reason
        marker["new_files"]["TASK.md"] += (f"\n## Latest control\n\nReassigned: {args.reason}\n"
                                             f"Stopped writer: {args.quiescence_evidence or 'Prior assignment completed'}\n")
    entry.update(write_scope=scopes, depends_on=list(args.depends_on))
    if "executor" in marker:
        entry["executor"] = marker["executor"]
    state.update(phase="execution", status="active", current_milestone=marker["milestone"])
    if marker["invalidate_review"]:
        state["review"]["reviewer_id"] = None
    proposed = {f"workers/{args.worker_id}/{name}": content for name, content in marker["new_files"].items()}
    proposed["STATE.json"] = json.dumps(state)
    validate_candidate(runtime, proposed)
    atomic_write_json(worker_dir / REASSIGNMENT_MARKER, marker)
    recover_reassignment(runtime, worker_dir / REASSIGNMENT_MARKER)
    assert_valid(runtime)
    print(
        f"PROJECT_LEAD reassigned {args.worker_id}: {old_status['status']} -> ready "
        f"(assignment {new_revision}; archived assignment {archived_revision})"
    )
    return 0


WORKER_TRANSITIONS = {
    "ready": {"active", "blocked", "waiting-owner", "completed", "inactive"},
    "active": {"blocked", "waiting-owner", "completed", "inactive"},
    "blocked": {"active", "waiting-owner", "inactive"},
    "waiting-owner": {"active", "blocked", "inactive"},
    "completed": {"inactive"},
    "inactive": set(),
}


def command_set_worker_status(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError("Cannot update Worker status in a completed project; reopen it first")
    registry = {item["id"]: item for item in state["workers"]}
    if args.worker_id not in registry:
        raise StateError(f"Unknown Worker: {args.worker_id}")
    from lifecycle import check_control_revision
    check_control_revision(sys.modules[__name__], runtime, args, registry[args.worker_id])
    if getattr(args, "actor", "worker") != registry[args.worker_id].get("executor", "worker"):
        raise StateError("Only the current executor may update this assignment")
    expected_revision = getattr(args, "assignment_revision", None)
    if expected_revision is not None:
        from relay import worker
        if worker(sys.modules[__name__], runtime, args.worker_id)[3] != expected_revision:
            raise StateError("Stale assignment status update")
    status_path_value = checked_relative_path(
        runtime, registry[args.worker_id]["status_path"], "worker.status_path"
    )
    status = read_json(status_path_value)
    original_status = dict(status)
    old_status = status["status"]
    if args.status != old_status and args.status not in WORKER_TRANSITIONS[old_status]:
        raise StateError(f"Invalid Worker transition: {old_status} -> {args.status}")
    if args.status in {"active", "completed"} and args.status != old_status:
        for dependency in registry[args.worker_id]["depends_on"]:
            dependency_status_path = checked_relative_path(
                runtime, registry[dependency]["status_path"], "dependency.status_path"
            )
            dependency_status = read_json(dependency_status_path)["status"]
            if dependency_status != "completed":
                raise StateError(
                    f"Cannot activate {args.worker_id}; dependency {dependency} "
                    f"is {dependency_status}"
                )
    status["status"] = args.status
    status["summary"] = require_nonempty(args.summary, "summary")
    status["next_action"] = args.next_action.strip()
    if args.files_changed is not None:
        status["files_changed"] = args.files_changed
    if args.verification is not None:
        status["verification"] = args.verification
    if status == original_status:
        print(f"Unchanged {args.worker_id}: {old_status}")
        return 0
    status["last_updated"] = utc_now()
    previous_status_text = status_path_value.read_text(encoding="utf-8")
    atomic_write_json(status_path_value, status)
    try:
        assert_valid(runtime)
    except Exception:
        atomic_write_text(status_path_value, previous_status_text)
        raise
    print(f"Updated {args.worker_id}: {old_status} -> {args.status}")
    return 0


def completion_history_revisions(runtime: Path) -> list[int]:
    history_dir = runtime / "history"
    if not history_dir.is_dir():
        return []
    return [
        int(match.group(1))
        for path in history_dir.iterdir()
        if path.is_dir() and (match := COMPLETION_HISTORY_RE.fullmatch(path.name)) is not None
    ]


def archive_project_completion(runtime: Path, reason: str) -> int:
    runtime = runtime.resolve()
    state = load_state(runtime)
    history_errors = validate_completion_history(runtime, state["project_id"])
    if history_errors:
        raise StateError("\n".join(history_errors))
    current_state_text = state_path(runtime).read_text(encoding="utf-8")
    history_dir = runtime / "history"
    history_dir.mkdir(exist_ok=True)
    revisions = completion_history_revisions(runtime)
    if revisions:
        latest = history_dir / f"completion-{max(revisions):04d}"
        archived_state = latest / "STATE.json"
        if archived_state.is_file() and archived_state.read_text(encoding="utf-8") == current_state_text:
            return max(revisions)

    revision = max(revisions, default=0) + 1
    destination = history_dir / f"completion-{revision:04d}"
    temporary = Path(
        tempfile.mkdtemp(prefix=f".completion-{revision:04d}-", dir=history_dir)
    )
    try:
        for name in ("STATE.json", "memory.jsonl"):
            write_new_text(temporary / name, (runtime / name).read_text(encoding="utf-8"))
        for name in ("PLAN.md", "OWNER_DIRECTIVES.md", "HANDOFF.md",
                     "OWNER_STATUS.md", "PROJECT_STATUS.md"):
            # Absent for a minimal standalone project; archive only what exists.
            optional = runtime / name
            if optional.is_file():
                write_new_text(temporary / name, optional.read_text(encoding="utf-8"))
        for worker in state["workers"]:
            task = checked_relative_path(runtime, worker["task_path"], "worker.task_path")
            status = checked_relative_path(runtime, worker["status_path"], "worker.status_path")
            snapshot_worker_dir = temporary / task.parent.relative_to(runtime)
            write_new_text(
                temporary / task.relative_to(runtime), task.read_text(encoding="utf-8")
            )
            write_new_text(
                temporary / status.relative_to(runtime), status.read_text(encoding="utf-8")
            )
            write_new_text(
                snapshot_worker_dir / "BLOCKER.md",
                (task.parent / "BLOCKER.md").read_text(encoding="utf-8"),
            )
        for key in ("task_path", "status_path", "report_path"):
            source = checked_relative_path(runtime, state["review"][key], f"review.{key}")
            # Review files only exist once a review has been assigned.
            if source.is_file():
                write_new_text(
                    temporary / source.relative_to(runtime), source.read_text(encoding="utf-8")
                )
        write_new_json(
            temporary / "REOPEN.json",
            {
                "schema_version": SCHEMA_VERSION,
                "reopened_at": utc_now(),
                "reason": reason,
            },
        )
        os.replace(temporary, destination)
        for stale in history_dir.glob(f".completion-{revision:04d}-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return revision


def command_reopen_project(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    state = load_state(runtime)
    if state["phase"] != "complete" or state["status"] != "complete":
        raise StateError("Only a complete/complete project can be reopened")
    reason = require_nonempty(args.reason, "reason")
    milestone = require_nonempty(args.milestone, "milestone")
    revision = archive_project_completion(runtime, reason)

    state["phase"] = "planning"
    state["status"] = "active"
    state["current_milestone"] = milestone
    state["review"].update({"required": False, "level": "none", "reviewer_id": None})
    state["next_action"] = {
        "actor": "project-lead",
        "instruction": "Interpret the actionable Owner request and reassign a suitable completed Worker.",
    }
    save_state(runtime, state)
    assert_valid(runtime)
    print(
        f"PROJECT_LEAD reopened project: complete -> planning/active "
        f"(archived completion {revision})"
    )
    return 0


PHASE_TRANSITIONS = {
    "planning": {"planning", "execution", "complete"},
    "execution": {"execution", "planning", "review", "complete"},
    "review": {"review", "execution", "complete"},
    "complete": {"complete"},
}
PROJECT_STATUS_TRANSITIONS = {
    "active": {"active", "blocked", "waiting-owner", "complete"},
    "blocked": {"active", "blocked", "waiting-owner", "complete"},
    "waiting-owner": {"active", "blocked", "waiting-owner", "complete"},
    "complete": {"complete"},
}


def require_completion_ready(runtime: Path, state: dict[str, Any]) -> None:
    unfinished: list[str] = []
    for worker in state["workers"]:
        status_path_value = checked_relative_path(
            runtime, worker["status_path"], "worker.status_path"
        )
        status = read_json(status_path_value)["status"]
        if status not in {"completed", "inactive"}:
            unfinished.append(f"{worker['id']}={status}")
    if unfinished:
        raise StateError(
            "Cannot complete project; unfinished Workers: " + ", ".join(unfinished)
        )
    if pending_owner_events(runtime):
        raise StateError("Cannot complete project while Owner feedback is pending")
    if state["review"]["required"]:
        review_status_file = runtime / "review" / "STATUS.json"
        review_status = read_json(review_status_file) if review_status_file.is_file() else {}
        if (
            state["review"]["reviewer_id"] is None
            or review_status.get("status") != "completed"
            or review_status.get("reviewer_id") != state["review"]["reviewer_id"]
            or review_status.get("verdict") != "approved"
        ):
            raise StateError("Cannot complete project; required review is unfinished or stale, or not approved")


def command_set_project(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError("Completed project state is frozen; use reopen-project for actionable work")
    phase = args.phase or state["phase"]
    status = args.status or state["status"]
    if phase not in PHASE_TRANSITIONS[state["phase"]]:
        raise StateError(f"Invalid project phase transition: {state['phase']} -> {phase}")
    if status not in PROJECT_STATUS_TRANSITIONS[state["status"]]:
        raise StateError(f"Invalid project status transition: {state['status']} -> {status}")
    if (phase == "complete") != (status == "complete"):
        raise StateError("phase and status must both be complete or both be non-complete")
    if phase == "review" and (
        not state["review"]["required"] or state["review"]["reviewer_id"] is None
    ):
        raise StateError("Cannot enter review phase without an assigned review")
    if state["phase"] == "review" and phase == "execution":
        review_status_file = runtime / "review" / "STATUS.json"
        review_status = read_json(review_status_file) if review_status_file.is_file() else {}
        if (
            state["review"]["reviewer_id"] is None
            or review_status.get("reviewer_id") != state["review"]["reviewer_id"]
            or review_status.get("status") != "completed"
        ):
            raise StateError("Cannot leave review for execution while review is unfinished")
        state["review"]["reviewer_id"] = None
    if phase == "complete":
        require_completion_ready(runtime, state)
    if args.mode and args.mode != state["mode"]:
        if args.mode == "standalone" and state["workers"]:
            raise StateError("Cannot return to standalone while Workers are registered")
        if args.mode == "standalone" and state["review"]["required"]:
            raise StateError("Cannot return to standalone while a review is required")
        state["mode"] = args.mode
    state["phase"] = phase
    state["status"] = status
    if args.milestone:
        state["current_milestone"] = require_nonempty(args.milestone, "milestone")
    if args.next_actor or args.next_action:
        if not (args.next_actor and args.next_action):
            raise StateError("--next-actor and --next-action must be supplied together")
        if phase == "complete" and args.next_actor.upper() != "OWNER":
            raise StateError("A completed project's next actor must be OWNER")
        state["next_action"] = {
            "actor": require_nonempty(args.next_actor, "next-actor"),
            "instruction": require_nonempty(args.next_action, "next-action"),
        }
    elif phase == "complete":
        state["next_action"] = {
            "actor": "OWNER",
            "instruction": "Project complete; answer read-only questions or explicitly reopen for actionable work.",
        }
    previous_state_text = state_path(runtime).read_text(encoding="utf-8")
    save_state(runtime, state)
    try:
        assert_valid(runtime)
    except Exception:
        atomic_write_text(state_path(runtime), previous_state_text)
        raise
    print(f"Project is {phase}/{status}")
    return 0


def review_history_revisions(runtime: Path) -> list[int]:
    history_dir = runtime / "review" / "history"
    if not history_dir.is_dir():
        return []
    return [
        int(match.group(1))
        for path in history_dir.iterdir()
        if path.is_dir() and (match := REVIEW_HISTORY_RE.fullmatch(path.name)) is not None
    ]


def write_review_archive(
    runtime: Path, revision: int, files: dict[str, str]
) -> None:
    review_dir = runtime / "review"
    history_dir = review_dir / "history"
    history_dir.mkdir(exist_ok=True)
    destination = history_dir / f"review-{revision:04d}"
    if destination.exists():
        for name, content in files.items():
            if (destination / name).read_text(encoding="utf-8") != content:
                raise StateError(f"Existing review archive conflicts: {destination}")
        for stale in history_dir.glob(f".review-{revision:04d}-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
        return
    temporary = Path(tempfile.mkdtemp(prefix=f".review-{revision:04d}-", dir=history_dir))
    try:
        for name, content in files.items():
            write_new_text(temporary / name, content)
        os.replace(temporary, destination)
        for stale in history_dir.glob(f".review-{revision:04d}-*"):
            if stale.is_dir():
                shutil.rmtree(stale)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def validate_review_assignment_marker(value: dict[str, Any], path: Path) -> None:
    required = {
        "schema_version",
        "reviewer_id",
        "level",
        "archive_revision",
        "old_files",
        "new_files",
    }
    if set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
        raise StateError(f"Invalid pending review marker: {path}")
    if not isinstance(value.get("reviewer_id"), str) or not REVIEWER_ID_RE.fullmatch(
        value["reviewer_id"]
    ):
        raise StateError(f"Invalid reviewer in pending review marker: {path}")
    if value.get("level") not in {"balanced", "strong"}:
        raise StateError(f"Invalid level in pending review marker: {path}")
    archive_revision = value.get("archive_revision")
    if archive_revision is not None and (
        not isinstance(archive_revision, int) or archive_revision < 1
    ):
        raise StateError(f"Invalid archive revision in pending review marker: {path}")
    for key in ("old_files", "new_files"):
        files = value.get(key)
        if not isinstance(files, dict) or set(files) != {"TASK.md", "STATUS.json", "REPORT.md"}:
            raise StateError(f"Invalid {key} in pending review marker: {path}")
        if not all(isinstance(content, str) for content in files.values()):
            raise StateError(f"Invalid {key} content in pending review marker: {path}")
    # A first assignment rewrites nothing, so old_files may hold empty strings;
    # only a non-empty prior status has to parse as a valid review status.
    if value["old_files"]["STATUS.json"]:
        try:
            old_status = json.loads(value["old_files"]["STATUS.json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise StateError(f"Invalid status JSON in pending review marker: {path}") from exc
        if not isinstance(old_status, dict):
            raise StateError(f"Invalid status object in pending review marker: {path}")
        if validate_review_status(old_status, path):
            raise StateError(f"Invalid archived review: {path}")
    try:
        new_status = json.loads(value["new_files"]["STATUS.json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise StateError(f"Invalid status JSON in pending review marker: {path}") from exc
    if not isinstance(new_status, dict):
        raise StateError(f"Invalid status object in pending review marker: {path}")
    if validate_review_status(new_status, path) or (
        new_status.get("reviewer_id") != value["reviewer_id"]
        or new_status.get("status") != "ready"
    ):
        raise StateError(f"Pending review does not publish the expected ready status: {path}")


def recover_review_assignment(runtime: Path, marker_path: Path) -> None:
    marker = read_json(marker_path)
    validate_review_assignment_marker(marker, marker_path)
    review_dir = runtime / "review"
    for name in ("TASK.md", "STATUS.json", "REPORT.md"):
        target = review_dir / name
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current not in {marker["old_files"][name], marker["new_files"][name]}:
            raise StateError(
                f"Pending review assignment conflicts with newer review/{name}; "
                "refusing to overwrite it"
            )
    state = load_state(runtime)
    state["review"].update(
        {
            "required": True,
            "level": marker["level"],
            "reviewer_id": marker["reviewer_id"],
        }
    )
    state["phase"] = "review"
    state["status"] = "active"
    state["next_action"] = {
        "actor": marker["reviewer_id"],
        "instruction": f"Continue {marker['reviewer_id']} and complete the assigned review.",
    }
    changes = {"review/" + name: content for name, content in marker["new_files"].items()}
    changes["STATE.json"] = json.dumps(state)
    validate_candidate(runtime, changes)
    if marker["archive_revision"] is not None:
        write_review_archive(runtime, marker["archive_revision"], marker["old_files"])
    for name, content in marker["new_files"].items():
        atomic_write_text(review_dir / name, content)
    save_state(runtime, state)
    assert_valid(runtime)
    marker_path.unlink()


def recover_pending_review_assignment(runtime: Path) -> None:
    marker_path = runtime / "review" / REVIEW_ASSIGNMENT_MARKER
    if marker_path.is_file():
        recover_review_assignment(runtime, marker_path)


def command_assign_review(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    history_errors = validate_review_history(runtime / "review")
    if history_errors:
        raise StateError("\n".join(history_errors))
    if not REVIEWER_ID_RE.fullmatch(args.reviewer_id):
        raise StateError("reviewer-id must match reviewer-N with N greater than zero")
    if args.level not in {"balanced", "strong"}:
        raise StateError("Review level must be balanced or strong")
    if args.level == "strong":
        strong_justification = require_nonempty(
            args.strong_justification or "", "strong-justification"
        )
    else:
        strong_justification = args.strong_justification or "Not required."
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError("Cannot assign review to a completed project")
    current_status_path = runtime / "review" / "STATUS.json"
    current_status = read_json(current_status_path) if current_status_path.is_file() else {}
    if (
        state["review"]["required"]
        and state["review"]["reviewer_id"] is not None
        and current_status.get("status", "not-requested") not in {"completed", "not-requested"}
    ):
        raise StateError("A review assignment is already active")
    unfinished_workers = []
    for worker in state["workers"]:
        worker_status_path = checked_relative_path(
            runtime, worker["status_path"], "worker.status_path"
        )
        worker_status = read_json(worker_status_path)["status"]
        if worker_status not in {"completed", "inactive"}:
            unfinished_workers.append(f"{worker['id']}={worker_status}")
    if unfinished_workers:
        raise StateError(
            "Cannot assign review while Workers are unfinished: "
            + ", ".join(unfinished_workers)
        )
    task = (
        f"# Review Task: {args.reviewer_id}\n\n"
        f"## Objective\n\n{require_nonempty(args.objective, 'objective')}\n\n"
        f"## Review level\n\n{args.level}\n\n"
        f"## Tier justification\n\n{strong_justification}\n\n"
        f"## Scope\n\n{markdown_list(args.scope)}\n\n"
        f"## Completion criteria\n\n{markdown_list(args.completion_criterion)}\n"
    )
    review_status = read_template_json("review-status.json")
    review_status.update(
        {
            "reviewer_id": args.reviewer_id,
            "status": "ready",
            "summary": "Review assignment is ready.",
            "last_updated": utc_now(),
            "revision": current_status.get("revision", 0) + 1,
            "verdict": None,
        }
    )
    review_dir = runtime / "review"
    # Reviews are opt-in: the directory only appears once a review is assigned.
    review_dir.mkdir(parents=True, exist_ok=True)
    # A first assignment has no previous files; the recovery marker records the
    # empty string so a crash mid-assignment still restores a known state.
    old_files = {
        name: (
            (review_dir / name).read_text(encoding="utf-8")
            if (review_dir / name).is_file()
            else ""
        )
        for name in ("TASK.md", "STATUS.json", "REPORT.md")
    }
    marker = {
        "schema_version": SCHEMA_VERSION,
        "reviewer_id": args.reviewer_id,
        "level": args.level,
        "archive_revision": (
            max(review_history_revisions(runtime), default=0) + 1
            if current_status.get("status") == "completed"
            else None
        ),
        "old_files": old_files,
        "new_files": {
            "TASK.md": task,
            "STATUS.json": json.dumps(review_status, indent=2, ensure_ascii=False) + "\n",
            "REPORT.md": read_template_text("review-report.md"),
        },
    }
    marker_path = review_dir / REVIEW_ASSIGNMENT_MARKER
    state["review"].update(required=True, level=args.level, reviewer_id=args.reviewer_id)
    state.update(phase="review", status="active")
    proposed = {"review/" + name: content for name, content in marker["new_files"].items()}
    proposed["STATE.json"] = json.dumps(state)
    validate_candidate(runtime, proposed)
    atomic_write_json(marker_path, marker)
    recover_review_assignment(runtime, marker_path)
    assert_valid(runtime)
    print(f"Assigned {args.reviewer_id} ({args.level})")
    return 0


REVIEW_TRANSITIONS = {
    "not-requested": {"ready"},
    "ready": {"active", "blocked", "completed"},
    "active": {"blocked", "completed"},
    "blocked": {"active", "completed"},
    "completed": set(),
}


def command_set_review_status(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError("Cannot update review status in a completed project; reopen it first")
    if not state["review"]["required"] or state["review"]["reviewer_id"] is None:
        raise StateError("No review assignment is active")
    path = runtime / "review" / "STATUS.json"
    status = read_json(path)
    original_status = dict(status)
    if status["reviewer_id"] != args.reviewer_id:
        raise StateError("Reviewer ID does not match the active assignment")
    if "revision" in status and args.assignment_revision != status["revision"]:
        raise StateError("Current review --assignment-revision is required")
    old_status = status["status"]
    if args.status != old_status and args.status not in REVIEW_TRANSITIONS[old_status]:
        raise StateError(f"Invalid review transition: {old_status} -> {args.status}")
    status["status"] = args.status
    if args.verdict is not None:
        if args.status != "completed":
            raise StateError("A verdict requires a completed review")
        status["verdict"] = args.verdict
    elif args.status != "completed":
        status["verdict"] = None
    status["summary"] = require_nonempty(args.summary, "summary")
    if args.verification is not None:
        status["verification"] = args.verification
    if status == original_status:
        print(f"Unchanged review: {old_status}")
        return 0
    status["last_updated"] = utc_now()
    previous_status_text = path.read_text(encoding="utf-8")
    atomic_write_json(path, status)
    try:
        assert_valid(runtime)
    except Exception:
        atomic_write_text(path, previous_status_text)
        raise
    print(f"Updated review: {old_status} -> {args.status}")
    return 0


def command_record_owner_feedback(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    if not WORKER_ID_RE.fullmatch(args.worker_id):
        raise StateError("worker-id must match worker-N")
    state = load_state(runtime)
    if state["status"] == "complete":
        raise StateError(
            "Completed project feedback must be classified by PROJECT_LEAD: "
            "answer read-only requests without mutation or use reopen-project for actionable work"
        )
    if args.worker_id not in {item["id"] for item in state["workers"]}:
        raise StateError(f"Unknown Worker: {args.worker_id}")
    message = require_nonempty(args.message, "message")
    now = utc_now()
    event_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{args.worker_id}-"
        + uuid.uuid4().hex[:8]
    )
    content = read_template_text("owner-feedback.md")
    replacements = {
        "{{EVENT_ID}}": event_id,
        "{{WORKER_ID}}": args.worker_id,
        "{{CREATED_AT}}": now,
        "{{RAW_MESSAGE}}": message,
    }
    for old, new in replacements.items():
        content = content.replace(old, new)
    path = runtime / "inbox" / "owner" / f"{event_id}.md"
    write_new_text(path, content)
    print(path)
    return 0


def owner_event_frontmatter(content: str, path: Path) -> tuple[re.Match[str], str]:
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", content, re.DOTALL)
    if frontmatter is None:
        raise StateError(f"Owner feedback event has no valid frontmatter: {path}")
    status = re.search(
        r"(?m)^status:\s*[\"']?([a-z-]+)[\"']?\s*$", frontmatter.group(1)
    )
    if status is None:
        raise StateError(f"Owner feedback event has no valid status: {path}")
    return frontmatter, status.group(1)


def command_resolve_owner_feedback(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    runtime = runtime_dir(root)
    assert_valid(runtime)
    if not OWNER_EVENT_ID_RE.fullmatch(args.event_id):
        raise StateError("event-id contains invalid characters")
    resolution = require_nonempty(args.resolution, "resolution")
    path = runtime / "inbox" / "owner" / f"{args.event_id}.md"
    if not path.exists():
        archived = runtime / "inbox" / "owner" / "history" / f"{args.event_id}.md"
        if archived.is_file():
            path = archived
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StateError(f"Unknown Owner feedback event: {args.event_id}") from exc
    frontmatter, status = owner_event_frontmatter(content, path)
    if status == "resolved":
        print(f"Owner feedback already resolved; no files changed: {args.event_id}")
        return 0
    if status != "pending":
        raise StateError(f"Owner feedback event is not pending: {args.event_id}")
    updated_frontmatter = re.sub(
        r"(?m)^status:\s*[\"']?pending[\"']?\s*$",
        'status: "resolved"',
        frontmatter.group(0),
        count=1,
    )
    updated = updated_frontmatter + content[frontmatter.end() :]
    updated = (
        updated.rstrip()
        + "\n\n## Project Lead Resolution\n\n"
        + f"Resolved at: {utc_now()}\n\n{resolution}\n"
    )
    atomic_write_text(path, updated)
    print(f"Resolved Owner feedback: {args.event_id}")
    return 0


def pending_owner_events(runtime: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted((runtime / "inbox" / "owner").glob("*.md")):
        content = path.read_text(encoding="utf-8")
        _, status = owner_event_frontmatter(content, path)
        if status == "pending":
            result.append(path)
    return result


def status_snapshot(runtime: Path, *, validate: bool = True) -> dict[str, Any]:
    if validate:
        assert_valid(runtime)
    state = load_state(runtime)
    workers: list[dict[str, Any]] = []
    for registry in state["workers"]:
        path = checked_relative_path(runtime, registry["status_path"], "worker.status_path")
        status = read_json(path)
        workers.append(
            {
                "id": registry["id"],
                "status": status["status"],
                "summary": status["summary"],
                "verification": status["verification"],
                "next_action": status["next_action"],
            }
        )
    review_assigned = state["review"]["reviewer_id"] is not None
    # review/STATUS.json only exists once a review has actually been assigned.
    review_status_path = runtime / "review" / "STATUS.json"
    review_status = read_json(review_status_path) if review_status_path.is_file() else {}
    return {
        "project_id": state["project_id"],
        "mode": state["mode"],
        "phase": state["phase"],
        "status": state["status"],
        "current_milestone": state["current_milestone"],
        "workers": workers,
        "review": {
            "required": state["review"]["required"],
            "level": state["review"]["level"],
            "reviewer_id": state["review"]["reviewer_id"],
            "status": review_status.get("status", "not-requested") if review_assigned else "not-assigned",
            "summary": (
                review_status.get("summary", "")
                if review_assigned
                else ("Review is required but not assigned." if state["review"]["required"] else "")
            ),
        },
        "owner_status": "OWNER_STATUS.md" if (runtime / "OWNER_STATUS.md").is_file() else None,
        "project_status": "PROJECT_STATUS.md" if (runtime / "PROJECT_STATUS.md").is_file() else None,
        "pending_owner_feedback": len(pending_owner_events(runtime)),
        "next_action": state["next_action"],
        "last_updated": state["last_updated"],
    }


def render_status(snapshot: dict[str, Any]) -> str:
    lines = [
        f"Project: {snapshot['project_id']}",
        f"State: {snapshot['phase']} / {snapshot['status']} ({snapshot['mode']})",
        f"Milestone: {snapshot['current_milestone']}",
        "Workers:",
    ]
    if snapshot["workers"]:
        for worker in snapshot["workers"]:
            summary = worker["summary"] or "No summary yet."
            lines.append(f"- {worker['id']}: {worker['status']} — {summary}")
    else:
        lines.append("- None registered.")
    review = snapshot["review"]
    lines.append(
        "Review: "
        + (
            f"{review['status']} ({review['level']}, {review['reviewer_id']}) — "
            f"{review['summary'] or 'No summary yet.'}"
            if review["required"]
            else "not requested"
        )
    )
    lines.append(f"Pending Owner feedback: {snapshot['pending_owner_feedback']}")
    # PROJECT_STATUS.md is the human page in v1.1; OWNER_STATUS.md is a legacy
    # variant that is only mentioned when a pre-v1.1 project still carries one.
    if snapshot["project_status"]:
        lines.append(f"Human page: .agent-project-manager/{snapshot['project_status']}")
    if snapshot["owner_status"]:
        lines.append(f"Owner summary: .agent-project-manager/{snapshot['owner_status']}")
    next_action = snapshot["next_action"]
    lines.append(f"Next: {next_action['actor']} — {next_action['instruction']}")
    return "\n".join(lines)


def render_project_status(runtime: Path, snapshot: dict[str, Any]) -> str:
    """Render the human-facing bilingual page. Empty sections are omitted."""
    from memory import read_entries

    def bilingual(zh: str, en: str) -> str:
        return f"{zh} / {en}"

    entries = read_entries(runtime) if (runtime / "memory.jsonl").is_file() else []
    goals = [entry["text"] for entry in entries if entry["kind"] in {"human-intent", "direction"}]
    decisions = [entry["text"] for entry in entries if entry["kind"] in {"decision", "rejected"}]
    risks = [entry["text"] for entry in entries if entry["kind"] in {"constraint", "open-question"}]

    lines = [
        f"# Project Status: {snapshot['project_id']}",
        "",
        f"_{bilingual('更新', 'Updated')}: {snapshot['last_updated']} · "
        f"{bilingual('模式', 'Mode')}: {snapshot['mode']}_",
        "",
    ]

    if goals:
        lines += [f"## {bilingual('目标', 'Goal')}", ""]
        lines += [f"- {text}" for text in goals]
        lines.append("")

    lines += [f"## {bilingual('当前状态', 'Current state')}", "", snapshot["current_milestone"], ""]

    active = [w for w in snapshot["workers"] if w["status"] in ACTIVE_WORKER_STATUSES]
    recent = [w for w in snapshot["workers"] if w["status"] not in ACTIVE_WORKER_STATUSES]
    if recent:
        lines += [f"## {bilingual('最近进展', 'Recent progress')}", ""]
        lines += [f"- {w['id']}: {w['status']} — {w['summary'] or 'No summary.'}" for w in recent]
        lines.append("")

    open_tasks = [f"- {w['id']}: {w['status']} — {w['next_action'] or w['summary']}" for w in active]
    if snapshot["pending_owner_feedback"]:
        open_tasks.append(f"- {bilingual('待用户决定', 'Awaiting an Owner decision')}: "
                          f"{snapshot['pending_owner_feedback']}")
    if open_tasks:
        lines += [f"## {bilingual('待办', 'Open tasks')}", "", *open_tasks, ""]

    if decisions:
        lines += [f"## {bilingual('关键决策', 'Key decisions')}", ""]
        lines += [f"- {text}" for text in decisions]
        lines.append("")

    if risks:
        lines += [f"## {bilingual('风险', 'Risks')}", ""]
        lines += [f"- {text}" for text in risks]
        lines.append("")

    next_action = snapshot["next_action"]
    lines += [f"## {bilingual('下一步', 'Next step')}", "",
              f"{next_action['actor']} — {next_action['instruction']}", ""]
    return "\n".join(lines)


def synthesize_handoff(
    runtime: Path, state: dict[str, Any], snapshot: dict[str, Any]
) -> str:
    """Build a cold-start briefing from files that already exist.

    v1.1 removed HANDOFF.md: it duplicated STATE.json, memory.jsonl and PLAN.md,
    and a duplicated hand-written summary is exactly the kind of state that goes
    stale. This renders the same information on demand instead, so a fresh Lead
    conversation can still pick the project up with no prior chat.
    """
    from memory import read_entries

    entries = read_entries(runtime) if (runtime / "memory.jsonl").is_file() else []

    def texts(kinds: set[str]) -> list[str]:
        return [entry["text"] for entry in entries if entry["kind"] in kinds]

    goals = texts({"human-intent"})
    directions = texts({"direction"})
    decisions = texts({"decision", "rejected"})
    constraints = texts({"constraint"})
    questions = texts({"open-question"})

    lines = [
        "# Handoff",
        "",
        "## Final goal / 最终目标",
    ]
    lines += [f"- {text}" for text in goals] or ["- (Not recorded yet.)"]
    if directions:
        lines += ["", "## Direction / 方向"] + [f"- {text}" for text in directions]
    lines += [
        "",
        "## Current position / 当前位置",
        f"- Phase: {state['phase']} · Status: {state['status']} · Mode: {state['mode']}",
        f"- Milestone: {state['current_milestone']}",
    ]

    completed = [w for w in snapshot["workers"] if w["status"] in {"completed", "inactive"}]
    active = [w for w in snapshot["workers"] if w["status"] in ACTIVE_WORKER_STATUSES]
    if completed:
        lines += ["", "## Completed / 已完成"]
        lines += [f"- {w['id']}: {w['summary'] or w['status']}" for w in completed]
    if active:
        lines += ["", "## Active roles / 进行中的角色"]
        lines += [f"- {w['id']}: {w['status']} — {w['next_action'] or w['summary']}" for w in active]
    if not snapshot["workers"]:
        lines += ["", "## Active roles / 进行中的角色", "- None; standalone mode runs in one conversation."]

    if decisions:
        lines += ["", "## Important decisions / 重要决策"]
        lines += [f"- {text}" for text in decisions]
    if constraints:
        lines += ["", "## Constraints / 约束"]
        lines += [f"- {text}" for text in constraints]
    if questions:
        lines += ["", "## Open questions / 待确认"]
        lines += [f"- {text}" for text in questions]

    plan_path = runtime / "PLAN.md"
    if plan_path.is_file():
        lines += ["", "## Plan / 计划", f"- Read {plan_path}."]
    lines += [
        "",
        "## Next action / 下一步",
        f"- {state['next_action']['actor']} — {state['next_action']['instruction']}",
        "",
    ]
    return "\n".join(lines)


def command_status_refresh(args: argparse.Namespace) -> int:
    runtime = runtime_dir(project_root(args.project_root))
    snapshot = status_snapshot(runtime)
    if snapshot["status"] == "complete" and not args.force:
        raise StateError("Completed project is frozen; pass --force to refresh its status page")
    target = runtime / "PROJECT_STATUS.md"
    if target.is_file() and not args.overwrite:
        raise StateError(
            f"{target.name} already exists; edit it directly or pass --overwrite to regenerate"
        )
    atomic_write_text(target, render_project_status(runtime, snapshot))
    print(f"Wrote {target}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    runtime = runtime_dir(project_root(args.project_root))
    errors = validate_runtime(runtime)
    if not errors and (runtime / "TRANSPORT.json").exists():
        from relay import load_transport, bound
        try:
            transport = load_transport(sys.modules[__name__], runtime)
            for role in transport["bindings"]:
                bound(sys.modules[__name__], runtime, transport, role, require_current=False)
        except StateError as exc:
            errors.append(str(exc))
    if not errors and (runtime / "WORKSPACE.json").exists():
        from workspace import load, checked_path
        try:
            workspace_state = load(sys.modules[__name__], runtime)
            for item in workspace_state["artifacts"]:
                checked_path(sys.modules[__name__], runtime.parent, item["path"])
                if item.get("location"):
                    checked_path(sys.modules[__name__], runtime.parent, item["location"], storage=True)
        except StateError as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Valid runtime: {runtime}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    runtime = runtime_dir(project_root(args.project_root))
    snapshot = status_snapshot(runtime)
    if args.json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    else:
        print(render_status(snapshot))
    return 0


def common_project_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=".", help="Repository root (default: current directory)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, validate, and summarize agent-project-manager runtime state."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize .agent-project-manager without overwriting")
    common_project_root(init_parser)
    init_parser.add_argument("--project-id", required=True)
    init_parser.add_argument("--profile", default="generic")
    init_parser.add_argument("--mode", choices=sorted(MODES), default="standalone")
    init_parser.set_defaults(func=command_init)

    add_parser = subparsers.add_parser("add-worker", help="Register a bounded Worker assignment")
    common_project_root(add_parser)
    add_parser.add_argument("--worker-id", required=True)
    add_parser.add_argument("--objective", required=True)
    add_parser.add_argument("--allowed-scope", action="append", required=True)
    add_parser.add_argument("--read-dependency", action="append", default=[])
    add_parser.add_argument("--do-not-modify", action="append", default=[])
    add_parser.add_argument("--depends-on", action="append", default=[])
    add_parser.add_argument("--plan-section", action="append", default=[])
    add_parser.add_argument("--completion-criterion", action="append", required=True)
    add_parser.add_argument("--coordination-justification")
    add_parser.set_defaults(func=command_add_worker)

    reassign_parser = subparsers.add_parser(
        "reassign-worker",
        help="PROJECT_LEAD: archive and reassign a completed or confirmed stopped Worker",
    )
    common_project_root(reassign_parser)
    reassign_parser.add_argument("--worker-id", required=True)
    reassign_parser.add_argument("--milestone", required=True)
    reassign_parser.add_argument("--objective", required=True)
    reassign_parser.add_argument("--allowed-scope", action="append", required=True)
    reassign_parser.add_argument("--read-dependency", action="append", default=[])
    reassign_parser.add_argument("--do-not-modify", action="append", default=[])
    reassign_parser.add_argument("--depends-on", action="append", default=[])
    reassign_parser.add_argument("--plan-section", action="append", default=[])
    reassign_parser.add_argument("--completion-criterion", action="append", required=True)
    reassign_parser.add_argument("--assignment-revision", type=int)
    reassign_parser.add_argument("--reason")
    reassign_parser.add_argument("--quiescence-evidence")
    reassign_parser.set_defaults(
        func=command_reassign_worker,
        coordination_justification="Existing Worker reused; no new conversation required.",
    )

    worker_parser = subparsers.add_parser("set-worker-status", help="Update one Worker's owned status")
    common_project_root(worker_parser)
    worker_parser.add_argument("--worker-id", required=True)
    worker_parser.add_argument("--status", choices=sorted(WORKER_STATUSES), required=True)
    worker_parser.add_argument("--summary", required=True)
    worker_parser.add_argument("--next-action", default="")
    worker_parser.add_argument("--files-changed", action="append")
    worker_parser.add_argument("--verification", action="append")
    worker_parser.add_argument("--assignment-revision", type=int, help="Reject stale relay status writes")
    worker_parser.add_argument("--actor", choices=["worker", "lead"], default="worker")
    worker_parser.set_defaults(func=command_set_worker_status)

    project_parser = subparsers.add_parser("set-project", help="Update Project Lead-owned global state")
    common_project_root(project_parser)
    project_parser.add_argument("--mode", choices=sorted(MODES), help="standalone (default) or leader")
    project_parser.add_argument("--phase", choices=sorted(PHASES))
    project_parser.add_argument("--status", choices=sorted(PROJECT_STATUSES))
    project_parser.add_argument("--milestone")
    project_parser.add_argument("--next-actor")
    project_parser.add_argument("--next-action")
    project_parser.set_defaults(func=command_set_project)

    reopen_parser = subparsers.add_parser(
        "reopen-project",
        help="PROJECT_LEAD only: archive a completed project and return it to planning",
    )
    common_project_root(reopen_parser)
    reopen_parser.add_argument("--reason", required=True)
    reopen_parser.add_argument("--milestone", required=True)
    reopen_parser.set_defaults(func=command_reopen_project)

    review_parser = subparsers.add_parser("assign-review", help="Create a bounded review assignment")
    common_project_root(review_parser)
    review_parser.add_argument("--reviewer-id", required=True)
    review_parser.add_argument("--level", choices=["balanced", "strong"], required=True)
    review_parser.add_argument("--objective", required=True)
    review_parser.add_argument("--scope", action="append", default=[])
    review_parser.add_argument("--completion-criterion", action="append", required=True)
    review_parser.add_argument("--strong-justification")
    review_parser.set_defaults(func=command_assign_review)

    review_status_parser = subparsers.add_parser("set-review-status", help="Update Reviewer-owned status")
    common_project_root(review_status_parser)
    review_status_parser.add_argument("--reviewer-id", required=True)
    review_status_parser.add_argument("--status", choices=sorted(REVIEW_STATUSES), required=True)
    review_status_parser.add_argument("--summary", required=True)
    review_status_parser.add_argument("--verification", action="append")
    review_status_parser.add_argument("--assignment-revision", type=int)
    review_status_parser.add_argument("--verdict", choices=["approved", "changes-requested"])
    review_status_parser.set_defaults(func=command_set_review_status)

    feedback_parser = subparsers.add_parser(
        "record-owner-feedback", help="Preserve ambiguous Owner feedback verbatim"
    )
    common_project_root(feedback_parser)
    feedback_parser.add_argument("--worker-id", required=True)
    feedback_parser.add_argument("--message", required=True)
    feedback_parser.set_defaults(func=command_record_owner_feedback)

    resolve_feedback_parser = subparsers.add_parser(
        "resolve-owner-feedback",
        help="PROJECT_LEAD only: mark one preserved Owner feedback event resolved",
    )
    common_project_root(resolve_feedback_parser)
    resolve_feedback_parser.add_argument("--event-id", required=True)
    resolve_feedback_parser.add_argument("--resolution", required=True)
    resolve_feedback_parser.set_defaults(func=command_resolve_owner_feedback)

    validate_parser = subparsers.add_parser("validate", help="Validate runtime structure and invariants")
    common_project_root(validate_parser)
    validate_parser.set_defaults(func=command_validate)

    status_parser = subparsers.add_parser("status", help="Render a concise management status")
    common_project_root(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Emit a JSON snapshot")
    status_parser.set_defaults(func=command_status)
    refresh_parser = subparsers.add_parser(
        "status-refresh", help="Regenerate the bilingual human-facing PROJECT_STATUS.md"
    )
    common_project_root(refresh_parser)
    refresh_parser.add_argument("--overwrite", action="store_true")
    refresh_parser.add_argument("--force", action="store_true", help="Allow refreshing a completed project")
    refresh_parser.set_defaults(func=command_status_refresh)
    recover_parser = subparsers.add_parser("recover", help="Explicitly recover a reported interrupted update")
    common_project_root(recover_parser)
    recover_parser.set_defaults(func=command_status, json=False)
    from relay import add_commands
    add_commands(subparsers, sys.modules[__name__])
    from lifecycle import add_commands as add_lifecycle_commands
    from workspace import add_commands as add_workspace_commands
    from memory import add_commands as add_memory_commands
    add_lifecycle_commands(subparsers, sys.modules[__name__])
    add_workspace_commands(subparsers, sys.modules[__name__])
    add_memory_commands(subparsers, sys.modules[__name__])
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "init":
            runtime = runtime_dir(project_root(args.project_root))
            from lifecycle import recover_control_review
            if args.command != "recover":
                markers = [*runtime.glob(f"{ADD_WORKER_MARKER_PREFIX}*.json"),
                           *runtime.glob(f"workers/*/{REASSIGNMENT_MARKER}"),
                           *runtime.glob(f"review/{REVIEW_ASSIGNMENT_MARKER}"),
                           *runtime.glob("review/.cancel-review.json")]
                if markers:
                    raise StateError("Pending update; run the recover subcommand through Python: "
                                     + ", ".join(str(p) for p in markers))
            else:
                recover_pending_worker_additions(runtime)
                recover_pending_review_assignment(runtime)
                recover_pending_reassignments(runtime)
                recover_control_review(sys.modules[__name__], runtime)
        return int(args.func(args))
    except StateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except MemoryError as exc:
        # A genuine out-of-memory condition: report it instead of a traceback.
        print(f"ERROR: out of memory: {exc}", file=sys.stderr)
        return 1
    except MemoryContentError as exc:
        # Malformed memory content is a state problem, not an internal crash.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
