# Privacy

invigilator runs on your machine, inside Claude Code. It has no server, makes no network calls and collects nothing. This page says what it reads and writes there.

## What it reads

- The reviewer hook reads the tool call Claude Code is about to make, such as the file an Edit changes or the command a Bash call runs, and the project's `.claude/invigilator.json`.
- The QA hook reads the session transcript file that Claude Code passes to Stop and SubagentStop hooks. The transcript can hold anything you or the agent typed. The hook reads only the tool calls and, for each result, whether it failed and the start of its text, to decide whether the QA command passed after the last file write. It keeps nothing from them.
- The line at session start reads the same project file and the plugin options you set with `/plugin configure`.

## What it writes

Only empty files, named by hashes:

- one per project in the plugin's data folder under `~/.claude/plugins/data/`, so the line at session start shows once
- one per stop the QA hook blocks once, and one per change to a file the QA command runs that it tells you about, in the same folder, or in the temp folder when there is none. The hook deletes its own files there after 30 days.
- one per session in the temp folder when no Python is found, so that warning shows once

Claude Code deletes the plugin's data folder when you uninstall the plugin.

## What it sends

Nothing. The plugin's Python code imports no network module and starts no other program. To find Python, each hook runs `sh`, or Windows PowerShell on Windows without Git Bash, and `hooks/run.sh` calls `dirname`, `uname`, `sed`, `head` and `printf`, and `xcode-select -p` on macOS.

The files under `.github/` are this repository's CI on GitHub. They don't run on your machine.

## Questions

Open an issue at https://github.com/zhyuka/invigilator/issues.
