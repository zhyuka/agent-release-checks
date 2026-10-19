"""Read the run log of a real agent run, so the checks can run on it.

A run folder holds a run.json and, optionally, an artifacts/qa_report.json in
the format step_ran.py writes. run.json looks like this:

    {
      "agent": "page-builder",
      "role": "builder",
      "build": "build",
      "claims": ["QA passed"],
      "qa_commands": ["npm run check"],
      "tool_calls": [
        {"tool": "Write", "input": {"file_path": "build/index.html"}},
        {"tool": "Bash", "input": {"command": "npm run check"}, "ok": true}
      ],
      "build_sha256_before": "..."
    }

role is "builder" or "reviewer" and is the only required field. build is the
build folder, relative to the run folder, and defaults to "build". claims are
what the agent reported. qa_commands are the commands that count as the QA
step. tool_calls are the calls the agent made, in order, with Claude Code's
tool names and input fields; ok is false when the call failed and null when
its result is unknown. build_sha256_before is step_ran.build_sha256() of the
build before a reviewer ran.

read_transcript() turns a Claude Code session transcript into the same tool
calls, for the hooks in hooks/.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

RUN_LOG = "run.json"
ROLES = ("builder", "reviewer")
FIELDS = {"agent", "role", "build", "claims", "qa_commands", "tool_calls", "build_sha256_before"}


class RunLogError(ValueError):
    pass


@dataclass
class ToolCall:
    tool: str
    input: dict
    ok: Optional[bool] = True


@dataclass
class Run:
    folder: Path
    role: str
    build: Path
    agent: str = ""
    claims: list = field(default_factory=list)
    qa_commands: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    build_sha256_before: str = ""

    @property
    def artifacts(self):
        return self.folder / "artifacts"


def _strings(path, data, key):
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RunLogError(f"{path}: {key!r} must be a list of strings")
    return value


def _tool_call(path, number, call):
    if not isinstance(call, dict) or not isinstance(call.get("tool"), str):
        raise RunLogError(f"{path}: tool call {number} needs a \"tool\" name")
    tool_input, ok = call.get("input", {}), call.get("ok", True)
    if not isinstance(tool_input, dict):
        raise RunLogError(f"{path}: tool call {number}: \"input\" must be an object")
    if ok not in (True, False, None):
        raise RunLogError(f"{path}: tool call {number}: \"ok\" must be true, false or null")
    return ToolCall(call["tool"], tool_input, ok)


def load_run(folder):
    """The run in folder/run.json, or None when the folder has no run log."""
    folder = Path(folder)
    path = folder / RUN_LOG
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RunLogError(f"{path}: not valid JSON ({err})") from None
    if not isinstance(data, dict):
        raise RunLogError(f"{path}: must hold a JSON object")
    unknown = sorted(set(data) - FIELDS)
    if unknown:
        raise RunLogError(f"{path}: unknown field {unknown[0]!r}")
    if data.get("role") not in ROLES:
        raise RunLogError(f"{path}: \"role\" must be \"builder\" or \"reviewer\"")
    if not isinstance(data.get("build", "build"), str):
        raise RunLogError(f"{path}: \"build\" must be a folder path")
    build = folder / data.get("build", "build")
    if not build.is_dir():
        raise RunLogError(f"{path}: build folder {build} not found")
    calls = data.get("tool_calls", [])
    if not isinstance(calls, list):
        raise RunLogError(f"{path}: \"tool_calls\" must be a list")
    return Run(folder, data["role"], build, str(data.get("agent", "")),
               _strings(path, data, "claims"), _strings(path, data, "qa_commands"),
               [_tool_call(path, n, call) for n, call in enumerate(calls, 1)],
               str(data.get("build_sha256_before", "")))


def read_transcript(path):
    """Tool calls in a Claude Code transcript, in order, with ok taken from their results.

    The transcript is JSON Lines. Claude Code documents its path, not its
    format, so this reads the shape it writes today: assistant messages hold
    tool_use blocks, and user messages hold tool_result blocks that set
    is_error when the call failed. A call run in the background has an
    unknown result, and so does a call whose result is not in the file yet.
    Raises ValueError when the file holds no message in that shape, so a
    format change reads as "could not check", not as "no tool calls".
    """
    calls, by_id, seen_message = [], {}, False
    with open(Path(path).expanduser(), encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                seen_message = seen_message or _is_message(line)
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    call = ToolCall(str(block.get("name", "")), tool_input, None)
                    calls.append(call)
                    by_id[block.get("id")] = call
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    call = by_id[block["tool_use_id"]]
                    result = entry.get("toolUseResult")
                    background = call.input.get("run_in_background") or (
                        isinstance(result, dict) and result.get("backgroundTaskId"))
                    call.ok = None if background else not block.get("is_error", False)
    if not calls and not seen_message:
        raise ValueError(f"{path} holds no messages in the transcript format this reads")
    return calls


def _is_message(line):
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(entry, dict) and isinstance(entry.get("message"), dict)
