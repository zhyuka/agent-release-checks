"""Any agent that writes .claude/release-checks.json could turn the checks off.

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

from release_checks.hooks import DEFAULTS, reviewer_readonly

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "reviewer_readonly.py"

SHELL_WRITES = [
    "echo '{}' > .claude/release-checks.json",
    "echo '{}' >| ./.claude/release-checks.json",
    "printf '{}' >> .claude/release-checks.json",
    "sed -i '' 's/block-writes/block-all/' .claude/release-checks.json",
    "perl -pi -e 's/x/y/' .claude/release-checks.json",
    "rm .claude/release-checks.json",
    "rm -rf .claude",
    "mv .claude/release-checks.json .claude/off.json",
    "cp /tmp/other.json .claude/release-checks.json",
    "cp /tmp/other.json .claude/",
    "cp -r /tmp/elsewhere/.claude .",
    "truncate -s 0 .claude/release-checks.json",
    "tee .claude/release-checks.json < /tmp/other.json",
    "dd if=/tmp/other.json of=.claude/release-checks.json",
    "cd .claude && rm release-checks.json",
    "cd src && echo '{}' > ../.claude/release-checks.json",
    "rm .claude/*.json",
    "rm .claude/release-check?.json",
    "rm -rf .cl*",
    "F=.claude/release-checks.json; echo '{}' > $F",
    "echo .claude/release-checks.json | xargs rm",
    "find .claude -name '*.json' -delete",
    "find .claude -type f -exec rm {} +",
    "sudo rm .claude/release-checks.json",
    "bash -c 'echo {} > .claude/release-checks.json'",
    "python3 -c \"open('.claude/release-checks.json', 'w').write('{}')\"",
    "python3 -c \"import os; os.remove(os.path.join('.claude', 'release-checks' + '.json'))\"",
    "node -e \"require('fs').writeFileSync('.claude/release-checks.json', '{}')\"",
    "git checkout -- .claude/release-checks.json",
    "git restore .claude",
    "ln -sf /tmp/other.json .claude/release-checks.json",
    "ln -s .claude/release-checks.json c && echo '{}' > c",
    "rm .Claude/Release-Checks.json",
    "echo 'unclosed > .claude/release-checks.json",
]

SHELL_READS = [
    "cat .claude/release-checks.json",
    "jq .qa_commands .claude/release-checks.json",
    "python3 -c \"import json; print(json.load(open('.claude/release-checks.json')))\"",
    "cp .claude/release-checks.json /tmp/backup.json",
    "grep reviewer .claude/release-checks.json > /tmp/found.txt",
    "git add .claude/release-checks.json",
    "git diff .claude/release-checks.json",
    "git log -p -- .claude/release-checks.json",
    "mkdir -p .claude",
    "ls -la .claude",
    "echo hi > notes.md",
    "rm -rf build",
    "find src -type f -exec rm {} +",
    "rm *.json",
    "pip install agent-release-checks",
    "echo 'unclosed",
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
        self.config = self.project / ".claude" / "release-checks.json"
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
        paths = [str(self.config), ".claude/release-checks.json", "./.claude/release-checks.json",
                 "src/../.claude/release-checks.json",
                 str(self.project / ".claude" / "." / "release-checks.json"),
                 str(self.project / ".CLAUDE" / "Release-Checks.JSON")]
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
                                 ("Write", {"file_path": str(self.project / "docs" / "release-checks.json")}),
                                 ("Edit", {"file_path": str(self.project / ".claude" / "settings.json")}),
                                 ("Read", {"file_path": str(self.config)}),
                                 ("Grep", {"pattern": "reviewer", "path": str(self.config)})):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertIsNone(self.answer(tool, tool_input))

    def test_project_dir_comes_from_the_environment_first(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.project)}):
            output = self.answer("Bash", {"command": f"rm {self.config}"}, cwd=elsewhere)
        self.assertEqual(decision(output), "ask")

    def test_the_reason_says_what_is_at_stake(self):
        output = self.answer("Bash", {"command": "rm .claude/release-checks.json"})
        reason = output["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(".claude/release-checks.json", reason)
        self.assertIn("turn the release checks off", reason)
        self.assertIn("needs your OK", reason)
        self.assertIn("runs rm", reason)

    def test_a_reviewer_is_still_denied(self):
        output = self.answer("Bash", {"command": "rm .claude/release-checks.json"}, agent="code-reviewer")
        self.assertEqual(decision(output), "deny")
        output = self.answer("Write", {"file_path": str(self.config)}, agent="code-reviewer")
        self.assertEqual(decision(output), "deny")

    def test_the_hook_script_answers_ask(self):
        payload = json.dumps(event("Bash", {"command": "echo '{}' > .claude/release-checks.json"},
                                   cwd=self.project))
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project))
        done = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                              text=True, cwd=ROOT, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(decision(json.loads(done.stdout)), "ask")


if __name__ == "__main__":
    unittest.main()
