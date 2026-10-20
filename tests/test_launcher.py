"""Run the commands in hooks/hooks.json the way Claude Code runs them, from a plugin folder with spaces in its path.

Claude Code runs a shell-form hook command with sh -c on macOS and Linux and
with Git Bash on Windows, substituting the plugin folder for
${CLAUDE_PLUGIN_ROOT} (with forward slashes on Windows). On Windows without
Git Bash it runs the command in PowerShell and rewrites ${CLAUDE_PLUGIN_ROOT}
to ${env:CLAUDE_PLUGIN_ROOT}. These tests do the same with every shell they
find: sh, dash, bash, Git Bash, pwsh and Windows PowerShell. A shell that
isn't installed is left out, and the run prints which ones it used. CI sets
REQUIRE_SHELLS, such as "git-bash,pwsh,powershell", so a missing shell fails
there instead of passing untested.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from invigilator import hooks, plugin

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
COMMANDS = {event: groups[0]["hooks"][0]["command"] for event, groups in HOOKS.items()}
TOOLS = ("sh", "sed", "head", "dirname", "uname")  # what hooks/run.sh needs besides Python


def git_bash():
    candidates = [os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", ""), r"C:\Program Files\Git\bin\bash.exe"]
    found = shutil.which("bash")
    if found and "git" in found.lower():
        candidates.append(found)
    return next((c for c in candidates if c and Path(c).is_file()), None)


def find_shells():
    """{label: (kind, executable)} for each shell on this machine."""
    shells = {}
    if os.name == "nt":
        bash = git_bash()
        if bash:
            shells["git-bash"] = ("posix", bash)
    else:
        for name in ("sh", "dash", "bash"):
            if shutil.which(name):
                shells[name] = ("posix", shutil.which(name))
    for name in ("pwsh", "powershell"):
        if shutil.which(name):
            shells[name] = ("powershell", shutil.which(name))
    return shells


SHELLS = find_shells()
POSIX = {label: shell for label, shell in SHELLS.items() if shell[0] == "posix"}
WANTED = ("git-bash", "pwsh", "powershell") if os.name == "nt" else ("sh", "dash", "bash", "pwsh")
MISSING = [label for label in WANTED if label not in SHELLS]


def outcome(output):
    decision = output.get("hookSpecificOutput", {}).get("permissionDecision")
    if decision in ("deny", "ask"):
        return decision
    return "block" if output.get("decision") == "block" else "allow"


class PluginCopy:
    """A copy of the plugin in a folder whose path has spaces, and a way to run its hook commands."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "plugin root with spaces" / "invigilator plugin"
        for part in ("hooks", "invigilator", ".claude-plugin"):
            shutil.copytree(ROOT / part, self.root / part,
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "tests" / "fixtures", self.root / "tests" / "fixtures")
        (self.tmp / "temp").mkdir()

    def run(self, shell, event, payload, env=None, cwd=None, python=sys.executable):
        """Run the event's hook command in the shell, the way Claude Code would. (exit code, output, stderr)"""
        kind, executable = shell
        root = self.root.as_posix()
        if kind == "posix":
            argv = [executable, "-c", COMMANDS[event].replace("${CLAUDE_PLUGIN_ROOT}", root)]
        else:
            # -EncodedCommand carries the text exactly; how Claude Code quotes -Command is its own business.
            command = COMMANDS[event].replace("${CLAUDE_PLUGIN_ROOT}", "${env:CLAUDE_PLUGIN_ROOT}")
            encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
            argv = [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
        full_env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR", "INVIGILATOR_"))}
        full_env.update({"CLAUDE_PLUGIN_ROOT": root, "TMPDIR": str(self.tmp / "temp"),
                         "TEMP": str(self.tmp / "temp"), "TMP": str(self.tmp / "temp")})
        if python:
            full_env["INVIGILATOR_PYTHON"] = python
        # A fresh plugin data folder each time, so a stop the QA hook blocks once blocks in every shell.
        full_env["CLAUDE_PLUGIN_DATA"] = tempfile.mkdtemp(dir=self.tmp)
        full_env.update(env or {})
        done = subprocess.run(argv, input=payload.encode("utf-8"), capture_output=True,
                              cwd=cwd or self.root, env=full_env, timeout=120)
        stdout = done.stdout.decode("utf-8", "replace").strip()
        return done.returncode, json.loads(stdout) if stdout else {}, done.stderr.decode("utf-8", "replace")

    def bin_dir(self, name, fakes=None):
        """A folder that holds the tools run.sh needs and the given fake programs, and no Python."""
        folder = self.tmp / name
        folder.mkdir()
        for tool in TOOLS:
            real = shutil.which(tool)
            if real:
                (folder / tool).symlink_to(real)
        for program, body in (fakes or {}).items():
            path = folder / program
            path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            path.chmod(0o755)
        return str(folder)


def fixture_events():
    for path in sorted(FIXTURES.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        yield path, json.loads(text)["hook_event_name"], text


class EveryShellTest(unittest.TestCase):
    """Each fixture through each shell, the way hooks.json runs it."""

    @classmethod
    def setUpClass(cls):
        print(f"\n  hook commands run in: {', '.join(SHELLS) or 'no shell'}; "
              f"not found here: {', '.join(MISSING) or 'none'}", file=sys.stderr)

    def setUp(self):
        self.plugin = PluginCopy(self)

    def test_the_shells_ci_requires_are_here(self):
        required = [s for s in os.environ.get("REQUIRE_SHELLS", "").split(",") if s]
        self.assertEqual([s for s in required if s not in SHELLS], [])

    def test_every_fixture_gets_the_outcome_in_its_name_in_every_shell(self):
        self.assertTrue(SHELLS, "no shell found to run the hook commands in")
        for label, shell in SHELLS.items():
            for path, event, payload in fixture_events():
                with self.subTest(shell=label, fixture=path.name):
                    self.assertIn(event, COMMANDS, "hooks.json has no command for this fixture's event")
                    code, output, stderr = self.plugin.run(shell, event, payload)
                    self.assertEqual((code, outcome(output)), (0, path.stem.split("-")[0]),
                                     f"{output} {stderr}")
                    self.assertNotIn("systemMessage", output.get("hookSpecificOutput", {}))

    def test_the_search_finds_a_python_on_the_path(self):
        payload = (FIXTURES / "deny-reviewer-write.json").read_text(encoding="utf-8")
        for label, shell in SHELLS.items():
            with self.subTest(shell=label):
                code, output, stderr = self.plugin.run(shell, "PreToolUse", payload, python=None)
                self.assertEqual((code, outcome(output)), (0, "deny"), stderr)

    def test_session_status_says_what_the_hooks_block(self):
        payload = json.dumps({"session_id": "s1", "hook_event_name": "SessionStart",
                              "source": "startup", "cwd": "tests/fixtures/project"})
        for label, shell in SHELLS.items():
            with self.subTest(shell=label):
                code, output, stderr = self.plugin.run(
                    shell, "SessionStart", payload, env={"CLAUDE_PLUGIN_OPTION_SESSION_STATUS": "always"})
                self.assertEqual(code, 0, stderr)
                self.assertIn('must pass "npm run check"', output["systemMessage"])

    def test_without_python_the_call_goes_through_with_one_warning_per_session(self):
        payload = json.loads((FIXTURES / "deny-reviewer-write.json").read_text(encoding="utf-8"))
        for label, shell in SHELLS.items():
            with self.subTest(shell=label):
                if os.name != "nt":
                    # Not pwsh's own folder: that is /usr/bin on Ubuntu, which holds a python3.
                    path = self.plugin.bin_dir(f"bin-{label}")
                elif shell[0] == "posix":
                    path = str(Path(shell[1]).parent.parent / "usr" / "bin")
                else:
                    # The second line runs powershell from Windows' default PATH entry for it.
                    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
                    path = os.pathsep.join([str(Path(shell[1]).parent), system,
                                            os.path.join(system, "WindowsPowerShell", "v1.0")])
                answers = []
                for session in (f"{label}-a", f"{label}-a", f"{label}-b"):
                    payload["session_id"] = session
                    answers.append(self.plugin.run(shell, "PreToolUse", json.dumps(payload),
                                                   env={"PATH": path}, python=None))
                self.assertEqual([code for code, _, _ in answers], [0, 0, 0], answers)
                self.assertIn("needs Python 3.9 or later", answers[0][1]["systemMessage"])
                self.assertEqual(answers[1][1], {}, "the second call in a session warns again")
                self.assertIn("needs Python 3.9 or later", answers[2][1]["systemMessage"])


@unittest.skipUnless(POSIX and os.name != "nt", "needs sh, dash or bash on macOS or Linux")
class InterpreterSearchTest(unittest.TestCase):
    """run.sh moves past a Python that can't run the hook, and the next one still gets the input."""

    def test_a_store_alias_and_an_old_python_are_skipped(self):
        plugin = PluginCopy(self)
        payload = (FIXTURES / "deny-reviewer-write.json").read_text(encoding="utf-8")
        real = f'exec "{sys.executable}" "$@"'
        cases = {
            "store-alias": {"python3": "echo 'Python was not found' >&2; exit 9009", "python": real},
            "too-old": {"python3": "exit 3", "python": real},
            "reads-nothing-then-fails": {"python3": "exit 1", "python": real},
        }
        for label, shell in POSIX.items():
            for case, fakes in cases.items():
                with self.subTest(shell=label, case=case):
                    path = plugin.bin_dir(f"bin-{label}-{case}", fakes)
                    code, output, stderr = plugin.run(shell, "PreToolUse", payload,
                                                      env={"PATH": path}, python=None)
                    self.assertEqual((code, outcome(output)), (0, "deny"), stderr)


class RunPyTest(unittest.TestCase):
    """hooks/run.py always exits 0 with an answer Claude Code can read, except to say its Python is too old."""

    def run_py(self, *args, payload="", env=None, script=ROOT / "hooks" / "run.py", pre=""):
        code = f"import runpy, sys; {pre}sys.argv = [{str(script)!r}, *{list(args)!r}]; " \
               f"runpy.run_path({str(script)!r}, run_name='__main__')"
        full_env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR"))}
        full_env.update(env or {})
        # Bytes, as Claude Code sends them: text=True would encode in the locale's code page.
        done = subprocess.run([sys.executable, "-c", code], input=payload.encode("utf-8"), capture_output=True,
                              cwd=ROOT, env=full_env, timeout=60)
        stdout, stderr = done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
        return done.returncode, json.loads(stdout) if stdout.strip() else {}, stderr

    def test_a_python_older_than_3_9_exits_3_before_reading_the_input(self):
        code, output, _ = self.run_py("reviewer_readonly", pre="sys.version_info = (3, 8, 0); ",
                                      payload=(FIXTURES / "deny-reviewer-write.json").read_text())
        self.assertEqual((code, output), (3, {}))

    def test_input_that_is_not_json_is_reported_and_lets_the_call_through(self):
        code, output, stderr = self.run_py("reviewer_readonly", payload="not json")
        self.assertEqual(code, 0)
        self.assertIn("exited with code 1, so it let this call through", output["systemMessage"])
        self.assertIn("not JSON", stderr)

    def test_an_unknown_hook_name_is_reported(self):
        code, output, _ = self.run_py("no_such_hook", payload="{}")
        self.assertEqual(code, 0)
        self.assertIn("there is no hook named 'no_such_hook'", output["systemMessage"])

    def test_a_broken_plugin_copy_is_reported(self):
        plugin_copy = PluginCopy(self)
        (plugin_copy.root / "invigilator" / "plugin.py").write_text("this is not python\n")
        code, output, _ = self.run_py("reviewer_readonly", payload="{}",
                                      script=plugin_copy.root / "hooks" / "run.py")
        self.assertEqual(code, 0)
        self.assertIn("invigilator is off: it could not load", output["systemMessage"])

    def planted_hook(self, body):
        plugin_copy = PluginCopy(self)
        (plugin_copy.root / "hooks" / "reviewer_readonly.py").write_text(body, encoding="utf-8")
        return plugin_copy.root / "hooks" / "run.py"

    def test_a_crash_inside_a_hook_is_reported_and_its_partial_answer_dropped(self):
        script = self.planted_hook('print(\'{"decision": "block"}\')\nraise RuntimeError("planted")\n')
        code, output, stderr = self.run_py("reviewer_readonly", payload="{}", script=script)
        self.assertEqual(code, 0)
        self.assertEqual(list(output), ["systemMessage"])
        self.assertIn("the reviewer_readonly hook failed (RuntimeError: planted)", output["systemMessage"])
        self.assertIn("Traceback", stderr)

    def test_the_input_is_read_as_utf_8(self):
        script = self.planted_hook("import json, sys\n"
                                   "print(json.dumps({'systemMessage': json.load(sys.stdin)['text']}))\n")
        text = "caf\u00e9 \u2014 \u65e5\u672c"
        code, output, _ = self.run_py("reviewer_readonly", script=script,
                                      payload=json.dumps({"text": text}, ensure_ascii=False),
                                      env={"PYTHONIOENCODING": "cp1252:replace"})
        self.assertEqual((code, output), (0, {"systemMessage": text}))

    def test_a_byte_order_mark_before_the_input_is_skipped(self):
        # Windows PowerShell 5.1 adds one to what it pipes to Python on GitHub's Windows runners.
        payload = b"\xef\xbb\xbf" + (FIXTURES / "deny-reviewer-write.json").read_bytes()
        with tempfile.TemporaryDirectory() as project:
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR"))}
            env["CLAUDE_PROJECT_DIR"] = project
            done = subprocess.run([sys.executable, str(ROOT / "hooks" / "run.py"), "reviewer_readonly"],
                                  input=payload, capture_output=True, cwd=ROOT, env=env, timeout=60)
        self.assertEqual(outcome(json.loads(done.stdout)), "deny", done.stderr)

    def test_plugin_options_reach_the_hooks(self):
        with tempfile.TemporaryDirectory() as project:
            event = json.loads((FIXTURES / "deny-reviewer-write.json").read_text())
            env = {"CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_OPTION_REVIEWER_AGENTS": "auditor"}
            self.assertEqual(self.run_py("reviewer_readonly", payload=json.dumps(event), env=env)[1], {})
            event["agent_type"] = "auditor"
            output = self.run_py("reviewer_readonly", payload=json.dumps(event), env=env)[1]
            self.assertEqual(outcome(output), "deny")


class PluginOptionsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)

    def configure(self, **fields):
        (self.project / ".claude").mkdir(exist_ok=True)
        (self.project / ".claude" / "invigilator.json").write_text(json.dumps(fields))

    def test_without_options_or_a_file_the_defaults_apply(self):
        self.assertEqual(hooks.load_config(self.project, {}), (hooks.DEFAULTS, ""))

    def test_options_set_who_is_a_reviewer_and_a_builder(self):
        env = {"CLAUDE_PLUGIN_OPTION_REVIEWER_AGENTS": " auditor, *-review ,",
               "CLAUDE_PLUGIN_OPTION_BUILDER_AGENTS": "main",
               "CLAUDE_PLUGIN_OPTION_REVIEWER_SHELL": "block-all"}
        config, problem = hooks.load_config(self.project, env)
        self.assertEqual((config["reviewer_agents"], config["builder_agents"], config["reviewer_shell"],
                          problem), (["auditor", "*-review"], ["main"], "block-all", ""))

    def test_a_field_in_the_project_file_wins_over_the_option(self):
        self.configure(reviewer_agents=["code-reviewer"], qa_commands=["make check"])
        config, _ = hooks.load_config(self.project, {"CLAUDE_PLUGIN_OPTION_REVIEWER_AGENTS": "auditor",
                                                      "CLAUDE_PLUGIN_OPTION_BUILDER_AGENTS": "main"})
        self.assertEqual((config["reviewer_agents"], config["builder_agents"], config["qa_commands"]),
                         (["code-reviewer"], ["main"], ["make check"]))

    def test_a_broken_file_still_reports_its_problem_and_the_options_apply(self):
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "invigilator.json").write_text("{")
        config, problem = hooks.load_config(self.project, {"CLAUDE_PLUGIN_OPTION_BUILDER_AGENTS": "main"})
        self.assertIn("could not be read", problem)
        self.assertEqual(config["builder_agents"], ["main"])

    def test_a_bad_shell_option_is_ignored_and_named(self):
        options, problem = hooks.plugin_options({"CLAUDE_PLUGIN_OPTION_REVIEWER_SHELL": "off"})
        self.assertEqual(options, {})
        self.assertIn("reviewer_shell is 'off'", problem)

    def test_the_hook_scripts_read_the_options_themselves(self):
        # hooks.load_config() layers the options, so a hook script run on its own sees them too.
        event = json.loads((FIXTURES / "deny-reviewer-write.json").read_text())
        event["agent_type"] = "auditor"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR"))}
        for options, expected in (({}, {}), ({"CLAUDE_PLUGIN_OPTION_REVIEWER_AGENTS": "auditor"}, "deny")):
            done = subprocess.run([sys.executable, str(ROOT / "hooks" / "reviewer_readonly.py")],
                                  input=json.dumps(event), capture_output=True, text=True, timeout=60,
                                  env={**env, "CLAUDE_PROJECT_DIR": str(self.project), **options})
            output = json.loads(done.stdout) if done.stdout.strip() else {}
            self.assertEqual(output if expected == {} else outcome(output), expected, done.stderr)

    def test_an_empty_list_option_means_no_agent(self):
        self.assertEqual(hooks.plugin_options({"CLAUDE_PLUGIN_OPTION_REVIEWER_AGENTS": ""})[0],
                         {"reviewer_agents": []})


class SessionStatusTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project, self.data = Path(tmp.name, "project"), Path(tmp.name, "data")
        self.project.mkdir()

    def status(self, mode=None, data=True, **env):
        env = {"CLAUDE_PROJECT_DIR": str(self.project), **env}
        if mode:
            env["CLAUDE_PLUGIN_OPTION_SESSION_STATUS"] = mode
        if data:
            env["CLAUDE_PLUGIN_DATA"] = str(self.data)
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
        done = subprocess.run([sys.executable, str(ROOT / "hooks" / "run.py"), "session_status"],
                              input=payload, capture_output=True, text=True, timeout=60,
                              env={**{k: v for k, v in os.environ.items()
                                      if not k.startswith(("CLAUDE_PLUGIN_", "CLAUDE_PROJECT_DIR"))}, **env})
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)["systemMessage"] if done.stdout.strip() else ""

    def configure(self, text):
        (self.project / ".claude").mkdir(exist_ok=True)
        (self.project / ".claude" / "invigilator.json").write_text(text)

    def test_once_shows_the_line_the_first_time_and_again_when_it_changes(self):
        first = self.status()
        self.assertEqual(first, "invigilator is on. Reviewers (*reviewer*) can't write files. The QA "
                                "check is off until you set a QA command: run /invigilator:setup.")
        self.assertEqual(self.status(), "")
        self.configure('{"builder_agents": ["main"], "qa_commands": ["npm test"]}')
        self.assertIn('Builders (the main session) must pass "npm test" after their last edit',
                      self.status())
        self.assertEqual(self.status(), "")

    def test_always_and_off(self):
        self.assertIn("invigilator is on", self.status("always"))
        self.assertIn("invigilator is on", self.status("always"))
        self.assertEqual(self.status("off"), "")

    def test_once_without_a_data_folder_shows_nothing(self):
        self.assertEqual(self.status(data=False), "")

    def test_problems_show_every_time_even_when_off(self):
        self.configure('{"reviewer_shell": "off"}')
        for _ in range(2):
            message = self.status("off")
            self.assertIn("reviewer_shell", message)
            self.assertIn("Using the default config.", message)
        self.assertIn("session_status is 'sometimes'", self.status("sometimes"))
        self.assertIn("reviewer_shell is 'none'",
                      self.status("off", CLAUDE_PLUGIN_OPTION_REVIEWER_SHELL="none"))

    def test_the_line_names_block_all_and_the_settings_file(self):
        self.configure('{"reviewer_shell": "block-all", "builder_agents": [], "qa_commands": ["make"]}')
        self.assertEqual(self.status("always"),
                         "invigilator is on. Reviewers (*reviewer*) can't write files or run shell "
                         "commands. The QA check is off because no agent counts as a builder. "
                         "Settings: .claude/invigilator.json.")


if __name__ == "__main__":
    unittest.main()
