from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import statectl


class StandaloneDefaultTests(unittest.TestCase):
    """Standalone must be the default, and delegation must be explicitly opted into."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apm standalone ")
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / ".agent-project-manager"
        self.cli("init", "--project-id", "standalone-test")

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = statectl.main([*args, "--project-root", str(self.root)])
            except SystemExit as exc:  # argparse rejects malformed arguments itself.
                result = exc.code if isinstance(exc.code, int) else 2
        self.assertEqual(result, code, stderr.getvalue())
        return stdout.getvalue() if result == 0 else stderr.getvalue()

    def add_worker(self, code=0):
        return self.cli("add-worker", "--worker-id", "worker-1", "--objective", "Do work",
                        "--allowed-scope", "src/**", "--completion-criterion", "Pass", code=code)

    def test_new_project_starts_standalone_with_no_workers(self):
        state = statectl.load_state(self.runtime)
        self.assertEqual(state["mode"], "standalone")
        self.assertEqual(state["workers"], [])
        self.assertFalse(state["review"]["required"])

    def test_add_worker_is_refused_until_leader_mode_is_enabled(self):
        self.assertIn("Standalone mode has no Workers", self.add_worker(code=2))
        self.cli("set-project", "--mode", "leader")
        self.add_worker()
        state = statectl.load_state(self.runtime)
        self.assertEqual(state["mode"], "leader")
        self.assertEqual([worker["id"] for worker in state["workers"]], ["worker-1"])

    def test_mode_can_only_be_set_to_known_values(self):
        self.cli("set-project", "--mode", "solo", code=2)

    def test_standalone_cannot_register_workers_even_via_a_crafted_state(self):
        state = statectl.load_state(self.runtime)
        state["workers"] = [{"id": "worker-1", "task_path": "workers/worker-1/TASK.md",
                             "status_path": "workers/worker-1/STATUS.json",
                             "write_scope": ["src/**"], "depends_on": []}]
        statectl.atomic_write_json(self.runtime / "STATE.json", state)
        self.assertIn("standalone mode must not register Workers", self.cli("validate", code=1))

    def test_returning_to_standalone_is_blocked_while_workers_exist(self):
        self.cli("set-project", "--mode", "leader")
        self.add_worker()
        self.assertIn("Cannot return to standalone", self.cli("set-project", "--mode", "standalone", code=2))

    def test_review_cannot_be_assigned_while_a_worker_is_unfinished(self):
        self.cli("set-project", "--mode", "leader")
        self.add_worker()
        self.assertIn("unfinished", self.cli(
            "assign-review", "--reviewer-id", "reviewer-1", "--level", "balanced",
            "--objective", "Check the implementation",
            "--completion-criterion", "Findings recorded", code=2))

    def test_returning_to_standalone_is_blocked_while_a_review_is_required(self):
        self.cli("set-project", "--mode", "leader")
        self.add_worker()
        self.cli("set-worker-status", "--worker-id", "worker-1", "--status", "completed",
                 "--summary", "Delivered the scoped change")
        self.cli("assign-review", "--reviewer-id", "reviewer-1", "--level", "balanced",
                 "--objective", "Check the implementation",
                 "--completion-criterion", "Findings recorded")
        self.assertTrue(statectl.load_state(self.runtime)["review"]["required"])
        # Workers block the switch first; the review guard is a second, independent net.
        self.assertIn("Workers are registered",
                      self.cli("set-project", "--mode", "standalone", code=2))
        state = statectl.load_state(self.runtime)
        state["workers"] = []
        statectl.atomic_write_json(self.runtime / "STATE.json", state)
        self.assertIn("review is required",
                      self.cli("set-project", "--mode", "standalone", code=2))

    def test_standalone_cannot_require_review_even_via_a_crafted_state(self):
        state = statectl.load_state(self.runtime)
        state["review"]["required"] = True
        statectl.atomic_write_json(self.runtime / "STATE.json", state)
        self.assertIn("standalone mode must not require a review", self.cli("validate", code=1))

    def test_status_and_snapshot_report_the_mode(self):
        self.assertIn("(standalone)", self.cli("status"))
        self.assertEqual(json.loads(self.cli("status", "--json"))["mode"], "standalone")
        self.cli("set-project", "--mode", "leader")
        self.assertEqual(json.loads(self.cli("status", "--json"))["mode"], "leader")


class StatusPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apm page ")
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / ".agent-project-manager"
        self.cli("init", "--project-id", "page-test")

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args, code=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                result = statectl.main([*args, "--project-root", str(self.root)])
            except SystemExit as exc:  # argparse rejects malformed arguments itself.
                result = exc.code if isinstance(exc.code, int) else 2
        self.assertEqual(result, code, stderr.getvalue())
        return stdout.getvalue() if result == 0 else stderr.getvalue()

    def test_init_writes_a_real_page_not_placeholders(self):
        text = (self.runtime / "PROJECT_STATUS.md").read_text(encoding="utf-8")
        self.assertIn("# Project Status: page-test", text)
        self.assertNotIn("{{", text)
        self.assertIn("standalone", text)

    def test_page_is_bilingual_and_omits_empty_sections(self):
        self.cli("memory-add", "--kind", "human-intent", "--text", "Keep the CLI minimal")
        self.cli("status-refresh", "--overwrite")
        text = (self.runtime / "PROJECT_STATUS.md").read_text(encoding="utf-8")
        self.assertIn("目标 / Goal", text)
        self.assertIn("下一步 / Next step", text)
        # No risks were recorded, so that section must not appear at all.
        self.assertNotIn("风险 / Risks", text)

    def test_refresh_refuses_to_clobber_an_edited_page_without_overwrite(self):
        self.assertIn("already exists", self.cli("status-refresh", code=2))
        self.cli("status-refresh", "--overwrite")

    def test_context_compiler_points_at_the_page_and_only_relevant_memory(self):
        self.cli("memory-add", "--kind", "human-intent", "--text", "Ship a minimal CLI")
        self.cli("memory-add", "--kind", "rejected", "--text", "Rejected a background daemon")
        result = json.loads(self.cli("context", "--role", "lead", "--task", "simplify the CLI"))
        self.assertEqual(result["task"], "simplify the CLI")
        self.assertTrue(result["project_status_path"].endswith("PROJECT_STATUS.md"))
        kinds = {entry["kind"] for entry in result["memory"]}
        self.assertIn("human-intent", kinds)
        self.assertNotIn("rejected", kinds)

    def test_context_without_task_keeps_the_role_packet(self):
        result = json.loads(self.cli("context", "--role", "lead"))
        self.assertIn("status", result)
        self.assertNotIn("task", result)


if __name__ == "__main__":
    unittest.main()
