---
name: check
description: Run invigilator check on a build folder or a run folder and report each check's result. Use before saying a build is done or ready to release.
argument-hint: "[folder]"
---

Run the release checks on the folder in $ARGUMENTS. If no folder was given, use the project's build output folder. If you can't tell which folder that is, ask.

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m invigilator check <folder>
```

It needs Python 3.9 or later. On Windows, use `python` or `py -3` if `python3` isn't found, and in PowerShell set `$env:PYTHONPATH = "${CLAUDE_PLUGIN_ROOT}"` first instead of the prefix.

The folder is a build folder, or a run folder with a `run.json`. The format is in `${CLAUDE_PLUGIN_ROOT}/README.md`.

Report the table as printed. Give every fail with its detail and every skip with its reason. A skip is not a pass. Exit code 0 means no check failed, 1 means one failed, and 2 means the input could not be read.

Don't edit the build to make a check pass unless the user asks you to.
