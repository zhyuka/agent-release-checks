"""How the Claude Code plugin runs its hooks.

hooks/run.py calls run() with a hook's name. run() runs the hook and makes
sure Claude Code always gets an answer it can read. When something goes
wrong, the call goes through and the user sees one warning, so a broken
plugin never blocks work. The hooks read the plugin options themselves, in
hooks.load_config().

session_status() is the SessionStart hook: one line that says what the hooks
will block, shown once per project unless the session_status option says
"always" or "off". A config problem is shown every time.
"""
import contextlib
import hashlib
import io
import json
import os
import runpy
import sys
import traceback
from pathlib import Path

from . import hooks

HOOK_DIR = Path(__file__).resolve().parent.parent / "hooks"
SCRIPTS = {"reviewer_readonly": HOOK_DIR / "reviewer_readonly.py",
           "qa_before_done": HOOK_DIR / "qa_before_done.py"}
STATUS_MODES = ("once", "always", "off")
SETUP_SKILL = "/invigilator:setup"


def run(argv):
    """Run the hook named in argv[0] on the input on stdin and print its answer. Always 0."""
    name = argv[0] if argv else ""
    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure:  # Claude Code sends UTF-8; Python on Windows would read the locale's code page.
        # utf-8-sig skips a leading byte order mark, which Windows PowerShell 5.1 can add
        # to what it pipes to Python. It does on GitHub's Windows runners.
        reconfigure(encoding="utf-8-sig", errors="replace")
    answer = io.StringIO()
    try:
        with contextlib.redirect_stdout(answer):
            code = session_status() if name == "session_status" else _run_script(name)
    except Exception as err:  # a broken hook must not block the user's work
        traceback.print_exc()
        _say(f"invigilator: the {name or 'unnamed'} hook failed ({type(err).__name__}: {err}), "
             "so it let this call through")
        return 0
    if code:
        _say(f"invigilator: the {name} hook exited with code {code}, so it let this call "
             "through. Run claude --debug to see why")
    else:
        sys.stdout.write(answer.getvalue())
    return 0


def _run_script(name):
    if name not in SCRIPTS:
        raise ValueError(f"there is no hook named {name!r}")
    try:
        runpy.run_path(str(SCRIPTS[name]), run_name="__main__")
    except SystemExit as done:
        if done.code is None or isinstance(done.code, int):
            return done.code or 0
        print(done.code, file=sys.stderr)
        return 1
    return 0


def _say(message):
    print(json.dumps({"systemMessage": message + "."}))


def session_status(environ=None):
    """SessionStart: print what the hooks will block, and any config problem."""
    environ = os.environ if environ is None else environ
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    event = event if isinstance(event, dict) else {}
    project = environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    config, problem = hooks.load_config(project, environ)
    problems = [problem] if problem else []
    option_problem = hooks.plugin_options(environ)[1]
    if option_problem:
        problems.append(option_problem)
    mode = (environ.get(hooks.OPTION + "SESSION_STATUS") or "once").strip().lower()
    if mode not in STATUS_MODES:
        problems.append(f"the plugin option session_status is {mode!r}, not \"once\", "
                        "\"always\" or \"off\", so it is ignored")
        mode = "once"
    accepted, user_file, _ = hooks.accepted_qa_commands(project, environ)
    from_file = (Path(project) / hooks.CONFIG).is_file() and not hooks.project_file(project)[1]
    line = status_line(config, from_file, proposed=hooks.proposed_qa_commands(project, config),
                       user_file=user_file if accepted is not None else None)
    shown = [f"invigilator: {p}." for p in problems]
    if mode == "always" or (mode == "once" and _first_time(environ, project, line)):
        shown.append(line)
    if shown:
        print(json.dumps({"systemMessage": " ".join(shown)}))
    return 0


def status_line(config, from_file=False, proposed=(), user_file=None):
    """One line that says which agents the hooks hold to which rule. proposed are QA commands
    the project file names that don't count yet; user_file is the user's own config when it
    set this project's QA commands."""
    parts = ["invigilator is on."]
    if config["reviewer_agents"]:
        rule = ("can't write files or run shell commands" if config["reviewer_shell"] == "block-all"
                else "can't write files")
        parts.append(f"Reviewers ({_agents(config['reviewer_agents'])}) {rule}.")
    else:
        parts.append("No agent counts as a reviewer.")
    offer = " or ".join(f'"{qa}"' for qa in proposed)
    if not config["qa_commands"] and proposed:
        parts.append(f"The QA check is off: this project's {hooks.CONFIG.as_posix()} proposes {offer}, "
                     f"which doesn't count until you accept it with {SETUP_SKILL}.")
    elif not config["qa_commands"]:
        parts.append(f"The QA check is off until you set a QA command: run {SETUP_SKILL}.")
    elif not config["builder_agents"]:
        parts.append("The QA check is off because no agent counts as a builder.")
    else:
        commands = " or ".join(f'"{qa}"' for qa in config["qa_commands"])
        parts.append(f"Builders ({_agents(config['builder_agents'])}) must pass {commands} "
                     "after their last edit before they stop.")
        if proposed:
            parts.append(f"The project's file also proposes {offer}, which doesn't count until you "
                         f"accept it with {SETUP_SKILL}.")
    files = [hooks.CONFIG.as_posix()] if from_file else []
    if user_file:
        home = Path(os.path.expanduser("~"))
        files.append(f"~/{Path(user_file).relative_to(home).as_posix()}" if home in Path(user_file).parents
                     else str(user_file))
    if files:
        parts.append(f"Settings: {' and '.join(files)}.")
    return " ".join(parts)


def _agents(patterns):
    return ", ".join("the main session" if p.lower() == "main" else p for p in patterns)


def _first_time(environ, project, line):
    """True the first time this line shows for this project. Needs the plugin's data folder."""
    data = environ.get("CLAUDE_PLUGIN_DATA")
    if not data:
        return False
    key = hashlib.sha256(f"{Path(project).resolve()}\n{line}".encode("utf-8")).hexdigest()[:16]
    marker = Path(data) / "status-shown" / key
    if marker.exists():
        return False
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    return True
