from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import statectl  # noqa: E402


class StateCtlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        if args and args[0] == "set-review-status" and "--assignment-revision" not in args:
            path = self.root / ".agent-project-manager/review/STATUS.json"
            if path.exists():
                revision = json.loads(path.read_text(encoding="utf-8")).get("revision", 1)
                args = (*args, "--assignment-revision", str(revision))
            if "--status" in args and args[args.index("--status") + 1] == "completed":
                args = (*args, "--verdict", "approved")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = statectl.main([*args, "--project-root", str(self.root)])
        return result, stdout.getvalue(), stderr.getvalue()

    def init(self) -> Path:
        result, _, error = self.run_cli(
            "init", "--project-id", "sample-project", "--profile", "generic"
        )
        self.assertEqual(result, 0, error)
        self._leader_mode = False
        return self.root / ".agent-project-manager"

    def leader_mode(self) -> None:
        """Opt into delegation; standalone is the default and has no roles."""
        if not getattr(self, "_leader_mode", False):
            result, _, error = self.run_cli("set-project", "--mode", "leader", "--status", "active")
            self.assertEqual(result, 0, error)
            self._leader_mode = True

    def add_worker(
        self,
        worker_id: str = "worker-1",
        scope: str = "src/**",
        *extra: str,
    ) -> tuple[int, str, str]:
        # Delegation is opt-in: switch modes before registering the first Worker.
        self.leader_mode()
        return self.run_cli(
            "add-worker",
            "--worker-id",
            worker_id,
            "--objective",
            f"Complete {worker_id} work.",
            "--allowed-scope",
            scope,
            "--read-dependency",
            "tests/**",
            "--completion-criterion",
            "Relevant tests pass.",
            *extra,
        )

    def reassign_worker(
        self,
        worker_id: str = "worker-1",
        scope: str = "src/m2/**",
        *extra: str,
    ) -> tuple[int, str, str]:
        return self.run_cli(
            "reassign-worker",
            "--worker-id",
            worker_id,
            "--milestone",
            "M2",
            "--objective",
            "Complete the M2 assignment.",
            "--allowed-scope",
            scope,
            "--read-dependency",
            "src/m1/**",
            "--completion-criterion",
            "M2 tests pass.",
            *extra,
        )

    def test_init_is_idempotent_and_does_not_overwrite(self) -> None:
        runtime = self.init()
        before = (runtime / "STATE.json").read_bytes()
        result, output, error = self.run_cli(
            "init", "--project-id", "different-project", "--profile", "other"
        )
        self.assertEqual(result, 0, error)
        self.assertIn("Already initialized", output)
        self.assertEqual((runtime / "STATE.json").read_bytes(), before)

    def test_partial_runtime_is_rejected(self) -> None:
        (self.root / ".agent-project-manager").mkdir()
        result, _, error = self.run_cli(
            "init", "--project-id", "sample-project", "--profile", "generic"
        )
        self.assertEqual(result, 2)
        self.assertIn("partial runtime", error)

    def test_init_publishes_atomically_without_partial_runtime(self) -> None:
        original_rename = Path.rename

        def crash_before_publish(path: Path, target: Path) -> Path:
            if Path(target).resolve() == (self.root / ".agent-project-manager").resolve():
                raise RuntimeError("crash before publish")
            return original_rename(path, target)

        with mock.patch.object(Path, "rename", crash_before_publish):
            with self.assertRaisesRegex(RuntimeError, "crash before publish"):
                self.run_cli(
                    "init", "--project-id", "sample-project", "--profile", "generic"
                )
        self.assertFalse((self.root / ".agent-project-manager").exists())
        self.assertEqual(self.init(), self.root / ".agent-project-manager")

    def test_init_creates_only_the_three_minimal_files(self) -> None:
        """The manager must cost less than the work it saves: three files, nothing else."""
        runtime = self.init()
        self.assertEqual(
            sorted(path.name for path in runtime.iterdir()),
            ["PROJECT_STATUS.md", "STATE.json", "memory.jsonl"],
        )
        # Everything else appears only when a command first needs it.
        for name in ("PLAN.md", "OWNER_STATUS.md", "OWNER_DIRECTIVES.md", "HANDOFF.md",
                     "workers", "review", "inbox"):
            self.assertFalse((runtime / name).exists(), name)
        self.assertEqual(self.run_cli("validate")[0], 0)

        # The bilingual human page is the human-facing entry point.
        page = (runtime / "PROJECT_STATUS.md").read_text(encoding="utf-8")
        self.assertIn("# Project Status: sample-project", page)
        result, output, error = self.run_cli("status")
        self.assertEqual(result, 0, error)
        self.assertIn("PROJECT_STATUS.md", output)

    def test_worker_registration_status_and_summary(self) -> None:
        runtime = self.init()
        result, _, error = self.add_worker()
        self.assertEqual(result, 0, error)
        result, _, error = self.run_cli(
            "set-project",
            "--phase",
            "execution",
            "--status",
            "active",
            "--milestone",
            "Implement",
            "--next-actor",
            "worker-1",
            "--next-action",
            "Continue worker-1.",
        )
        self.assertEqual(result, 0, error)
        result, _, error = self.run_cli(
            "set-worker-status",
            "--worker-id",
            "worker-1",
            "--status",
            "active",
            "--summary",
            "Implementation is underway.",
            "--next-action",
            "Run tests.",
            "--files-changed",
            "src/example.py",
            "--verification",
            "unit tests pending",
        )
        self.assertEqual(result, 0, error)
        result, output, error = self.run_cli("status")
        self.assertEqual(result, 0, error)
        self.assertIn("worker-1: active", output)
        self.assertIn("Implementation is underway.", output)
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual(state["workers"][0]["task_path"], "workers/worker-1/TASK.md")

    def test_add_worker_marker_recovers_after_interruption(self) -> None:
        runtime = self.init()
        with mock.patch.object(
            statectl, "recover_add_worker", side_effect=RuntimeError("crash")
        ):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.add_worker()
        marker = statectl.add_worker_marker_path(runtime, "worker-1")
        self.assertTrue(marker.is_file())
        self.assertFalse((runtime / "workers" / "worker-1").exists())
        self.assertEqual(self.run_cli("status")[0], 2)
        self.assertTrue(marker.exists())
        self.assertEqual(self.run_cli("recover")[0], 0)
        self.assertFalse(marker.exists())
        self.assertTrue((runtime / "workers" / "worker-1" / "TASK.md").is_file())
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in state["workers"]], ["worker-1"])

    def test_new_project_does_not_create_meaningless_reviewer(self) -> None:
        runtime = self.init()
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        review = state["review"]
        self.assertFalse(review["required"])
        self.assertEqual(review["level"], "none")
        self.assertIsNone(review["reviewer_id"])
        # No review has ever been assigned, so there is no review directory at all.
        self.assertFalse((runtime / "review").exists())

    def test_v020_schema_v1_runtime_without_history_is_compatible(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        history = runtime / "workers" / "worker-1" / "history"
        # v0.2.0 runtimes had Schema v1 but no assignment-history directory.
        self.assertFalse(history.exists())
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_blocker_recovery_and_completion_transitions(self) -> None:
        self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-project", "--phase", "execution", "--status", "active"
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "blocked",
                "--summary",
                "Architecture decision required.",
                "--next-action",
                "Return to Project Lead.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-project", "--phase", "execution", "--status", "blocked"
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "active",
                "--summary",
                "Decision received; implementation resumed.",
                "--next-action",
                "Complete validation.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-project", "--phase", "execution", "--status", "active"
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Implementation and tests completed.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-project", "--phase", "complete", "--status", "complete"
            )[0],
            0,
        )
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_more_than_three_workers_requires_justification(self) -> None:
        self.init()
        for number in range(1, 4):
            extra = (
                ()
                if number == 1
                else (
                    "--coordination-justification",
                    f"module-{number} is an independent parallel adapter.",
                )
            )
            result, _, error = self.add_worker(
                f"worker-{number}", f"module-{number}/**", *extra
            )
            self.assertEqual(result, 0, error)
        result, _, error = self.add_worker("worker-4", "module-4/**")
        self.assertEqual(result, 2)
        self.assertIn("requires --coordination-justification", error)
        result, _, error = self.add_worker(
            "worker-4",
            "module-4/**",
            "--coordination-justification",
            "Four independent platform adapters can be validated separately.",
        )
        self.assertEqual(result, 0, error)

    def test_duplicate_active_write_scope_is_rejected(self) -> None:
        self.init()
        self.assertEqual(self.add_worker()[0], 0)
        result, _, error = self.add_worker("worker-2", "src/**")
        self.assertEqual(result, 2)
        self.assertIn("Write scope overlaps", error)

    def test_milestone_change_does_not_register_a_new_worker(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-project",
                "--phase",
                "execution",
                "--status",
                "active",
                "--milestone",
                "M2",
            )[0],
            0,
        )
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in state["workers"]], ["worker-1"])

    def test_completed_worker_reassignment_archives_history_and_continues(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker(scope="src/m1/**")[0], 0)
        early_result, _, early_error = self.reassign_worker()
        self.assertEqual(early_result, 2)
        self.assertIn("not completed", early_error)
        self.assertFalse((runtime / "workers" / "worker-1" / "history").exists())
        blocker = runtime / "workers" / "worker-1" / "BLOCKER.md"
        blocker.write_text("# Blocker\n\nResolved M1 evidence.\n", encoding="utf-8")
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "M1 completed.",
                "--files-changed",
                "src/m1/result.py",
                "--verification",
                "M1 tests passed.",
            )[0],
            0,
        )
        old_task = (runtime / "workers" / "worker-1" / "TASK.md").read_text(
            encoding="utf-8"
        )
        direct_result, _, direct_error = self.run_cli(
            "set-worker-status",
            "--worker-id",
            "worker-1",
            "--status",
            "ready",
            "--summary",
            "Unsafe direct restart.",
        )
        self.assertEqual(direct_result, 2)
        self.assertIn("Invalid Worker transition", direct_error)

        result, output, error = self.reassign_worker()
        self.assertEqual(result, 0, error)
        self.assertIn("completed -> ready", output)
        worker_dir = runtime / "workers" / "worker-1"
        history = worker_dir / "history" / "assignment-0001"
        self.assertEqual((history / "TASK.md").read_text(encoding="utf-8"), old_task)
        self.assertEqual(
            json.loads((history / "STATUS.json").read_text(encoding="utf-8"))["status"],
            "completed",
        )
        self.assertIn(
            "Resolved M1 evidence.",
            (history / "BLOCKER.md").read_text(encoding="utf-8"),
        )
        new_task = (worker_dir / "TASK.md").read_text(encoding="utf-8")
        self.assertIn("## Assignment revision\n\n2", new_task)
        self.assertIn("Complete the M2 assignment.", new_task)
        status = json.loads((worker_dir / "STATUS.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["files_changed"], [])

        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["workers"]), 1)
        self.assertEqual(state["workers"][0]["id"], "worker-1")
        self.assertEqual(state["workers"][0]["write_scope"], ["src/m2/**"])
        self.assertEqual(state["current_milestone"], "M2")
        self.assertEqual(state["next_action"]["actor"], "worker-1")
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "active",
                "--summary",
                "M2 execution resumed in the same Worker conversation.",
            )[0],
            0,
        )
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_additional_parallel_worker_requires_justification(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker("worker-1", "src/core/**")[0], 0)
        result, _, error = self.add_worker("worker-2", "src/adapter/**")
        self.assertEqual(result, 2)
        self.assertIn("additional Worker requires --coordination-justification", error)
        result, _, error = self.add_worker(
            "worker-2",
            "src/adapter/**",
            "--coordination-justification",
            "The adapter is independently testable and ready in parallel.",
        )
        self.assertEqual(result, 0, error)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-2",
                "--status",
                "active",
                "--summary",
                "Independent adapter work started in parallel.",
            )[0],
            0,
        )
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in state["workers"]], ["worker-1", "worker-2"])

    def test_reassignment_preserves_active_write_scope_ownership(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker("worker-1", "src/m1/**")[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "M1 completed.",
            )[0],
            0,
        )
        self.assertEqual(
            self.add_worker(
                "worker-2",
                "src/shared/**",
                "--coordination-justification",
                "A separate active responsibility needs isolated context.",
            )[0],
            0,
        )
        result, _, error = self.reassign_worker("worker-1", "src/shared/**")
        self.assertEqual(result, 2)
        self.assertIn("Write scope overlaps with worker-2", error)
        self.assertFalse((runtime / "workers" / "worker-1" / "history").exists())

    def test_owner_feedback_is_verbatim_and_unique(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        message = "This direction feels too heavy; keep the interface."
        result, first_path, error = self.run_cli(
            "record-owner-feedback",
            "--worker-id",
            "worker-1",
            "--message",
            message,
        )
        self.assertEqual(result, 0, error)
        result, second_path, error = self.run_cli(
            "record-owner-feedback",
            "--worker-id",
            "worker-1",
            "--message",
            message,
        )
        self.assertEqual(result, 0, error)
        self.assertNotEqual(first_path, second_path)
        events = list((runtime / "inbox" / "owner").glob("*.md"))
        self.assertEqual(len(events), 2)
        self.assertTrue(all(message in path.read_text(encoding="utf-8") for path in events))
        snapshot = statectl.status_snapshot(runtime)
        self.assertEqual(snapshot["pending_owner_feedback"], 2)

    def test_owner_feedback_status_is_read_only_from_frontmatter(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        result, event_path, error = self.run_cli(
            "record-owner-feedback",
            "--worker-id",
            "worker-1",
            "--message",
            "First line\nstatus: pending\nKeep this verbatim.",
        )
        self.assertEqual(result, 0, error)
        event_id = Path(event_path.strip()).stem
        self.assertEqual(
            self.run_cli(
                "resolve-owner-feedback",
                "--event-id",
                event_id,
                "--resolution",
                "Handled without reopening.",
            )[0],
            0,
        )
        self.assertEqual(statectl.status_snapshot(runtime)["pending_owner_feedback"], 0)
        content = (runtime / "inbox" / "owner" / f"{event_id}.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("First line\nstatus: pending\nKeep this verbatim.", content)
        self.assertIn('status: "resolved"', content.split("---", 2)[1])

    def test_review_assignment_and_completion(self) -> None:
        self.init()
        self.leader_mode()  # Reviews only exist in delegated (leader) projects.
        result, _, error = self.run_cli(
            "assign-review",
            "--reviewer-id",
            "reviewer-1",
            "--level",
            "balanced",
            "--objective",
            "Review the bounded implementation.",
            "--scope",
            "src/**",
            "--completion-criterion",
            "Report evidence for every finding.",
        )
        self.assertEqual(result, 0, error)
        self.assertEqual(
            self.run_cli(
                "set-review-status",
                "--reviewer-id",
                "reviewer-1",
                "--status",
                "active",
                "--summary",
                "Review started.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-review-status",
                "--reviewer-id",
                "reviewer-1",
                "--status",
                "completed",
                "--summary",
                "Review completed with evidence.",
                "--verification",
                "All acceptance checks inspected.",
            )[0],
            0,
        )
        self.assertEqual(self.run_cli("validate")[0], 0)

    def test_review_must_finish_and_be_invalidated_before_execution_resumes(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Implementation completed.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "assign-review",
                "--reviewer-id",
                "reviewer-1",
                "--level",
                "balanced",
                "--objective",
                "Review implementation.",
                "--completion-criterion",
                "Evidence is complete.",
            )[0],
            0,
        )
        result, _, error = self.run_cli(
            "set-project", "--phase", "execution", "--status", "active"
        )
        self.assertEqual(result, 2)
        self.assertIn("review is unfinished", error)
        result, _, error = self.add_worker(
            "worker-2",
            "docs/**",
            "--coordination-justification",
            "Independent documentation context.",
        )
        self.assertEqual(result, 2)
        self.assertIn("during review", error)
        self.assertEqual(
            self.run_cli(
                "set-review-status",
                "--reviewer-id",
                "reviewer-1",
                "--status",
                "completed",
                "--summary",
                "Review completed.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-project", "--phase", "execution", "--status", "active"
            )[0],
            0,
        )
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertTrue(state["review"]["required"])
        self.assertIsNone(state["review"]["reviewer_id"])
        result, _, error = self.run_cli(
            "set-project", "--phase", "complete", "--status", "complete"
        )
        self.assertEqual(result, 2)
        self.assertIn("required review is unfinished or stale", error)

    def test_strong_review_requires_explicit_value_justification(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Implementation completed.",
            )[0],
            0,
        )
        base = (
            "assign-review",
            "--reviewer-id",
            "reviewer-1",
            "--level",
            "strong",
            "--objective",
            "Review high-risk integration.",
            "--completion-criterion",
            "Security and integration evidence is complete.",
        )
        result, _, error = self.run_cli(*base)
        self.assertEqual(result, 2)
        self.assertIn("strong-justification", error)
        result, _, error = self.run_cli(
            *base,
            "--strong-justification",
            "The change crosses a security boundary and alters the core protocol.",
        )
        self.assertEqual(result, 0, error)
        task = (runtime / "review" / "TASK.md").read_text(encoding="utf-8")
        self.assertIn("crosses a security boundary", task)

    def test_completed_project_reopens_with_snapshot_and_reuses_worker(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Initial release completed.",
                "--verification",
                "Initial tests passed.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli("set-project", "--phase", "complete", "--status", "complete")[0],
            0,
        )
        completed_state = (runtime / "STATE.json").read_text(encoding="utf-8")
        result, output, error = self.run_cli(
            "reopen-project",
            "--reason",
            "Owner requested an actionable correction.",
            "--milestone",
            "M2 correction",
        )
        self.assertEqual(result, 0, error)
        self.assertIn("archived completion 1", output)
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual((state["phase"], state["status"]), ("planning", "active"))
        self.assertEqual([worker["id"] for worker in state["workers"]], ["worker-1"])
        self.assertEqual(
            json.loads(
                (runtime / "workers" / "worker-1" / "STATUS.json").read_text(
                    encoding="utf-8"
                )
            )["status"],
            "completed",
        )
        snapshot = runtime / "history" / "completion-0001"
        self.assertEqual((snapshot / "STATE.json").read_text(encoding="utf-8"), completed_state)
        # v1.1 archives only what the project actually has. This project never
        # assigned a review and never created PLAN.md, OWNER_DIRECTIVES.md,
        # HANDOFF.md or OWNER_STATUS.md, so none of them are archived.
        for path in (
            snapshot / "memory.jsonl",
            snapshot / "workers" / "worker-1" / "TASK.md",
            snapshot / "workers" / "worker-1" / "STATUS.json",
            snapshot / "workers" / "worker-1" / "BLOCKER.md",
        ):
            self.assertTrue(path.is_file(), path)
        for name in ("PLAN.md", "OWNER_DIRECTIVES.md", "HANDOFF.md", "OWNER_STATUS.md"):
            self.assertFalse((snapshot / name).exists(), name)
        self.assertFalse((snapshot / "review").exists())
        self.assertIn(
            "Owner requested an actionable correction.",
            (snapshot / "REOPEN.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(self.reassign_worker()[0], 0)
        self.assertEqual(
            json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))["workers"][0][
                "id"
            ],
            "worker-1",
        )
        self.assertTrue(
            (runtime / "workers" / "worker-1" / "history" / "assignment-0001").is_dir()
        )

    def test_reopen_reuses_snapshot_after_archive_state_crash_boundary(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Done.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli("set-project", "--phase", "complete", "--status", "complete")[0],
            0,
        )
        self.assertEqual(statectl.archive_project_completion(runtime, "Crash simulation"), 1)
        result, _, error = self.run_cli(
            "reopen-project",
            "--reason",
            "Crash simulation",
            "--milestone",
            "Resume",
        )
        self.assertEqual(result, 0, error)
        self.assertEqual(len(list((runtime / "history").glob("completion-*"))), 1)

    def test_read_only_status_does_not_reopen_completed_project(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Done.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli("set-project", "--phase", "complete", "--status", "complete")[0],
            0,
        )
        before = (runtime / "STATE.json").read_bytes()
        self.assertEqual(self.run_cli("status")[0], 0)
        self.assertEqual((runtime / "STATE.json").read_bytes(), before)
        self.assertFalse((runtime / "history").exists())

    def test_completion_rejects_unfinished_work_review_and_feedback(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        result, _, error = self.run_cli(
            "set-project", "--phase", "complete", "--status", "complete"
        )
        self.assertEqual(result, 2)
        self.assertIn("unfinished Workers", error)
        self.assertEqual(
            json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))["status"],
            "active",
        )
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Done.",
            )[0],
            0,
        )
        feedback_result, feedback_path, error = self.run_cli(
            "record-owner-feedback",
            "--worker-id",
            "worker-1",
            "--message",
            "Please change the behavior.",
        )
        self.assertEqual(feedback_result, 0, error)
        result, _, error = self.run_cli(
            "set-project", "--phase", "complete", "--status", "complete"
        )
        self.assertEqual(result, 2)
        self.assertIn("Owner feedback is pending", error)
        event_id = Path(feedback_path.strip()).stem
        self.assertEqual(
            self.run_cli(
                "resolve-owner-feedback",
                "--event-id",
                event_id,
                "--resolution",
                "Converted into the accepted plan.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli("set-project", "--phase", "complete", "--status", "complete")[0],
            0,
        )

    def test_review_is_invalidated_and_archived_before_re_review(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "M1 done.",
            )[0],
            0,
        )
        review_args = (
            "assign-review",
            "--reviewer-id",
            "reviewer-1",
            "--level",
            "balanced",
            "--objective",
            "Review implementation.",
            "--completion-criterion",
            "Evidence is complete.",
        )
        self.assertEqual(self.run_cli(*review_args)[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-review-status",
                "--reviewer-id",
                "reviewer-1",
                "--status",
                "completed",
                "--summary",
                "Changes requested with evidence.",
            )[0],
            0,
        )
        self.assertEqual(self.reassign_worker()[0], 0)
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertTrue(state["review"]["required"])
        self.assertIsNone(state["review"]["reviewer_id"])
        self.assertEqual(statectl.status_snapshot(runtime)["review"]["status"], "not-assigned")
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Review fixes completed.",
            )[0],
            0,
        )
        result, _, error = self.run_cli(
            "set-project", "--phase", "complete", "--status", "complete"
        )
        self.assertEqual(result, 2)
        self.assertIn("required review is unfinished or stale", error)
        self.assertEqual(self.run_cli(*review_args)[0], 0)
        archived = runtime / "review" / "history" / "review-0001"
        self.assertTrue(archived.is_dir())
        self.assertIn(
            "Changes requested",
            (archived / "STATUS.json").read_text(encoding="utf-8"),
        )

    def test_review_assignment_marker_recovers_after_interruption(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Implementation done.",
            )[0],
            0,
        )
        args = (
            "assign-review",
            "--reviewer-id",
            "reviewer-1",
            "--level",
            "balanced",
            "--objective",
            "Review implementation.",
            "--completion-criterion",
            "Evidence is complete.",
        )
        with mock.patch.object(
            statectl, "recover_review_assignment", side_effect=RuntimeError("crash")
        ):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.run_cli(*args)
        marker = runtime / "review" / statectl.REVIEW_ASSIGNMENT_MARKER
        self.assertTrue(marker.is_file())
        self.assertEqual(self.run_cli("status")[0], 2)
        self.assertTrue(marker.exists())
        self.assertEqual(self.run_cli("recover")[0], 0)
        self.assertFalse(marker.exists())
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "review")
        self.assertEqual(state["review"]["reviewer_id"], "reviewer-1")
        self.assertEqual(
            json.loads((runtime / "review" / "STATUS.json").read_text(encoding="utf-8"))[
                "status"
            ],
            "ready",
        )

    def test_completed_project_is_frozen_except_explicit_reopen(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Done.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli("set-project", "--phase", "complete", "--status", "complete")[0],
            0,
        )
        before = (runtime / "STATE.json").read_bytes()
        result, _, error = self.run_cli(
            "set-project",
            "--phase",
            "complete",
            "--status",
            "complete",
            "--next-actor",
            "OWNER",
            "--next-action",
            "Mutate frozen state.",
        )
        self.assertEqual(result, 2)
        self.assertIn("state is frozen", error)
        self.assertEqual((runtime / "STATE.json").read_bytes(), before)
        result, _, error = self.run_cli(
            "set-worker-status",
            "--worker-id",
            "worker-1",
            "--status",
            "inactive",
            "--summary",
            "Unsafe mutation.",
        )
        self.assertEqual(result, 2)
        self.assertIn("completed project", error)
        result, _, error = self.run_cli(
            "record-owner-feedback",
            "--worker-id",
            "worker-1",
            "--message",
            "Actionable new work.",
        )
        self.assertEqual(result, 2)
        self.assertIn("use reopen-project", error)

    def test_inactive_dependency_cannot_activate_and_does_not_mutate(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker("worker-1", "src/core/**")[0], 0)
        self.assertEqual(
            self.add_worker(
                "worker-2",
                "src/adapter/**",
                "--depends-on",
                "worker-1",
                "--coordination-justification",
                "Independent adapter context.",
            )[0],
            0,
        )
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "inactive",
                "--summary",
                "Abandoned without a completed deliverable.",
            )[0],
            0,
        )
        status_path_value = runtime / "workers" / "worker-2" / "STATUS.json"
        before = status_path_value.read_bytes()
        result, _, error = self.run_cli(
            "set-worker-status",
            "--worker-id",
            "worker-2",
            "--status",
            "active",
            "--summary",
            "Unsafe activation.",
        )
        self.assertEqual(result, 2)
        self.assertIn("dependency worker-1 is inactive", error)
        self.assertEqual(status_path_value.read_bytes(), before)

    def test_dependency_cannot_be_bypassed_or_reassigned_under_ready_dependent(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker("worker-1", "src/core/**")[0], 0)
        self.assertEqual(
            self.add_worker(
                "worker-2",
                "src/adapter/**",
                "--depends-on",
                "worker-1",
                "--coordination-justification",
                "Independent adapter context.",
            )[0],
            0,
        )
        status_path_value = runtime / "workers" / "worker-2" / "STATUS.json"
        before = status_path_value.read_bytes()
        result, _, error = self.run_cli(
            "set-worker-status",
            "--worker-id",
            "worker-2",
            "--status",
            "completed",
            "--summary",
            "Unsafe dependency bypass.",
        )
        self.assertEqual(result, 2)
        self.assertIn("dependency worker-1 is ready", error)
        self.assertEqual(status_path_value.read_bytes(), before)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "Core done.",
            )[0],
            0,
        )
        result, _, error = self.reassign_worker("worker-1", "src/core-v2/**")
        self.assertEqual(result, 2)
        self.assertIn("nonterminal dependent worker-2", error)

    def test_reassignment_marker_recovers_after_interruption(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "M1 done.",
            )[0],
            0,
        )
        with mock.patch.object(statectl, "recover_reassignment", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.reassign_worker()
        worker_dir = runtime / "workers" / "worker-1"
        marker = worker_dir / statectl.REASSIGNMENT_MARKER
        self.assertTrue(marker.is_file())
        self.assertEqual(
            json.loads((worker_dir / "STATUS.json").read_text(encoding="utf-8"))["status"],
            "completed",
        )
        self.assertEqual(self.run_cli("status")[0], 2)
        self.assertTrue(marker.exists())
        self.assertEqual(self.run_cli("recover")[0], 0)
        self.assertFalse(marker.exists())
        self.assertEqual(
            json.loads((worker_dir / "STATUS.json").read_text(encoding="utf-8"))["status"],
            "ready",
        )
        self.assertTrue((worker_dir / "history" / "assignment-0001").is_dir())

    def test_reassignment_recovery_refuses_newer_worker_content(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        self.assertEqual(
            self.run_cli(
                "set-worker-status",
                "--worker-id",
                "worker-1",
                "--status",
                "completed",
                "--summary",
                "M1 done.",
            )[0],
            0,
        )
        with mock.patch.object(statectl, "recover_reassignment", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.reassign_worker()
        task = runtime / "workers" / "worker-1" / "TASK.md"
        task.write_text("# Newer external task\n", encoding="utf-8")
        result, _, error = self.run_cli("recover")
        self.assertEqual(result, 2)
        self.assertIn("refusing to overwrite", error)
        self.assertEqual(task.read_text(encoding="utf-8"), "# Newer external task\n")

    def test_glob_parent_scope_overlap_is_rejected(self) -> None:
        self.init()
        self.assertEqual(self.add_worker("worker-1", "src/**")[0], 0)
        result, _, error = self.add_worker(
            "worker-2",
            "src/adapter/**",
            "--coordination-justification",
            "Independent adapter context.",
        )
        self.assertEqual(result, 2)
        self.assertIn("Write scope overlaps", error)
        result, _, error = self.add_worker(
            "worker-3",
            "**",
            "--coordination-justification",
            "This broad scope should still be rejected.",
        )
        self.assertEqual(result, 2)
        self.assertIn("Write scope overlaps", error)

    def test_literal_parent_scope_overlap_is_rejected(self) -> None:
        self.init()
        self.assertEqual(self.add_worker("worker-1", "src/adapter")[0], 0)
        result, _, error = self.add_worker(
            "worker-2",
            "src/adapter/generated",
            "--coordination-justification",
            "Separate generated-code context.",
        )
        self.assertEqual(result, 2)
        self.assertIn("Write scope overlaps", error)

    def test_tampered_escaping_path_fails_validation(self) -> None:
        runtime = self.init()
        self.assertEqual(self.add_worker()[0], 0)
        state = json.loads((runtime / "STATE.json").read_text(encoding="utf-8"))
        state["workers"][0]["task_path"] = "../outside.md"
        with (runtime / "STATE.json").open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(json.dumps(state))
        result, _, error = self.run_cli("validate")
        self.assertEqual(result, 1)
        self.assertIn("normalized path", error)


if __name__ == "__main__":
    unittest.main()
