import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from invigilator.readonly import (FOLD_CASE, ReadOnlyTools, audit, changes, command_writes, in_temp,
                                  project_scope, resolve_path, reviewer_scope, snapshot, stub_reviewer,
                                  writes)

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
        self.assertEqual(writes("Write", {"file_path": "/p/a.html", "content": "x"}), "writes /p/a.html")
        self.assertTrue(writes("Edit", {"file_path": "/p/a.html"}))
        self.assertTrue(writes("NotebookEdit", {"notebook_path": "/p/a.ipynb"}))
        self.assertTrue(writes("PowerShell", {"command": "Get-ChildItem"}))
        self.assertTrue(writes("Bash", {"command": "rm a"}))
        self.assertEqual(writes("Bash", {"command": "ls"}), "")
        self.assertEqual(writes("Read", {"file_path": "/p/a.html"}), "")
        self.assertEqual(writes("Grep", {"pattern": "x"}), "")


PROJECT = "/work/site"


def stop_counts(command, cwd=PROJECT, project=PROJECT):
    return bool(command_writes(command, project_scope(project, cwd)))


def reviewer_denies(command, cwd=PROJECT, project=PROJECT):
    return bool(command_writes(command, reviewer_scope(project, cwd)))


class ProjectScopeTest(unittest.TestCase):
    """The Stop hook counts a write that lands in the project, or where it can't tell."""

    def check(self, cases):
        wrong = {c: e for c, e in cases if stop_counts(c) != e}
        self.assertEqual(wrong, {})

    def test_project_relative_paths(self):
        self.check([("touch build/a", True), ("echo hi > notes.md", True), ("rm -rf build", True),
                    ("sed -i s/a/b/ src/app.js", True), ("git checkout .", True), ("npm run build", True)])

    def test_cd_moves_the_starting_folder(self):
        self.check([("cd /elsewhere && touch a", False), ("cd src && touch a", True), ("cd .. && touch a", False),
                    ("cd /elsewhere; cd /work/site/src && touch a", True), ("cd /elsewhere && cd - && touch a", True),
                    ("(cd /elsewhere && touch a) && touch b", True), ("(cd /elsewhere && touch a)", False),
                    ("cd /elsewhere && (cd /work/site && touch a)", True), ('cd "$DIR" && touch a', True),
                    ("pushd /elsewhere && touch a && popd && touch b", True),
                    ("cd /elsewhere && echo $(touch a)", True), ("git -C /elsewhere/repo checkout .", False)])

    def test_absolute_paths_in_and_out(self):
        self.check([("touch /work/site/a", True), ("touch /work/other/a", False), ("touch /work/site-old/a", False),
                    ("touch /work/site/../other/a", False), ("touch src/../../other/a", False),
                    ("echo hi > /elsewhere/log", False), ("cp /work/site/a /elsewhere/", False),
                    ("mv /work/site/a /elsewhere/", True), ("cp /elsewhere/a src/", True),
                    ("touch /elsewhere/a build/b", True), ("touch /elsewhere/a /work/other/b", False)])

    def test_temp_and_home_are_outside_unless_the_project_is_there(self):
        home = os.path.expanduser("~")
        self.check([("touch /tmp/a", False), ("mktemp", False), ("echo x >> ~/notes.md", False),
                    ("cat > $HOME/a", False), ("cat > ${HOME}/a", False)])
        self.assertTrue(stop_counts("touch ~/site/a", project=os.path.join(home, "site")))
        self.assertTrue(stop_counts("touch /tmp/proj/a", project="/tmp/proj"))

    def test_a_target_it_cannot_place_counts(self):
        self.check([('touch "$OUT"', True), ("touch `pwd`/a", True), ("ls | xargs rm", True),
                    ("cd /elsewhere && npm install", True), ("cd /elsewhere && make", True),
                    ("cd /elsewhere && sed 's/a/b/w out.txt' in.txt", True),("touch ~nosuchuser12345/a", True),
                    ("echo 'unclosed", True)])

    def test_write_tools(self):
        scope = project_scope(PROJECT, PROJECT)
        self.assertTrue(writes("Write", {"file_path": "build/a.html"}, scope))
        self.assertTrue(writes("Edit", {"file_path": "/work/site/a.html"}, scope))
        self.assertEqual(writes("Write", {"file_path": "/elsewhere/a.html"}, scope), "")
        self.assertEqual(writes("NotebookEdit", {"notebook_path": "~/a.ipynb"}, scope), "")
        self.assertTrue(writes("Write", {"file_path": ""}, scope), "no target counts")
        self.assertTrue(writes("PowerShell", {"command": "Set-Content /elsewhere/a x"}, scope))
        self.assertTrue(writes("Write", {"file_path": "a.html"}, scope.at("/work/site/src")))
        self.assertEqual(writes("Write", {"file_path": "a.html"}, scope.at("/elsewhere")), "")

    @unittest.skipUnless(FOLD_CASE, "paths differ only by case on this system")
    def test_case_is_ignored_where_the_file_system_ignores_it(self):
        self.assertTrue(stop_counts("touch /WORK/Site/a"))

    def test_windows_paths(self):
        project = "C:\\Users\\me\\site"
        self.assertEqual(resolve_path("C:\\Users\\me\\site\\..\\x.txt", ""), "C:\\Users\\me\\x.txt")
        self.assertEqual(resolve_path("/c/Users/me/a", project), "C:\\Users\\me\\a")
        self.assertEqual(resolve_path("build/a", project), "C:\\Users\\me\\site\\build\\a")
        self.assertTrue(stop_counts("touch build/a", cwd=project, project=project))
        self.assertTrue(stop_counts("touch C:/USERS/ME/Site/a", cwd=project, project=project))
        self.assertTrue(stop_counts("touch /c/Users/me/site/a", cwd=project, project=project))
        self.assertFalse(stop_counts("touch C:/Users/me/other/a", cwd=project, project=project))
        self.assertFalse(stop_counts("cd .. && touch a", cwd=project, project=project))
        self.assertTrue(stop_counts("cd .. && cd site/build && touch a", cwd=project, project=project))
        self.assertFalse(stop_counts("cd /c/Users/me && touch a", cwd=project, project=project))


