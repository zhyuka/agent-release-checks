# Changelog

Each version here matches `version` in `.claude-plugin/plugin.json`, the marketplace entry in `.claude-plugin/marketplace.json`, and `pyproject.toml`. Claude Code installs an update only when that number changes.

## 0.3.2

### Changed

- When QA ran on its own after the last edit and failed, the QA hook blocks only the first stop. Its reason tells the builder to fix the cause and run QA again. If a check can't pass here, the builder should stop and tell you which check fails and why, and not edit the QA command, the files it runs or `.claude/invigilator.json` to get it to pass. The next stop with no new edit goes through, and you see a message that QA failed. Before, the hook blocked every stop until QA passed. In the live eval where QA can't pass, Claude Haiku 4.5 then edited `qa.py` to skip the failing check in 4 of 6 runs and said QA passed, and one more run hit its turn limit. With this change the plugin arm scored 0.94 and 0.89, up from 0.56 and 0.67. No run edited `qa.py`, all 6 ended, and each one stopped after a single block for the failure and said QA can't pass here.
- A pass doesn't count when it came after the builder changed a file the QA command runs, such as `qa.py` in `python3 qa.py`, while QA was failing. The hook blocks the first stop and asks the builder to undo the change, or to tell you what it changed and why. The next stop with no new edit goes through, with a message that names the file. After that message, a pass counts again.
- The QA hook keeps an empty file for each stop it has blocked once, and for each change to the check it has told you about, in the plugin's data folder, or in the temp folder when there is none. If it can't write the file, it blocks every such stop, as before.

### Fixed

- On Windows, a reviewer's `ls > /dev/null` or `2>/dev/null` counted as a write, because Python's Windows path rules turn `/dev/null` into `\dev\null`. CI's first run with annotations showed it.
- A launcher test passed only where pwsh's folder had no Python. CI's first run with annotations showed this too.
- Three live eval graders gave wrong answers. The one that checks `qa.py` wasn't edited expected `file_path` first in an Edit call's input, but Claude Code writes `replace_all` first, so it missed every Edit call. The one that checks `qa.py` still holds its failing check passed after an agent turned the failing branch into `pass`. The one that checks the main session started a general-purpose subagent failed a run whose Agent call left out `subagent_type`, which starts that subagent.

## 0.3.1

### Fixed

- On Windows without Git Bash, the hooks let every call through. PowerShell doesn't hand its own input to a program it starts, so Python got an empty hook input, failed to read it, and failed open. The PowerShell line now reads the input once as UTF-8 and pipes it to each Python it tries. CI caught this on the first run of 0.3.0, where the launcher tests run every hook command in pwsh on Ubuntu, macOS and Windows, and in Windows PowerShell.

## 0.3.0

### Added

