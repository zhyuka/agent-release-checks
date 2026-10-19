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
calls, for the hooks in hooks/. Transcript reads it from the end instead, for
a hook that has to answer fast on a transcript of hundreds of megabytes.
"""
import json
import os
import re
import stat
import time
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
    """One tool call. ok is None when the result is unknown. started is False when
    Claude Code refused the call or could not start it, so it did nothing. cwd is
    the working folder the transcript records for the call, or "" when it has none."""
    tool: str
    input: dict
    ok: Optional[bool] = True
    started: bool = True
    id: str = ""
    offset: int = -1
    end: int = -1
    has_result: bool = False
    cwd: str = ""


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


# What Claude Code writes as the result of a shell call that it moved to the background,
# in the versions this was checked against (2.1.136 to 2.1.288). Subagent transcripts
# carry no toolUseResult, so this text is the only sign there.
BACKGROUND = re.compile(r"Command (running in background with ID:|was manually backgrounded by user|"
                        r"was moved to the background|did not complete within its \S+ timeout and was "
                        r"moved to the background)")
TOOL_USE = b'"type":"tool_use"'
CHUNK = 1 << 22
# A longer line is skipped. Tool calls are model output and stay far below this; the
# longest line of any kind in 5,300 real transcripts was 8.5 MB.
MAX_LINE = 16 << 20


class TranscriptError(ValueError):
    pass


def _open(path, buffering=0):
    """A binary file object for a regular file. Opening never waits on a FIFO or a device."""
    path = Path(path).expanduser()
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TranscriptError(f"{path} is not a regular file")
        return os.fdopen(fd, "rb", buffering=buffering), info.st_size
    except BaseException:
        os.close(fd)
        raise


def _parse(line):
    try:
        entry = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return entry if isinstance(entry, dict) else None


def _content(entry):
    message = entry.get("message") if entry else None
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _new_call(block, offset, entry):
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    call_id = block.get("id") if isinstance(block.get("id"), str) else ""
    cwd = entry.get("cwd") if isinstance(entry.get("cwd"), str) else ""
    return ToolCall(str(block.get("name", "")), tool_input, None, id=call_id, offset=offset, cwd=cwd)


def tool_uses(line):
    """The tool calls in one raw transcript line, in order. A line that is not a record gives none."""
    entry = _parse(line)
    return [_new_call(block, -1, entry) for block in _content(entry) if block.get("type") == "tool_use"]


def _text(content):
    if isinstance(content, str):
        return content
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            return part["text"]
    return ""


def _set_result(call, block, entry):
    """Take ok and started for call from its tool_result block."""
    text, error = _text(block.get("content")), block.get("is_error") is True
    result = entry.get("toolUseResult")
    background = bool(call.input.get("run_in_background")) or bool(
        isinstance(result, dict) and result.get("backgroundTaskId")) or bool(BACKGROUND.match(text))
    call.ok = None if background else not error
    # An error result that is not an exit code means Claude Code refused the call or could
    # not start it: a permission or hook denial, a rejected prompt, a tool_use_error.
    call.started = not error or text.startswith(("Exit code", "[Request interrupted"))
    call.has_result = True


class Deadline:
    def __init__(self, seconds):
        self.seconds, self.end = seconds, time.monotonic() + seconds

    def check(self):
        if time.monotonic() > self.end:
            raise TranscriptError(f"reading it took longer than {self.seconds:g} seconds")


class Transcript:
    """A Claude Code transcript read from the end, so the hook can stop once it knows the answer.

    Only lines that hold a tool_use block are parsed. A call's result is looked up
    when it is needed, by searching forward from the call for its id.
    """

    def __init__(self, path, deadline=None):
        self.path = path
        self.deadline = deadline or Deadline(3600)
        self.file, self.size = _open(path)
        self.calls_seen = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.file.close()

    def _read(self, start, length):
        self.deadline.check()
        self.file.seek(start)
        data = self.file.read(length)
        return data or b""

    def _regions(self, end, floor=0):
        """(offset, bytes) runs of whole lines between floor and end, the last run first.

        A line longer than MAX_LINE is dropped, and so is the line that floor cuts.
        Memory stays near CHUNK + MAX_LINE."""
        pos, tail, tail_len, dropping = end, [], 0, False
        while pos > floor:
            start = max(floor, pos - CHUNK)
            chunk = self._read(start, pos - start)
            cut = chunk.find(b"\n") + 1 if start > 0 else 0
            if start > 0 and cut == 0:
                if not dropping:
                    tail.insert(0, chunk)
                    tail_len += len(chunk)
                    if tail_len > MAX_LINE:
                        tail, tail_len, dropping = [], 0, True
                pos = start
                continue
            body = chunk[cut:]
            if dropping:
                body = body[:body.rfind(b"\n") + 1]
                dropping = False
            else:
                body += b"".join(tail)
            if body:
                yield start + cut, body
            tail, tail_len = [chunk[:cut]], cut
            pos = start

    def lines_with(self, needles, end=None, floor=0):
        """(offset, line) for each line between floor and end that holds one of needles, the last first."""
        for offset, region in self._regions(self.size if end is None else end, floor):
            hits = set()
            for needle in needles:
                i = region.rfind(needle)
                while i >= 0:
                    hits.add(region.rfind(b"\n", 0, i) + 1)
                    i = region.rfind(needle, 0, i)
            for start in sorted(hits, reverse=True):
                stop = region.find(b"\n", start)
                yield offset + start, region[start:stop if stop >= 0 else len(region)]

    def calls(self):
        """ToolCalls from the last to the first, with ok unknown until resolve() is called."""
        for offset, line in self.lines_with((TOOL_USE,)):
            entry = _parse(line)
            for block in reversed(_content(entry)):
                if block.get("type") == "tool_use":
                    self.calls_seen += 1
                    call = _new_call(block, offset, entry)
                    call.end = offset + len(line)
                    yield call

    def resolve(self, call):
        """Find call's tool_result after it and set ok and started. Leaves ok None if there is none."""
        if call.has_result or not call.id:
            return call
        needle = b'"tool_use_id":"' + call.id.encode() + b'"'
        pos, step = call.end, 1 << 16  # the result is nearly always within a few KB
        while pos < self.size:
            data = self._read(pos, min(step, self.size - pos))
            step = min(step * 4, CHUNK)
            i = data.find(needle)
            while i >= 0:
                entry = _parse(self._line_at(pos + i))
                for block in _content(entry):
                    if block.get("type") == "tool_result" and block.get("tool_use_id") == call.id:
                        _set_result(call, block, entry)
                        return call
                i = data.find(needle, i + 1)
            pos += max(1, len(data) - len(needle) + 1)
        return call

    def _line_at(self, offset):
        """The whole line that holds the byte at offset, or b"" if it is longer than MAX_LINE."""
        start, back = offset, b""
        while start > 0:
            piece = self._read(max(0, start - (1 << 16)), min(start, 1 << 16))
            nl = piece.rfind(b"\n")
            if nl >= 0:
                back = piece[nl + 1:] + back
                break
            back, start = piece + back, start - len(piece)
            if len(back) > MAX_LINE:
                return b""
        line, pos = back, offset
        while pos < self.size:
            piece = self._read(pos, min(1 << 16, self.size - pos))
            nl = piece.find(b"\n")
            if nl >= 0:
                return line + piece[:nl]
            line, pos = line + piece, pos + len(piece)
            if len(line) > MAX_LINE:
                return b""
        return line

    def appears_before(self, offset, needle, floor=0):
        """Whether needle occurs in the file between floor and offset."""
        pos = floor
        while pos < offset:
            data = self._read(pos, min(CHUNK + len(needle), offset - pos))
            if needle in data:
                return True
            pos += CHUNK
        return False

    def holds_messages(self):
        """Whether any line is a record with a message, the shape this reads."""
        for _, line in self.lines_with((b'"message":',)):
            if isinstance((_parse(line) or {}).get("message"), dict):
                return True
        return False

    def has_other_tool_use(self):
        """A tool_use spelled some other way than Claude Code writes it today, such as with spaces."""
        return any(True for _ in self.lines_with((b'"tool_use"',)))


