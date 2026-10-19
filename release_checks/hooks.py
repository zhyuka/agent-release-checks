"""Claude Code hooks that apply the release checks to real runs.

hooks/reviewer_readonly.py runs on PreToolUse. It denies any call that writes
files when the acting agent is a reviewer, and denies a reviewer spawning a
subagent that is not a reviewer. For every agent, it asks the user before a
call that writes the config file below, since changing it can turn the checks off.

hooks/qa_before_done.py runs on Stop and SubagentStop. It blocks a builder
from stopping when the transcript shows a file write after the last QA
command that passed. It reads the transcript from the end and stops once it
knows the answer, and lets the agent stop with a warning when it cannot read it.

Both read the project config at .claude/release-checks.json:

    {
      "reviewer_agents": ["*reviewer*"],
      "reviewer_shell": "block-writes",
      "builder_agents": ["*builder*"],
      "qa_commands": ["npm run check"]
    }

Agent patterns are shell-style globs, matched without case against the
agent_type field Claude Code puts in the hook input. "main" stands for the
main session when it runs without --agent. reviewer_shell is "block-writes"
to deny only shell commands that write files, or "block-all" to deny every
shell command. The QA hook does nothing until qa_commands names a command.

Plugin options are the values a user sets with /plugin configure. Claude
Code keeps them per user, not per project, and passes each one to hooks as
CLAUDE_PLUGIN_OPTION_<KEY>. They can set who counts as a reviewer or a
builder and the reviewer shell rule. A field in the project file overrides
the option, and qa_commands can only be set there, since a QA command
belongs to a project.
"""
import fnmatch
import json
import os
import shlex
import sys
import time
from pathlib import Path

from . import readonly, runlog, step_ran

CONFIG = Path(".claude") / "release-checks.json"
DEFAULTS = {"reviewer_agents": ["*reviewer*"], "reviewer_shell": "block-writes",
            "builder_agents": ["*builder*"], "qa_commands": []}
SHELL_MODES = ("block-writes", "block-all")
OPTION = "CLAUDE_PLUGIN_OPTION_"
LIST_OPTIONS = ("reviewer_agents", "builder_agents")


def plugin_options(environ):
    """(config fields the plugin options set, problem). A bad value is left out and named."""
    options = {}
    for key in LIST_OPTIONS:
        value = environ.get(OPTION + key.upper())
        if value is not None:
            options[key] = [part.strip() for part in value.split(",") if part.strip()]
    shell_mode = (environ.get(OPTION + "REVIEWER_SHELL") or "").strip()
    if shell_mode in SHELL_MODES:
        options["reviewer_shell"] = shell_mode
    elif shell_mode:
        return options, (f"the plugin option reviewer_shell is {shell_mode!r}, not \"block-writes\" "
                         "or \"block-all\", so it is ignored")
    return options, ""


def load_config(project_dir, environ=None):
    """(config, problem): the defaults, then the plugin options in environ (os.environ when
    it is None), then each field the project file sets. A missing file leaves the defaults
    and options. A broken file leaves them too, with a problem to show the user, since a
    typo should not pass quietly. A bad option value is left out; plugin_options() names it."""
    environ = os.environ if environ is None else environ
    base = {**DEFAULTS, **plugin_options(environ)[0]}
    path = Path(project_dir) / CONFIG
    if not path.is_file():
        return base, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        return base, f"{path} could not be read ({err})"
    if not isinstance(data, dict):
        return base, f"{path} must hold a JSON object"
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        return base, f"{path} has an unknown field {unknown[0]!r}"
    config = {**base, **data}
    for key in ("reviewer_agents", "builder_agents", "qa_commands"):
        if not isinstance(config[key], list) or not all(isinstance(v, str) for v in config[key]):
            return base, f"{path}: {key!r} must be a list of strings"
    if config["reviewer_shell"] not in SHELL_MODES:
        return base, f"{path}: \"reviewer_shell\" must be \"block-writes\" or \"block-all\""
    return config, ""


def matches(agent, patterns):
    return any(fnmatch.fnmatchcase(agent.lower(), p.lower()) for p in patterns)


def label(agent):
    return "the main session" if agent == "main" else agent


def reviewer_readonly(event, config):
    """PreToolUse output that denies a reviewer's write, or asks the user before
    any agent changes the release-checks config, or None to leave the call alone."""
    if event.get("hook_event_name") != "PreToolUse":
        return None
    return _deny_reviewer_write(event, config) or _ask_before_config_change(event)


