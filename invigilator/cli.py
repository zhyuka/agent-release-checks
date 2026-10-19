"""invigilator demo | invigilator check <folder>

Also runs as python -m invigilator.
"""
import argparse
import sys
from pathlib import Path

from . import calibrate, check, readonly, runlog, step_ran, vacuous

BUILDS = Path(__file__).resolve().parent.parent / "examples" / "builds"


def table(headers, rows):
    rows = [list(map(str, row)) for row in [headers, *rows]]
    widths = [max(len(row[i]) for row in rows) for i in range(len(headers))]
    for row in rows:
        print("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())


def demo_vacuous():
    print("1. Vacuous pass: naive score vs guarded score")
    rows = vacuous.compare(BUILDS)
    table(["build", "naive", "rules that inspected nothing", "guarded"],
          [(name, f"{naive.value:.2f}",
            f"{sum(r.inspected == 0 for r in naive.results)} of {len(naive.results)}", guarded)
           for name, naive, guarded in rows])
    real = [(name, naive) for name, naive, guarded in rows if guarded.value is not None]
    for name, naive, guarded in rows:
        if guarded.value is None:
            beaten = [n for n, s in real if s.value < naive.value]
            print(f"  Naive scorer ranks '{name}' above {len(beaten)} of {len(real)} real builds"
                  f" ({', '.join(beaten) or 'none'}). The guard refuses to score it.")


def demo_readonly(runs=91, seed=0):
    print(f"2. Read-only audit: {runs} runs of a stub reviewer told to stay read-only")
    rows = []
    for label, tools in (("read and write", readonly.FileTools),
                         ("read-only layer", readonly.ReadOnlyTools)):
        results = readonly.audit(readonly.stub_reviewer, BUILDS / "good", runs, seed, tools)
        edited = [r for r in results if r.changes]
        blocked = [r for r in results if r.blocked]
        rows.append((label, f"{len(edited)} of {runs}", f"{len(blocked)} of {runs}"))
        if tools is readonly.FileTools:
            print(f"  {len(edited)} of {runs} runs edited files despite a read-only instruction:")
            for r in edited:
                print(f"    run {r.number}: {', '.join(r.changes)}")
    table(["tools given", "runs that edited files", "writes blocked"], rows)
    print("  Same prompt both times. The instruction did not stop the writes; the tool layer did.")


def demo_step_ran(runs=33):
    print(f"3. Step-ran verification: {runs} runs per stub agent")
    rows = []
    for agent in (step_ran.qa_agent, step_ran.cheap_agent):
        results = step_ran.trial(agent, BUILDS / "good", runs)
        claimed = sum(claim == "QA passed" for claim, _ in results)
        accepted = sum(v.accepted for _, v in results)
        reasons = "; ".join(sorted({v.reason for _, v in results}))
        rows.append((agent.__name__, f"{claimed} of {runs}", f"{accepted} of {runs}",
                     f"{runs - accepted} of {runs}", reasons))
    table(["agent", "claimed QA passed", "accepted", "rejected", "verifier reason"], rows)


def demo_calibration():
    print("4. Calibration: one planted bad case and one known-good case per check")
    results = calibrate.calibrate_all(BUILDS / "good")
    table(["check", "planted bad caught", "known good passed"],
          [(c.check, f"{c.caught} of {c.bad}", f"{c.passed} of {c.good}") for c in results])
    ok = sum(c.ok for c in results)
    print(f"  {ok} of {len(results)} checks passed calibration.")
    return ok == len(results)


def demo():
    if not BUILDS.is_dir():
        print(f"invigilator: the demo reads {BUILDS}, which only exists in a clone of the repo",
              file=sys.stderr)
        return 2
    for part in (demo_vacuous, demo_readonly, demo_step_ran):
        part()
        print()
    return 0 if demo_calibration() else 1


def run_check(folder):
    """Exit 0 when no check failed, 1 when one did, 2 when the input is unusable."""
    try:
        results = check.check_folder(folder)
    except runlog.RunLogError as err:
        print(f"invigilator: {err}", file=sys.stderr)
        return 2
    print(f"Release checks for {folder}")
    table(["check", "result", "detail"], [(r.check, r.status, r.detail) for r in results])
    ran = [r for r in results if r.status != "skip"]
    failed = [r for r in ran if r.status == "fail"]
    print(f"  {len(ran)} of {len(results)} checks ran: "
          f"{len(ran) - len(failed)} passed, {len(failed)} failed.")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="invigilator",
                                     description="Release checks for agent-built software.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the stub-agent demo and calibration")
    folder = commands.add_parser("check", help="run the checks on a build folder or a run folder")
    folder.add_argument("folder", help="a build folder, or a run folder with a run.json")
    args = parser.parse_args(argv)
    if args.command == "check":
        return run_check(args.folder)
    return demo()
