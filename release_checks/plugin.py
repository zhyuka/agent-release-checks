"""How the Claude Code plugin runs its hooks.

hooks/run.py calls run() with a hook's name. run() layers the plugin's
options into the config, runs the hook, and makes sure Claude Code always
gets an answer it can read. When something goes wrong, the call goes
through and the user sees one warning, so a broken plugin never blocks work.

Plugin options are the values a user sets with /plugin configure. Claude
Code keeps them per user, not per project, and passes each one to hooks as
CLAUDE_PLUGIN_OPTION_<KEY>. They say who counts as a reviewer or a builder.
A field in the project's .claude/release-checks.json overrides the option,
and qa_commands can only be set there, since a QA command belongs to a project.

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
OPTION = "CLAUDE_PLUGIN_OPTION_"
LIST_OPTIONS = ("reviewer_agents", "builder_agents")
STATUS_MODES = ("once", "always", "off")
SETUP_SKILL = "/agent-release-checks:setup"
_project_file_config = hooks.load_config


def plugin_options(environ):
    """(config fields the plugin options set, problem). A bad value is left out and named."""
    options = {}
    for key in LIST_OPTIONS:
        value = environ.get(OPTION + key.upper())
        if value is not None:
            options[key] = [part.strip() for part in value.split(",") if part.strip()]
    shell = (environ.get(OPTION + "REVIEWER_SHELL") or "").strip()
    if shell in hooks.SHELL_MODES:
        options["reviewer_shell"] = shell
    elif shell:
        return options, (f"the plugin option reviewer_shell is {shell!r}, not \"block-writes\" "
                         "or \"block-all\", so it is ignored")
    return options, ""


def load_config(project_dir, environ=None):
    """hooks.load_config with the plugin options layered in: the defaults, then
    the options, then each field the project file sets. Returns (config, problem)
    where problem is the project file's, as before."""
    environ = os.environ if environ is None else environ
    config, problem = _project_file_config(project_dir)
    options, _ = plugin_options(environ)
    in_file = set() if problem else _fields_in(Path(project_dir) / hooks.CONFIG)
    config.update({key: value for key, value in options.items() if key not in in_file})
    return config, problem


def _fields_in(path):
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


@contextlib.contextmanager
def plugin_config(environ=None):
    """While the block runs, the hooks read the layered config."""
    hooks.load_config = lambda project_dir: load_config(project_dir, environ)
    try:
        yield
    finally:
        hooks.load_config = _project_file_config


def run(argv):
    """Run the hook named in argv[0] on the input on stdin and print its answer. Always 0."""
    name = argv[0] if argv else ""
    reconfigure = getattr(sys.stdin, "reconfigure", None)
    if reconfigure:  # Claude Code sends UTF-8; Python on Windows would read the locale's code page
        reconfigure(encoding="utf-8", errors="replace")
    answer = io.StringIO()
    try:
        with plugin_config(), contextlib.redirect_stdout(answer):
            code = session_status() if name == "session_status" else _run_script(name)
    except Exception as err:  # a broken hook must not block the user's work
        traceback.print_exc()
        _say(f"release-checks: the {name or 'unnamed'} hook failed ({type(err).__name__}: {err}), "
             "so it let this call through")
        return 0
    if code:
        _say(f"release-checks: the {name} hook exited with code {code}, so it let this call "
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
    config, problem = load_config(project, environ)
    problems = [f"{problem}. Using the default config"] if problem else []
    option_problem = plugin_options(environ)[1]
    if option_problem:
        problems.append(option_problem)
    mode = (environ.get(OPTION + "SESSION_STATUS") or "once").strip().lower()
    if mode not in STATUS_MODES:
        problems.append(f"the plugin option session_status is {mode!r}, not \"once\", "
                        "\"always\" or \"off\", so it is ignored")
        mode = "once"
    line = status_line(config, from_file=not problem and (Path(project) / hooks.CONFIG).is_file())
    shown = [f"release-checks: {p}." for p in problems]
    if mode == "always" or (mode == "once" and _first_time(environ, project, line)):
        shown.append(line)
    if shown:
        print(json.dumps({"systemMessage": " ".join(shown)}))
    return 0


def status_line(config, from_file=False):
    """One line that says which agents the hooks hold to which rule."""
    parts = ["release-checks is on."]
    if config["reviewer_agents"]:
        rule = ("can't write files or run shell commands" if config["reviewer_shell"] == "block-all"
                else "can't write files")
        parts.append(f"Reviewers ({_agents(config['reviewer_agents'])}) {rule}.")
    else:
        parts.append("No agent counts as a reviewer.")
    if not config["qa_commands"]:
        parts.append(f"The QA check is off until you set a QA command: run {SETUP_SKILL}.")
    elif not config["builder_agents"]:
        parts.append("The QA check is off because no agent counts as a builder.")
    else:
        commands = " or ".join(f'"{qa}"' for qa in config["qa_commands"])
        parts.append(f"Builders ({_agents(config['builder_agents'])}) must pass {commands} "
                     "after their last edit before they stop.")
    if from_file:
        parts.append(f"Settings: {hooks.CONFIG.as_posix()}.")
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
