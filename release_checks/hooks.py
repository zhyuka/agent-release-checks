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
"""
import fnmatch
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from . import readonly, runlog, shell, step_ran

CONFIG = Path(".claude") / "release-checks.json"
DEFAULTS = {"reviewer_agents": ["*reviewer*"], "reviewer_shell": "block-writes",
            "builder_agents": ["*builder*"], "qa_commands": []}
SHELL_MODES = ("block-writes", "block-all")


def load_config(project_dir):
    """(config, problem). A missing file gives the defaults. A broken file gives
    the defaults and a problem to show the user, since a typo should not pass quietly."""
    path = Path(project_dir) / CONFIG
    if not path.is_file():
        return dict(DEFAULTS), ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        return dict(DEFAULTS), f"{path} could not be read ({err})"
    if not isinstance(data, dict):
        return dict(DEFAULTS), f"{path} must hold a JSON object"
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        return dict(DEFAULTS), f"{path} has an unknown field {unknown[0]!r}"
    config = {**DEFAULTS, **data}
    for key in ("reviewer_agents", "builder_agents", "qa_commands"):
        if not isinstance(config[key], list) or not all(isinstance(v, str) for v in config[key]):
            return dict(DEFAULTS), f"{path}: {key!r} must be a list of strings"
    if config["reviewer_shell"] not in SHELL_MODES:
        return dict(DEFAULTS), f"{path}: \"reviewer_shell\" must be \"block-writes\" or \"block-all\""
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


SHELLS = ("Bash", "PowerShell")
STOP_SECONDS = 15  # well inside the 60 second timeout hooks.json gives the Stop hooks
RESULT_WAIT = 1.0  # how long to wait for a QA result Claude Code has not written yet
# The shell parser takes about 0.1 s on 100 KB of quoted text, and grows with the square of
# it. The longest shell command in 120,000 real ones was 72 KB.
MAX_COMMAND = 128 << 10
# Claude Code sometimes copies earlier records back into the transcript, with the same ids.
# In real transcripts the copy sat at most 25 MB after the original. Looking back this far
# for an earlier copy of the deciding call, and for an earlier QA run, keeps a stop on a
# transcript of hundreds of megabytes fast.
LOOK_BACK = 64 << 20

# git commands that change only refs, the index, the object store, git config or a remote,
# and the ones that only read. None of them changes the files QA checks, so a commit, a
# fetch or a push after QA passed needs no second QA run. A pre-commit hook that rewrites
# files is the exception this cannot see.
GIT_REF_ONLY = {
    "add", "stage", "commit", "fetch", "push", "tag", "branch", "remote", "notes", "reflog", "gc",
    "prune", "repack", "pack-refs", "maintenance", "update-ref", "symbolic-ref", "update-index",
    "commit-graph", "multi-pack-index", "prune-packed", "config", "ls-remote", "status", "log",
    "diff", "show", "rev-parse", "rev-list", "ls-files", "ls-tree", "describe", "blame",
    "shortlog", "show-ref", "for-each-ref", "cat-file", "merge-base", "check-ignore", "grep",
    "whatchanged", "version", "help"}
GIT_DRY_RUN = {"pull", "clean", "rm", "mv", "merge"}  # ref-only too when run with --dry-run or -n
# Commands that change no files, allowed around ref-only git commands. Not sed, awk or tee.
SHELL_GLUE = {"cd", "pushd", "popd", "true", ":", "echo", "printf", "pwd", "sleep", "wait", "tail", "head",
              "grep", "egrep", "fgrep", "rg", "cat", "wc", "sort", "uniq", "cut", "tr", "nl", "column",
              "jq", "date", "ls"}
COMMIT_MESSAGE = re.compile(r"\$\(cat <<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*\n?\s*\)", re.DOTALL)
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", re.DOTALL)

# PowerShell commands that write files, by cmdlet or alias, when they start a command.
PS_WRITERS = {
    "set-content", "add-content", "out-file", "new-item", "remove-item", "move-item", "copy-item",
    "rename-item", "clear-content", "clear-item", "set-item", "set-itemproperty", "new-itemproperty",
    "remove-itemproperty", "rename-itemproperty", "expand-archive", "compress-archive", "export-csv",
    "export-clixml", "tee-object", "start-bitstransfer", "set-acl", "ni", "ri", "rm", "rmdir", "rd",
    "del", "erase", "mi", "mv", "move", "cpi", "cp", "copy", "rni", "ren", "md", "mkdir", "sc", "ac",
    "clc", "cli", "si", "sp", "tee"}
PS_STATEMENT = re.compile(r"(?:^|[;|\n{}()=]|&&|\|\|)\s*(?:&\s*|\.\s+)?([^\s;|&(){}='\"]+)((?:[^;|\n{}()&=]|&(?!&))*)")
PS_REDIRECT = re.compile(r"(?<![-=>])[1-6*]?>>?(?!&)\s*(?!\$null\b)[^\s;|&)>]", re.IGNORECASE)
PS_DOTNET = re.compile(r"\[(?:System\.)?IO\.(?:File|Directory)\]::(?:Write|Append|Create|Delete|Move|Copy|"
                       r"Replace)|-OutFile\b", re.IGNORECASE)


def _command(call):
    """The shell command line of call, or None when it is missing or too long to read in time."""
    command = call.input.get("command")
    if call.tool not in SHELLS or not isinstance(command, str):
        return None
    if len(command) > MAX_COMMAND and len(shell._drop_heredocs(command)) > MAX_COMMAND:
        return None
    return command


def _qa_status(call, qa_commands):
    command = _command(call)
    return step_ran.qa_in_command(command, qa_commands) if command is not None else ""


def _powershell_code(command):
    """command with each string literal turned into '' and comments dropped, so a > or a
    cmdlet name inside a string or a comment is not read as code."""
    out, i, n = [], 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "@" and command[i + 1:i + 2] in ("'", '"') and command[i + 2:i + 3] in ("\r", "\n"):
            end = command.find("\n" + command[i + 1] + "@", i + 2)
            out.append("''")
            i = n if end < 0 else end + 3
        elif ch in "'\"":
            j = i + 1
            while j < n and not (command[j] == ch and command[j + 1:j + 2] != ch):
                j += 2 if command[j] == ch or (ch == '"' and command[j] == "`") else 1
            out.append("''")
            i = j + 1
        elif command.startswith("<#", i):
            end = command.find("#>", i + 2)
            out.append(" ")
            i = n if end < 0 else end + 2
        elif ch == "#" and (i == 0 or command[i - 1] in " \t\r\n;{}()|"):
            end = command.find("\n", i)
            i = n if end < 0 else end
        else:
            out.append(command[i:i + 2] if ch == "`" else ch)
            i += 2 if ch == "`" else 1
    return "".join(out)


def _powershell_writes(command):
    """Why a PowerShell command writes files, or "". Like the Bash reading, it sees the
    command line, not what a script does."""
    command = _powershell_code(command)
    if PS_REDIRECT.search(command):
        return "redirects output to a file"
    if PS_DOTNET.search(command):
        return "writes through .NET or -OutFile"
    for name, rest in PS_STATEMENT.findall(command):
        program = re.sub(r"\.exe$", "", os.path.basename(name.replace("\\", "/")).lower())
        if program in PS_WRITERS:
            return f"runs {program}"
        if "-" in program or program.startswith(("$", "[", "@")):
            continue  # a cmdlet that does not write, a variable or an expression
        words = [program] + [w for w in rest.split() if not re.match(r"[1-6*]?>", w)]
        if program == "git" and _git_ref_only(words[1:]):
            continue
        # The PowerShell quoting is gone, so hand the shell reading plain words.
        reason = readonly.command_writes(" ".join(shlex.quote(w) for w in words))
        if reason and not reason.startswith(("redirects", "the command could not be parsed")):
            return reason
    return ""


def _git_ref_only(args):
    """Whether git with these arguments changes only refs, the index, objects or config."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-c", "--config-env", "--exec-path") or args[i].startswith(("-c", "--config-env=")):
            return False  # git -c can make git run another program
        i += 2 if args[i] in ("-C", "--git-dir", "--work-tree", "--namespace") else 1
    sub, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
    if any(a.startswith(("--output", "--output-directory")) or a == "-o" for a in rest):
        return False
    dry = any(a in ("--dry-run", "-n") for a in rest)
    return sub in GIT_REF_ONLY or (dry and sub in GIT_DRY_RUN)


