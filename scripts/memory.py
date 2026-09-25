"""Compressed durable project memory; append-only JSONL, no raw transcripts.

Memory holds conclusions an agent would otherwise have to rediscover:
long-term user intent, decisions, constraints, lessons, rejected directions,
current direction and genuinely open questions. It deliberately does not hold
chat logs, agent reasoning or routine changes.
"""
from __future__ import annotations

import functools
import io
import json
import re
from pathlib import Path

KINDS = (
    "human-intent",
    "decision",
    "constraint",
    "lesson",
    "rejected",
    "direction",
    "open-question",
)


class MemoryError(Exception):
    """Raised for malformed memory content; the CLI reports it like a state error."""

# Consumption guard: an unconsolidated file past these bounds is reported so the
# agent consolidates instead of loading everything.
ACTIVE_LINE_BUDGET = 120
ACTIVE_BYTE_BUDGET = 24_000

ARCHIVE_HEADER = "# Project Memory Archive\n\nConsolidated from memory.jsonl. Read only for a specific historical detail.\n"
ARCHIVE_RE = re.compile(r"^### (MEM-[0-9]{4,}) ")


def memory_path(runtime: Path) -> Path:
    return runtime / "memory.jsonl"


def archive_path(runtime: Path) -> Path:
    return runtime / "memory-archive.md"


def read_entries(runtime: Path) -> list[dict]:
    path = memory_path(runtime)
    if not path.is_file():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MemoryError(f"memory.jsonl line {number} is not valid JSON: {exc}") from exc
        if (not isinstance(value, dict) or set(value) != {"id", "kind", "text", "added"}
                or value["kind"] not in KINDS
                or not isinstance(value["text"], str) or not value["text"].strip()
                or not isinstance(value["id"], str) or not re.fullmatch(r"MEM-[0-9]{4,}", value["id"])):
            raise MemoryError(f"memory.jsonl line {number} is malformed")
        entries.append(value)
    return entries


def write_entries(runtime: Path, entries: list[dict]) -> None:
    text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    path = memory_path(runtime)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    temporary.replace(path)


def next_id(entries: list[dict]) -> str:
    highest = 0
    for entry in entries:
        highest = max(highest, int(entry["id"].split("-")[1]))
    return f"MEM-{highest + 1:04d}"


def relevant(runtime: Path, task: str, *, kinds: tuple[str, ...] | None = None, limit: int = 12) -> list[dict]:
    """Select only memory entries plausibly relevant to a task, by term overlap.

    User intent, constraints and decisions are always surfaced when requested
    broadly: they are the requirements an agent must not silently drop.
    """
    entries = read_entries(runtime)
    if kinds:
        entries = [entry for entry in entries if entry["kind"] in kinds]
    else:
        always = {"human-intent", "constraint", "decision"}
        scored = []
        terms = {term for term in re.findall(r"[a-z0-9]{3,}", task.lower())}
        for entry in entries:
            if entry["kind"] in always:
                scored.append((2, entry))
                continue
            overlap = len(terms & {term for term in re.findall(r"[a-z0-9]{3,}", entry["text"].lower())})
            if overlap:
                scored.append((1, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [entry for _, entry in scored[:limit]]
    return entries[:limit]


def active_budget_note(runtime: Path) -> str | None:
    path = memory_path(runtime)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    lines = len([line for line in text.splitlines() if line.strip()])
    if lines > ACTIVE_LINE_BUDGET or len(text.encode("utf-8")) > ACTIVE_BYTE_BUDGET:
        return (f"memory.jsonl has {lines} entries; run memory-consolidate to keep the "
                f"active file small before reading all of it")
    return None


def command_memory_add(args, api) -> int:
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    text = api.require_nonempty(args.text, "text")
    entries = read_entries(runtime)
    entry = {"id": next_id(entries), "kind": args.kind, "text": text, "added": api.utc_now()}
    entries.append(entry)
    write_entries(runtime, entries)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def command_memory_show(args, api) -> int:
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    kinds = (args.kind,) if args.kind else None
    entries = relevant(runtime, args.task or "", kinds=kinds, limit=args.limit)
    note = active_budget_note(runtime)
    print(json.dumps({"entries": entries, "budget_note": note}, ensure_ascii=False, indent=2))
    return 0


def command_memory_consolidate(args, api) -> int:
    runtime = api.runtime_dir(api.project_root(args.project_root))
    api.assert_valid(runtime)
    entries = read_entries(runtime)
    if not entries:
        print("Memory is empty; nothing to consolidate")
        return 0
    # Keep the newest entry per (kind, text) so duplicates collapse, then drop
    # entries older than the retention window into the human-readable archive.
    keep: list[dict] = []
    seen: set[tuple[str, str]] = set()
    dropped: list[dict] = []
    for entry in entries:
        key = (entry["kind"], entry["text"].strip().lower())
        if key in seen:
            dropped.append(entry)
        else:
            seen.add(key)
            keep.append(entry)
    if len(keep) > args.keep:
        # User intent and constraints are never archived. They are requirements an
        # agent must not silently drop, and because read_entries only reads
        # memory.jsonl, archiving one would make it invisible to `relevant()`
        # forever. Only the surplus of these kinds is exempted from the cap, so a
        # project with many constraints keeps a larger active file by design
        # rather than losing its requirements.
        protected_kinds = {"human-intent", "constraint"}
        drop_count = len(keep) - args.keep
        archivable = [entry for entry in keep if entry["kind"] not in protected_kinds]
        to_drop = {id(entry) for entry in archivable[:drop_count]}
        dropped = [entry for entry in keep if id(entry) in to_drop] + dropped
        keep = [entry for entry in keep if id(entry) not in to_drop]
    if not dropped:
        print(f"Memory already consolidated: {len(keep)} entries")
        return 0
    protected = [entry for entry in keep if entry["kind"] in {"human-intent", "constraint"}]
    if not args.apply:
        print(json.dumps({"would_archive": [entry["id"] for entry in dropped],
                          "remaining": len(keep),
                          "protected": len(protected)}, ensure_ascii=False, indent=2))
        print("Preview only; pass --apply to rewrite memory.jsonl and append the archive")
        return 0
    existing = ""
    path = archive_path(runtime)
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
    block = "".join(
        f"\n### {entry['id']} ({entry['kind']}, {entry['added']})\n\n{entry['text']}\n"
        for entry in dropped
    )
    api.atomic_write_text(path, (existing or ARCHIVE_HEADER) + block)
    write_entries(runtime, keep)
    print(f"Archived {len(dropped)} entries; {len(keep)} remain active"
          f" ({len(protected)} protected intent/constraint entries kept)")
    return 0


def add_commands(subparsers, api) -> None:
    p = subparsers.add_parser("memory-add", help="Append one compressed durable memory entry")
    api.common_project_root(p)
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--text", required=True)
    p.set_defaults(func=functools.partial(command_memory_add, api=api))

    p = subparsers.add_parser("memory-show", help="Read only memory entries relevant to a task")
    api.common_project_root(p)
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--task", help="Current task; selects relevant entries instead of the whole file")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=functools.partial(command_memory_show, api=api))

    p = subparsers.add_parser("memory-consolidate", help="Compress and archive old memory entries")
    api.common_project_root(p)
    p.add_argument("--keep", type=int, default=60, help="Active entries retained before archiving older ones")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=functools.partial(command_memory_consolidate, api=api))
