"""Feed recorded hook inputs to the hook scripts and check what they answer.

Each fixture in tests/fixtures/hooks is shaped like the hook input examples in
Claude Code's hooks reference. Its name starts with the expected outcome:
deny (PreToolUse denies the call), block (the agent may not stop yet) or allow.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from invigilator.runlog import read_transcript

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
READONLY, QA = ROOT / "hooks" / "reviewer_readonly.py", ROOT / "hooks" / "qa_before_done.py"
SCRIPTS = {"PreToolUse": READONLY, "Stop": QA, "SubagentStop": QA}


def run_hook(script, payload, project=None):
    """Run a hook script on payload. Each run gets its own plugin data folder, so every stop
    is the first one for its state."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    with tempfile.TemporaryDirectory() as data:
        env["CLAUDE_PLUGIN_DATA"] = data
        done = subprocess.run([sys.executable, str(script)], input=payload, capture_output=True,
                              text=True, cwd=ROOT, env=env, timeout=60)
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}, done.stderr


def outcome(output):
    if output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        return "deny"
    return "block" if output.get("decision") == "block" else "allow"


def fixture(name):
    return (FIXTURES / "hooks" / f"{name}.json").read_text()


class HookScriptTest(unittest.TestCase):
    def test_every_fixture_gets_the_outcome_in_its_name(self):
        seen = {}
        for path in sorted((FIXTURES / "hooks").glob("*.json")):
            event = json.loads(path.read_text())["hook_event_name"]
            code, output, stderr = run_hook(SCRIPTS[event], path.read_text())
            expected = path.stem.split("-")[0]
            self.assertEqual((code, outcome(output)), (0, expected), f"{path.name}: {output} {stderr}")
            if expected == "deny":
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
                self.assertIsInstance(output["hookSpecificOutput"]["permissionDecisionReason"], str)
            if expected == "block":
                self.assertIsInstance(output["reason"], str)
            seen.setdefault(SCRIPTS[event].name, set()).add(expected)
        self.assertEqual(seen, {"reviewer_readonly.py": {"deny", "allow"},
                                "qa_before_done.py": {"block", "allow"}})

    def test_each_script_leaves_other_events_alone(self):
        for path in sorted((FIXTURES / "hooks").glob("*.json")):
            event = json.loads(path.read_text())["hook_event_name"]
            other = QA if SCRIPTS[event] is READONLY else READONLY
            self.assertEqual(run_hook(other, path.read_text())[:2], (0, {}), path.name)

    def test_deny_names_the_agent_and_what_the_call_does(self):
        reason = run_hook(READONLY, fixture("deny-reviewer-bash-write"))[1][
            "hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("blocked this Bash call: it runs sed -i", reason)
        self.assertIn("lists code-reviewer as a reviewer", reason)
        reason = run_hook(READONLY, fixture("deny-reviewer-spawns-writer"))[1][
            "hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("general-purpose subagent", reason)

    def test_block_says_why_and_what_to_run(self):
        reasons = {name: run_hook(QA, fixture(name))[1]["reason"] for name in
                   ("block-main-no-qa", "block-main-qa-failed", "block-main-qa-piped",
                    "block-builder-qa-before-edit", "block-builder-qa-in-background")}
        self.assertIn("'npm run check' never ran", reasons["block-main-no-qa"])
        self.assertIn("QA (`npm run check`) failed after your last edit", reasons["block-main-qa-failed"])
        self.assertIn("tell the user which check fails and why", reasons["block-main-qa-failed"])
        self.assertIn("exit code was not seen", reasons["block-main-qa-piped"])
        self.assertIn("before the last file write", reasons["block-builder-qa-before-edit"])
        self.assertIn("result was not recorded", reasons["block-builder-qa-in-background"])
        self.assertTrue(all("run `npm run check`" in r for name, r in reasons.items() if name != "block-main-qa-failed"))

    def test_unreadable_transcript_is_reported_not_passed_quietly(self):
        output = run_hook(QA, fixture("allow-missing-transcript"))[1]
        self.assertIn("the QA check did not run for the main session", output["systemMessage"])

    def test_a_transcript_in_an_unknown_format_is_reported_not_passed_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "t.jsonl")
            path.write_text('{"kind": "turn", "calls": [{"tool": "Write"}]}\n')
            event = json.loads(fixture("block-main-no-qa"))
            event["transcript_path"] = str(path)
            output = run_hook(QA, json.dumps(event))[1]
        self.assertIn("did not run", output["systemMessage"])
        self.assertIn("no messages in the transcript format", output["systemMessage"])

    def test_input_that_is_not_json_is_a_non_blocking_error(self):
        code, output, stderr = run_hook(READONLY, "not json")
        self.assertEqual((code, output), (1, {}))
        self.assertIn("not JSON", stderr)


class HookConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)

    def configure(self, text):
        (self.project / ".claude").mkdir(exist_ok=True)
        (self.project / ".claude" / "invigilator.json").write_text(text)

    def test_without_a_config_reviewers_are_still_read_only_and_qa_is_off(self):
        self.assertEqual(outcome(run_hook(READONLY, fixture("deny-reviewer-write"), self.project)[1]), "deny")
        self.assertEqual(run_hook(QA, fixture("block-main-no-qa"), self.project)[1], {})

    def test_project_dir_wins_over_cwd(self):
        self.configure(json.dumps({"reviewer_agents": ["auditor"]}))
        self.assertEqual(run_hook(READONLY, fixture("deny-reviewer-write"), self.project)[1], {})

    def test_a_catch_all_builder_pattern_still_skips_claude_codes_own_agents(self):
        self.configure(json.dumps({"builder_agents": ["*"], "qa_commands": ["npm run check"]}))
        self.assertEqual(run_hook(QA, fixture("allow-internal-agent-stops"), self.project)[1], {})
        event = json.loads(fixture("block-main-no-qa"))
        event["cwd"] = str(self.project)  # the transcript's build/index.html is then in this project
        self.assertEqual(outcome(run_hook(QA, json.dumps(event), self.project)[1]), "block")

    def test_the_readonly_hook_only_answers_pre_tool_use(self):
        event = json.loads(fixture("deny-reviewer-write"))
        event["hook_event_name"] = "PostToolUse"
        self.assertEqual(run_hook(READONLY, json.dumps(event))[1], {})

    def test_block_all_denies_every_shell_command(self):
        self.configure(json.dumps({"reviewer_shell": "block-all"}))
        output = run_hook(READONLY, fixture("allow-reviewer-bash-read"), self.project)[1]
        self.assertEqual(outcome(output), "deny")

    def test_broken_config_is_reported_and_defaults_apply(self):
        for text, problem in (("{", "could not be read"),
                              ('{"reviewer_agent": ["x"]}', "unknown field 'reviewer_agent'"),
                              ('{"reviewer_shell": "off"}', "must be \"block-writes\" or \"block-all\""),
                              ('{"qa_commands": "npm test"}', "must be a list of strings")):
            self.configure(text)
            output = run_hook(READONLY, fixture("deny-reviewer-write"), self.project)[1]
            self.assertEqual(outcome(output), "deny", text)
            self.assertIn(problem, output["systemMessage"])
            self.assertIn("Using the default config", output["systemMessage"])


class TranscriptTest(unittest.TestCase):
    def test_results_set_ok(self):
        calls = read_transcript(FIXTURES / "transcripts" / "builder-qa-failed.jsonl")
        self.assertEqual([(c.tool, c.ok) for c in calls], [("Write", True), ("Bash", False)])

    def test_background_and_missing_results_are_unknown(self):
        calls = read_transcript(FIXTURES / "transcripts" / "builder-qa-background.jsonl")
        self.assertEqual([c.ok for c in calls], [True, None])
        lines = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm run check"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "toolUseResult": {"backgroundTaskId": "b1"}, "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "moved to the background"}]}},
            "not json {",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "t.jsonl")
            path.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines))
            self.assertEqual([c.ok for c in read_transcript(path)], [None, None])


if __name__ == "__main__":
    unittest.main()
