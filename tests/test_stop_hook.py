"""The Stop and SubagentStop hook on transcripts shaped the way real ones are, and on bad input.

Each case here is a failure seen when real Claude Code transcripts (versions 2.1.136 to
2.1.288) were replayed through the hook: a call that wrote nothing counted as a write, a
QA run Claude Code moved to the background counted as a pass, a record copied back into
the file counted twice, PowerShell went unread, and bad input crashed or hung the hook.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import accepted
from invigilator import hooks, readonly, runlog, step_ran

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
TRANSCRIPTS = FIXTURES / "transcripts"
QA = ROOT / "hooks" / "qa_before_done.py"
QA_COMMANDS = ["npm run check"]


def run_hook(payload, project=FIXTURES / "project", timeout=60, data=None, env=None):
    """Run the Stop hook on payload. data is the plugin data folder, which keeps the stops the
    hook blocked once; without one, each run gets a fresh folder."""
    full_env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA")}
    if project:
        full_env["CLAUDE_PROJECT_DIR"] = str(project)
    with tempfile.TemporaryDirectory() as fresh, tempfile.TemporaryDirectory() as user:
        full_env["CLAUDE_CONFIG_DIR"] = accepted.accept(accepted.project_of(payload, project, ROOT), user)
        full_env.update(env or {})
        if data is None:
            full_env.setdefault("CLAUDE_PLUGIN_DATA", fresh)
        elif data:
            full_env["CLAUDE_PLUGIN_DATA"] = str(data)
        done = subprocess.run([sys.executable, str(QA)], input=payload, capture_output=True, text=True,
                              cwd=ROOT, env=full_env, timeout=timeout)
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}, done.stderr


def stop_event(path, **extra):
    event = {"session_id": "abc123", "transcript_path": str(path), "cwd": str(FIXTURES / "project"),
             "permission_mode": "default", "hook_event_name": "Stop", "stop_hook_active": False,
             "last_assistant_message": "Done."}
    event.update(extra)
    return json.dumps(event)


def tool_use(call_id, name, tool_input):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]}}


def tool_result(call_id, text="ok", error=False):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": error}]}}


WRITE = [tool_use("toolu_w", "Write", {"file_path": "build/index.html", "content": "<main></main>"}),
         tool_result("toolu_w")]
CHECK = [tool_use("toolu_q", "Bash", {"command": "npm run check"}), tool_result("toolu_q")]
DONE = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}]


def lines(*records):
    return "".join((r if isinstance(r, str) else json.dumps(r, separators=(",", ":"))) + "\n" for r in records)


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def transcript(self, text, name="t.jsonl"):
        path = self.tmp / name
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return path

    def outcome(self, payload, **kwargs):
        code, output, stderr = run_hook(payload, **kwargs)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 0, stderr)
        if output.get("decision") == "block":
            return "block"
        return "warn" if output.get("systemMessage") else "allow"


class WhatCountsTest(TempDir):
    """Calls that wrote nothing, QA that did not finish, and records Claude Code copied."""

    def test_reasons_for_the_new_block_fixtures(self):
        for name, reason in (("block-main-qa-replayed", "ran before the last file write"),
                             ("block-main-powershell-write-after-qa", "ran before the last file write"),
                             ("block-builder-qa-moved-to-background", "result was not recorded")):
            output = run_hook((FIXTURES / "hooks" / f"{name}.json").read_text())[1]
            self.assertIn(reason, output["reason"], name)

    def test_a_failed_edit_and_a_refused_command_wrote_nothing(self):
        calls = runlog.read_transcript(TRANSCRIPTS / "builder-failed-edit-after-qa.jsonl")
        self.assertEqual([(c.tool, c.ok) for c in calls], [("Write", True), ("Bash", True), ("Edit", False)])
        calls = runlog.read_transcript(TRANSCRIPTS / "builder-refused-write-after-qa.jsonl")
        self.assertEqual([(c.tool, c.ok, c.started) for c in calls],
                         [("Write", True, True), ("Bash", True, True), ("Bash", False, False)])
        failed = runlog.read_transcript(TRANSCRIPTS / "builder-qa-failed.jsonl")[1]
        self.assertTrue(failed.started, "a command that ran and exited non-zero still ran")

    def test_a_command_moved_to_the_background_has_no_result_yet(self):
        # Subagent transcripts carry no toolUseResult, so only the result text says so.
        calls = runlog.read_transcript(TRANSCRIPTS / "builder-qa-moved-to-background.jsonl")
        self.assertEqual([c.ok for c in calls], [True, None])
        for text in ("Command running in background with ID: b1. Output is being written to: /tmp/b1",
                     "Command was manually backgrounded by user with ID: b2. Output is being written to: /tmp/b2",
                     "Command was moved to the background (ID: b3) so that a message that arrived while it "
                     "was running can reach you; it was not interrupted."):
            path = self.transcript(lines(*WRITE, CHECK[0], tool_result("toolu_q", text)))
            self.assertEqual([c.ok for c in runlog.read_transcript(path)], [True, None], text)
            self.assertEqual(self.outcome(stop_event(path)), "block", text)

    def test_a_copied_record_counts_where_it_first_appeared(self):
        calls = runlog.read_transcript(TRANSCRIPTS / "builder-qa-replayed.jsonl")
        self.assertEqual([c.tool for c in calls], ["Write", "Bash", "Edit"])

    def test_reading_from_the_end_agrees_with_reading_from_the_start(self):
        project = str(FIXTURES / "project")
        for path in sorted(TRANSCRIPTS.glob("*.jsonl")):
            for scope in (None, readonly.project_scope(project, project)):
                forward = hooks._verdict(runlog.read_transcript(path), QA_COMMANDS, scope=scope)
                backward, _ = hooks.stop_verdict(path, QA_COMMANDS, runlog.Deadline(30), scope)
                self.assertEqual(forward and (forward.accepted, forward.reason),
                                 backward and (backward.accepted, backward.reason), (path.name, scope))

    def test_only_the_lines_that_decide_are_parsed(self):
        reads = [tool_use(f"toolu_r{n}", "Read", {"file_path": f"/p/src/{n}.js"}) for n in range(5000)]
        path = self.transcript(lines(*reads, *WRITE, *CHECK, *DONE))
        with mock.patch.object(runlog, "_parse", wraps=runlog._parse) as parse:
            verdict, _ = hooks.stop_verdict(path, QA_COMMANDS, runlog.Deadline(30))
        self.assertTrue(verdict.accepted)
        self.assertLess(parse.call_count, 10)

    def test_the_look_back_past_the_last_write_is_bounded(self):
        filler = [tool_use(f"toolu_r{n}", "Read", {"file_path": f"/p/src/{n}.js"}) for n in range(200)]
        path = self.transcript(lines(*CHECK, *filler, *WRITE, *DONE))
        verdict, _ = hooks.stop_verdict(path, QA_COMMANDS, runlog.Deadline(30))
        self.assertIn("ran before the last file write", verdict.reason)
        with mock.patch.object(hooks, "LOOK_BACK", 1000):
            verdict, _ = hooks.stop_verdict(path, QA_COMMANDS, runlog.Deadline(30))
        self.assertFalse(verdict.accepted)
        self.assertIn("did not run after the last file write", verdict.reason)

    def test_json_written_with_spaces_is_still_read(self):
        text = "".join(json.dumps(r, separators=(", ", ": ")) + "\n" for r in CHECK + WRITE + DONE)
        self.assertEqual(self.outcome(stop_event(self.transcript(text))), "block")


class GitTest(unittest.TestCase):
    """git commands that change only refs, the index or a remote are not writes QA has to see again."""

    def test_ref_only_git_is_not_a_write(self):
        ref_only = ["git fetch origin", "git remote update", "git pull --dry-run", "git -C repo fetch --prune",
                    "git add -A && git commit -m 'Fix nav' && git push", "cd repo && git fetch && git status",
                    "git tag v1.2", "git branch -D old", "git push origin main 2>&1 | tail -5",
                    "git commit -m \"$(cat <<'EOF'\nFix the nav (#12)\n\nCo-Authored-By: x\nEOF\n)\""]
        writes = ["git pull", "git checkout main", "git stash", "git reset --hard", "git log > out.txt",
                  "git fetch && rm -rf build", "git commit -m x#1 && rm -rf build", "git fetch; echo $(rm x)",
                  "git -c alias.f='!rm -rf build' f", "git diff --output=patch.txt", "npm run build && git add -A",
                  "git push 2>&1 | tee push.log", "git fetch | sed -i s/a/b/ notes.txt"]
        for command in ref_only:
            self.assertTrue(step_ran.ref_only(command), command)
            self.assertEqual(step_ran.wrote(runlog.ToolCall("Bash", {"command": command}), QA_COMMANDS), "", command)
        for command in writes:
            self.assertFalse(step_ran.ref_only(command), command)

    def test_the_shell_reading_follows_comments_substitutions_and_heredocs(self):
        # ref_only() reads the command with shell.split(), which keeps a # inside a word, ends a
        # comment at the line, and gives each substitution and heredoc its own commands. So it
        # needs no guard that gives up on a #, a backtick, $(, <( or <<.
        ref_only = ["git commit -m Fix#12 && git push", "git fetch origin # get the latest\ngit status",
                    "git push origin \"$(git rev-parse --abbrev-ref HEAD)\"", "git log `git rev-parse HEAD`",
                    "git commit -F - <<'EOF'\nFix the nav $(rm -rf build)\nEOF", "cat <(git show HEAD:a) | head -5",
                    "cd 'site #2' && git add -A && git commit -m 'wip'"]
        writes = ["git fetch # done\nrm -rf build", "git fetch; echo `rm x`", "git log $(rm -rf build)",
                  "git fetch && cat <(rm x)", "git push 2> >(tee push.log)",
                  "git commit -F - <<EOF\nFix the nav $(rm -rf build)\nEOF", "git fetch\nbash <<'EOF'\nrm x\nEOF",
                  "cd site#2 && rm -rf build"]
        for command in ref_only:
            self.assertTrue(step_ran.ref_only(command), command)
        for command in writes:
            self.assertFalse(step_ran.ref_only(command), command)
            self.assertTrue(step_ran.wrote(runlog.ToolCall("Bash", {"command": command}), QA_COMMANDS), command)

    def test_committing_after_qa_passed_needs_no_second_run(self):
        calls = runlog.read_transcript(TRANSCRIPTS / "builder-commit-after-qa.jsonl")
        self.assertTrue(hooks._verdict(calls, QA_COMMANDS).accepted)


class OneRuleTest(unittest.TestCase):
    """The Stop hook and the check command on a run.json count the same calls as writes."""

    def test_run_json_calls_follow_the_stop_hook_rules(self):
        cases = [
            ("a failed Edit", runlog.ToolCall("Edit", {"file_path": "build/a.html"}, False)),
            ("a refused command", runlog.ToolCall("Bash", {"command": "rm -rf build"}, False, started=False)),
            ("a ref-only git command", runlog.ToolCall("Bash", {"command": "git add -A && git commit -m x"})),
            ("a PowerShell read", runlog.ToolCall("PowerShell", {"command": "Get-ChildItem build"})),
        ]
        write = runlog.ToolCall("Write", {"file_path": "build/index.html"})
        qa = runlog.ToolCall("Bash", {"command": "npm run check"})
        for name, call in cases:
            calls = [write, qa, call]
            self.assertEqual(step_ran.last_write(calls, QA_COMMANDS), 0, name)
            self.assertTrue(step_ran.qa_ran(calls, QA_COMMANDS).accepted, name)
            self.assertTrue(hooks._verdict(calls, QA_COMMANDS).accepted, name)
        written = runlog.ToolCall("PowerShell", {"command": "Set-Content build/a.txt x"})
        self.assertEqual(step_ran.last_write([write, qa, written], QA_COMMANDS), 2)

    def test_a_powershell_qa_run_counts_in_a_run_json_too(self):
        calls = [runlog.ToolCall("Write", {"file_path": "build/index.html"}),
                 runlog.ToolCall("PowerShell", {"command": "npm run check"})]
        self.assertTrue(step_ran.qa_ran(calls, QA_COMMANDS).accepted)

    def test_the_hooks_reach_no_private_helper_in_another_module(self):
        import ast
        for module in ("hooks", "plugin", "step_ran", "readonly", "runlog", "check", "cli"):
            source = (ROOT / "invigilator" / f"{module}.py").read_text(encoding="utf-8")
            reached = sorted({f"{node.value.id}.{node.attr}" for node in ast.walk(ast.parse(source))
                              if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                              and node.value.id in ("hooks", "plugin", "shell", "step_ran", "readonly", "runlog")
                              and node.attr.startswith("_")})
            self.assertEqual(reached, [], module)


class ProjectScopeTest(TempDir):
    """A write counts only when it lands in the project, or where the hook can't tell."""

    def bash(self, command, cwd=None):
        record = tool_use("toolu_b", "Bash", {"command": command})
        return [dict(record, cwd=cwd) if cwd else record, tool_result("toolu_b")]

    def test_a_write_outside_the_project_needs_no_qa_run(self):
        outside = [tool_use("toolu_o", "Write", {"file_path": str(self.tmp / "notes.md")}), tool_result("toolu_o")]
        self.assertEqual(self.outcome(stop_event(self.transcript(lines(*outside, *DONE)))), "allow")
        self.assertEqual(self.outcome(stop_event(self.transcript(lines(*WRITE, *CHECK, *outside, *DONE)))), "allow")
        self.assertEqual(self.outcome(stop_event(self.transcript(lines(*CHECK, *outside, *WRITE, *DONE)))), "block")

    def test_a_relative_path_starts_where_the_call_ran(self):
        # The project is CLAUDE_PROJECT_DIR. The hook input's cwd, or the cwd on the
        # transcript record, says where build/index.html is.
        path = self.transcript(lines(*WRITE, *DONE))
        self.assertEqual(self.outcome(stop_event(path, cwd=str(self.tmp))), "allow")
        self.assertEqual(self.outcome(stop_event(path)), "block")
        moved = [dict(WRITE[0], cwd=str(FIXTURES / "project")), WRITE[1]]
        self.assertEqual(self.outcome(stop_event(self.transcript(lines(*moved, *DONE), "moved.jsonl"),
                                                 cwd=str(self.tmp))), "block")
        (self.tmp / ".claude").mkdir()
        config = {"qa_commands": QA_COMMANDS, "builder_agents": ["main"]}
        (self.tmp / ".claude" / "invigilator.json").write_text(json.dumps(config))
        self.assertEqual(self.outcome(stop_event(path, cwd=str(self.tmp)), project=None), "block",
                         "without CLAUDE_PROJECT_DIR the project is the hook input's cwd")

    def test_a_shell_cd_moves_where_a_write_lands(self):
        for command, expected in ((f"cd '{self.tmp}' && touch notes.txt", "allow"),
                                  ("cd build && touch notes.txt", "block"),
                                  (f"(cd '{self.tmp}' && touch a) && touch b", "block"),
                                  ("cd .. && touch notes.txt", "allow"),
                                  ("echo hi >> ~/notes.txt", "allow"),
                                  (f"touch '{self.tmp}/a' build/b", "block"),
                                  ('touch "$OUT"', "block"),
                                  (f"ls '{self.tmp}' | xargs rm", "block")):
            path = self.transcript(lines(*self.bash(command), *DONE))
            self.assertEqual(self.outcome(stop_event(path)), expected, command)

    def test_inline_python_writes_are_placed(self):
        # The literal target of an inline Python write is placed like a shell redirect: a write
        # in the project blocks, one in temp or home does not, and a target it cannot place blocks.
        for command, expected in (
                ("python3 -c \"open('build/x','w')\"", "block"),
                ("python3 -c \"open('/tmp/x','w')\"", "allow"),
                ("python3 -c \"import pathlib; pathlib.Path('/tmp/d').mkdir()\"", "allow"),
                ("python3 -c \"import os; os.remove(os.path.join(os.getcwd(),'f'))\"", "block"),
                ("python3 -c \"import shutil; shutil.move('build/a','/tmp/b')\"", "block"),
                ("python3 -c \"import json; json.dump({}, open('/tmp/a.json','w'))\"", "allow"),
                ("python3 -c \"import os; os.symlink('build/a','/tmp/l')\"", "allow"),
                ("python3 -c \"import tempfile; tempfile.NamedTemporaryFile()\"", "allow"),
                ("python3 -c \"import subprocess; subprocess.run(cmd)\"", "block"),
                ("python3 -c \"import subprocess; subprocess.run(['ls'])\"", "allow"),
                ("python3 -c \"open(target,'w')\"", "block")):
            path = self.transcript(lines(*self.bash(command), *DONE))
            self.assertEqual(self.outcome(stop_event(path)), expected, command)


