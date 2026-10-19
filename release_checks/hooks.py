"""Claude Code hooks that apply the release checks to real runs.

hooks/reviewer_readonly.py runs on PreToolUse. It denies any call that writes
files when the acting agent is a reviewer, and denies a reviewer spawning a
subagent that is not a reviewer.

hooks/qa_before_done.py runs on Stop and SubagentStop. It blocks a builder
from stopping when the transcript shows a file write after the last QA
command that passed.

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
import sys
from pathlib import Path

from . import readonly, runlog, step_ran

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
    """PreToolUse output that denies a reviewer's write, or None to leave the call alone."""
    agent = event.get("agent_type") or "main"
    if event.get("hook_event_name") != "PreToolUse" or not matches(agent, config["reviewer_agents"]):
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
    if not matches(agent, config["builder_agents"]):
        return None
    try:
        calls = runlog.read_transcript(path)
    except (OSError, TypeError, ValueError) as err:
        return {"systemMessage": f"release-checks: the QA check did not run for {label(agent)}, "
                                 f"because its transcript could not be read ({err})."}
    if step_ran.last_write(calls, config["qa_commands"]) < 0:
        return None  # nothing was built in this run, so there is nothing to QA
    verdict = step_ran.qa_ran(calls, config["qa_commands"])
    if verdict.accepted:
        return None
    commands = " or ".join(f"`{qa}`" for qa in config["qa_commands"])
    return {"decision": "block",
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
