import tempfile
import unittest
from pathlib import Path

from release_checks.readonly import ReadOnlyTools, audit, changes, snapshot, stub_reviewer

GOOD = Path(__file__).resolve().parent.parent / "examples" / "builds" / "good"


class ReadOnlyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_before = snapshot(GOOD)
        cls.open_runs = audit(stub_reviewer, GOOD, 91, seed=0)
        cls.locked_runs = audit(stub_reviewer, GOOD, 91, seed=0, tools=ReadOnlyTools)

    def test_snapshot_diff_finds_changed_added_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a")
            (root / "b.txt").write_text("b")
            before = snapshot(root)
            (root / "a.txt").write_text("a2")
            (root / "b.txt").unlink()
            (root / "c.txt").write_text("c")
            self.assertEqual(changes(before, snapshot(root)),
                             ["changed a.txt", "added c.txt", "deleted b.txt"])

    def test_stub_reviewer_edits_in_a_small_seeded_fraction(self):
        edited = [r for r in self.open_runs if r.changes]
        self.assertTrue(0 < len(edited) < 10)
        self.assertEqual([r for r in self.open_runs if r.blocked], [])

    def test_read_only_tools_block_every_write(self):
        self.assertEqual([r for r in self.locked_runs if r.changes], [])
        self.assertEqual([r.number for r in self.locked_runs if r.blocked],
                         [r.number for r in self.open_runs if r.changes])

    def test_audit_never_touches_the_source_workspace(self):
        self.assertEqual(snapshot(GOOD), self.source_before)


if __name__ == "__main__":
    unittest.main()
