"""python -m release_checks demo"""
import argparse
from pathlib import Path

from . import calibrate, readonly, step_ran, vacuous

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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m release_checks")
    parser.add_argument("command", choices=["demo"])
    parser.parse_args(argv)
    for part in (demo_vacuous, demo_readonly, demo_step_ran):
        part()
        print()
    return 0 if demo_calibration() else 1
