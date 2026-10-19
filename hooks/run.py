"""Run one invigilator hook: python run.py reviewer_readonly | qa_before_done | session_status

hooks/run.sh, or the PowerShell line in hooks.json, calls this with each
Python it finds. The exit code is for that launcher, not for Claude Code:
0 means the hook ran and its answer, if any, is on stdout; 3 means this
Python is older than 3.9, so the launcher should try another. The hook
input on stdin is read only after the version check, so the next Python
still gets it.

This file has to parse on any Python, which is why it does nothing else.
"""
import os
import sys

if sys.version_info < (3, 9):
    sys.exit(3)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from invigilator import plugin
except Exception as err:
    import json
    print(json.dumps({"systemMessage": "invigilator is off: it could not load (%s)." % err}))
    sys.exit(0)

sys.exit(plugin.run(sys.argv[1:]))
