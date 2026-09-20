"""Reproducible text/context proxies, never token or billing measurements."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "204002a817866d3ba652ab790ecb103ae538fb63"


def command(script, project, *args):
    result = subprocess.run([sys.executable, str(script), *args, "--project-root", str(project)],
                            capture_output=True, text=True, encoding="utf-8", check=True,
                            env=__import__("os").environ.copy() | {"PYTHONIOENCODING": "utf-8"})
    return result.stdout


def normalized_packet(script, project):
    value = json.loads(command(script, project, "context", "--role", "lead"))
    value["command_argv"] = ["python", "statectl.py"]
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = text.replace(json.dumps(str(project))[1:-1], "$PROJECT")
    return {"utf8_bytes": len(text.encode("utf-8")), "words_proxy": len(text.split())}


def main():
    with tempfile.TemporaryDirectory(prefix="tao-v070-proxy-") as temporary:
        base = Path(temporary).resolve()
        archive = subprocess.run(["git", "archive", "--format=zip", BASELINE], cwd=ROOT,
                                 check=True, capture_output=True).stdout
        baseline = base / "baseline"
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            z.extractall(baseline)
        files = ["SKILL.md", "references/orchestration-protocol.md", "references/runtime-state.md",
                 "references/host-dispatch.md", "profiles/openai-codex.md"]
        docs = [{"path": f, "before_words": len((baseline / f).read_text(encoding="utf-8").split()),
                 "after_words": len((ROOT / f).read_text(encoding="utf-8").split())} for f in files]
        project = base / "project space"
        project.mkdir()
        script = ROOT / "scripts/statectl.py"
        command(script, project, "init", "--project-id", "proxy", "--profile", "generic")
        command(script, project, "add-worker", "--worker-id", "worker-1", "--objective", "Current task",
                "--allowed-scope", "src/**", "--completion-criterion", "Verified output")
        runtime = project / ".tiered-agent"
        cases = []
        for historical_count in (0, 100):
            state = json.loads((runtime / "STATE.json").read_text())
            template = json.loads((runtime / "workers/worker-1/STATUS.json").read_text())
            for number in range(2, historical_count + 2):
                role = f"worker-{number}"
                folder = runtime / "workers" / role
                folder.mkdir()
                (folder / "TASK.md").write_text("Historical task", encoding="utf-8")
                (folder / "BLOCKER.md").write_text("None", encoding="utf-8")
                status = dict(template, worker_id=role, status="completed", summary="Prior result " * 50,
                              verification=["Verified prior output " * 100])
                (folder / "STATUS.json").write_text(json.dumps(status), encoding="utf-8")
                state["workers"].append({"id": role, "task_path": f"workers/{role}/TASK.md",
                                         "status_path": f"workers/{role}/STATUS.json",
                                         "write_scope": ["old/**"], "depends_on": []})
            (runtime / "STATE.json").write_text(json.dumps(state), encoding="utf-8")
            cases.append({"active_workers": 1, "terminal_workers": historical_count,
                          "before": normalized_packet(baseline / "scripts/statectl.py", project),
                          "after": normalized_packet(script, project)})
        result = {"kind": "static-context-proxy", "baseline_commit": BASELINE,
                  "method": "UTF-8 text, str.split word counts, normalized synthetic context packets; not model tokens",
                  "files": docs,
                  "conditional_housekeeping_reference_words": len((ROOT / "references/workspace-housekeeping.md").read_text(encoding="utf-8").split()),
                  "context_cases": cases,
                  "documented_success_path_calls": {"includes": "state result write, packet preparation, host sends and receipts; excludes shared assignment/binding/observation work",
                                                    "before": 7, "after_with_combined_callback": 6,
                                                    "kind": "protocol-step count, not live-host telemetry"},
                  "actual_coordination_token_ratio": None, "actual_token_target_verified": False,
                  "live_host_dispatch_verified": False}
        (ROOT / "benchmarks/v0.7.0-static-context.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
