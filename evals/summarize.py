"""Summarize a `claude plugin eval --json` result as Markdown.

    python3 evals/summarize.py results.json

For each case it prints the score with the plugin and without it, the runs in
each arm that ended on an error (a turn cap, a timeout, an API error), and for
each grader how many runs passed it in each arm. Field names follow the
schemaVersion 1 result document. It never changes the exit code of a run; it
exits 2 only when the file can't be read.
"""
import json
import sys


def fmt(value, signed=False):
    if value is None:
        return "n/a"
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def passes(runs, name):
    """'N of M' runs that passed the grader, with runs that were never graded named."""
    passed = graded = 0
    for run in runs:
        for grader in run.get("graders") or []:
            if grader.get("name") == name:
                graded += 1
                passed += bool(grader.get("passed"))
    text = f"{passed} of {len(runs)}"
    if graded < len(runs):
        text += f" ({len(runs) - graded} not graded)"
    return text


def errors(runs):
    return [(n, run["error"]) for n, run in enumerate(runs, 1) if run.get("error")]


def summarize(doc):
    lines = []
    cases = doc.get("cases") or []
    total = doc.get("aggregates") or {}
    lines.append(f"## Live evals: Claude Code {doc.get('claudeVersion', '?')}, {len(cases)} cases, "
                 f"${doc.get('costUsd', 0):.2f}, {doc.get('durationSeconds', 0)}s")
    lines.append("")
    lines.append(f"{total.get('casesPassed', '?')} of {total.get('casesTotal', '?')} cases at or above "
                 f"the threshold of {(doc.get('suite') or {}).get('threshold', '?')}. "
                 f"Mean delta: {fmt(total.get('meanDelta'), True)}.")
    if doc.get("partial"):
        lines.append("")
        lines.append(f"**Partial result** ({doc.get('partialReason', 'reason not given')}): "
                     "some runs never started, so the scores below are not comparable to a full run.")
    lines += ["", "| case | with plugin | without | delta | with-arm runs that errored | "
                  "baseline runs that errored |", "| --- | --- | --- | --- | --- | --- |"]
    for case in cases:
        agg, arms = case.get("aggregates") or {}, case.get("arms") or {}
        with_runs, without_runs = arms.get("with") or [], arms.get("without") or []
        lines.append(f"| {case.get('name')} | {fmt(agg.get('score'))} | {fmt(agg.get('scoreWithout'))} | "
                     f"{fmt(agg.get('delta'), True)} | {len(errors(with_runs))} of {len(with_runs)} | "
                     f"{len(errors(without_runs))} of {len(without_runs)} |")
    for case in cases:
        arms = case.get("arms") or {}
        with_runs, without_runs = arms.get("with") or [], arms.get("without") or []
        lines += ["", f"### {case.get('name')}", "",
                  "| grader | type | weight | with plugin | without |",
                  "| --- | --- | --- | --- | --- |"]
        for grader in case.get("graders") or []:
            name = grader.get("name")
            config = grader.get("config") or {}
            kind = grader.get("type", "?") + (" (with-only)" if config.get("arm") == "with-only" else "")
            lines.append(f"| {name} | {kind} | {grader.get('weight', 1)} | {passes(with_runs, name)} | "
                         f"{passes(without_runs, name) if without_runs else 'not run'} |")
        for arm, runs in (("with plugin", with_runs), ("without", without_runs)):
            for number, error in errors(runs):
                lines.append(f"- {arm}, run {number}: {error}")
        for advisory in case.get("advisories") or []:
            lines.append(f"- advisory: {advisory}")
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as err:
        print(f"summarize: could not read {argv[1]}: {err}", file=sys.stderr)
        return 2
    sys.stdout.write(summarize(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
