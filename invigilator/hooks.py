"""Claude Code hooks that apply the release checks to real runs.

hooks/reviewer_readonly.py runs on PreToolUse. It denies any call that writes
files when the acting agent is a reviewer, and denies a reviewer spawning a
subagent that is not a reviewer. For every agent, it asks the user before a
call that writes the config file below, since changing it can turn the checks off.

hooks/qa_before_done.py runs on Stop and SubagentStop. It blocks a builder
from stopping when the transcript shows a file write after the last QA
command that passed. When QA ran on its own after the last write and failed,
or passed only after the builder changed a file the QA command runs while QA
was failing, it blocks the first stop and lets a later one through with a
message to the user, until the next write. It reads the transcript from the
end and stops once it knows the answer, and lets the agent stop with a
warning when it cannot read it.

Both read the project config at .claude/invigilator.json:

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
shell command. The QA hook does nothing until a QA command counts.

Plugin options are the values a user sets with /plugin configure. Claude
Code keeps them per user, not per project, and passes each one to hooks as
CLAUDE_PLUGIN_OPTION_<KEY>. They set who counts as a reviewer or a builder
and the reviewer shell rule. The project file can add reviewers and builders
and make the shell rule block-all, but it can't take any of them away, since a
repository someone clones must not be able to turn a check off.

For the same reason the project file's qa_commands only propose what counts
as QA. The commands that count are the ones the user accepted for the project
in their own config, invigilator.json in the Claude Code config folder
(CLAUDE_CONFIG_DIR, else ~/.claude):

    {"projects": {"~/code/shop": {"qa_commands": ["npm run check"]}}}

A key is a project folder and covers the folders under it. A * in a key
matches any characters, slashes included, so "~/code/shop*" also covers a
worktree such as ~/code/shop-fix. When several keys match, the longest wins.
A CLAUDE_CONFIG_DIR inside the project is ignored.
"""
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import time
from pathlib import Path

from . import readonly, runlog, step_ran

CONFIG = Path(".claude") / "invigilator.json"
PROOFS = Path(".claude") / "invigilator-proofs.json"  # project.py writes it; see there
DEFAULTS = {"reviewer_agents": ["*reviewer*"], "reviewer_shell": "block-writes",
            "builder_agents": ["*builder*"], "qa_commands": []}