def _ref_only(command):
    """Whether a shell command line runs only ref-only git commands and commands that do
    nothing to files, such as cd. Keyed on the command, so it holds whatever the shell
    reading in readonly.py says about git."""
    text = COMMIT_MESSAGE.sub("MESSAGE", command)
    if re.search(r"[`#]|\$\(|[<>]\(|<<", QUOTED.sub("''", text)):
        return False  # a substitution, a heredoc or a comment this check does not follow
    try:
        commands = shell.parse(text)
    except ValueError:
        return False
    seen_git = False
    for simple in commands:
        if any(shell.writes_file(op, target) for op, target in simple.redirects):
            return False
        words = shell.program(simple.words)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == "git":
            if not _git_ref_only(words[1:]):
                return False
            seen_git = True
        elif name not in SHELL_GLUE:
            return False
    return seen_git


def _writes(call, qa_commands):
    """Why a builder's tool call wrote files, or "". A QA command is never a write, and
    neither is a write tool that failed or a shell command Claude Code never started."""
    if call.tool in readonly.WRITE_TOOLS:
        return "" if call.ok is False else readonly.writes(call.tool, call.input)
    if call.tool not in SHELLS:
        return readonly.writes(call.tool, call.input)
    command = _command(call)
    if not call.started or (command is not None and _qa_status(call, qa_commands)):
        return ""
    if command is None:
        return "the command is missing or too long to read"
    if call.tool == "PowerShell":
        return _powershell_writes(command)
    reason = readonly.command_writes(command)
    return "" if reason and _ref_only(command) else reason


