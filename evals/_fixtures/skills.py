"""Workspaces for the skill eval cases. A case's scaffold.sh runs

    python3 ../_fixtures/skills.py <workspace>

in the empty run directory. The graders in each case read these files, so change them together.

review-buggy and review-clean: stats.py with mean() committed, and an uncommitted change
that adds median(). The buggy change also breaks mean() on an empty list and gets median()
wrong for an even number of values. The clean one is correct.

miss-leftover and miss-clean: src/cart.py once shipped a debug print, which a later commit
removed. In miss-leftover, src/report.py still has one.
"""
import subprocess
import sys
from pathlib import Path

GIT = ["git", "-c", "user.name=eval", "-c", "user.email=eval@example.invalid", "-c", "commit.gpgsign=false"]

MEAN = '''def mean(values):
    """The average of values, or 0.0 for an empty list."""
    if not values:
        return 0.0
    return sum(values) / len(values)
'''

BUGGY = '''def mean(values):
    """The average of values, or 0.0 for an empty list."""
    if values is None:
        return 0.0
    return sum(values) / len(values)


def median(values):
    """The middle value of values, or 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
'''

CLEAN = MEAN + '''

def median(values):
    """The middle value of values, or 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
'''

CART_SHIPPED = '''import logging

log = logging.getLogger(__name__)


def total(prices):
    print("debug prices:", prices)
    return sum(prices)
'''

CART_FIXED = CART_SHIPPED.replace('    print("debug prices:", prices)\n', '    log.debug("prices: %s", prices)\n')

REPORT = '''import logging

log = logging.getLogger(__name__)


def summary(rows):
    {line}
    return {{"rows": len(rows), "total": sum(row["amount"] for row in rows)}}
'''


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(message):
    subprocess.run(GIT + ["add", "-A"], check=True)
    subprocess.run(GIT + ["commit", "-q", "-m", message], check=True)


def review(change):
    subprocess.run(GIT + ["init", "-q"], check=True)
    write("stats.py", MEAN)
    commit("Add mean()")
    write("stats.py", change)


def miss(leftover):
    subprocess.run(GIT + ["init", "-q"], check=True)
    write("src/cart.py", CART_SHIPPED)
    line = 'print("DEBUG rows:", rows)' if leftover else 'log.debug("rows: %s", rows)'
    write("src/report.py", REPORT.format(line=line))
    commit("Add the cart and the report")
    write("src/cart.py", CART_FIXED)
    commit("Remove a debug print from total()")


WORKSPACES = {
    "review-buggy": lambda: review(BUGGY),
    "review-clean": lambda: review(CLEAN),
    "miss-leftover": lambda: miss(True),
    "miss-clean": lambda: miss(False),
}

if __name__ == "__main__":
    WORKSPACES[sys.argv[1]]()
