# Privacy

invigilator runs on your machine, inside Claude Code, or as the `invigilator` command in a shell, a git hook or CI. It has no server, makes no network calls and collects nothing. This page says what it reads and writes there.

## What it reads

- The reviewer hook reads the tool call Claude Code is about to make, such as the file an Edit changes or the command a Bash call runs, the project's `.claude/invigilator.json`, and your own `invigilator.json` in your Claude Code config folder, which lists the QA commands you accepted for each project folder.
- The QA hook reads the session transcript file that Claude Code passes to Stop and SubagentStop hooks. The transcript can hold anything you or the agent typed. The hook reads only the tool calls and, for each result, whether it failed and the start of its text, to decide whether the QA command passed after the last file write. It keeps nothing from them.
- The line at session start reads the same two files and the plugin options you set with `/plugin configure`.
- `invigilator check` and `invigilator prove` read the checks listed in those two files, the files each check's command names and each check's cases, and `.claude/invigilator-proofs.json`. With `--base`, they read the project file and the cases at that git ref through `git`.

## What it writes

`invigilator prove` writes `.claude/invigilator-proofs.json` in the project: for each check it proved, a hash of the check, and each case's path, hash and exit code. The hooks write only empty files, named by hashes:

- one per project in the plugin's data folder under `~/.claude/plugins/data/`, so the line at session start shows once
- one per stop the QA hook blocks once, and one per change to a file the QA command runs that it tells you about, in the same folder, or in the temp folder when there is none. The hook deletes its own files there after 30 days.
- one per session in the temp folder when no Python is found, so that warning shows once

Claude Code deletes the plugin's data folder when you uninstall the plugin.

## What it sends

Nothing. The plugin's Python code imports no network module. The hooks start no other program. To find Python, each hook runs `sh`, or Windows PowerShell on Windows without Git Bash, and `hooks/run.sh` calls `dirname`, `uname`, `sed`, `head` and `printf`, and `xcode-select -p` on macOS.

`invigilator check` and `invigilator prove` start the commands your project's checks name, and `git` for `--base`. What those commands do is up to the project. Read a project's checks before you run them, as you would its tests.

The files under `.github/` are this repository's CI on GitHub. They don't run on your machine. The templates under `ci/` run only where you copy them, and install invigilator from GitHub there.

## Questions

Open an issue at https://github.com/zhyuka/invigilator/issues.
