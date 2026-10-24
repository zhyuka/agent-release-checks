"""Trigger tests: does each skill fire when it should, and stay quiet when it shouldn't?

    python3 evals/triggers.py [--skill review] [--runs 3] [--model claude-haiku-4-5] [--max-cost 6]

evals/triggers/<skill>.json lists about 20 queries, half that should fire the skill and half
near misses that shouldn't, each marked "train" or "test". A description is tuned only on
the train queries, so the test queries are a held-out score. Each query runs --runs times
in a fresh scratch repository, as `claude -p` with this plugin loaded and at most
--max-turns turns, and counts as fired when the Skill tool was called with the skill's name.
The runs leave out your own user settings, skills and CLAUDE.md, so the score doesn't depend
on what else is installed on this machine; --user-settings puts them back. Claude Code's
built-in skills, such as code-review, still compete. A run that fails before the model
answers, such as a sign-in that didn't load, is tried once more.

It prints, per skill and split, how many runs fired as they should, and the cost. Every run
is a paid model call on whatever account `claude` is signed in to. It writes the full
results as JSON to --out, outside the repository by default; they stay local.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERIES = Path(__file__).resolve().parent / "triggers"
PLUGIN = "invigilator"

CART = '''def total(prices):
    return sum(prices)
'''
CHANGED = '''def total(prices):
    return sum(prices)


def mean(prices):
    return total(prices) / len(prices)
'''


def scaffold(folder):
    """A small git repository with one commit and one uncommitted change."""
    git = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "-c", "commit.gpgsign=false"]
    (folder / "src").mkdir()
    (folder / "src" / "cart.py").write_text(CART, encoding="utf-8")
    subprocess.run(git + ["init", "-q"], cwd=folder, check=True)
    subprocess.run(git + ["add", "."], cwd=folder, check=True)
    subprocess.run(git + ["commit", "-q", "-m", "Add total()"], cwd=folder, check=True)
    (folder / "src" / "cart.py").write_text(CHANGED, encoding="utf-8")


def fired(stream, skill):
    """(fired, the skills the run called, cost in USD, error or "") from stream-json output."""
    called, cost, error = [], 0.0, ""
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use" and block.get("name") == "Skill":
                    called.append(str((block.get("input") or {}).get("skill", "")))
        elif event.get("type") == "result":
            cost = event.get("total_cost_usd") or 0.0
            if event.get("is_error") and event.get("subtype") != "error_max_turns":
                error = str(event.get("result") or event.get("subtype"))
    names = {skill, f"{PLUGIN}:{skill}"}
    return any(c.lstrip("/") in names for c in called), called, cost, error


def run_one(query, skill, model, max_turns, user_settings=False):
    sources = "user,project,local" if user_settings else "project,local"
    for attempt in range(2):
        with tempfile.TemporaryDirectory(prefix="trigger-") as tmp:
            folder = Path(tmp)
            scaffold(folder)
            done = subprocess.run(
                ["claude", "-p", query, "--plugin-dir", str(ROOT), "--model", model,
                 "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns),
                 "--strict-mcp-config", "--setting-sources", sources],
                cwd=folder, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        hit, called, cost, error = fired(done.stdout, skill)
        if not done.stdout.strip():
            error = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
        # Nothing was billed, so the model never answered: try once more.
        if not (error and not cost and not called):
            break
    return {"fired": hit, "called": called, "cost": cost, "error": error, "attempts": attempt + 1}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill", action="append", help="a skill to test; all with a query file by default")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="claude-haiku-4-5")
    parser.add_argument("--max-turns", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--max-cost", type=float, default=6.0, help="stop starting runs past this many USD")
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "invigilator-triggers.json"))
    parser.add_argument("--user-settings", action="store_true",
                        help="load your own user settings, skills and CLAUDE.md too")
    args = parser.parse_args(argv)
    skills = args.skill or sorted(p.stem for p in QUERIES.glob("*.json"))
    queries = {skill: json.loads((QUERIES / f"{skill}.json").read_text(encoding="utf-8")) for skill in skills}
    # Run number outermost, so a stop at --max-cost leaves fewer runs of every query, not no runs of the last skill.
    jobs = [(skill, item, number) for number in range(args.runs) for skill in skills for item in queries[skill]]
    spent, lock, results = [0.0], threading.Lock(), []

    def work(job):
        skill, item, number = job
        with lock:
            if spent[0] >= args.max_cost:
                return None
        outcome = run_one(item["query"], skill, args.model, args.max_turns, args.user_settings)
        with lock:
            spent[0] += outcome["cost"]
        return {"skill": skill, "run": number, **item, **outcome}

    with ThreadPoolExecutor(args.jobs) as pool:
        for result in pool.map(work, jobs):
            if result is not None:
                results.append(result)
    Path(args.out).write_text(json.dumps({"model": args.model, "runs": args.runs, "results": results}, indent=2),
                              encoding="utf-8")
    print(f"Trigger tests: {args.model}, {args.runs} runs per query, at most {args.max_turns} turns, "
          f"${spent[0]:.2f}, {len(results)} of {len(jobs)} runs done, "
          f"{'with' if args.user_settings else 'without'} your user settings, "
          f"{sum(r['attempts'] > 1 for r in results)} tried twice")
    print("  A run that ended on an error counts only under errors.")
    print("  skill          split  should fire  fired      should not  stayed quiet  errors")
    for skill in skills:
        for split in ("train", "test"):
            mine = [r for r in results if r["skill"] == skill and r["split"] == split]
            ran = [r for r in mine if not r["error"]]
            yes = [r for r in ran if r["should_trigger"]]
            no = [r for r in ran if not r["should_trigger"]]
            print(f"  {skill:<14} {split:<6} {len(yes):<12} {sum(r['fired'] for r in yes):<10} "
                  f"{len(no):<11} {sum(not r['fired'] for r in no):<13} {len(mine) - len(ran)}")
    print(f"  Full results: {args.out}")
    for r in results:
        if r["error"]:
            print(f"  error: {r['skill']} {r['split']} run {r['run']}: {r['error']}: {r['query']}")
        elif r["fired"] != r["should_trigger"]:
            what = "didn't fire" if r["should_trigger"] else f"fired ({', '.join(r['called'])})"
            print(f"  miss: {r['skill']} {r['split']} run {r['run']}: {what}: {r['query']}")
    return 0 if len(results) == len(jobs) and not any(r["error"] for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
