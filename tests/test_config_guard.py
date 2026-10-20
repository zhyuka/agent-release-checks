"""Any agent that writes .claude/invigilator.json could turn the checks off.

The PreToolUse hook answers "ask" for such a call, whoever makes it, so the
user decides. Reads of the file, and writes elsewhere, are left alone.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from invigilator.hooks import DEFAULTS, reviewer_readonly

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "reviewer_readonly.py"

SHELL_WRITES = [
    "echo '{}' > .claude/invigilator.json",
    "echo '{}' >| ./.claude/invigilator.json",
    "printf '{}' >> .claude/invigilator.json",
    "sed -i '' 's/block-writes/block-all/' .claude/invigilator.json",
    "perl -pi -e 's/x/y/' .claude/invigilator.json",
    "rm .claude/invigilator.json",
    "rm -rf .claude",
    "mv .claude/invigilator.json .claude/off.json",
    "cp /tmp/other.json .claude/invigilator.json",
    "cp /tmp/other.json .claude/",
    "cp -r /tmp/elsewhere/.claude .",
    "truncate -s 0 .claude/invigilator.json",
    "tee .claude/invigilator.json < /tmp/other.json",
    "dd if=/tmp/other.json of=.claude/invigilator.json",
    "cd .claude && rm invigilator.json",
    "cd src && echo '{}' > ../.claude/invigilator.json",
    "rm .claude/*.json",
    "rm .claude/invigilato?.json",
    "rm -rf .cl*",
    "F=.claude/invigilator.json; echo '{}' > $F",
    "echo .claude/invigilator.json | xargs rm",
    "find .claude -name '*.json' -delete",
    "find .claude -type f -exec rm {} +",
    "sudo rm .claude/invigilator.json",
    "bash -c 'echo {} > .claude/invigilator.json'",
    "python3 -c \"open('.claude/invigilator.json', 'w').write('{}')\"",
    "python3 -c \"import os; os.remove(os.path.join('.claude', 'invigilator' + '.json'))\"",
    "node -e \"require('fs').writeFileSync('.claude/invigilator.json', '{}')\"",
    "git checkout -- .claude/invigilator.json",
    "git restore .claude",
    "ln -sf /tmp/other.json .claude/invigilator.json",
    "ln -s .claude/invigilator.json c && echo '{}' > c",
    "rm .Claude/Invigilator.json",
    "echo 'unclosed > .claude/invigilator.json",
    "rm \"$CLAUDE_DIR/invigilator.json\"",
]

SHELL_READS = [
    "cat .claude/invigilator.json",
    "jq .qa_commands .claude/invigilator.json",
    "python3 -c \"import json; print(json.load(open('.claude/invigilator.json')))\"",
    "cp .claude/invigilator.json /tmp/backup.json",
    "grep reviewer .claude/invigilator.json > /tmp/found.txt",
    "git add .claude/invigilator.json",
    "git diff .claude/invigilator.json",
    "git log -p -- .claude/invigilator.json",
    "mkdir -p .claude",
    "ls -la .claude",
    "echo hi > notes.md",
    "rm -rf build",
    "find src -type f -exec rm {} +",
    "rm *.json",
    "pip install invigilator",
    "echo 'unclosed",
    # The file's name without .json is also the package's and the repo folder's name.
    "cd invigilator && echo 'unclosed",
    "rm -rf \"$WORK/invigilator\"",
    "python -m invigilator demo > \"$OUT\"",
]


def event(tool, tool_input, agent=None, cwd=None):
    out = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
    if agent:
        out["agent_type"] = agent
    if cwd:
        out["cwd"] = str(cwd)
    return out


def decision(output):
    return (output or {}).get("hookSpecificOutput", {}).get("permissionDecision", "allow")


class ConfigGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / "src").mkdir()
        self.config = self.project / ".claude" / "invigilator.json"
        self.config.write_text("{}\n")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, tool, tool_input, agent="main", cwd=None):
        return reviewer_readonly(event(tool, tool_input, agent, cwd or self.project), dict(DEFAULTS))

    def test_shell_writes_to_the_config_ask_for_every_agent(self):
        for agent in ("main", "builder", "general-purpose"):
            for command in SHELL_WRITES:
                with self.subTest(agent=agent, command=command):
                    self.assertEqual(decision(self.answer("Bash", {"command": command}, agent)), "ask")

    def test_shell_reads_of_the_config_are_left_alone(self):
        for command in SHELL_READS:
            with self.subTest(command=command):
                self.assertIsNone(self.answer("Bash", {"command": command}))

    def test_file_tool_writes_ask_however_the_path_is_written(self):
        paths = [str(self.config), ".claude/invigilator.json", "./.claude/invigilator.json",
                 "src/../.claude/invigilator.json",
                 str(self.project / ".claude" / "." / "invigilator.json"),
                 str(self.project / ".CLAUDE" / "Invigilator.JSON")]
        for tool in ("Write", "Edit", "MultiEdit"):
            for path in paths:
                with self.subTest(tool=tool, path=path):
                    self.assertEqual(decision(self.answer(tool, {"file_path": path})), "ask")

    def test_a_symlink_to_the_config_counts(self):
        link = self.project / "settings-link.json"
        try:
            link.symlink_to(self.config)
        except (OSError, NotImplementedError):
            self.skipTest("this file system cannot make symlinks")
        self.assertEqual(decision(self.answer("Write", {"file_path": str(link)})), "ask")
        self.assertEqual(decision(self.answer("Bash", {"command": "echo '{}' > settings-link.json"})), "ask")

    def test_other_files_and_reads_are_left_alone(self):
        for tool, tool_input in (("Write", {"file_path": str(self.project / "notes.md")}),
                                 ("Write", {"file_path": str(self.project / "docs" / "invigilator.json")}),
                                 ("Edit", {"file_path": str(self.project / ".claude" / "settings.json")}),
                                 ("Read", {"file_path": str(self.config)}),
                                 ("Grep", {"pattern": "reviewer", "path": str(self.config)})):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIsNone(self.answer(tool, tool_input))

    def test_project_dir_comes_from_the_environment_first(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        # Quoted, since bash reads a backslash outside quotes as an escape. Git Bash also takes C:/ and /c/.
        paths = [f"'{self.config}'", self.config.as_posix()]
        if os.name == "nt":
            paths.append("/" + self.config.drive[0].lower() + self.config.as_posix()[2:])
        for path in paths:
            with self.subTest(path=path):
                with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)}):
                    output = self.answer("Bash", {"command": f"rm {path}"}, cwd=elsewhere)
                self.assertEqual(decision(output), "ask")

    def test_the_reason_says_what_is_at_stake(self):
        output = self.answer("Bash", {"command": "rm .claude/invigilator.json"})
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(".claude/invigilator.json", reason)
        self.assertIn("turn the release checks off", reason)
        self.assertIn("needs your OK", reason)
        self.assertIn("runs rm", reason)

    def test_a_reviewer_is_still_denied(self):
        output = self.answer("Bash", {"command": "rm .claude/invigilator.json"}, agent="code-reviewer")
        self.assertEqual(decision(output), "deny")
        output = self.answer("Write", {"file_path": str(self.config)}, agent="code-reviewer")
        self.assertEqual(decision(output), "deny")

    def test_the_hook_script_answers_ask(self):
        payload = json.dumps(event("Bash", {"command": "echo '{}' > .claude/invigilator.json"},
                                   cwd=self.project))
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project))
        done = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                              text=True, cwd=ROOT, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(decision(json.loads(done.stdout)), "ask")


if __name__ == "__main__":
    unittest.main()