def _verdict(calls, qa_commands, ran_before=False):
    """Whether a QA command passed after the last write in calls, with the same reasons as
    step_ran.qa_ran(). None when nothing in calls wrote files. ran_before says whether a
    QA command ran before the first of calls, or is None when that is not known."""
    runs = [(i, status, call) for i, call in enumerate(calls)
            for status in [_qa_status(call, qa_commands)] if status]
    written = max((i for i, call in enumerate(calls) if _writes(call, qa_commands)), default=-1)
    if written < 0:
        return None
    after = [(status, call) for i, status, call in runs if i > written]
    names = " or ".join(repr(qa) for qa in qa_commands)
    if not runs and ran_before is False:
        return step_ran.Verdict(False, f"QA command {names} never ran")
    if not runs and ran_before is None:
        return step_ran.Verdict(False, f"QA command {names} did not run after the last file write")
    if not after:
        return step_ran.Verdict(False, f"QA command {names} ran before the last file write, not after it")
    if any(status == "seen" and call.ok is True for status, call in after):
        return step_ran.Verdict(True, "QA command passed after the last file write")
    if any(status == "seen" and call.ok is False for status, call in after):
        return step_ran.Verdict(False, "QA command failed after the last file write")
    if any(status == "masked" for status, _ in after):
        return step_ran.Verdict(False, "QA output went through a pipe or into another command, "
                                       "so its exit code was not seen")
    return step_ran.Verdict(False, "QA command ran but its result was not recorded")


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
        if any(_qa_status(earlier, qa_commands) for earlier in runlog.tool_uses(line)):
            if own_id and transcript.appears_before(offset, own_id, floor):
                raise _Replayed()
            return True
    return False if floor == 0 else None


def stop_verdict(path, qa_commands, deadline):
    """(verdict, waiting) for the transcript at path, read from the end.

    verdict is None when the agent wrote nothing. waiting is True when the newest QA run
    after the last write has no result in the file yet, so a second look may find it."""
    with runlog.Transcript(path, deadline) as transcript:
        tail = []  # the calls after the one being looked at, newest first
        try:
            for call in transcript.calls():
                status = _qa_status(call, qa_commands)
                if status:
                    transcript.resolve(call)
                    if status == "seen" and call.ok is True:
                        if _replayed(transcript, call):
                            raise _Replayed()
                        return step_ran.Verdict(True, "QA command passed after the last file write"), False
                elif call.tool in readonly.WRITE_TOOLS or (call.tool in SHELLS and _writes(call, qa_commands)):
                    transcript.resolve(call)
                    if _writes(call, qa_commands):
                        calls = [call] + tail[::-1]
                        verdict = _verdict(calls, qa_commands, _ran_before(transcript, call, qa_commands))
                        newest = next((c for c in tail if _qa_status(c, qa_commands)), None)
                        waiting = bool(newest and not newest.has_result and _qa_status(newest, qa_commands) == "seen")
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
