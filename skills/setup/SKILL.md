---
name: setup
description: Set up agent-release-checks for this project. Finds the project's check command, asks which agents are builders and reviewers, and writes .claude/release-checks.json once the user confirms. Use after installing the plugin, when asked to configure release checks, or when the plugin says the QA check is off.
argument-hint: "[qa command]"
---

Write `.claude/release-checks.json` for this project. The plugin's hooks read it from the project root on every call. Don't write it until the user confirms.

1. Read `.claude/release-checks.json` if it exists, and keep its fields unless the user changes them.

2. Find the QA command. If `$ARGUMENTS` names one, use that. Otherwise look for these, and note the file each candidate came from:
   - `package.json` scripts named `check`, `verify`, `test`, `lint` or `typecheck`. Use `npm run <name>`, or `pnpm` or `yarn` when the project has `pnpm-lock.yaml` or `yarn.lock`.
   - `Makefile` targets named `check` or `test`: `make check`.
   - `tox.ini`: `tox`. A pytest setup (`pytest.ini`, `[tool.pytest` in `pyproject.toml`, `[tool:pytest]` in `setup.cfg`, or `test_*.py` files): `python -m pytest`.
   - `Cargo.toml`: `cargo test`. `go.mod`: `go test ./...`.
   - A step in `.github/workflows/*.yml` that runs one of the above.

   Prefer the one command that runs the whole check, such as a `check` script that runs lint and tests, over `test` alone. The command has to exit non-zero when the check fails.

3. Ask the user, in one message:
   - Which command counts as QA, offering what you found. More than one is allowed.
   - Which agents are builders: the main session (`main`), agents whose names match `*builder*` (the default), or names they give.
   - Which agents are reviewers: `*reviewer*` (the default) or names they give. And whether a reviewer may still run shell commands that don't write files (`block-writes`, the default) or no shell commands at all (`block-all`).

   An agent's name is the `name` in its frontmatter, and a plugin's agent is `plugin-name:agent-name`. Patterns are shell-style globs, matched without case.

4. Show the file you will write, and write it after the user says yes. Claude Code then asks the user to approve the write, because the plugin asks before any agent changes this file. It takes only `reviewer_agents`, `reviewer_shell`, `builder_agents` and `qa_commands`. A field you leave out keeps its default. For example:

   ```json
   {
     "builder_agents": ["main", "*builder*"],
     "qa_commands": ["npm run check"]
   }
   ```

5. Check the file. Run this from the project root:

   ```
   echo '{"hook_event_name": "SessionStart"}' | CLAUDE_PLUGIN_OPTION_SESSION_STATUS=always python3 "${CLAUDE_PLUGIN_ROOT}/hooks/run.py" session_status
   ```

   On Windows, use `python` or `py -3` if `python3` isn't found. In PowerShell, run `$env:CLAUDE_PLUGIN_OPTION_SESSION_STATUS = "always"` first and leave the prefix off. The output's `systemMessage` says in one line what the hooks will block, or names what is wrong with the file. It doesn't see the user's plugin options. Report the line.

6. Tell the user the file applies from the next tool call, with no restart, and that committing it gives the whole team the same checks. A builder that runs the QA command through a pipe or with `|| true` doesn't count as having run it.
