---
name: setup
description: Set up invigilator for this project. Finds the project's check command, asks which agents are builders and reviewers, and once the user confirms writes .claude/invigilator.json and the user's own entry in ~/.claude/invigilator.json. Use after installing the plugin, when asked to configure release checks, or when the plugin says the QA check is off or a QA command doesn't count yet.
argument-hint: "[qa command]"
---

Set up two files, and don't write either until the user confirms. The plugin's hooks read both on every call.

- `.claude/invigilator.json` in the project, which the team can commit. It names reviewers and builders and proposes the QA command. It can add reviewers and builders, but not take any away from the user's plugin options.
- The user's own `invigilator.json` in the Claude Code config folder: `$CLAUDE_CONFIG_DIR/invigilator.json` when that variable is set, else `~/.claude/invigilator.json`. Only a QA command listed there for this project counts, so a repository can't decide what counts as QA.

1. Read both files if they exist, and keep their fields and the other projects' entries unless the user changes them. If the project file already proposes a QA command the user hasn't accepted, offer to accept it.

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

4. Show both changes, and write them after the user says yes. Claude Code then asks the user to approve each write, because the plugin asks before any agent changes either file.

   The project file takes only `reviewer_agents`, `reviewer_shell`, `builder_agents` and `qa_commands`. A field you leave out keeps its default. For example:

   ```json
   {
     "builder_agents": ["main", "*builder*"],
     "qa_commands": ["npm run check"]
   }
   ```

   The user's file holds only `projects`. Add or replace this project's entry, keyed by the project's absolute path, with the same `qa_commands`:

   ```json
   {
     "projects": {
       "/Users/sam/code/shop": {"qa_commands": ["npm run check"]}
     }
   }
   ```

   A key covers the folders under it, and can start with `~`. A `*` matches any characters, slashes included, so `~/code/shop*` also covers worktrees such as `~/code/shop-fix`; offer that when the project has worktrees. When several keys match, the longest wins.

5. Check the files. Run this from the project root:

   ```
   echo '{"hook_event_name": "SessionStart"}' | CLAUDE_PLUGIN_OPTION_SESSION_STATUS=always python3 "${CLAUDE_PLUGIN_ROOT}/hooks/run.py" session_status
   ```

   On Windows, use `python` or `py -3` if `python3` isn't found. In PowerShell, run `$env:CLAUDE_PLUGIN_OPTION_SESSION_STATUS = "always"` first and leave the prefix off. The output's `systemMessage` says in one line what the hooks will block, or names what is wrong with a file. It doesn't see the user's plugin options. Report the line.

6. Tell the user both files apply from the next tool call, with no restart. Committing the project file gives the team the same reviewers and builders and proposes the QA command; each teammate runs `/invigilator:setup` once to accept it. A builder that runs the QA command through a pipe or with `|| true` doesn't count as having run it.