class PowerShellTest(unittest.TestCase):
    def test_writes_are_read_from_cmdlets_redirects_and_programs(self):
        writes = ["New-Item -ItemType Directory build", "Get-Date > out.txt", "echo hi>out.txt", "cd a; rm b",
                  "$null = New-Item x", "[void](New-Item x)", "Invoke-WebRequest $u -OutFile f.zip",
                  "[IO.File]::WriteAllText('a', 'b')", "'x' | Out-File y.txt", "Get-Content a | Tee-Object b",
                  "git checkout main", "git -C repo stash", "npm install", "& cp a b",
                  "Get-ChildItem | Where-Object { $_.Length >= 10 }", "# it's a note\nRemove-Item dist -Recurse"]
        reads = ["Get-ChildItem *.md | Select-Object Name", "git status 2>$null", "git log 2>&1 | Select-Object -First 5",
                 "Get-Process | Format-Table", "$md = 1; Write-Output $md", "Write-Output 'done' | Out-Null",
                 "Select-String -Pattern '^> |x' notes.txt", "Write-Output \"a => b\"", "$h = @\"\nTo: <a@b.c>\n\"@\n$h",
                 "Write-Output 'it''s > fine'", "# don't > touch\nGet-ChildItem", ". ./build.ps1",
                 'git commit -m "fix(nav): spacing"', "git -C repo add -A; git push"]
        for command in writes:
            self.assertTrue(step_ran.powershell_writes(command), command)
        for command in reads:
            self.assertEqual(step_ran.powershell_writes(command), "", command)