# The project file can also list the project's checks, which project.py reads. The hooks
# only make sure the field is a list.
PROJECT_FIELDS = (*DEFAULTS, "checks")
USER_FIELDS = ("qa_commands", "checks")
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
    it is None), then what the project file adds, and the QA commands the user accepted for
    the project. A missing file leaves the defaults and options. A broken file leaves them
    too, with a problem to show the user, since a typo should not pass quietly. The problem
    is one or more sentences without the last full stop. A bad option value is left out;
    plugin_options() names it."""
    environ = os.environ if environ is None else environ
    base = {**DEFAULTS, **plugin_options(environ)[0]}
    accepted, _, user_problem = accepted_qa_commands(project_dir, environ)
    base["qa_commands"] = accepted or []
    data, problem = project_file(project_dir)
    if problem or not data:
        problem = problem and f"{problem}. Using the default config"
        return base, ". ".join(p for p in (problem, user_problem) if p)
    config = {key: base[key] + [p for p in data.get(key, []) if p not in base[key]] for key in LIST_OPTIONS}
    config["reviewer_shell"] = "block-all" if "block-all" in (base["reviewer_shell"], data.get("reviewer_shell")) \
        else base["reviewer_shell"]
    config["qa_commands"] = base["qa_commands"]
    return config, user_problem


def project_file(project_dir):
    """(the fields in the project's .claude/invigilator.json, problem). {} when there is none."""
    path = Path(project_dir) / CONFIG
    if not path.is_file():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        return {}, f"{path} could not be read ({err})"
    if not isinstance(data, dict):
        return {}, f"{path} must hold a JSON object"
    unknown = sorted(set(data) - set(PROJECT_FIELDS))
    if unknown:
        return {}, f"{path} has an unknown field {unknown[0]!r}"
    for key in ("reviewer_agents", "builder_agents", "qa_commands"):
        if not _strings(data.get(key, [])):
            return {}, f"{path}: {key!r} must be a list of strings"
    if not isinstance(data.get("checks", []), list):
        return {}, f"{path}: \"checks\" must be a list"
    if data.get("reviewer_shell", SHELL_MODES[0]) not in SHELL_MODES:
        return {}, f"{path}: \"reviewer_shell\" must be \"block-writes\" or \"block-all\""
    return data, ""


def _strings(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def proposed_qa_commands(project_dir, config):
    """The QA commands the project file names that don't count, because the user hasn't accepted them."""
    data, problem = project_file(project_dir)
    return [] if problem else [qa for qa in data.get("qa_commands", []) if qa not in config["qa_commands"]]


def user_config_path(project_dir, environ):
    """(the user's own config file, problem): invigilator.json in CLAUDE_CONFIG_DIR, else in
    ~/.claude. None when CLAUDE_CONFIG_DIR is inside the project, since a repository must not
    choose the file that decides what counts as QA."""
    folder = environ.get("CLAUDE_CONFIG_DIR")
    if not folder:
        return Path(os.path.expanduser("~")) / ".claude" / "invigilator.json", ""
    folder = os.path.abspath(os.path.expanduser(folder))
    if _inside(folder, project_dir):
        return None, (f"CLAUDE_CONFIG_DIR ({folder}) is inside the project, so the QA commands "
                      "you accepted there don't count")
    return Path(folder) / "invigilator.json", ""


def _inside(path, folder):
    """True when path is folder or under it, after following links."""
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    try:
        return os.path.normcase(os.path.commonpath([path, folder])) == os.path.normcase(folder)
    except ValueError:  # on different Windows drives
        return False


def accepted_qa_commands(project_dir, environ):
    """(the QA commands the user accepted for this project, or None when no entry covers it
    or the entry lists none, the user's config file, problem)."""
    entry, path, problem, broken = user_entry(project_dir, environ)
    if broken:
        return None, path, f"{problem}, so no QA command counts"
    return (entry or {}).get("qa_commands"), path, problem


def user_entry(project_dir, environ):
    """(the user's entry for this project, or None when no entry covers it, the user's config
    file, problem, broken). An entry holds qa_commands, checks or both. broken is True when
    the file is there but can't be used, so nothing in it counts."""
    path, problem = user_config_path(project_dir, environ)
    if path is None or not path.is_file():
        return None, path, problem, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        return None, path, f"{path} could not be read ({err})", True
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict) or set(data) != {"projects"}:
        return None, path, f'{path} must hold {{"projects": {{...}}}} and nothing else', True
    names = {_comparable(os.path.abspath(project_dir)), _comparable(os.path.realpath(project_dir))}
    best = None
    for key, entry in projects.items():
        if not isinstance(entry, dict) or not entry or set(entry) - set(USER_FIELDS) \
                or not _strings(entry.get("qa_commands", [])) or not isinstance(entry.get("checks", []), list):
            return None, path, (f'{path}: the entry for {key!r} must be {{"qa_commands": [...]}}, '
                                'and can also hold "checks": [...]'), True
        pattern = _comparable(os.path.expanduser(key)).rstrip("/") or "/"
        if any(fnmatch.fnmatchcase(n, pattern) or fnmatch.fnmatchcase(n, pattern + "/*") for n in names) \
                and (best is None or len(key) > len(best[0])):
            best = (key, entry)
    return (best[1] if best else None), path, "", False


def _comparable(path):
    """A path with forward slashes, without case on Windows and macOS, whose disks usually ignore it."""
    path = path.replace("\\", "/")
    return path.lower() if sys.platform in ("win32", "darwin") else path


def matches(agent, patterns):
    return any(fnmatch.fnmatchcase(agent.lower(), p.lower()) for p in patterns)


def _where(event):
    """(project root, working folder) for a hook input: the project is CLAUDE_PROJECT_DIR when
    it is set, else the input's cwd."""
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    return os.environ.get("CLAUDE_PROJECT_DIR") or cwd or ".", cwd


def label(agent):
    return "the main session" if agent == "main" else agent


def reviewer_readonly(event, config):
    """PreToolUse output that denies a reviewer's write, or asks the user before
    any agent changes the invigilator config, or None to leave the call alone."""
    if event.get("hook_event_name") != "PreToolUse":
        return None
    return _deny_reviewer_write(event, config) or _ask_before_config_change(event)


def _ask_before_config_change(event):
    """Any agent that writes the project's config, its proof records or the user's own config
    could change the checks, so the user decides."""
    tool, tool_input = event.get("tool_name", ""), event.get("tool_input") or {}
    project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    cwd = event.get("cwd") or project
    path = os.path.join(os.path.abspath(project), str(CONFIG))
    reason = readonly.touch_check(tool, tool_input, path, cwd)
    proofs = "" if reason else readonly.touch_check(
        tool, tool_input, os.path.join(os.path.abspath(project), str(PROOFS)), cwd)
    if reason:
        what = (f"{CONFIG.as_posix()}. That file sets who counts as a reviewer or a builder, "
                "proposes the project's QA commands and lists its checks, so changing it changes "
                "the release checks")
    elif proofs:
        reason = proofs
        what = (f"{PROOFS.as_posix()}. That file records that each check failed its planted bad "
                "cases and passed its good ones. invigilator prove writes it, and a change by hand "
                "could make an unproven check count")
    else:
        user_file = user_config_path(project, os.environ)[0]
        reason = user_file and readonly.touch_check(tool, tool_input, str(user_file), cwd)
        if not reason:
            return None
        what = (f"your own invigilator config, {user_file}. That file decides which QA commands "
                "count for your projects, so changing it can turn the QA check off or weaken it")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"invigilator: this {tool} call {reason}, which changes {what}. "
                                    "It needs your OK."}}


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
        # A write that lands only in a temp folder outside the project, such as notes in /tmp, is allowed.
        project, cwd = _where(event)
        reason = readonly.writes(tool, tool_input, readonly.reviewer_scope(project, cwd))
    if not reason:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"invigilator blocked this {tool} call: it {reason}. The project config lists "
            f"{label(agent)} as a reviewer, and reviewers are read-only. Put the change you would "
            "make in your review instead.")}}


