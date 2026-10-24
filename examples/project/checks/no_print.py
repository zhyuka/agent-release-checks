"""Fail when a Python file under the path given calls print(). The app logs instead.

    python checks/no_print.py src
"""
import ast
import sys
from pathlib import Path


def prints(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"]


def main(target):
    target = Path(target)
    files = [target] if target.is_file() else sorted(target.rglob("*.py"))
    if not files:
        print(f"no-print: no Python files under {target}")
        return 1  # nothing inspected is not a pass
    found = [f"{path}:{line}" for path in files for line in prints(path)]
    for where in found:
        print(f"no-print: {where} calls print(); use the logger")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
