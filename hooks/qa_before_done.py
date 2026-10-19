#!/usr/bin/env python3
"""Stop and SubagentStop hook: a builder may not stop before its QA passes. See release_checks/hooks.py.

Whatever goes wrong in here, the agent may stop and the user sees why the check did not run.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from release_checks import hooks  # noqa: E402

    code = hooks.main(hooks.qa_before_done)
except Exception as err:  # noqa: BLE001 - a crash here would read as a hook error, not as a reason
    print(json.dumps({"systemMessage": f"release-checks: the QA check did not run, because the hook "
                                       f"failed ({type(err).__name__}: {str(err)[:200]})."}))
    code = 0
sys.exit(code)