STOP_SECONDS = 15  # well inside the 60 second timeout hooks.json gives the Stop hooks
RESULT_WAIT = 1.0  # how long to wait for a QA result Claude Code has not written yet
# Claude Code sometimes copies earlier records back into the transcript, with the same ids.
# In real transcripts the copy sat at most 25 MB after the original. Looking back this far
# for an earlier copy of the deciding call, and for an earlier QA run, keeps a stop on a
# transcript of hundreds of megabytes fast.
LOOK_BACK = 64 << 20


def _verdict(calls, qa_commands, ran_before=False, scope=None, checks=None, reported=None):
    """step_ran.qa_ran() for calls, or None when nothing in calls wrote files. ran_before
    says whether a QA command ran before the first of calls, or is None when that is not known.
    With checks and reported, as for step_ran.check_edit(), a pass that came after a change to
    a file the QA command runs, made while QA was failing, does not count."""
    written = step_ran.last_write(calls, qa_commands, scope)
    if written < 0:
        return None
    verdict = step_ran.qa_ran(calls, qa_commands, ran_before, written)
    if verdict.accepted and checks:
        edit, name = step_ran.check_edit(calls, qa_commands, checks, reported)
        if edit is not None:
            return _check_edited(verdict.write, name, edit)
    return verdict


def _check_edited(write, name, edit):
    return step_ran.Verdict(False, f"{name}, which the QA command runs, changed after QA failed",
                            "check-edited", write, name, edit)


def check_files(qa_commands, project):
    """{name: path} for the files the QA commands run: each word of a QA command that names an
    existing file inside the project, resolved against the project root. name is the path from
    the root, with forward slashes. `npm run check` or `make check` names none."""
    root = os.path.abspath(project)
    found = {}
    for qa in qa_commands:
        for word in shlex.split(qa):
            path = os.path.normpath(os.path.join(root, word))
            try:
                name = os.path.relpath(path, root)
            except ValueError:  # another drive on Windows
                continue
            if name != os.curdir and name.split(os.sep)[0] != os.pardir and os.path.isfile(path):
                found.setdefault(Path(name).as_posix(), path)
    return found


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


def _check_edit_before(transcript, call, qa_commands, checks, reported=None):
    """step_ran.check_edit() on the calls before call that run QA or name a file in checks.
    Only the lines that hold a QA command or such a file's name are parsed, and only within
    LOOK_BACK of call, so on a longer transcript a failure before that is not seen."""
    needles = set(_qa_needles(qa_commands))
    for name in checks:
        for ascii_only in (True, False):
            needles.add(json.dumps(name.rsplit("/", 1)[-1], ensure_ascii=ascii_only)[1:-1].encode("utf-8"))
    found = {}  # id (or a stand-in) -> (offset, position in the line, call), the earliest copy
    for offset, line in transcript.lines_with(tuple(needles), end=call.offset, floor=_floor(call)):
        if runlog.TOOL_USE not in line:
            continue
        for n, earlier in enumerate(runlog.tool_uses(line)):
            earlier.offset, earlier.end = offset, offset + len(line)
            found[earlier.id or (offset, n)] = (offset, n, earlier)
    calls = [c for _, _, c in sorted(found.values(), key=lambda item: item[:2])]
    for earlier in calls:
        if step_ran.qa_status(earlier, qa_commands) == "seen" or any(
                step_ran.wrote(earlier, qa_commands, scope) for scope in checks.values()):
            transcript.resolve(earlier)
    return step_ran.check_edit(calls, qa_commands, checks, reported)


