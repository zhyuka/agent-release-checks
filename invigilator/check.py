"""invigilator check <folder>: run the three checks on your own build or run.

The folder is a run folder with a run.json (see runlog.py), or a bare build
folder. A check that has nothing to read is reported as skipped, with the
reason, never as passed.
"""
from dataclasses import dataclass
from pathlib import Path

from . import readonly, runlog, step_ran, vacuous


@dataclass
class Result:
    check: str
    status: str
    detail: str


def check_build(build):
    """The guarded score has to exist and reach the QA pass mark."""
    score = vacuous.guarded_score(build)
    if score.value is None:
        return Result("build score", "fail", str(score))
    failed = [r.rule for r in score.results if not r.passed]
    detail = f"{score}" + (f"; failed: {', '.join(failed)}" if failed else "")
    if score.value < step_ran.PASS_MARK:
        return Result("build score", "fail", f"{detail}; pass mark {step_ran.PASS_MARK:.2f}")
    return Result("build score", "pass", detail)


def check_readonly(run):
    """A reviewer run passes only if no tool call wrote files and the build is unchanged."""
    if run.role != "reviewer":
        return Result("read-only", "skip", f"role is {run.role}, not reviewer")
    hits = [f"{call.tool}: {reason}" for call in run.tool_calls
            for reason in [readonly.writes(call.tool, call.input)] if reason]
    if run.build_sha256_before and step_ran.build_sha256(run.build) != run.build_sha256_before:
        hits.append("the build's SHA-256 differs from build_sha256_before")
    if hits:
        return Result("read-only", "fail", "; ".join(hits))
    detail = f"no file writes in {len(run.tool_calls)} tool calls"
    if run.build_sha256_before:
        return Result("read-only", "pass", detail + "; build unchanged")
    return Result("read-only", "pass", detail + "; build hash before the review not recorded")


def check_step_ran(run):
    """A "QA passed" claim needs evidence, and every piece of evidence given has to hold."""
    if not any(claim.strip().lower() == "qa passed" for claim in run.claims):
        return Result("step ran", "skip", "the agent did not claim QA passed")
    verdicts = []
    if (run.artifacts / step_ran.REPORT).is_file():
        verdicts.append(step_ran.verify("QA passed", run.build, run.artifacts))
    if run.qa_commands:
        verdicts.append(step_ran.qa_ran(run.tool_calls, run.qa_commands))
    if not verdicts:
        return Result("step ran", "fail",
                      f"no evidence: no artifacts/{step_ran.REPORT} and no qa_commands to look for")
    rejected = [v.reason for v in verdicts if not v.accepted]
    if rejected:
        return Result("step ran", "fail", "; ".join(rejected))
    return Result("step ran", "pass", "; ".join(v.reason for v in verdicts))


def check_folder(folder):
    """Results for every check. Raises runlog.RunLogError for unusable input."""
    folder = Path(folder)
    if not folder.is_dir():
        raise runlog.RunLogError(f"{folder}: not a folder")
    run = runlog.load_run(folder)
    if run is None:
        return [check_build(folder),
                Result("read-only", "skip", f"no {runlog.RUN_LOG}"),
                Result("step ran", "skip", f"no {runlog.RUN_LOG}")]
    return [check_build(run.build), check_readonly(run), check_step_ran(run)]
