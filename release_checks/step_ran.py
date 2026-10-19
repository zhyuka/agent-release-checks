"""Step-ran verification: a "QA passed" claim needs evidence tied to the exact build.

The evidence is a QA report that names the SHA-256 of the build it checked
and is not older than that build.

For a real agent run, qa_ran() reads the tool calls instead: the QA command
has to succeed after the agent's last file write.
"""
import hashlib
import json
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import readonly, shell
from .vacuous import guarded_score

REPORT = "qa_report.json"
PASS_MARK = 0.75


def build_sha256(folder):
    folder, digest = Path(folder), hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(folder).as_posix().encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def qa_agent(build, artifacts):
    """Stub agent that runs QA (the guarded scorer) and writes the report."""
    score = guarded_score(build)
    passed = score.value is not None and score.value >= PASS_MARK
    report = {"build_sha256": build_sha256(build), "score": score.value, "passed": passed}
    (Path(artifacts) / REPORT).write_text(json.dumps(report), encoding="utf-8")
    return "QA passed" if passed else "QA failed"


def cheap_agent(build, artifacts):
    """Stub for a cheaper model with the same instructions. Skips QA, reports success."""
    return "QA passed"


@dataclass
class Verdict:
    accepted: bool
    reason: str


def verify(claim, build, artifacts):
    if claim != "QA passed":
        return Verdict(False, f"claim was {claim!r}")
    report_path = Path(artifacts) / REPORT
    if not report_path.is_file():
        return Verdict(False, "no QA report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("build_sha256") != build_sha256(build):
        return Verdict(False, "report names a different build")
    newest = max((p.stat().st_mtime_ns for p in Path(build).rglob("*") if p.is_file()), default=0)
    if report_path.stat().st_mtime_ns < newest:
        return Verdict(False, "report is older than the build")
    if report.get("passed") is not True:
        return Verdict(False, "report says QA did not pass")
    return Verdict(True, "report matches this build")


def qa_in_command(command, qa_commands):
    """"seen" when a QA command runs and its exit code is the command line's own,
    "masked" when a later pipe or command decides the exit code, "" when no QA runs.

    A QA command matches when a simple command starts with its words, so
    "npm run check -- --fast" matches "npm run check" and "echo npm run check" does not.
    """
    try:
        commands = shell.parse(command)
    except ValueError:
        return ""
    patterns = [shlex.split(qa) for qa in qa_commands]
    for i, simple in enumerate(commands):
        words = shell.program(simple.words)
        if any(p and words[:len(p)] == p for p in patterns):
            chained = all(c.then == "&&" for c in commands[i:-1])
            return "seen" if chained and commands[-1].then != "&" else "masked"
    return ""


def qa_ran(calls, qa_commands):
    """Accept a "QA passed" claim only if a QA command succeeded after the last file write.

    calls are tool calls in order, each with .tool, .input and .ok (True, False,
    or None when the result is unknown, such as a command run in the background).
    """
    last_write, runs = -1, []
    for i, call in enumerate(calls):
        status = qa_in_command(call.input.get("command", ""), qa_commands) if call.tool == "Bash" else ""
        if status:
            runs.append((i, status, call))
        elif readonly.writes(call.tool, call.input):
            last_write = i
    after = [(status, call) for i, status, call in runs if i > last_write]
    names = " or ".join(repr(qa) for qa in qa_commands)
    if not runs:
        return Verdict(False, f"QA command {names} never ran")
    if not after:
        return Verdict(False, f"QA command {names} ran before the last file write, not after it")
    if any(status == "seen" and call.ok is True for status, call in after):
        return Verdict(True, "QA command passed after the last file write")
    if any(status == "seen" and call.ok is False for status, call in after):
        return Verdict(False, "QA command failed after the last file write")
    if any(status == "masked" for status, _ in after):
        return Verdict(False, "QA output went through a pipe or into another command, "
                              "so its exit code was not seen")
    return Verdict(False, "QA command ran but its result was not recorded")


def run_pipeline(agent, source, run=1):
    """Build into a fresh folder, hand it to the agent for QA, verify the claim."""
    with tempfile.TemporaryDirectory() as tmp:
        build, artifacts = Path(tmp) / "build", Path(tmp) / "artifacts"
        shutil.copytree(source, build)
        (build / "build.txt").write_text(f"run {run}\n", encoding="utf-8")
        artifacts.mkdir()
        claim = agent(build, artifacts)
        return claim, verify(claim, build, artifacts)


def trial(agent, source, runs):
    return [run_pipeline(agent, source, n) for n in range(1, runs + 1)]