def _ask_before_config_change(event):
    """Any agent that writes the project's config could turn the checks off, so the user decides."""
    tool, tool_input = event.get("tool_name", ""), event.get("tool_input") or {}
    project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    path = os.path.join(os.path.abspath(project), str(CONFIG))
    reason = readonly.touch_check(tool, tool_input, path, event.get("cwd") or project)
    if not reason:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": (
            f"release-checks: this {tool} call {reason}, which changes {CONFIG.as_posix()}. That "
            "file sets who counts as a reviewer or a builder and which QA commands must pass, so "
            "changing it can turn the release checks off or change them. It needs your OK.")}}


def _deny_reviewer_write(event, config):
    agent = event.get("agent_type") or "main"
    if not matches(agent, config["reviewer_agents"]):
        return None
    tool, tool_input = event.get("tool_name", ""), event.get("tool_input") or {}
    if tool == "Agent":
        spawned = tool_input.get("subagent_type") or "general-purpose"
        reason = "" if matches(spawned, config["reviewer_agents"]) else \
            f"spawns a {spawned} subagent, which is not a reviewer and could write for it"
    elif tool == "Bash" and config["reviewer_shell"] == "block-all":
        reason = "runs a shell command, and reviewer_shell is block-all"
    else:
        reason = readonly.writes(tool, tool_input)
    if not reason:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"release-checks blocked this {tool} call: it {reason}. The project config lists "
            f"{label(agent)} as a reviewer, and reviewers are read-only. Put the change you would "
            "make in your review instead.")}}


STOP_SECONDS = 15  # well inside the 60 second timeout hooks.json gives the Stop hooks
RESULT_WAIT = 1.0  # how long to wait for a QA result Claude Code has not written yet
# Claude Code sometimes copies earlier records back into the transcript, with the same ids.
# In real transcripts the copy sat at most 25 MB after the original. Looking back this far
# for an earlier copy of the deciding call, and for an earlier QA run, keeps a stop on a
# transcript of hundreds of megabytes fast.
LOOK_BACK = 64 << 20


def _verdict(calls, qa_commands, ran_before=False):
    """step_ran.qa_ran() for calls, or None when nothing in calls wrote files. ran_before
    says whether a QA command ran before the first of calls, or is None when that is not known."""
    written = step_ran.last_write(calls, qa_commands)
    if written < 0:
        return None
    return step_ran.qa_ran(calls, qa_commands, ran_before, written)


class _Replayed(Exception):
    """The deciding call is a copy of an earlier record, so the end-first reading is not safe."""


def _qa_needles(qa_commands):
    out = set()
    for qa in qa_commands:
        for ascii_only in (True, False):
            out.add(json.dumps(qa, ensure_ascii=ascii_only)[1:-1].encode("utf-8"))
    return tuple(out)


def _own_id(call):
    return b'"id":"' + call.id.encode() + b'"' if call.id else None


def _floor(call):
    return max(0, call.offset - LOOK_BACK)


def _replayed(transcript, call):
    own_id = _own_id(call)
    return bool(own_id) and transcript.appears_before(call.offset, own_id, _floor(call))


def _ran_before(transcript, call, qa_commands):
    """Whether a QA command ran before call: True, False, or None when the look-back did not
    reach the start of the file. Raises _Replayed if call is a copy of an earlier record."""
    own_id, floor = _own_id(call), _floor(call)
    needles = _qa_needles(qa_commands) + ((own_id,) if own_id else ())
    for offset, line in transcript.lines_with(needles, end=call.offset, floor=floor):
        if own_id and own_id in line:
            raise _Replayed()
        if any(step_ran.qa_status(earlier, qa_commands) for earlier in runlog.tool_uses(line)):
            if own_id and transcript.appears_before(offset, own_id, floor):
                raise _Replayed()
            return True
    return False if floor == 0 else None


