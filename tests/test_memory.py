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


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apm memory ")
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / ".agent-project-manager"
        self.cli("init", "--project-id", "memory-test")

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

    def add(self, kind, text):
        return json.loads(self.cli("memory-add", "--kind", kind, "--text", text))

    def test_init_creates_an_empty_memory_file(self):
        self.assertTrue((self.runtime / "memory.jsonl").is_file())
        self.assertEqual((self.runtime / "memory.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(json.loads(self.cli("memory-show"))["entries"], [])

    def test_entries_are_appended_with_stable_increasing_ids(self):
        first = self.add("human-intent", "No extra runtime dependencies")
        second = self.add("decision", "Plain files only")
        self.assertEqual([first["id"], second["id"]], ["MEM-0001", "MEM-0002"])
        lines = (self.runtime / "memory.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_unknown_kind_and_empty_text_are_rejected(self):
        self.cli("memory-add", "--kind", "not-a-kind", "--text", "x", code=2)
        self.cli("memory-add", "--kind", "lesson", "--text", "   ", code=2)

    def test_malformed_memory_line_is_reported_not_silently_ignored(self):
        (self.runtime / "memory.jsonl").write_text("{not json}\n", encoding="utf-8")
        self.assertIn("not valid JSON", self.cli("memory-show", code=1))

    def test_task_filter_keeps_intent_and_drops_irrelevant_entries(self):
        self.add("human-intent", "Deployment must stay a single command")
        self.add("rejected", "Rejected a Redis queue because it adds operations burden")
        entries = json.loads(self.cli("memory-show", "--task", "write the deployment guide"))["entries"]
        kinds = {entry["kind"] for entry in entries}
        # Intent is a standing requirement, so it always survives filtering.
        self.assertIn("human-intent", kinds)
        self.assertNotIn("rejected", kinds)

    def test_kind_filter_returns_only_that_kind(self):
        self.add("lesson", "Cache invalidation needs a version key")
        self.add("constraint", "Python 3.9 compatibility is mandatory")
        entries = json.loads(self.cli("memory-show", "--kind", "constraint"))["entries"]
        self.assertEqual([entry["kind"] for entry in entries], ["constraint"])

    def test_consolidate_previews_then_archives_and_deduplicates(self):
        for index in range(5):
            self.add("lesson", f"Observation {index}")
        self.add("decision", "Use plain files")
        self.add("decision", "use plain files")  # Duplicate modulo case.
        output = self.cli("memory-consolidate", "--keep", "3")
        preview = json.loads(output[: output.rindex("}") + 1])
        self.assertEqual(preview["remaining"], 3)
        self.assertIn("Preview only", output)
        self.assertEqual((self.runtime / "memory.jsonl").read_text(encoding="utf-8").count("\n"), 7)
        self.cli("memory-consolidate", "--keep", "3", "--apply")
        remaining = [json.loads(line) for line in
                     (self.runtime / "memory.jsonl").read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(remaining), 3)
        archive = (self.runtime / "memory-archive.md").read_text(encoding="utf-8")
        # 7 entries -> the case-insensitive duplicate collapses (1 dropped), then
        # the retention window drops 3 of the remaining 6.
        self.assertEqual(archive.count("### MEM-"), 4)
        # The case-insensitive duplicate is archived, never kept twice.
        self.assertEqual(sum(1 for entry in remaining if entry["kind"] == "decision"), 1)

    def test_consolidate_on_empty_memory_is_a_no_op(self):
        self.assertIn("empty", self.cli("memory-consolidate", "--apply"))

    def test_consolidate_never_archives_intent_or_constraint(self):
        """Long-lived human intent and constraints survive any retention window.

        They are requirements an agent must not silently drop, and since
        read_entries only reads memory.jsonl, archiving one would hide it from
        relevant() forever.
        """
        self.add("human-intent", "Ship a verified offline import pipeline")
        self.add("constraint", "Never require network access")
        for index in range(10):
            self.add("lesson", f"Observation {index}")

        output = self.cli("memory-consolidate", "--keep", "2")
        preview = json.loads(output[: output.rindex("}") + 1])
        # 12 entries, cap 2, but the two protected entries are exempt from the cap.
        self.assertEqual(preview["remaining"], 2)
        self.assertEqual(preview["protected"], 2)
        self.cli("memory-consolidate", "--keep", "2", "--apply")

        remaining = [json.loads(line) for line in
                     (self.runtime / "memory.jsonl").read_text(
                         encoding="utf-8").splitlines() if line]
        kinds = {entry["kind"] for entry in remaining}
        self.assertIn("human-intent", kinds)
        self.assertIn("constraint", kinds)
        archive = (self.runtime / "memory-archive.md").read_text(encoding="utf-8")
        self.assertNotIn("Ship a verified offline import pipeline", archive)
        self.assertNotIn("Never require network access", archive)

        # A constraint cannot be pushed out even by a flood of newer entries.
        for index in range(30):
            self.add("lesson", f"Later observation {index}")
        self.cli("memory-consolidate", "--keep", "1", "--apply")
        kept = (self.runtime / "memory.jsonl").read_text(encoding="utf-8")
        self.assertIn("Never require network access", kept)
        self.assertIn("Ship a verified offline import pipeline", kept)

    def test_protected_entries_still_reach_relevant_selection(self):
        self.add("human-intent", "Ship a verified offline import pipeline")
        self.add("constraint", "Never require network access")
        for index in range(10):
            self.add("lesson", f"Unrelated observation {index}")
        self.cli("memory-consolidate", "--keep", "1", "--apply")
        output = self.cli("memory-show", "--task", "unrelated cleanup")
        entries = json.loads(output[: output.rindex("}") + 1])["entries"]
        texts = {entry["text"] for entry in entries}
        self.assertIn("Ship a verified offline import pipeline", texts)
        self.assertIn("Never require network access", texts)

    def test_validation_rejects_memory_missing_from_runtime(self):
        (self.runtime / "memory.jsonl").unlink()
        self.assertIn("memory.jsonl", self.cli("validate", code=1))


if __name__ == "__main__":
    unittest.main()
