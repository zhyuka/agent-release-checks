---
name: check
description: Run the project's own checks with invigilator check and report each result, or run the built-in release checks on a build or run folder. Use before saying a change is done, ready to merge or ready to release.
argument-hint: "[folder]"
---

Run the checks and report what they print. Don't judge the work yourself in their place.

With no folder in $ARGUMENTS, run the checks the project lists in `.claude/invigilator.json`, from the project root:

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m invigilator check
```

With a folder, run the built-in checks on that build folder, or on a run folder with a `run.json` (the format is in `${CLAUDE_PLUGIN_ROOT}/README.md`):

```
PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m invigilator check <folder>
```

If `invigilator` is installed as a command, `invigilator check` does the same. It needs Python 3.9 or later. On Windows, use `python` or `py -3` if `python3` isn't found, and in PowerShell set `$env:PYTHONPATH = "${CLAUDE_PLUGIN_ROOT}"` first instead of the prefix.

Report the table as printed, with every result that isn't `pass` and its detail. Only `pass` passes:

- `fail`: the check ran and found a problem. Its last lines of output follow the table.
- `unproven`: the check has no current record that it fails its planted bad cases and passes its good ones, so it doesn't count yet. `invigilator prove <name>` runs them.
- `removed`: the change took away a check, or one of its cases, that the base branch has.
- `skip`, in folder mode: the check had nothing to read. A skip is not a pass.

Exit code 0 means every check passed, 1 means one didn't, and 2 means the checks couldn't be read.

When a check fails, fix the cause in the work and run it again. If it can't pass for a reason outside the change, such as a service this machine can't reach, stop and tell the user you're blocked: which check, its output, and why. Don't edit a check, its cases, the files it runs, `.claude/invigilator-proofs.json` or `.claude/invigilator.json` to get a pass, and don't re-prove a check the change broke unless the user asked for that check to change.