def stop_verdict(path, qa_commands, deadline):
    """(verdict, waiting) for the transcript at path, read from the end.

    verdict is None when the agent wrote nothing. waiting is True when the newest QA run
    after the last write has no result in the file yet, so a second look may find it."""
    qa_status, wrote = step_ran.qa_status, step_ran.wrote
    with runlog.Transcript(path, deadline) as transcript:
        tail = []  # the calls after the one being looked at, newest first
        try:
            for call in transcript.calls():
                status = qa_status(call, qa_commands)
                if status:
                    transcript.resolve(call)
                    if status == "seen" and call.ok is True:
                        if _replayed(transcript, call):
                            raise _Replayed()
                        return step_ran.Verdict(True, "QA command passed after the last file write"), False
                elif call.tool in readonly.WRITE_TOOLS or (call.tool in step_ran.SHELLS and wrote(call, qa_commands)):
                    transcript.resolve(call)
                    if wrote(call, qa_commands):
                        calls = [call] + tail[::-1]
                        verdict = _verdict(calls, qa_commands, _ran_before(transcript, call, qa_commands))
                        newest = next((c for c in tail if qa_status(c, qa_commands)), None)
                        waiting = bool(newest and not newest.has_result and qa_status(newest, qa_commands) == "seen")
                        return verdict, waiting
                tail.append(call)
        except _Replayed:
            calls = runlog.read_transcript(path, deadline)
            return _verdict(calls, qa_commands), False
        if transcript.calls_seen == 0 and (transcript.has_other_tool_use() or not transcript.holds_messages()):
            calls = runlog.read_transcript(path, deadline)  # raises when nothing in it is a message
            return _verdict(calls, qa_commands), False
        return None, False


def _usable_qa_commands(qa_commands):
    """The QA commands that split into words, and a problem naming the first that does not."""
    usable, problem = [], ""
    for qa in qa_commands:
        try:
            words, why = shlex.split(qa), "is empty"
        except ValueError as err:
            words, why = [], f"cannot be split into words ({err})"
        if words:
            usable.append(qa)
        elif not problem:
            problem = f"the QA command {qa!r} in qa_commands {why}"
    return usable, problem


def qa_before_done(event, config):
    """Stop or SubagentStop output that keeps a builder working, or None to let it stop."""
    if not config["qa_commands"] or event.get("hook_event_name") not in ("Stop", "SubagentStop"):
        return None
    if event.get("hook_event_name") == "SubagentStop":
        agent, path = event.get("agent_type") or "", event.get("agent_transcript_path")
        if not agent:
            return None  # Claude Code's own helper agents report an empty agent_type
    else:
        agent, path = event.get("agent_type") or "main", event.get("transcript_path")
    agent = agent if isinstance(agent, str) else str(agent)
    if not matches(agent, config["builder_agents"]):
        return None
    qa_commands, problem = _usable_qa_commands(config["qa_commands"])
    note = {"systemMessage": f"release-checks: {problem}, so it is left out."} if problem else {}
    if not qa_commands:
        return {"systemMessage": f"release-checks: the QA check did not run for {label(agent)}, because {problem}."}
    deadline = runlog.Deadline(STOP_SECONDS)
    try:
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise TypeError("the hook input has no transcript path")
        path = os.path.expanduser(path)
        verdict, waiting = stop_verdict(path, qa_commands, deadline)
        waited = 0.0
        while waiting and waited < RESULT_WAIT:  # Claude Code writes the transcript asynchronously
            size = os.stat(path).st_size
            time.sleep(0.1)
            waited += 0.1
            if os.stat(path).st_size != size:
                verdict, waiting = stop_verdict(path, qa_commands, deadline)
    except (OSError, TypeError, ValueError, RecursionError, MemoryError) as err:
        return {"systemMessage": f"release-checks: the QA check did not run for {label(agent)}, "
                                 f"because its transcript could not be read ({err})."}
    if verdict is None or verdict.accepted:
        return note or None  # nothing was built in this run, or QA passed after the last write
    commands = " or ".join(f"`{qa}`" for qa in qa_commands)
    return {**note, "decision": "block",
            "reason": (f"release-checks: {verdict.reason}. The project config lists {label(agent)} "
                       f"as a builder, so run {commands} after your last edit and get it to pass "
                       "before you stop. Run it on its own, not through a pipe or with || true, so "
                       "its exit code counts.")}


def main(hook):
    """Read the hook input on stdin, print the hook output as JSON, exit 0."""
    try:
        event = json.load(sys.stdin)
    except ValueError as err:
        print(f"release-checks: the hook input is not JSON ({err})", file=sys.stderr)
        return 1
    if not isinstance(event, dict):
        print("release-checks: the hook input is not a JSON object", file=sys.stderr)
        return 1
    project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    config, problem = load_config(project)
    output = hook(event, config) or {}
    if problem:
        output["systemMessage"] = f"release-checks: {problem}. Using the default config."
    if output:
        print(json.dumps(output))
    return 0