def stop_verdict(path, qa_commands, deadline, scope=None, checks=None, reported=None):
    """(verdict, waiting) for the transcript at path, read from the end.

    verdict is None when the agent wrote nothing. Its write is the last file write, except
    on a pass, where the reading stops without looking for it. waiting is True when the
    newest QA run after the last write has no result in the file yet, so a second look may
    find it. With a scope, only the writes that land where it counts are writes. With checks
    and reported, as for step_ran.check_edit(), a pass that came after a change to a file the
    QA command runs, made while QA was failing, is a "check-edited" verdict."""
    qa_status = step_ran.qa_status

    def wrote(call):
        return step_ran.wrote(call, qa_commands, scope)

    with runlog.Transcript(path, deadline) as transcript:
        tail = []  # the calls after the one being looked at, newest first
        edited = None  # (call, name) for a change to the check before the last pass
        try:
            for call in transcript.calls():
                status = qa_status(call, qa_commands)
                if status and edited is None:
                    transcript.resolve(call)
                    if status == "seen" and call.ok is True:
                        if _replayed(transcript, call):
                            raise _Replayed()
                        edit, name = _check_edit_before(transcript, call, qa_commands, checks, reported) \
                            if checks else (None, "")
                        if edit is None:
                            return step_ran.Verdict(True, "QA command passed after the last file write",
                                                    "passed"), False
                        edited = (edit, name)  # go on back to the last write, which names the state
                elif wrote(call):
                    transcript.resolve(call)
                    if wrote(call):
                        if edited:
                            return _check_edited(call, edited[1], edited[0]), False
                        calls = [call] + tail[::-1]
                        verdict = _verdict(calls, qa_commands, _ran_before(transcript, call, qa_commands), scope)
                        newest = next((c for c in tail if qa_status(c, qa_commands)), None)
                        waiting = bool(newest and not newest.has_result and qa_status(newest, qa_commands) == "seen")
                        return verdict, waiting
                tail.append(call)
        except _Replayed:
            calls = runlog.read_transcript(path, deadline)
            return _verdict(calls, qa_commands, scope=scope, checks=checks, reported=reported), False
        if edited:
            return _check_edited(edited[0], edited[1], edited[0]), False
        if transcript.calls_seen == 0 and (transcript.has_other_tool_use() or not transcript.holds_messages()):
            calls = runlog.read_transcript(path, deadline)  # raises when nothing in it is a message
            return _verdict(calls, qa_commands, scope=scope, checks=checks, reported=reported), False
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
    note = {"systemMessage": f"invigilator: {problem}, so it is left out."} if problem else {}
    if not qa_commands:
        return {"systemMessage": f"invigilator: the QA check did not run for {label(agent)}, because {problem}."}
    deadline = runlog.Deadline(STOP_SECONDS)
    # Only writes inside the project need QA: notes in /tmp or ~/.claude don't. A write the
    # command line does not place counts wherever it is.
    project, cwd = _where(event)
    scope = readonly.project_scope(project, cwd)
    files = check_files(qa_commands, project)
    checks = {name: readonly.files_scope([file], cwd) for name, file in files.items()}
    session = event.get("session_id")

    def reported(call):  # the user was told about this change to a file QA runs, which ended that state
        return os.path.lexists(_marker(session, "reported", call))

    try:
        if not isinstance(path, (str, os.PathLike)) or not str(path):
            raise TypeError("the hook input has no transcript path")
        path = os.path.expanduser(path)
        verdict, waiting = stop_verdict(path, qa_commands, deadline, scope, checks, reported)
        waited = 0.0
        while waiting and waited < RESULT_WAIT:  # Claude Code writes the transcript asynchronously
            size = os.stat(path).st_size
            time.sleep(0.1)
            waited += 0.1
            if os.stat(path).st_size != size:
                verdict, waiting = stop_verdict(path, qa_commands, deadline, scope, checks, reported)
    except (OSError, TypeError, ValueError, RecursionError, MemoryError) as err:
        return {"systemMessage": f"invigilator: the QA check did not run for {label(agent)}, "
                                 f"because its transcript could not be read ({err})."}
    if verdict is None or verdict.accepted:
        return note or None  # nothing was built in this run, or QA passed after the last write
    commands = " or ".join(f"`{qa}`" for qa in qa_commands)
    if verdict.kind in ("failed", "check-edited"):
        return _block_once(event, verdict, agent, commands, list(files), note)
    return {**note, "decision": "block",
            "reason": (f"invigilator: {verdict.reason}. The project config lists {label(agent)} "
                       f"as a builder, so run {commands} after your last edit and get it to pass "
                       "before you stop. Run it on its own, not through a pipe or with || true, so "
                       "its exit code counts.")}


