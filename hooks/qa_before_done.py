#!/usr/bin/env python3
"""Stop and SubagentStop hook: a builder may not stop before its QA passes. See release_checks/hooks.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from release_checks import hooks  # noqa: E402

sys.exit(hooks.main(hooks.qa_before_done))
