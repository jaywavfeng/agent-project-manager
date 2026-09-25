from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import statectl as api
import workspace
import lifecycle


class V070Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.rt = self.root / ".agent-project-manager"
        self.cli("init", "--project-id", "v070", "--profile", "generic")
        self._leader_mode = False

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, code=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = api.main([*args, "--project-root", str(self.root)])
        self.assertEqual(result, code, out.getvalue() + err.getvalue())
        return out.getvalue() if result == 0 else err.getvalue()

    def read(self, path):
        return json.loads((self.rt / path).read_text(encoding="utf-8"))

    def leader_mode(self):
        """Opt into delegation; standalone is the default and has no roles."""
        if not self._leader_mode:
            self.cli("set-project", "--mode", "leader", "--status", "active")
            self._leader_mode = True

    def add(self, role="worker-1", scope="src/a/**", *extra):
        # Delegation is opt-in: switch modes before registering the first Worker.
        self.leader_mode()
        self.cli("add-worker", "--worker-id", role, "--objective", "Implement", "--allowed-scope", scope,
                 "--completion-criterion", "Pass", "--coordination-justification", "Independent task", *extra)

    def status(self, status, role="worker-1", *extra, code=0):
        return self.cli("set-worker-status", "--worker-id", role, "--status", status,
                        "--summary", status, *extra, code=code)

    def control(self, action, revision=1, *extra, code=0):
        return self.cli("control-worker", "--worker-id", "worker-1", "--action", action,
                        "--assignment-revision", str(revision), "--reason", "Resolved cause",
                        "--quiescence-evidence", "Host stop confirmed; no process remains", *extra, code=code)

    def reassign(self, role="worker-1", *extra, code=0):
        return self.cli("reassign-worker", "--worker-id", role, "--milestone", "M2", "--objective", "Next",
                        "--allowed-scope", "src/c/**", "--completion-criterion", "Pass", *extra, code=code)

    def bind(self, role, **extra):
        args = []
        for key, value in extra.items():
            args += ["--" + key.replace("_", "-"), value]
        self.cli("bind-thread", "--role", role, "--thread-id", role + "-thread", "--host-id", "local",
                 "--cwd", str(self.root), "--route-source", "owner",
                 "--evidence", "Owner authorized bidirectional messages", *args)

    def observation(self, role):
        path = self.root / "observation.json"
        path.write_text(json.dumps({"thread_id": role + "-thread", "host_id": "local", "cwd": str(self.root), "status": "idle"}))
        return str(path)

    def prepare(self, sender, recipient, revision=1, code=0):
        extra = ["--observation", self.observation(recipient)] if sender == "lead" else []
        output = self.cli("prepare-message", "--sender", sender, "--recipient", recipient,
                          "--assignment-revision", str(revision), *extra, code=code)
        return json.loads(output) if code == 0 else output

    def receipt(self, packet, result="sent", code=0):
        return self.cli("record-message", "--sender", packet["sender"], "--recipient", packet["recipient"],
                        "--event-id", packet["event_id"], "--result", result, "--evidence", "Host result", code=code)

    def execution(self):
        self.cli("set-project", "--phase", "execution", "--status", "active")

    def test_direct_lead_execution_needs_no_worker(self):
        self.execution()
        self.cli("set-project", "--phase", "complete", "--status", "complete")
        self.assertEqual(self.read("STATE.json")["workers"], [])

    def test_takeover_retains_scope_results_and_fences_old_worker(self):
        self.add()
        self.status("blocked", "worker-1", "--verification", "Partial result passed")
        self.control("takeover")
        self.assertEqual(self.read("STATE.json")["workers"][0]["executor"], "lead")
        self.assertEqual(self.read("workers/worker-1/STATUS.json")["verification"], ["Partial result passed"])
        self.status("active", "worker-1", "--assignment-revision", "1", code=2)
        self.status("active", "worker-1", "--assignment-revision", "2", code=2)
        self.status("active", "worker-1", "--actor", "lead", code=2)
        self.status("active", "worker-1", "--actor", "lead", "--assignment-revision", "2")
        self.status("completed", "worker-1", "--actor", "lead", "--assignment-revision", "2")
        self.cli("validate")

    def test_stopped_work_reuse_and_resume(self):
        self.add()
        self.status("blocked")
        self.control("resume")
        self.status("active", "worker-1", "--assignment-revision", "2")
        self.control("cancel", 2)
        self.assertEqual(self.read("workers/worker-1/STATUS.json")["status"], "inactive")
        self.reassign("worker-1", "--assignment-revision", "3", "--reason", "New task", "--quiescence-evidence", "Stopped")
        self.assertEqual(self.read("workers/worker-1/STATUS.json")["status"], "ready")
        self.cli("validate")

    def test_completed_dependents_do_not_lock_new_upstream_or_create_false_cycle(self):
        self.add()
        self.status("completed")
        self.add("worker-2", "src/b/**", "--depends-on", "worker-1")
        self.status("completed", "worker-2")
        self.reassign("worker-1", "--depends-on", "worker-2")
        self.cli("validate")

    def test_control_prevalidation_leaves_no_marker_on_scope_conflict(self):
        self.add()
        self.status("inactive")
        self.add("worker-2", "src/a/**")
        before = (self.rt / "STATE.json").read_bytes()
        self.control("takeover", code=2)
        self.assertEqual((self.rt / "STATE.json").read_bytes(), before)
        self.assertFalse((self.rt / "workers/worker-1/.reassign.json").exists())

    def test_control_crash_recovers_without_fabricating_completion(self):
        self.add()
        self.status("blocked")
        with mock.patch.object(api, "recover_reassignment", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.control("takeover")
        self.cli("status", code=2)
        self.cli("recover")
        self.cli("validate")
        self.assertEqual(self.read("workers/worker-1/history/assignment-0001/STATUS.json")["status"], "blocked")

    def review(self):
        self.leader_mode()  # A required review only exists in delegated projects.
        self.cli("assign-review", "--reviewer-id", "reviewer-1", "--level", "balanced",
                 "--objective", "Review", "--completion-criterion", "Approve correct result")

    def review_status(self, status, revision=1, verdict=None, code=0):
        extra = ["--verdict", verdict] if verdict else []
        self.cli("set-review-status", "--reviewer-id", "reviewer-1", "--status", status,
                 "--assignment-revision", str(revision), "--summary", "Review result", *extra, code=code)

    def test_review_verdict_gate_and_cancel_fix_loop(self):
        self.add()
        self.status("completed")
        self.execution()
        self.review()
        self.review_status("blocked")
        self.cli("cancel-review", "--assignment-revision", "1", "--reason", "Fix failure",
                 "--quiescence-evidence", "Reviewer stopped")
        self.assertTrue(self.read("STATE.json")["review"]["required"])
        self.review_status("completed", 1, "approved", code=2)
        self.reassign()
        self.status("completed")
        self.review()
        self.review_status("completed", 3, "changes-requested")
        self.cli("set-project", "--phase", "complete", "--status", "complete", code=2)
        self.review_status("completed", 3, "approved")
        self.cli("set-project", "--phase", "complete", "--status", "complete")
        self.cli("validate")

    def test_bidirectional_receipts_uncertainty_and_resume(self):
        self.add()
        self.execution()
        self.bind("lead")
        self.bind("worker-1")
        packet = self.prepare("lead", "worker-1")
        self.prepare("lead", "worker-1", code=2)
        self.receipt(packet, "unknown")
        self.prepare("lead", "worker-1", code=2)
        self.receipt(packet)
        self.receipt(packet, "not-sent", code=2)
        self.status("blocked")
        callback = self.prepare("worker-1", "lead")
        self.receipt(callback)
        self.prepare("worker-1", "lead", code=2)
        self.control("resume")
        self.prepare("lead", "worker-1", 2)
        self.receipt(packet, code=2)

    def test_reviewer_messages_and_stale_revision(self):
        self.execution()
        self.review()
        self.bind("lead")
        self.bind("reviewer-1")
        self.receipt(self.prepare("lead", "reviewer-1"))
        self.review_status("completed", verdict="changes-requested")
        self.receipt(self.prepare("reviewer-1", "lead"))
        self.prepare("reviewer-1", "lead", 8, code=2)

    def test_combined_callback_is_persisted_and_deduplicated(self):
        self.add()
        self.execution()
        self.bind("lead")
        self.bind("worker-1")
        args = ("prepare-message", "--sender", "worker-1", "--recipient", "lead",
                "--assignment-revision", "1", "--status", "completed", "--summary", "Passed",
                "--verification", "All criteria tested")
        packet = json.loads(self.cli(*args))
        self.assertEqual(self.read("workers/worker-1/STATUS.json")["status"], "completed")
        self.receipt(packet)
        stamp = self.read("workers/worker-1/STATUS.json")["last_updated"]
        self.cli(*args, code=2)
        self.assertEqual(self.read("workers/worker-1/STATUS.json")["last_updated"], stamp)

    def test_cancelled_reviewer_binding_remains_valid_but_not_dispatchable(self):
        self.execution()
        self.review()
        self.bind("reviewer-1")
        self.cli("cancel-review", "--assignment-revision", "1", "--reason", "Fix",
                 "--quiescence-evidence", "Stopped")
        self.cli("validate")
        self.cli("context", "--role", "reviewer-1", code=2)

    def test_context_size_is_bounded_by_active_not_terminal_workers(self):
        self.add()
        initial = len(self.cli("context", "--role", "lead"))
        state = self.read("STATE.json")
        for n in range(2, 102):
            role = f"worker-{n}"
            directory = self.rt / "workers" / role
            directory.mkdir()
            (directory / "TASK.md").write_text("Prior task")
            (directory / "BLOCKER.md").write_text("No blocker")
            status = api.read_template_json("worker-status.json")
            status.update(worker_id=role, status="completed", summary="Prior result " * 200,
                          verification=["Verified " * 200], last_updated=api.utc_now())
            api.atomic_write_json(directory / "STATUS.json", status)
            state["workers"].append({"id": role, "task_path": f"workers/{role}/TASK.md",
                                     "status_path": f"workers/{role}/STATUS.json",
                                     "write_scope": ["old/**"], "depends_on": []})
        api.atomic_write_json(self.rt / "STATE.json", state)
        packet = self.cli("context", "--role", "lead")
        self.assertLess(len(packet), initial + 30)
        self.assertEqual(json.loads(packet)["worker_page"]["terminal_total"], 100)
        self.assertEqual(len(json.loads(self.cli("status", "--json"))["workers"]), 101)

    def test_housekeeping_move_and_restore_interruption_reconcile(self):
        directory = self.artifact()
        original = api.atomic_write_json
        def fail_manifest(path, value):
            if path.name == "WORKSPACE.json" and any(i["state"] == "quarantined" for i in value["artifacts"]):
                raise OSError("manifest write interrupted")
            return original(path, value)
        with mock.patch.object(api, "atomic_write_json", fail_manifest):
            self.cli("housekeep", "--apply")
        self.assertFalse(directory.exists())
        self.cli("housekeep", "--apply")
        self.assertEqual(self.read("WORKSPACE.json")["artifacts"][0]["state"], "quarantined")
        def fail_restore(path, value):
            if path.name == "WORKSPACE.json" and any(i["state"] == "current" for i in value["artifacts"]):
                raise OSError("restore manifest interrupted")
            return original(path, value)
        with mock.patch.object(api, "atomic_write_json", fail_restore):
            with self.assertRaises(OSError):
                self.cli("workspace-restore", "--path", "runs/one")
        self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())
        self.assertFalse(self.read("WORKSPACE.json")["artifacts"][0]["released"])

    def test_requested_independent_thread_and_subagent_rejection(self):
        self.add()
        path = self.root / "receipt.json"
        receipt = {"runtime_kind": "independent-thread", "thread_id": "worker-1-thread", "host_id": "local",
                   "requested_model": "economy", "requested_reasoning": "high", "source": "host creation response"}
        path.write_text(json.dumps(receipt))
        self.bind("worker-1", route_source="requested", receipt=str(path))
        receipt["runtime_kind"] = "subagent"
        path.write_text(json.dumps(receipt))
        self.cli("bind-thread", "--role", "worker-1", "--thread-id", "worker-1-thread", "--host-id", "local",
                 "--cwd", str(self.root), "--route-source", "requested", "--receipt", str(path), "--evidence", "host", code=2)

    def artifact(self, kind="temporary", path="runs/one", *extra):
        directory = self.root / path
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.txt").write_text("generated")
        self.cli("workspace-register", "--path", path, "--producer", "lead", "--assignment-revision", "1",
                 "--kind", kind, "--reproduce", "Run task fixture", "--movable", "--released",
                 "--quiescence-evidence", "Producer and all consumers stopped", *extra)
        return directory

    def test_housekeeping_preview_quarantine_restore_and_idempotence(self):
        directory = self.artifact()
        before = (self.rt / "WORKSPACE.json").read_bytes()
        preview = json.loads(self.cli("housekeep"))
        self.assertEqual(preview["candidate_count"], 1)
        self.assertEqual(before, (self.rt / "WORKSPACE.json").read_bytes())
        self.cli("housekeep", "--apply")
        self.assertFalse(directory.exists())
        summary = (self.rt / "HOUSEKEEPING.json").read_bytes()
        self.cli("housekeep", "--apply")
        self.assertEqual(summary, (self.rt / "HOUSEKEEPING.json").read_bytes())
        self.cli("workspace-restore", "--path", "runs/one")
        self.assertEqual((directory / "result.txt").read_text(), "generated")
        self.assertFalse(self.read("WORKSPACE.json")["artifacts"][0]["released"])

    def test_retention_and_changed_quarantine_protection(self):
        self.artifact()
        self.cli("housekeep", "--apply")
        location = self.root / self.read("WORKSPACE.json")["artifacts"][0]["location"]
        with mock.patch.object(workspace, "now", return_value=workspace.now() + timedelta(days=8)):
            (location / "new.txt").write_text("new evidence")
            result = json.loads(self.cli("housekeep", "--apply"))
            self.assertEqual(result["actions"], [])
            self.assertTrue(location.exists())

    def test_expired_unchanged_temporary_deleted_evidence_and_intermediate_retained(self):
        self.artifact()
        evidence = self.artifact("evidence", "runs/proof")
        self.artifact("intermediate", "runs/intermediate")
        self.cli("housekeep", "--apply")
        with mock.patch.object(workspace, "now", return_value=workspace.now() + timedelta(days=8)):
            self.cli("housekeep", "--apply")
        items = {i["path"]: i for i in self.read("WORKSPACE.json")["artifacts"]}
        self.assertEqual(items["runs/one"]["state"], "deleted")
        self.assertEqual(items["runs/intermediate"]["state"], "archived")
        self.assertTrue(evidence.exists())

    def test_active_scope_reference_and_tracked_files_are_preserved(self):
        directory = self.artifact()
        self.add("worker-1", "runs/**")
        self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())

        self.status("completed")
        (self.rt / "HANDOFF.md").write_text("Keep runs/one as evidence", encoding="utf-8")
        self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())
        (self.rt / "HANDOFF.md").write_text("No reference", encoding="utf-8")
        subprocess.run(["git", "init", str(self.root)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "runs"], check=True, capture_output=True)
        self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())

    def test_verification_evidence_is_protected_after_worker_completes(self):
        directory = self.artifact()
        self.add()
        self.status("completed", "worker-1", "--verification", "Acceptance evidence: runs/one/result.txt")
        self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())

    def test_feedback_archive_and_context_do_not_read_historical_content(self):
        self.add()
        path = Path(self.cli("record-owner-feedback", "--worker-id", "worker-1", "--message", "Direction").strip())
        event = path.stem
        self.cli("resolve-owner-feedback", "--event-id", event, "--resolution", "Applied")
        self.cli("housekeep", "--apply")
        self.assertFalse(path.exists())
        self.cli("resolve-owner-feedback", "--event-id", event, "--resolution", "Duplicate")
        history = self.rt / "inbox/owner/history"
        for n in range(200):
            (history / f"old-{n}.md").write_text("Historical content" * 50)
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            if "history" in path.parts:
                raise AssertionError("Ordinary context read historical content")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", guarded):
            packet = json.loads(self.cli("context", "--role", "lead"))
        self.assertEqual(packet["pending_events"], [])

    def test_due_and_complete_reads_do_not_mutate(self):
        self.cli("housekeep", "--apply")
        self.assertFalse(json.loads(self.cli("housekeep", "--if-due"))["due"])
        with mock.patch.object(workspace, "now", return_value=workspace.now() + timedelta(days=8)):
            self.assertTrue(json.loads(self.cli("context", "--role", "lead"))["housekeeping_due"])
        self.execution()
        self.cli("set-project", "--phase", "complete", "--status", "complete")
        before = {p: p.read_bytes() for p in self.rt.rglob("*") if p.is_file()}
        self.cli("context", "--role", "lead")
        self.cli("housekeep")
        self.cli("housekeep", "--apply", code=2)
        self.assertEqual(before, {p: p.read_bytes() for p in self.rt.rglob("*") if p.is_file()})

    def test_changed_after_preview_and_move_failure_preserve_artifact(self):
        directory = self.artifact()
        original = workspace.snapshot
        calls = []
        def changing(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                (directory / "result.txt").write_text("new result")
            return original(*args, **kwargs)
        with mock.patch.object(workspace, "snapshot", changing):
            self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())
        with mock.patch.object(Path, "rename", side_effect=OSError("busy")):
            self.cli("housekeep", "--apply")
        self.assertTrue(directory.exists())
        self.cli("housekeep", "--apply")
        self.assertFalse(directory.exists())

    def test_links_and_escape_rejected(self):
        for path in ("../outside", "src/cache", "C:/temp", ".agent-project-manager/history"):
            self.cli("workspace-register", "--path", path, "--producer", "lead", "--assignment-revision", "1",
                     "--kind", "temporary", "--reproduce", "command", code=2)
        directory = self.artifact()
        external = self.root / "outside"
        external.mkdir()
        (external / "keep.txt").write_text("keep")
        link = directory / "linked"
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], capture_output=True)
            if result.returncode:
                self.skipTest("Host does not allow symlinks/junctions")
        self.cli("housekeep", "--apply")
        self.assertTrue((external / "keep.txt").exists())
        self.assertTrue(directory.exists())
        if os.name == "nt" and not link.is_symlink():
            os.rmdir(link)


if __name__ == "__main__":
    unittest.main()
