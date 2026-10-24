"""The shipping rule for a skill: it beats the same frontmatter with a one-line body, or ties
it at lower cost, on cases where it has to catch a planted bad case and pass a good one.

    python3 evals/shipping_rule.py [--tag skill] [--runs 3] [--max-cost 6]

It copies the plugin twice into a scratch folder. In the second copy every skill named in
STUBS keeps its frontmatter, and its body is the one line there. It runs the cases tagged
--tag through `claude plugin eval` on each copy, with no baseline arm, and prints each
case's score and the run cost side by side. Every run is a paid model call on whatever
account `claude` is signed in to. Reports stay local (--no-publish); the JSON results go to
the scratch folder, whose path it prints.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPY = (".claude-plugin", "agents", "hooks", "invigilator", "skills", "evals", "README.md", "plugin.json")
STUBS = {
    "review": "Have the change reviewed and report what the review found.",
    "miss-to-check": "Turn the missed defect into a check that would have caught it.",
}


def copy_plugin(folder, stub):
    folder.mkdir(parents=True)
    for name in COPY:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, folder / name, ignore=shutil.ignore_patterns("results", "__pycache__"))
        else:
            shutil.copy2(source, folder / name)
    if stub:
        for skill, line in STUBS.items():
            path = folder / "skills" / skill / "SKILL.md"
            front = path.read_text(encoding="utf-8").split("---", 2)[1]
            path.write_text(f"---{front}---\n\n{line}\n", encoding="utf-8")
    return folder


def run(folder, out, args):
    command = ["claude", "plugin", "eval", str(folder), "--tag", args.tag, "--ablation", "none",
               "--scaffold", "--trust-plugin", "--no-publish", "--threshold", "0",
               "--runs", str(args.runs), "--max-cost-usd", str(args.max_cost / 2),
               "--judge-model", args.judge, "--concurrency", "2", "--json", str(out),
               "--allow-tools", "Bash", "Edit", "Write"]
    done = subprocess.run(command, cwd=folder)
    if not out.is_file():
        print(f"shipping_rule: no result from {folder} (exit {done.returncode})", file=sys.stderr)
        return None
    return json.loads(out.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", default="skill")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-cost", type=float, default=6.0, help="USD for both copies together")
    parser.add_argument("--judge", default="claude-haiku-4-5")
    args = parser.parse_args(argv)
    scratch = Path(tempfile.mkdtemp(prefix="shipping-rule-"))
    docs = {}
    for arm, stub in (("skill", False), ("one-line body", True)):
        folder = copy_plugin(scratch / arm.replace(" ", "-"), stub)
        docs[arm] = run(folder, scratch / f"{arm.replace(' ', '-')}.json", args)
    if None in docs.values():
        return 2
    print(f"\nShipping rule, cases tagged {args.tag}, {args.runs} runs each. Results: {scratch}")
    print(f"  {'case':<24} {'skill':>6} {'one-line body':>14}")
    scores = {arm: {c["name"]: (c.get("aggregates") or {}).get("score") for c in doc.get("cases") or []}
              for arm, doc in docs.items()}
    for name in scores["skill"]:
        mine, stub = scores["skill"][name], scores["one-line body"].get(name)
        print(f"  {name:<24} {mine if mine is None else f'{mine:.2f}':>6} {stub if stub is None else f'{stub:.2f}':>14}")
    for arm, doc in docs.items():
        print(f"  {arm}: ${doc.get('costUsd', 0):.2f}, {doc.get('durationSeconds', 0)} s"
              + (" (partial)" if doc.get("partial") else ""))
    print("  Per grader: python3 evals/summarize.py <result.json>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