class BadInputTest(TempDir):
    """The hook answers every input without a traceback, and says so when it cannot check."""

    def test_odd_hook_input_never_crashes(self):
        good = TRANSCRIPTS / "builder-qa-passed.jsonl"
        for payload in ("[" * 100000, json.dumps({"hook_event_name": "Stop"}),
                        stop_event(good, agent_type=5), stop_event(good, agent_type=["page-builder"]),
                        stop_event(5), stop_event(""), json.dumps({"hook_event_name": "SubagentStop",
                                                                   "agent_type": "page-builder"})):
            code, _, stderr = run_hook(payload)
            self.assertNotIn("Traceback", stderr, payload[:80])
            self.assertIn(code, (0, 1), payload[:80])
        code, _, stderr = run_hook(stop_event(good, cwd=5), project=None)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 0)

    def test_a_transcript_that_is_not_a_regular_file_is_reported(self):
        targets = [self.tmp]
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.tmp / "fifo")
            targets.append(self.tmp / "fifo")
        if os.path.exists("/dev/zero"):
            targets.append(Path("/dev/zero"))
        for target in targets:
            code, output, stderr = run_hook(stop_event(target), timeout=20)
            self.assertIn("did not run", output.get("systemMessage", ""), target)

    def test_lines_that_are_not_records_are_skipped(self):
        for junk in ("[" * 100000, "\x00\xff garbage", '{"type":"assistant","message":{"content":[{"type":"tool_use",'):
            path = self.transcript(lines(*WRITE, *CHECK, junk, *DONE))
            self.assertEqual(self.outcome(stop_event(path)), "allow", junk[:20])
        self.assertEqual(self.outcome(stop_event(self.transcript(b"\x00\x01" * 100000))), "warn")

    def test_a_huge_command_counts_as_a_write_and_does_not_hang(self):
        huge = tool_use("toolu_h", "Bash", {"command": "echo '" + "a" * (2 << 20) + "'"})
        path = self.transcript(lines(*WRITE, *CHECK, huge, tool_result("toolu_h"), *DONE))
        start = time.monotonic()
        self.assertEqual(self.outcome(stop_event(path), timeout=20), "block")
        self.assertLess(time.monotonic() - start, 10)

    def test_qa_commands_that_can_never_match_are_reported(self):
        project = self.tmp / "project"
        (project / ".claude").mkdir(parents=True)
        path = self.transcript(lines(*WRITE, *DONE))
        for qa in (["npm run 'check"], [""], ["  "]):
            (project / ".claude" / "invigilator.json").write_text(json.dumps({"qa_commands": qa, "builder_agents": ["main"]}))
            code, output, stderr = run_hook(stop_event(path, cwd=str(project)), project=project)
            self.assertEqual(code, 0, stderr)
            self.assertNotIn("decision", output, qa)
            self.assertIn("did not run", output["systemMessage"], qa)
        (project / ".claude" / "invigilator.json").write_text(
            json.dumps({"qa_commands": ["", "npm run check"], "builder_agents": ["main"]}))
        output = run_hook(stop_event(path, cwd=str(project)), project=project)[1]
        self.assertEqual(output["decision"], "block")
        self.assertIn("is left out", output["systemMessage"])

    def test_a_qa_result_written_after_the_stop_starts_still_counts(self):
        path = self.transcript(lines(*WRITE, CHECK[0]))

        def finish():
            time.sleep(0.3)
            with open(path, "a", encoding="utf-8") as transcript:
                transcript.write(lines(CHECK[1], *DONE))

        writer = threading.Thread(target=finish)
        writer.start()
        try:
            self.assertEqual(self.outcome(stop_event(path)), "allow")
        finally:
            writer.join()

    def test_a_qa_result_cut_off_mid_line_blocks(self):
        path = self.transcript(lines(*WRITE, CHECK[0]) + json.dumps(CHECK[1])[:40])
        output = run_hook(stop_event(path))[1]
        self.assertIn("result was not recorded", output["reason"])

    def test_a_sparse_file_is_read_from_its_end(self):
        path = self.tmp / "sparse.jsonl"
        with open(path, "wb") as transcript:
            transcript.truncate(256 << 20)
            transcript.seek(0, 2)
            transcript.write(b"\n" + lines(*CHECK, *WRITE, *DONE).encode())
        start = time.monotonic()
        self.assertEqual(self.outcome(stop_event(path), timeout=30), "block")
        self.assertLess(time.monotonic() - start, 15)

    def test_a_stop_hook_loop_gets_the_same_answer_each_time(self):
        # Claude Code ends the turn itself after 8 blocks in a row (CLAUDE_CODE_STOP_HOOK_BLOCK_CAP).
        # QA that did not run after the last write, or ran hidden, or has no result, blocks every
        # stop, with the same data folder each time.
        piped = [tool_use("toolu_p", "Bash", {"command": "npm run check | tail -5"}), tool_result("toolu_p")]
        unrecorded = [tool_use("toolu_u", "Bash", {"command": "npm run check"}),
                      tool_result("toolu_u", "Command running in background with ID: b1.")]
        for name, records in (("ran before", CHECK + WRITE), ("never ran", WRITE), ("piped", WRITE + piped),
                              ("unrecorded", WRITE + unrecorded)):
            path = self.transcript(lines(*records, *DONE), f"{name}.jsonl")
            data = self.tmp / f"data-{name}"
            answers = [self.outcome(stop_event(path, stop_hook_active=True), data=data) for _ in range(3)]
            self.assertEqual(answers, ["block"] * 3, name)