def read_transcript(path, deadline=None):
    """Tool calls in a Claude Code transcript, in order, with ok taken from their results.

    The transcript is JSON Lines. Claude Code documents its path, not its
    format, so this reads the shape it writes today: assistant messages hold
    tool_use blocks, and user messages hold tool_result blocks that set
    is_error when the call failed. A call run in the background has an
    unknown result, and so does a call whose result is not in the file yet.
    A call that appears twice, as when Claude Code copies earlier records
    back into the file, counts where it first appeared.
    Raises ValueError when the file holds no message in that shape, so a
    format change reads as "could not check", not as "no tool calls".
    """
    deadline = deadline or Deadline(3600)
    calls, by_id, seen_message = [], {}, False
    file, _ = _open(path, buffering=1 << 20)
    with file:
        for number, line in enumerate(iter(lambda: file.readline(MAX_LINE), b"")):
            if number % 4096 == 0:
                deadline.check()
            if b'"tool_use"' not in line and b'"tool_result"' not in line:
                seen_message = seen_message or (b'"message"' in line and isinstance(
                    (_parse(line) or {}).get("message"), dict))
                continue
            entry = _parse(line)
            for block in _content(entry):
                if block.get("type") == "tool_use":
                    call = _new_call(block, -1, entry)
                    if call.id and call.id in by_id:
                        continue
                    calls.append(call)
                    if call.id:
                        by_id[call.id] = call
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    call = by_id[block["tool_use_id"]]
                    if not call.has_result:
                        _set_result(call, block, entry)
    if not calls and not seen_message:
        raise TranscriptError(f"{path} holds no messages in the transcript format this reads")
    return calls