- A launcher, `hooks/run.sh` and `hooks/run.py`, that finds Python on macOS, Linux and Windows. It tries `python3` and then `python`, and on Windows `py -3`, `python` and `python3`. It moves past the Microsoft Store's app execution aliases and skips macOS's `/usr/bin/python3` when the command line tools aren't installed. `INVIGILATOR_PYTHON` picks a particular Python.
- On Windows without Git Bash, PowerShell runs a second line in each hook command, which finds Python the same way.
- With no Python 3.9 or later, the hooks let every call through and warn once per session, instead of failing on every call.
- Plugin options for reviewer agents, builder agents, the reviewer shell rule and the line at session start, set with `/plugin configure`. A field in `.claude/invigilator.json` wins over the option.
- A SessionStart hook that says in one line what the hooks will block. It shows once per project and again when the settings change, and a config problem shows in every session.
- `/invigilator:setup`, a skill that finds the project's QA command and writes `.claude/invigilator.json` once you confirm.
- CI on Ubuntu, macOS and Windows. It runs every hook command on every fixture in sh, dash, bash, Git Bash, pwsh and Windows PowerShell from a plugin path with spaces, and runs `claude plugin validate --strict` on the marketplace and the plugin.
- The reviewer hook reads a shell command the way the shell does, including `bash -c` strings, `eval`, command substitutions, heredocs, `xargs`, `find -exec` and inline code such as `python -c`. On 699 commands from real agent sessions, labeled by hand, it denies none of the 423 that only read, down from 3, and misses 15 of the 191 that write, down from 39. Of 150 red-team writes made to look harmless, it misses 3, down from 88.
- A reviewer may write to a temp folder outside the project, such as `/tmp`. Of the 191 labeled writes, the 76 that land only in temp folders now go through.
- The temp exception checks every place a call can change, not just its named target. It denies a write that leaves temp through the source of a link (`ln`, `cp -l`, `cp -s`, `os.symlink` and the like), through a move or an archiver that removes its source (`mv`, `rsync --remove-source-files`, `tar --remove-files`, `zip -m`, `shutil.move`, `os.rename`), or through a git command pointed at a repository outside temp (`-C`, `--git-dir`, `--work-tree`, `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`). It resolves symlinks before it allows a temp target and denies a hard-linked one, and it denies `mount` and `bindfs`. A 52-case probe of these routes let 14 writes through before and lets none through now, and it still denies none of the 423 read-only commands.
- For every agent, a call that changes `.claude/invigilator.json` asks you first, since an agent could turn the checks off by editing it.
- The QA hook reads the transcript from the end. On the 10 largest transcripts tried, 86 to 347 MB, a stop takes 135 to 215 ms instead of 1.0 to 3.8 s.
- Replaying 5,287 real transcripts from Claude Code 2.1.136 to 2.1.288 found 68 stops that 0.2.0 blocked wrongly. 66 of them now let the agent stop, and the other 2 block because inline Python and a build step now count as writes. A failed Edit or Write, a command Claude Code refused to run, and a git command that changes only refs, the index or a remote no longer count as writes. The hook reads PowerShell commands, a QA run Claude Code moved to the background no longer counts as passed, and a record Claude Code copied back into the file counts once.
- The QA hook counts a write only when it lands inside the project, or where the hook can't tell. On the same transcripts, blocks went from 1,372 to 946, because notes in `/tmp` or `~/.claude` no longer send a builder back.
- The QA hook reads an inline Python write (`python -c` or a heredoc) and places its target from the code's syntax tree, the same way it places a shell redirect. It still counts a write whose target it can't place, and a subprocess, `exec` or `eval` it can't read. On the same transcripts, end-of-file blocks fell from 946 to 802, because an inline Python note written to a temp or home folder now places and no longer sends a builder back; code the hook can't read now counts as a possible write, which newly blocks a few.

### Changed

- Renamed from agent-release-checks to invigilator. The plugin is `invigilator@zhyuka`, the Python package and the command are `invigilator`, the project file is `.claude/invigilator.json`, the skills are `/invigilator:check` and `/invigilator:setup`, and `INVIGILATOR_PYTHON` picks a Python. The marketplace maps the old plugin name to the new one under `renames`.
- The hooks read their input as UTF-8 on every platform. Python on Windows had read it in the locale's code page.
- A hook that crashes or can't read its input now lets the call through with a warning and exits 0. Since Claude Code 2.1.299, on Windows without Git Bash, a PreToolUse hook that fails blocks the tool call, so before this change a missing Python there blocked every agent's edits. The PreToolUse hook also sets `"onFailure": "continue"`.
- `plugin.json` has `documentationUrl`, `supportUrl` and more keywords. The marketplace entry has `version`, `tags` and `homepage`.
- `.gitattributes` keeps `*.sh` files at LF line endings, which sh needs on a Windows checkout.
- The README starts with what the hooks do and how to install them, and adds sections on configuring, turning the plugin off, troubleshooting, what it reads, runs and sends, and how it compares.

## 0.2.0

- Three release checks with stub agents and a demo: a guard that refuses to score an empty build, a read-only audit of reviewer runs, and a verifier that accepts "QA passed" only with evidence tied to the build. Each check is calibrated on a planted bad case and a known-good one.
- A `check` command that runs the checks on a build folder, or on a run folder with a `run.json`.
- A Claude Code plugin and the `zhyuka` marketplace. A PreToolUse hook keeps reviewer agents read-only, and a Stop and SubagentStop hook keeps a builder from stopping until its QA command passes after its last edit. A skill runs the checks from a session.
- CI on Python 3.9 to 3.13.
