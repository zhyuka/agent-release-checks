"""The shell write classifier on the command fixtures.

tests/fixtures/commands/classes.json holds generic commands for each class the
classifier got wrong on recorded agent sessions. redteam.json holds attempts to
write while looking harmless. In both, every command under "writes" must be
denied and every command under "reads" allowed. Red-team entries marked
"unhandled" are known misses: the test checks they are still missed, so the
list stays true when the classifier changes.
"""
import json
import shlex
import time
import unittest
from pathlib import Path

from invigilator import shell
from invigilator.readonly import command_writes, writes

COMMANDS = Path(__file__).resolve().parent / "fixtures" / "commands"


def load(name):
    return json.loads((COMMANDS / name).read_text(encoding="utf-8"))


class CommandFixtureTest(unittest.TestCase):
    def check(self, name):
        data = load(name)
        for entry in data["writes"]:
            with self.subTest(fixture=name, cls=entry["class"], command=entry["command"]):
                reason = command_writes(entry["command"])
                if entry.get("unhandled"):
                    self.assertEqual(reason, "", "now caught: drop its unhandled mark")
                    self.assertTrue(entry.get("why"), "say why it is missed")
                else:
                    self.assertTrue(reason, "a write the classifier allowed")
        for entry in data["reads"]:
            with self.subTest(fixture=name, cls=entry["class"], command=entry["command"]):
                self.assertEqual(command_writes(entry["command"]), "", "a read the classifier denied")

    def test_classes_from_recorded_sessions(self):
        self.check("classes.json")

    def test_red_team(self):
        self.check("redteam.json")

    def test_fixtures_are_big_enough_to_mean_something(self):
        red = load("redteam.json")
        self.assertGreaterEqual(len(red["writes"]), 60)
        self.assertGreaterEqual(len({e["class"] for e in red["writes"]}), 20)
        classes = load("classes.json")
        self.assertGreaterEqual(len({e["class"] for e in classes["writes"] + classes["reads"]}), 40)

    def test_reasons_name_what_writes(self):
        self.assertEqual(command_writes("zsh -lc 'echo hi > notes.md'"),
                         "redirects output to notes.md (in zsh -c)")
        self.assertEqual(command_writes("python3 -c \"open('a.txt', 'w')\""),
                         "runs Python code that opens a file for writing")
        self.assertEqual(command_writes("git fetch origin"), "runs git fetch")
        self.assertEqual(command_writes("python tools/report.py --out r.html"),
                         "runs report.py with --out r.html")

    def test_unparseable_and_runaway_input_counts_as_a_write(self):
        self.assertEqual(command_writes("echo 'unclosed"), "the command could not be parsed")
        self.assertEqual(command_writes("echo $(ls"), "the command could not be parsed")
        nested = "ls"
        for _ in range(12):
            nested = "bash -c " + shlex.quote(nested)
        self.assertEqual(command_writes(nested), "nests commands too deeply to check")

    def test_powershell_is_not_parsed(self):
        self.assertTrue(writes("PowerShell", {"command": "Get-ChildItem"}))

    def test_fast_enough_for_a_hook(self):
        data = load("classes.json")
        commands = [e["command"] for e in data["writes"] + data["reads"]] * 5
        start = time.perf_counter()
        for command in commands:
            command_writes(command)
        per_command = (time.perf_counter() - start) / len(commands)
        self.assertLess(per_command, 0.01, "over 10 ms a command")


class ShellSplitTest(unittest.TestCase):
    def words(self, command):
        return [s.words for level in shell.split(command) for s in level]

    def test_comments_end_at_the_line(self):
        self.assertEqual(self.words("ls # rm x\npwd"), [["ls"], ["pwd"]])
        self.assertEqual(self.words("echo a#b"), [["echo", "a#b"]])

    def test_substitutions_are_their_own_commands(self):
        self.assertEqual(self.words("echo \"$(rm x)\" `touch y`"),
                         [["echo", "$(rm x)", "`touch y`"], ["rm", "x"], ["touch", "y"]])

    def test_heredoc_bodies_are_stdin_not_commands(self):
        (simple,), = shell.split("cat <<'EOF' > out.txt\nrm -rf x\nEOF")
        self.assertEqual(simple.words, ["cat"])
        self.assertEqual(simple.redirects, [(">", "out.txt")])
        self.assertEqual(simple.stdin, [("rm -rf x", False)])

    def test_the_separator_after_a_group_belongs_to_its_last_command(self):
        level, = shell.split("(cd site && npm test) &")
        self.assertEqual([(s.words, s.then) for s in level], [(["cd", "site"], "&&"), (["npm", "test"], "&")])
        level, = shell.split("{ make; } | tail")
        self.assertEqual([(s.words, s.then) for s in level], [(["make"], "|"), (["tail"], "")])

    def test_heredoc_bodies_are_dropped_for_sizing(self):
        self.assertEqual(shell.drop_heredocs("cat <<'EOF' > a\nbody\nEOF\nls"), "cat <<'EOF' > a\nls")

    def test_file_descriptor_numbers_are_not_words(self):
        (simple,), = shell.split("ls 2>&1 3>/dev/null")
        self.assertEqual(simple.words, ["ls"])
        self.assertEqual(simple.redirects, [(">&", "1"), (">", "/dev/null")])

    def test_redirect_targets_that_are_not_files(self):
        for operator, target in ((">", "/dev/null"), (">&", "2"), (">&", "-"), (">", "/dev/stderr"),
                                 (">", "/dev/fd/3"), ("<", "a.txt"), (">", ">(wc -l)")):
            self.assertFalse(shell.writes_file(operator, target), (operator, target))
        for operator, target in ((">", "a.txt"), (">>", "/dev/../tmp/a"), (">|", "a"), ("&>", "log")):
            self.assertTrue(shell.writes_file(operator, target), (operator, target))


if __name__ == "__main__":
    unittest.main()