class ReviewerScopeTest(unittest.TestCase):
    """A reviewer may write only to temp folders outside the project."""

    def test_writes_to_temp_are_allowed(self):
        allowed = ["touch /tmp/a", "mktemp", "mktemp -d", "mkdir -p /tmp/x && cp src/a.js /tmp/x/",
                   "git diff > /tmp/d.diff", "cd /tmp && touch a", "touch /private/tmp/a",
                   "touch /var/folders/ab/xyz/T/a", "ls -la > /dev/null"]
        self.assertEqual([c for c in allowed if reviewer_denies(c)], [])
        with mock.patch.dict(os.environ, {"TMPDIR": "/scratch/me/tmp/"}):
            self.assertFalse(reviewer_denies("echo x > $TMPDIR/a"))
            self.assertFalse(reviewer_denies("echo x > /scratch/me/tmp/a"))

    def test_every_other_write_is_denied(self):
        denied = ["touch a", "touch /tmp/a build/b", "cat > ~/.claude/notes.md", "touch /tmp/../etc/x",
                  "touch /tmpfoo/a", "ls /tmp | xargs rm", "(cd /tmp && touch a) && rm -rf build",
                  "cd /tmp && npm install", 'touch "$OUT"', "touch /elsewhere/a", "cp /tmp/a /work/site/",
                  "mv /tmp/a /tmp/b src/", "cd /tmp && cd - && touch a", "sed -i s/a/b/ /tmp/a src/b"]
        self.assertEqual([c for c in denied if not reviewer_denies(c)], [])

    def test_temp_inside_the_project_is_the_project(self):
        self.assertTrue(reviewer_denies("touch /tmp/proj/a", project="/tmp/proj"))
        self.assertTrue(reviewer_denies("cd /tmp/proj && touch a", project="/tmp/proj"))
        self.assertFalse(reviewer_denies("touch /tmp/other/a", project="/tmp/proj"))

    def test_write_tools(self):
        scope = reviewer_scope(PROJECT, PROJECT)
        self.assertEqual(writes("Write", {"file_path": "/tmp/notes.md"}, scope), "")
        self.assertTrue(writes("Write", {"file_path": "/work/site/a.md"}, scope))
        self.assertTrue(writes("Write", {"file_path": "~/a.md"}, scope))
        self.assertTrue(writes("Write", {"file_path": ""}, scope))
        self.assertTrue(writes("PowerShell", {"command": "Set-Content /tmp/a x"}, scope))

    @unittest.skipIf(os.name == "nt", "needs symlinks")
    def test_a_link_from_temp_into_the_project_is_the_project(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as other:
            os.symlink(project, os.path.join(other, "p"))
            self.assertTrue(reviewer_denies(f"touch {other}/p/a", cwd=project, project=project))
            self.assertFalse(reviewer_denies(f"touch {other}/a", cwd=project, project=project))

    def test_windows_temp(self):
        self.assertTrue(in_temp("C:\\Users\\me\\AppData\\Local\\Temp\\x"))
        self.assertFalse(in_temp("C:\\Users\\me\\AppData\\Local\\Tempest\\x"))
        self.assertFalse(in_temp("C:\\Users\\me\\site\\x"))
        project = "C:\\Users\\me\\site"
        self.assertFalse(reviewer_denies("touch C:/Users/me/AppData/Local/Temp/a", cwd=project, project=project))
        self.assertTrue(reviewer_denies("touch C:/Users/me/AppData/Roaming/a", cwd=project, project=project))


if __name__ == "__main__":
    unittest.main()