def edit(call_id, path, old="Helo", new="Hello"):
    return [tool_use(call_id, "Edit", {"replace_all": False, "file_path": str(path),
                                       "old_string": old, "new_string": new}),
            tool_result(call_id, "The file has been updated successfully.")]


def qa_run(call_id, passed, command="python3 qa.py"):
    return [tool_use(call_id, "Bash", {"command": command}),
            tool_result(call_id, "qa: PASS, all checks passed" if passed else
                        "Exit code 1\nqa: FAIL release-notes check", error=not passed)]


class BlockOnceTest(TempDir):
    """QA that ran on its own after the last write and failed, or that passed only after the
    builder changed a file the QA command runs while QA was failing, blocks the first stop. A
    later stop with no new write goes through, with a message to the user."""

    QA = ["python3 qa.py"]

    def setUp(self):
        super().setUp()
        self.project, self.data = self.tmp / "project", self.tmp / "data"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".claude" / "invigilator.json").write_text(
            json.dumps({"builder_agents": ["main", "*builder*"], "qa_commands": self.QA}))
        self.greet, self.qa = self.project / "greet.py", self.project / "qa.py"
        self.greet.write_text("")
        self.qa.write_text("")
        self.records = []

    def add(self, *records):
        """Add records to the transcript, and give its path."""
        self.records.extend(records)
        return self.transcript(lines(*self.records, *DONE))

    def stop(self, path, data=None, session="abc123", subagent=False, env=None):
        if subagent:
            payload = json.dumps({"session_id": session, "transcript_path": str(self.tmp / "main.jsonl"),
                                  "agent_transcript_path": str(path), "agent_id": "a1",
                                  "agent_type": "page-builder", "cwd": str(self.project),
                                  "hook_event_name": "SubagentStop", "stop_hook_active": False})
        else:
            payload = stop_event(path, cwd=str(self.project), session_id=session)
        code, output, stderr = run_hook(payload, project=self.project,
                                        data=self.data if data is None else data, env=env)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(code, 0, stderr)
        return output

    def test_a_visible_failure_blocks_once_then_lets_the_agent_stop(self):
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False))
        first = self.stop(path)
        self.assertEqual(first["decision"], "block")
        self.assertIn("QA (`python3 qa.py`) failed after your last edit. Fix the cause and run it again.",
                      first["reason"])
        self.assertIn("don't edit the QA command, the files it runs (qa.py) or .claude/invigilator.json",
                      first["reason"])
        self.assertNotIn("systemMessage", first)
        told = ("invigilator: QA (`python3 qa.py`) failed after the main session's last edit, and the "
                "main session stopped without passing it.")
        self.assertEqual(self.stop(path), {"systemMessage": told})
        self.assertEqual(self.stop(path, session="another")["decision"], "block", "each session has its own")
        # Running QA again without a write is the same state, whatever it gives.
        path = self.add(*qa_run("toolu_q2", False), *qa_run("toolu_q3", False, "python3 qa.py 2>&1 | tail -3"))
        self.assertEqual(self.stop(path), {"systemMessage": told})
        # A new write starts a new state: QA has to run after it, and a failure blocks once again.
        path = self.add(*edit("toolu_e2", self.greet, "Hello", "Hello!"))
        self.assertIn("ran before the last file write", self.stop(path)["reason"])
        self.assertIn("ran before the last file write", self.stop(path)["reason"])
        path = self.add(*qa_run("toolu_q4", False))
        self.assertEqual(self.stop(path)["decision"], "block")
        self.assertEqual(self.stop(path), {"systemMessage": told})
        path = self.add(*qa_run("toolu_q5", True))
        self.assertEqual(self.stop(path), {}, "a pass after the last write lets it stop quietly")

    def test_a_change_to_the_check_after_a_failure_does_not_count(self):
        for how, change in (
                ("Edit", edit("toolu_c", self.qa, "failures.append(", "pass  # ")),
                ("Write", [tool_use("toolu_c", "Write", {"file_path": str(self.qa), "content": "pass\n"}),
                           tool_result("toolu_c")]),
                ("sed -i", [tool_use("toolu_c", "Bash", {"command": "sed -i 's/failures.append/pass #/' qa.py"}),
                            tool_result("toolu_c")]),
                ("inline Python", [tool_use("toolu_c", "Bash", {
                    "command": "python3 - <<'EOF'\nopen('qa.py', 'w').write('pass')\nEOF"}), tool_result("toolu_c")])):
            data = self.tmp / f"data-{how}"
            records = edit("toolu_e1", self.greet) + qa_run("toolu_q1", False) + change + qa_run("toolu_q2", True)
            path = self.transcript(lines(*records, *DONE), f"{how}.jsonl")
            first = self.stop(path, data=data)
            self.assertEqual(first.get("decision"), "block", how)
            self.assertIn("you changed qa.py, which the QA command runs, after QA failed, and a pass that "
                          "comes from changing the check doesn't count. Undo that change, or stop and tell "
                          "the user what you changed and why.", first["reason"])
            told = ("invigilator: the main session changed qa.py, which QA (`python3 qa.py`) runs, after QA "
                    "failed, and then stopped. The QA pass after that change doesn't count.")
            # A QA re-run after the block is the same state, so the next stop goes through and tells the user.
            path = self.transcript(lines(*records, *qa_run("toolu_q3", True), *DONE), f"{how}.jsonl")
            self.assertEqual(self.stop(path, data=data), {"systemMessage": told}, how)
            self.assertEqual(self.stop(path, data=data), {}, f"{how}: the user was told, so that state is over")
        # Undoing the change is a new write, so the stop after it blocks once more.
        path = self.transcript(lines(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False),
                                     *edit("toolu_c", self.qa, "failures.append(", "pass  # "),
                                     *qa_run("toolu_q2", True), *edit("toolu_u", self.qa, "pass  # ", "failures.append("),
                                     *qa_run("toolu_q3", False), *DONE), "undone.jsonl")
        self.assertIn("failed after your last edit", self.stop(path, data=self.tmp / "data-Edit")["reason"])

    def test_once_the_user_is_told_about_a_change_to_the_check_a_pass_counts_again(self):
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False),
                        *edit("toolu_c1", self.qa, "failures.append(", "pass  # "), *qa_run("toolu_q2", True))
        self.assertIn("you changed qa.py", self.stop(path)["reason"])
        self.assertIn("the main session changed qa.py", self.stop(path)["systemMessage"])
        # That state is over: a later write and a pass let the agent stop with nothing to say.
        path = self.add(*edit("toolu_e2", self.greet, "Hello", "Hello!"), *qa_run("toolu_q3", True))
        self.assertEqual(self.stop(path), {})
        # A later failure starts fresh, under the rule for a failure.
        path = self.add(*edit("toolu_e3", self.greet, "Hello!", "Hello"), *qa_run("toolu_q4", False))
        self.assertIn("QA (`python3 qa.py`) failed after your last edit", self.stop(path)["reason"])
        self.assertIn("failed after the main session's last edit", self.stop(path)["systemMessage"])
        # And a change to the check after that failure is a new change to report.
        path = self.add(*edit("toolu_c2", self.qa, "x = 1", "x = 2"), *qa_run("toolu_q5", True))
        self.assertIn("you changed qa.py", self.stop(path)["reason"])
        self.assertIn("the main session changed qa.py", self.stop(path)["systemMessage"])
        self.assertEqual(self.stop(path), {})

    def test_a_change_to_the_check_before_any_failure_is_normal_work(self):
        change = edit("toolu_c", self.qa, "x = 1", "x = 2")
        for name, records in (
                ("before any QA run", change + qa_run("toolu_q1", True)),
                ("after a pass", edit("toolu_e1", self.greet) + qa_run("toolu_q1", True) + change
                 + qa_run("toolu_q2", True)),
                ("after a pass ended a failure", edit("toolu_e1", self.greet) + qa_run("toolu_q1", False)
                 + edit("toolu_e2", self.greet, "Hello", "Hello!") + qa_run("toolu_q2", True) + change
                 + qa_run("toolu_q3", True)),
                ("a hidden failure", edit("toolu_e1", self.greet) + qa_run("toolu_q1", False, "python3 qa.py | tail")
                 + change + qa_run("toolu_q2", True)),
                ("fixing the cause", edit("toolu_e1", self.greet) + qa_run("toolu_q1", False)
                 + edit("toolu_e2", self.greet, "Hello", "Hello!") + qa_run("toolu_q2", True))):
            path = self.transcript(lines(*records, *DONE), f"{name}.jsonl")
            self.assertEqual(self.stop(path, data=self.tmp / f"data-{name}"), {}, name)

    def test_the_files_a_qa_command_runs(self):
        (self.project / "scripts").mkdir()
        (self.project / "scripts" / "check.sh").write_text("")
        (self.tmp / "outside.py").write_text("")
        commands = ["python3 qa.py", "bash scripts/check.sh", "npm run check", "make check",
                    "python3 ../outside.py", "python3 missing.py", "pytest scripts", "python3 ./qa.py"]
        self.assertEqual(hooks.check_files(commands, str(self.project)),
                         {"qa.py": str(self.qa), "scripts/check.sh": str(self.project / "scripts" / "check.sh")})
        self.assertEqual(hooks.check_files(["npm run check", "make check"], str(self.project)), {})

    def test_a_marker_that_cannot_be_written_blocks_as_before(self):
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False))
        not_a_folder = self.tmp / "not-a-folder"
        not_a_folder.write_text("")
        for _ in range(2):
            output = self.stop(path, data=not_a_folder)
            self.assertEqual(output["decision"], "block")
            self.assertIn("could not record this block", output["systemMessage"])
            self.assertIn("so it will block the next stop too", output["systemMessage"])

    def test_without_a_plugin_data_folder_the_marker_goes_in_the_temp_folder(self):
        temp = self.tmp / "temp"
        temp.mkdir()
        env = {"TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)}
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False))
        self.assertEqual(self.stop(path, data="", env=env)["decision"], "block")
        self.assertEqual(len(list((temp / "invigilator-qa-blocked").iterdir())), 1)
        self.assertNotIn("decision", self.stop(path, data="", env=env))

    def test_old_markers_of_its_own_are_deleted(self):
        folder = self.data / "qa-blocked"
        folder.mkdir(parents=True)
        old_marker, fresh_marker = folder / ("qa-stop-" + "a" * 32), folder / ("qa-stop-" + "b" * 32)
        unrelated, lookalike = folder / "notes.txt", folder / ("qa-stop-" + "c" * 31)
        month_ago = time.time() - 31 * 86400
        for file in (old_marker, fresh_marker, unrelated, lookalike):
            file.write_text("")
            if file is not fresh_marker:
                os.utime(file, (month_ago, month_ago))
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False))
        self.assertEqual(self.stop(path)["decision"], "block")
        left = sorted(p.name for p in folder.iterdir())
        self.assertNotIn(old_marker.name, left, "an old marker of its own is deleted")
        self.assertIn(fresh_marker.name, left, "a fresh marker stays")
        self.assertIn(unrelated.name, left, "a file that is not a marker stays, however old")
        self.assertIn(lookalike.name, left, "so does one that only starts like a marker")
        self.assertEqual(len([n for n in left if hooks.MARKER_NAME.fullmatch(n)]), 2, "fresh marker and the new one")
        self.assertNotIn("decision", self.stop(path), "the new marker still works")
        # A best effort: a folder that can't be listed, or a marker that can't be deleted, raises nothing.
        hooks._prune(self.tmp / "no such folder")
        os.utime(fresh_marker, (month_ago, month_ago))
        with mock.patch.object(os, "unlink", side_effect=PermissionError("denied")):
            hooks._prune(folder)
        self.assertTrue(fresh_marker.exists())

    def test_subagent_stops_follow_the_same_rules(self):
        path = self.add(*edit("toolu_e1", self.greet), *qa_run("toolu_q1", False))
        self.assertIn("failed after your last edit", self.stop(path, subagent=True)["reason"])
        self.assertEqual(self.stop(path, subagent=True), {"systemMessage": (
            "invigilator: QA (`python3 qa.py`) failed after page-builder's last edit, and page-builder "
            "stopped without passing it.")})
        path = self.add(*edit("toolu_c", self.qa, "failures.append(", "pass  # "), *qa_run("toolu_q2", True))
        self.assertIn("you changed qa.py", self.stop(path, subagent=True)["reason"])
        self.assertIn("page-builder changed qa.py", self.stop(path, subagent=True)["systemMessage"])

    def test_reading_from_the_end_agrees_with_reading_from_the_start(self):
        scope = readonly.project_scope(str(self.project), str(self.project))
        checks = {"qa.py": readonly.files_scope([str(self.qa)], str(self.project))}
        change = edit("toolu_c", self.qa, "failures.append(", "pass  # ")
        failed, passed = qa_run("toolu_q1", False), qa_run("toolu_q2", True)
        cases = {
            "changed after a failure": edit("toolu_e1", self.greet) + failed + change + passed,
            "changed, then more work": edit("toolu_e1", self.greet) + failed + change + passed
            + edit("toolu_e2", self.greet, "a", "b") + qa_run("toolu_q3", True),
            "a copied pass": edit("toolu_e1", self.greet) + failed + change + passed + passed,
            "changed before": change + edit("toolu_e1", self.greet) + failed + edit("toolu_e2", self.greet)
            + passed,
            "failed": edit("toolu_e1", self.greet) + failed,
            "failed after a change": edit("toolu_e1", self.greet) + failed + change + qa_run("toolu_q3", False),
        }
        kinds = {}
        for name, records in cases.items():
            path = self.transcript(lines(*records, *DONE), f"{name}.jsonl")
            forward = hooks._verdict(runlog.read_transcript(path), self.QA, scope=scope, checks=checks)
            backward, _ = hooks.stop_verdict(path, self.QA, runlog.Deadline(30), scope, checks)
            # A pass read from the end does not look back for the last write, which only names a state to block.
            facts = [(v.accepted, v.reason, v.kind, None if v.accepted else v.write.id, v.check_file)
                     for v in (forward, backward)]
            self.assertEqual(facts[0], facts[1], name)
            kinds[name] = facts[1][2:4]
        self.assertEqual(kinds, {"changed after a failure": ("check-edited", "toolu_c"),
                                 "changed, then more work": ("check-edited", "toolu_e2"),
                                 "a copied pass": ("check-edited", "toolu_c"),
                                 "changed before": ("passed", None),
                                 "failed": ("failed", "toolu_e1"),
                                 "failed after a change": ("failed", "toolu_c")})
        # A change the user was told about ends that state on both readings, copied record or not.
        for name in ("changed after a failure", "changed, then more work", "a copied pass"):
            path = self.tmp / f"{name}.jsonl"
            told = lambda call: call.id == "toolu_c"  # noqa: E731
            forward = hooks._verdict(runlog.read_transcript(path), self.QA, scope=scope, checks=checks, reported=told)
            backward, _ = hooks.stop_verdict(path, self.QA, runlog.Deadline(30), scope, checks, told)
            self.assertEqual([(v.accepted, v.kind) for v in (forward, backward)], [(True, "passed")] * 2, name)

    def test_only_the_lines_that_decide_are_parsed(self):
        reads = [tool_use(f"toolu_r{n}", "Read", {"file_path": f"/p/src/{n}.js"}) for n in range(5000)]
        path = self.add(*reads, *edit("toolu_e1", self.greet), *qa_run("toolu_q1", False),
                        *edit("toolu_c", self.qa, "failures.append(", "pass  # "), *qa_run("toolu_q2", True))
        checks = {"qa.py": readonly.files_scope([str(self.qa)], str(self.project))}
        with mock.patch.object(runlog, "_parse", wraps=runlog._parse) as parse:
            verdict, _ = hooks.stop_verdict(path, self.QA, runlog.Deadline(30), None, checks)
        self.assertEqual(verdict.kind, "check-edited")
        self.assertLess(parse.call_count, 15)


if __name__ == "__main__":
    unittest.main()
