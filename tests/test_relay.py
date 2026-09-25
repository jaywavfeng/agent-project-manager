from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import statectl
import relay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apm 中文 spaces ")
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / ".agent-project-manager"
        self.call("init", "--project-id", "relay-test")
        # Relay exercises delegated roles, so opt out of the standalone default first.
        self.call("set-project", "--mode", "leader")
        self.call("add-worker", "--worker-id", "worker-1", "--objective", "Implement parser",
                  "--allowed-scope", "src/**", "--completion-criterion", "Parser tests pass")
        self.call("set-project", "--phase", "execution")
        self.bind("lead", "lead-thread")
        self.bind("worker-1", "worker-thread")
        self.observation = self.root / "observation.json"
        self.observe()

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = statectl.main([*args, "--project-root", str(self.root)])
        self.assertEqual(code, expected, stderr.getvalue())
        return stdout.getvalue() if code == 0 else stderr.getvalue()

    def bind(self, role, thread, *extra, expected=0):
        return self.call("bind-thread", "--role", role, "--thread-id", thread,
                         "--host-id", "local", "--cwd", str(self.root), "--route-source", "owner",
                         "--evidence", "Synthetic fixture: Owner selected task and authorized relay",
                         *extra, expected=expected)

    def observe(self, **overrides):
        value = {"thread_id": "worker-thread", "host_id": "local", "cwd": str(self.root), "status": "idle"}
        value.update(overrides)
        self.observation.write_text(json.dumps(value), encoding="utf-8")

    def packet(self, expected=0):
        return self.call("prepare-message", "--sender", "lead", "--recipient", "worker-1",
                         "--assignment-revision", "1",
                         "--observation", str(self.observation), expected=expected)

    def record(self, result, revision=1, expected=0, packet=None):
        """Record an outcome for the reservation at `revision` identically to the real flow."""
        out = packet if packet is not None else self.reserve(revision)
        event = json.loads(out)["event_id"]
        return self.call("record-message", "--sender", "lead", "--recipient", "worker-1",
                         "--event-id", event, "--result", result,
                         "--evidence", "Synthetic host receipt", expected=expected)

    def reserve(self, revision, sender="lead", recipient="worker-1"):
        """Reserve one delivery (must succeed) and return the emitted packet text."""
        args = ["prepare-message", "--sender", sender, "--recipient", recipient,
                "--assignment-revision", str(revision)]
        if (sender, recipient) == ("lead", "worker-1"):
            args += ["--observation", str(self.observation)]
        return self.call(*args, expected=0)

    def status(self, status, revision=1, expected=0):
        return self.call("set-worker-status", "--worker-id", "worker-1", "--status", status,
                         "--summary", "Fixture transition", "--assignment-revision", str(revision), expected=expected)

    def snapshot(self):
        return {str(p.relative_to(self.runtime)): p.read_bytes() for p in self.runtime.rglob("*") if p.is_file()}

    def test_two_assignments_use_same_thread_and_stale_messages_are_rejected(self):
        packets = []
        for revision in (1, 2):
            packet = json.loads(self.reserve(revision))
            packets.append(packet)
            # Simulated host delivery: Worker loads revision, starts, validates and completes.
            context = json.loads(self.call("context", "--role", "worker-1", "--assignment-revision", str(revision)))
            self.assertEqual(context["assignment_revision"], revision)
            self.status("active", revision)
            self.record("sent", revision, packet=json.dumps(packet))  # Worker may already be running when receipt arrives.
            self.status("completed", revision)
            callback = json.loads(self.reserve(revision, "worker-1", "lead"))
            self.assertEqual(callback["recipient"], "lead")
            # Reading context is read-only and must not mutate persisted state.
            before = self.snapshot()
            self.call("context", "--role", "worker-1")
            self.assertEqual(before, self.snapshot())
            if revision == 1:
                self.call("reassign-worker", "--worker-id", "worker-1", "--milestone", "M2",
                          "--objective", "Integrate parser", "--allowed-scope", "src/**",
                          "--completion-criterion", "Integration tests pass")
                self.assertTrue((self.runtime / "workers/worker-1/history/assignment-0001/TASK.md").exists())
                self.call("context", "--role", "worker-1", "--assignment-revision", "1", expected=2)
                self.status("active", 1, expected=2)
                self.call("prepare-message", "--sender", "lead", "--recipient", "worker-1",
                          "--assignment-revision", "1", "--observation", str(self.observation), expected=2)
        self.assertEqual([p["thread_id"] for p in packets], ["worker-thread"] * 2)
        self.assertTrue(all("model" not in p and "thinking" not in p for p in packets))

    def test_reservation_and_uncertain_delivery_block_duplicate_sends(self):
        packet = self.reserve(1)
        # The reservation itself is the pending state; a second send is blocked.
        self.packet(expected=2)
        self.record("unknown", packet=packet)
        self.packet(expected=2)
        # Only after a documented definite non-delivery may the same revision be sent again.
        self.record("not-sent", packet=packet)
        self.packet()

    def test_record_requires_reservation_and_current_revision(self):
        # Recording without a prior reservation must fail: no receipt matches the event.
        self.call("record-message", "--sender", "lead", "--recipient", "worker-1",
                  "--event-id", "worker-1:1:ready:never", "--result", "sent",
                  "--evidence", "Synthetic host receipt", expected=2)
        packet = self.reserve(1)
        self.record("sent", packet=packet)
        # A confirmed delivery cannot be reset to a retry.
        self.record("unknown", packet=packet, expected=2)

    def test_target_observations_reject_wrong_or_unavailable_runtime(self):
        for override in ({"thread_id": "other"}, {"host_id": "other"}, {"cwd": str(self.root.parent)},
                         {"status": "active"}, {"status": "archived"}, {"status": "setup"}, {"status": None}):
            with self.subTest(override=override):
                self.observe(**override)
                self.packet(expected=2)
                self.observe()

    def test_bindings_require_same_directory_and_distinct_role(self):
        self.bind("worker-1", "lead-thread", "--replace", expected=2)
        self.bind("worker-2", "other", expected=2)
        self.bind("worker-1", "other", "--cwd", str(self.root.parent), "--replace", expected=2)
        self.bind("worker-1", "other", expected=2)

    def test_active_and_uncertain_binding_cannot_be_replaced(self):
        packet = self.reserve(1)
        # A pending reservation already counts as uncertain delivery.
        self.bind("worker-1", "other", "--replace", expected=2)
        self.record("sent", packet=packet)
        self.status("active")
        self.bind("worker-1", "other", "--replace", expected=2)

    def test_native_binding_requires_actual_model_and_reasoning(self):
        receipt = {"thread_id": "native-thread", "host_id": "local", "requested_model": "economy-fixture",
                   "requested_reasoning": "xhigh", "effective_model": "economy-fixture",
                   "effective_reasoning": "xhigh", "source": "Synthetic effective host response"}
        path = self.root / "receipt.json"
        args = ("--replace", "--route-source", "attested", "--receipt", str(path))
        self.bind("worker-1", "native-thread", "--replace", "--route-source", "attested", expected=2)
        for change in ({"effective_model": None}, {"effective_reasoning": None},
                       {"effective_model": "strong-fixture"}, {"effective_reasoning": "low"},
                       {"thread_id": "different"}, {"source": ""}, {"effective_model": "synthetic"}):
            path.write_text(json.dumps(dict(receipt, **change)), encoding="utf-8")
            self.bind("worker-1", "native-thread", *args, expected=2)
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.bind("worker-1", "native-thread", *args)
        self.observe(thread_id="native-thread")
        self.assertEqual(json.loads(self.packet())["thread_id"], "native-thread")

    def test_dependency_and_owner_feedback_block_dispatch(self):
        self.call("add-worker", "--worker-id", "worker-2", "--objective", "Prepare input", "--allowed-scope", "data/**",
                  "--completion-criterion", "Input valid", "--coordination-justification", "Independent preparation")
        state = statectl.load_state(self.runtime)
        state["workers"][0]["depends_on"] = ["worker-2"]
        statectl.atomic_write_json(self.runtime / "STATE.json", state)
        self.assertIn("Dependency", self.packet(expected=2))
        self.call("set-worker-status", "--worker-id", "worker-2", "--status", "completed", "--summary", "Prepared")
        self.packet()
        self.call("record-owner-feedback", "--worker-id", "worker-1", "--message", "Change the direction")
        # Owner feedback is reported ahead of other blocking reasons, so assert the block itself.
        self.assertIn("Owner decisions", self.packet(expected=2))

    def test_blocker_callback_is_actionable_but_not_a_fresh_dispatch(self):
        self.status("blocked")
        callback = json.loads(self.call(
            "prepare-message", "--sender", "worker-1", "--recipient", "lead",
            "--assignment-revision", "1"))
        self.assertIn(":blocked:", callback["event_id"])
        self.packet(expected=2)

    def test_waiting_project_and_completed_project_do_not_dispatch(self):
        self.call("set-project", "--status", "waiting-owner")
        self.packet(expected=2)
        self.call("set-project", "--status", "active")
        self.status("completed")
        self.call("set-project", "--phase", "complete", "--status", "complete")
        self.packet(expected=2)
        self.bind("lead", "other", "--replace", expected=2)

    def test_no_transport_preserves_old_manual_workflow(self):
        (self.runtime / "TRANSPORT.json").unlink()
        self.call("context", "--role", "worker-1")
        self.call("status")
        self.call("validate")
        self.packet(expected=2)
        self.assertFalse((self.runtime / "TRANSPORT.json").exists())

    def test_queries_and_updates_skip_history_but_explicit_validate_checks_it(self):
        with mock.patch.object(statectl, "validate_assignment_history", side_effect=AssertionError("history read")), \
             mock.patch.object(statectl, "validate_review_history", side_effect=AssertionError("history read")), \
             mock.patch.object(statectl, "validate_completion_history", side_effect=AssertionError("history read")), \
             mock.patch.object(statectl, "completion_history_revisions", side_effect=AssertionError("history scan")):
            before = self.snapshot()
            self.call("status")
            self.call("context", "--role", "lead")
            self.call("context", "--role", "worker-1")
            self.assertEqual(before, self.snapshot())
            self.status("active")
        with mock.patch.object(statectl, "validate_assignment_history", return_value=["bad archived assignment"]):
            self.assertIn("bad archived assignment", self.call("validate", expected=1))

    def test_pending_queries_never_repair_or_open_editor(self):
        marker = self.runtime / "workers/worker-1" / statectl.REASSIGNMENT_MARKER
        marker.write_text("{}", encoding="utf-8")
        before = self.snapshot()
        with mock.patch.object(statectl, "recover_pending_reassignments", side_effect=AssertionError("repair")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("editor launch")):
            for args in (("status",), ("validate",), ("context", "--role", "worker-1")):
                self.assertIn("Pending update", self.call(*args, expected=2))
        self.assertEqual(before, self.snapshot())

    def test_cli_uses_explicit_interpreter_from_unrelated_directory(self):
        argv = json.loads(self.call("context", "--role", "worker-1"))["command_argv"]
        self.assertEqual(Path(argv[0]).resolve(), Path(sys.executable).resolve())
        result = subprocess.run([*argv, "status", "--project-root", str(self.root)],
                                cwd=self.root.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        bad = subprocess.run([*argv, "status", "--not-an-option"], cwd=self.root.parent,
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        help_result = subprocess.run([*argv, "bind-thread", "--help"], cwd=self.root.parent,
                                     capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--route-source", help_result.stdout)
        with self.assertRaises(OSError):
            subprocess.run([str(self.root / "missing-python"), *argv[1:], "status"], capture_output=True)


if __name__ == "__main__":
    unittest.main()