def _block_once(event, verdict, agent, commands, files, note):
    """Block the first stop after QA failed, or after the check changed, and let a later stop
    with no new write through with a message to the user. A marker file says which states were
    blocked already. When it can't be written, every such stop is blocked, as before. Once the
    user has been told about a change to the check, a second marker ends that state, so a
    later pass counts again."""
    who = label(agent)
    if verdict.kind == "failed":
        runs = f" ({', '.join(files)})" if files else ""
        reason = (f"invigilator: QA ({commands}) failed after your last edit. Fix the cause and run it "
                  "again. If it can't pass here, for example because a check needs something this "
                  f"environment lacks, don't edit the QA command, the files it runs{runs} or "
                  f"{CONFIG.as_posix()} to get it to pass. Stop instead, and tell the user which "
                  "check fails and why.")
        told = f"invigilator: QA ({commands}) failed after {who}'s last edit, and {who} stopped without passing it."
    else:
        name = verdict.check_file
        reason = (f"invigilator: you changed {name}, which the QA command runs, after QA failed, and a "
                  "pass that comes from changing the check doesn't count. Undo that change, or stop "
                  "and tell the user what you changed and why.")
        told = (f"invigilator: {who} changed {name}, which QA ({commands}) runs, after QA failed, and "
                "then stopped. The QA pass after that change doesn't count.")
    messages = [note["systemMessage"]] if note else []
    session = event.get("session_id")
    try:
        if _blocked_before(_marker(session, verdict.kind, verdict.write)):
            if verdict.kind == "check-edited":
                try:
                    _blocked_before(_marker(session, "reported", verdict.edit))
                except OSError:
                    pass  # the next new state after a pass blocks once more, as before this marker
            return {"systemMessage": " ".join(messages + [told])}
    except OSError as err:
        messages.append(f"invigilator: it could not record this block ({err}), so it will block the next stop too.")
    return {"systemMessage": " ".join(messages), "decision": "block", "reason": reason} if messages else \
        {"decision": "block", "reason": reason}


MARKER_PREFIX = "qa-stop-"
MARKER_NAME = re.compile(re.escape(MARKER_PREFIX) + "[0-9a-f]{32}")
MARKER_DAYS = 30  # a marker older than this is deleted the next time the hook writes one


def _marker(session, kind, call):
    """The marker file for a session, a kind ("failed" or "check-edited" for a block, "reported"
    for a change to the check the user was told about) and the call that decided it: the last
    write, or the change. It is in the plugin's data folder, or in the temp folder without one."""
    call_id = getattr(call, "id", "") or json.dumps(
        [getattr(call, "tool", ""), getattr(call, "input", {})], sort_keys=True, default=str)
    key = f"{session}\n{kind}\n{call_id}"
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    folder = Path(data) / "qa-blocked" if data else Path(tempfile.gettempdir()) / "invigilator-qa-blocked"
    return folder / (MARKER_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32])


def _blocked_before(marker):
    """True when the marker exists. Otherwise it makes the marker and gives False. Raises
    OSError when the marker can't be made."""
    if os.path.lexists(marker):
        return True
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.close(os.open(str(marker), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return True
    _prune(marker.parent)
    return False


def _prune(folder):
    """Delete this hook's own markers in folder that are older than MARKER_DAYS, by their names.
    A best effort: a file that can't be read or deleted is left, and nothing here raises."""
    cutoff = time.time() - MARKER_DAYS * 86400
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not MARKER_NAME.fullmatch(entry.name):
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(entry.path)
                except OSError:
                    continue  # gone already, or not this user's to delete
    except OSError:
        pass  # best effort: the folder can't be listed


def main(hook):
    """Read the hook input on stdin, print the hook output as JSON, exit 0."""
    try:
        event = json.load(sys.stdin)
    except ValueError as err:
        print(f"invigilator: the hook input is not JSON ({err})", file=sys.stderr)
        return 1
    if not isinstance(event, dict):
        print("invigilator: the hook input is not a JSON object", file=sys.stderr)
        return 1
    project = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or "."
    config, problem = load_config(project)
    output = hook(event, config) or {}
    if problem:
        output["systemMessage"] = f"invigilator: {problem}."
    if output:
        print(json.dumps(output))
    return 0
