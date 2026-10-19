import tempfile
import unittest
from pathlib import Path

from release_checks.readonly import (ReadOnlyTools, audit, changes, command_writes, snapshot,
                                     stub_reviewer, writes)

GOOD = Path(__file__).resolve().parent.parent / "examples" / "builds" / "good"

WRITING_COMMANDS = [
    "echo 'Looks fine.' > REVIEW_NOTES.md",
    "cat notes >> index.html",
    "npm test &> test.log",
    "sed -i 's/a/b/' index.html",
    "sed -Ei.bak 's/a/b/' index.html",
    "perl -pi -e 's/a/b/' index.html",
    "rm -rf build",
    "FORCE=1 sudo -n mv a b",
    "cd build && touch x",
    "git -C repo commit -m 'review notes'",
    "git stash",
    "find . -name '*.tmp' -delete",
    "find . -name '*.tmp' -exec rm {} \\;",
    "ls | xargs -n1 cp -t /tmp",
    "echo $(rm -rf x)",
    "echo `rm -rf x`",
    "ls\nrm notes.txt",
    "curl -sSLo page.html https://example.com",
    "npm install left-pad",
    "cat <<EOF > notes.md\nhello\nEOF",
    "echo 'unclosed",
]

READ_ONLY_COMMANDS = [
    "git diff --stat 2>&1 | head -20",
    "grep -rn 'a > b' src",
    "ls -la > /dev/null",
    "npm test 2>/dev/null",
    "sed -n '1,20p' index.html",
    "perl -Mstrict -ne 'print' index.html",
    "git -C repo log --oneline",
    "find . -name '*.html' -exec grep -l main {} +",
    "python - <<'EOF'\nif 1 > 0:\n    print('x')\nEOF",
    "curl -s https://example.com",
]


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

    def test_writing_commands_are_caught(self):
        missed = [c for c in WRITING_COMMANDS if not command_writes(c)]
        self.assertEqual(missed, [])

    def test_read_only_commands_pass(self):
        flagged = {c: command_writes(c) for c in READ_ONLY_COMMANDS if command_writes(c)}
        self.assertEqual(flagged, {})

    def test_reasons_name_what_was_written(self):
        self.assertEqual(command_writes("echo hi > notes.md"), "redirects output to notes.md")
        self.assertEqual(command_writes("git -C x commit -m y"), "runs git commit")

    def test_tool_calls_that_write(self):
        self.assertEqual(writes("Write", {"file_path": "/p/a.html", "content": "x"}), "Write /p/a.html")
        self.assertTrue(writes("Edit", {"file_path": "/p/a.html"}))
        self.assertTrue(writes("NotebookEdit", {"notebook_path": "/p/a.ipynb"}))
        self.assertTrue(writes("PowerShell", {"command": "Get-ChildItem"}))
        self.assertTrue(writes("Bash", {"command": "rm a"}))
        self.assertEqual(writes("Bash", {"command": "ls"}), "")
        self.assertEqual(writes("Read", {"file_path": "/p/a.html"}), "")
        self.assertEqual(writes("Grep", {"pattern": "x"}), "")


if __name__ == "__main__":
    unittest.main()
