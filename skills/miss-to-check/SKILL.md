---
name: miss-to-check
description: Turn a defect that got past review or QA into a project check that would have caught it, proven to fail on the defect and pass on the fix. Use when a bug, regression or mistake turned up after the work was called done, or when asked to make sure the same mistake can't get through again.
argument-hint: "[the defect that was missed]"
---

A miss is a defect the project's checks let through. Turn it into a check that fails on the defect and passes on the fix. It counts only once `invigilator prove` has seen it do both.

1. Pin down the miss from $ARGUMENTS or the conversation: what was wrong, in which file, and how it was found. If you can't say what the defect was, ask.

2. Make two cases inside the project, for example under `checks/cases/<name>/`:
   - `bad/`: the smallest copy of the files that still shows the defect. When git has the version that shipped it, start from that (`git show <commit>:<path>`).
   - `good/`: the same files with only the defect fixed.

3. Write the check: a command that exits non-zero when it finds the defect and 0 when it doesn't, and takes the path to inspect as an argument. Prefer a rule in a tool the project already runs, such as a linter rule or a test, over a new script. If it finds nothing to inspect, it should fail, not pass.

4. Write the check's entry to `checks/<name>.json`:

   ```json
   {
     "name": "<name>",
     "run": "{python} checks/<name>.py {target}",
     "target": "src",
     "bad": "checks/cases/<name>/bad",
     "good": "checks/cases/<name>/good"
   }
   ```

   `{target}` becomes the path to inspect, and `{python}` the Python that runs invigilator. The command runs from the project root without a shell.

5. Prove it from that file:

   ```
   PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m invigilator prove --check checks/<name>.json
   ```

   It has to report every bad case caught and every good case passed. If it doesn't, fix the check, not the cases: a bad case it passes is the defect it would miss, and a good case it fails is a false alarm. Use `invigilator prove` if it's installed as a command; on Windows use `python` or `py -3`.

6. Run the check's command on the project, with `{target}` set to its target, for example `python3 checks/<name>.py src`. If it fails there, the defect is still in the code: say where, and don't loosen the check to get a pass. Fixing it is a separate change, so ask first.

7. List it: add the whole entry from `checks/<name>.json`, every field, as an object in the `checks` list of `.claude/invigilator.json`, keeping the file's other fields. The list holds the entries themselves, not file names or check names. This write is the user's to approve. If it's refused, or you can't ask, don't write the file another way, even if the refusal suggests trying another tool: no shell command, script or other tool. Show the user the file as it should read, with the whole entry in `checks`, and say the check is already proven, so it counts once it's listed unchanged.

8. Report the check's name and command, the prove table as printed, the result on the project, and whether the check is listed or the entry the user still has to add.

If no command can tell the defect apart from the fix, for example because it takes a person's judgment, stop and say so. Don't add a check that passes everything.
