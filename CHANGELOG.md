# Changelog

Each version here matches `version` in `.claude-plugin/plugin.json`, the marketplace entry in `.claude-plugin/marketplace.json`, and `pyproject.toml`. Claude Code installs an update only when that number changes.

## 0.3.0

### Added

- A launcher, `hooks/run.sh` and `hooks/run.py`, that finds Python on macOS, Linux and Windows. It tries `python3` and then `python`, and on Windows `py -3`, `python` and `python3`. It moves past the Microsoft Store's app execution aliases and skips macOS's `/usr/bin/python3` when the command line tools aren't installed. `RELEASE_CHECKS_PYTHON` picks a particular Python.
- On Windows without Git Bash, PowerShell runs a second line in each hook command, which finds Python the same way.
- With no Python 3.9 or later, the hooks let every call through and warn once per session, instead of failing on every call.
- Plugin options for reviewer agents, builder agents, the reviewer shell rule and the line at session start, set with `/plugin configure`. A field in `.claude/release-checks.json` wins over the option.
- A SessionStart hook that says in one line what the hooks will block. It shows once per project and again when the settings change, and a config problem shows in every session.
- `/agent-release-checks:setup`, a skill that finds the project's QA command and writes `.claude/release-checks.json` once you confirm.
- CI on Ubuntu, macOS and Windows. It runs every hook command on every fixture in sh, dash, bash, Git Bash, pwsh and Windows PowerShell from a plugin path with spaces, and runs `claude plugin validate --strict` on the marketplace and the plugin.
- classifier: TODO
- stop hook: TODO

### Changed

- The hooks read their input as UTF-8 on every platform. Python on Windows had read it in the locale's code page.
- A hook that crashes or can't read its input now lets the call through with a warning and exits 0. Since Claude Code 2.1.299, on Windows without Git Bash, a PreToolUse hook that fails blocks the tool call, so before this change a missing Python there blocked every agent's edits. The PreToolUse hook also sets `"onFailure": "continue"`.
- `plugin.json` has `documentationUrl`, `supportUrl` and more keywords. The marketplace entry has `version`, `tags` and `homepage`.
- `.gitattributes` keeps `*.sh` files at LF line endings, which sh needs on a Windows checkout.
- The README starts with what the hooks do and how to install them, and adds sections on configuring, turning the plugin off, troubleshooting, what it reads, runs and sends, and how it compares.

## 0.2.0

- Three release checks with stub agents and a demo: a guard that refuses to score an empty build, a read-only audit of reviewer runs, and a verifier that accepts "QA passed" only with evidence tied to the build. Each check is calibrated on a planted bad case and a known-good one.
- `release-checks check <folder>` runs the checks on a build folder, or on a run folder with a `run.json`.
- A Claude Code plugin and the `zhyuka` marketplace. A PreToolUse hook keeps reviewer agents read-only, and a Stop and SubagentStop hook keeps a builder from stopping until its QA command passes after its last edit. The `/agent-release-checks:release-check` skill runs the checks from a session.
- CI on Python 3.9 to 3.13.
