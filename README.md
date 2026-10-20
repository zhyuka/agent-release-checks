# Invigilator

[![tests](https://github.com/zhyuka/invigilator/actions/workflows/tests.yml/badge.svg)](https://github.com/zhyuka/invigilator/actions/workflows/tests.yml)

An exam invigilator watches the room. Here that means reviewers can't touch the answers, and nobody hands in without sitting the test.

Two Claude Code hooks for projects where agents write and review code.

- A reviewer can't write files. When an agent your config names as a reviewer calls Edit or Write, or runs a shell command that writes, such as `sed -i`, `rm` or a `>` redirect, the hook denies the call and tells the agent to put the change in its review.
- A builder can't stop early. If it edited files after its last passing QA command, the stop hook sends it back to run QA, and if QA can't pass, to tell you why. A QA run piped into `tail` or ended with `|| true` doesn't count, because its exit code is hidden.

<!-- TODO: demo GIF at docs/demo.gif, 10 to 20 seconds: a reviewer's sed -i is denied, then a builder's stop is blocked until npm run check passes. Add it here as a Markdown image once the file exists. -->

Install from a Claude Code session:

```
/plugin marketplace add zhyuka/invigilator
/plugin install invigilator@zhyuka
```

Then run `/invigilator:setup` to choose your QA command. It needs Python 3.9 or later and nothing else. It has no dependencies and makes no network calls.

## Install

Each of these adds the `zhyuka` marketplace and installs the plugin for your user:

| Where | Command | Claude Code |
| --- | --- | --- |
| In a session | `/plugin install invigilator --marketplace zhyuka/invigilator` | 2.1.275 or later |
| In a shell | `claude plugin install invigilator --marketplace zhyuka/invigilator` | 2.1.292 or later |
| Either, on an older version | `/plugin marketplace add zhyuka/invigilator`, then `/plugin install invigilator@zhyuka` | |

The hooks need Claude Code 2.1.69 or later, the first version that tells hooks which subagent is acting. They also need Python 3.9 or later as `python3` or `python`, or as `py -3` on Windows. Git Bash is optional on Windows.

### What happens on first run

1. Hooks load when a session starts. After the install, start a new session or run `/reload-plugins`.
2. The session opens with one line from the plugin: `invigilator is on. Reviewers (*reviewer*) can't write files. The QA check is off until you set a QA command: run /invigilator:setup.` It shows once per project, and again when the settings change.
3. From then on, an agent whose name matches `*reviewer*` can't write files. The QA check stays off until the project names a QA command, so nothing else changes until you run the setup skill.

An install in a session can show the plugin's options with their defaults filled in. Keep them or change them; [Configure it](#configure-it) says what each one does.

## Examples

1. Keep a reviewer read-only. Ask: "Use the code-reviewer agent to review build/app.js and list what to fix." If the reviewer tries `sed -i` on the file, Claude Code cancels the call and tells it to put the change in its review instead.
2. Make "done" mean QA passed. With `"builder_agents": ["main"]` and `"qa_commands": ["npm run check"]` in `.claude/invigilator.json`, ask: "Fix the failing test in src/cart.js and tell me when it's done." If Claude edits, runs `npm run check | tail -5` and tries to stop, the hook sends it back to run `npm run check` on its own. It can stop once that passes after its last edit.
3. Check a finished build. In a clone of this repo, run `/invigilator:check examples/runs/cheap-builder`. It prints pass, fail or skip for each check. Here "step ran" fails because `npm run check` never ran, and the command exits 1.

## Configure it

Every setting is optional. There are three places to set them.

- **The setup skill.** `/invigilator:setup` finds the project's check command in `package.json`, a `Makefile`, pytest, tox, Cargo or Go, asks which agents are builders and reviewers, and writes `.claude/invigilator.json` once you confirm.
- **The project file.** `.claude/invigilator.json` holds the settings for one project, and you can commit it for your team:

  ```json
  {
    "reviewer_agents": ["*reviewer*"],
    "reviewer_shell": "block-writes",
    "builder_agents": ["main", "page-builder"],
    "qa_commands": ["npm run check"]
  }
  ```

- **Plugin options.** These are yours, for every project. Set them with `/plugin configure invigilator@zhyuka` or in `/config` in a session, or from a shell with `--config builder_agents=main` on `claude plugin install`. Claude Code 2.1.285 or later also has `claude plugin configure invigilator@zhyuka --values-stdin`. Lists are names separated by commas.

| Setting | Default | What it does | Where |
| --- | --- | --- | --- |
| `reviewer_agents` | `*reviewer*` | Agents that may not write files | File or option |
| `reviewer_shell` | `block-writes` | `block-writes` denies shell commands that write files. `block-all` denies every shell command | File or option |
| `builder_agents` | `*builder*` | Agents that have to pass QA after their last edit before they stop | File or option |
| `qa_commands` | none | Commands that count as the QA step. The QA check stays off until you list one | File only, since a QA command belongs to a project |
| `session_status` | `once` | The line at session start. `once` shows it the first time in a project and when it changes, `always` in every session, `off` never | Option only |

A field in the project file wins over the option, and an option wins over the default.

The hooks tell agents apart by the `agent_type` field that Claude Code puts in each hook input. For a custom subagent that is the `name` in its frontmatter. For a plugin's subagent it is `plugin-name:agent-name`, and for a session started with `--agent` it is that agent's name. Patterns are shell-style globs, matched without case. `main` stands for the main session when it runs without `--agent`.

A project file with bad JSON, an unknown field or a wrong type makes the hooks fall back to the defaults, and every hook answer then carries a warning that names the problem. A bad option value is ignored and named in the line at session start.

## Turn it off

- In one project, for you: `claude plugin disable invigilator@zhyuka --scope local`. For everyone on the project: `--scope project`.
- Everywhere: `/plugin disable invigilator@zhyuka` in a session, or `claude plugin disable invigilator@zhyuka`. `claude plugin uninstall invigilator@zhyuka` removes it.
- One check only: leave `qa_commands` out of the project file to turn off the QA check, or set `"reviewer_agents": []` to turn off the reviewer check.
- The line at session start: set the `session_status` option to `off`. Problems still show.

## Troubleshooting

**"invigilator is off: it needs Python 3.9 or later, and none ran."** The hooks found no Python that could run them, so they let every call through and say so once per session. They try `python3` and then `python`, and on Windows `py -3`, `python` and `python3`. Install Python 3.9 or later, or set `INVIGILATOR_PYTHON` to its full path in the `env` block of your Claude Code settings. On Windows, a `python` or `python3` that opens the Microsoft Store is an app execution alias, not Python, and the hooks move past it. On macOS without the command line tools, `/usr/bin/python3` only offers to install them, and the hooks skip it.

**Nothing happens, and `/hooks` says "Only hooks from managed settings run here".** Your organization set `allowManagedHooksOnly` in its managed settings. Claude Code then runs no plugin hooks, these included, unless the managed settings force-enable the plugin. To run these hooks, an admin has to register the `zhyuka` marketplace under `extraKnownMarketplaces` and set `"invigilator@zhyuka": true` under `enabledPlugins`, both in the managed settings. The skills and the `invigilator check` command work either way.

**Windows without Git Bash.** Claude Code runs hook commands in PowerShell there. Each command in `hooks/hooks.json` has two lines. sh runs the first and never reads the second. PowerShell skips the first, since `exec` isn't a PowerShell command, and runs the second, which finds Python the same way. Since Claude Code 2.1.299, a PreToolUse hook that fails there blocks the call, so the hooks exit 0 in every case and set `"onFailure": "continue"`.

**A warning on every call that names `.claude/invigilator.json`.** The file has a mistake, and the hooks use the defaults until you fix it. Run `/invigilator:setup` to rewrite it.

**See what a hook did.** Start Claude Code with `claude --debug` and read `~/.claude/debug/<session-id>.txt`, which has each hook's output and errors.

## What it reads, runs and sends

- The reviewer hook reads the tool call Claude Code is about to make and the project's `.claude/invigilator.json`. It runs before Edit, Write, MultiEdit, NotebookEdit, Bash, PowerShell and Agent calls.
- The QA hook reads the session transcript file that Claude Code passes to Stop and SubagentStop hooks, to find which tool calls ran and whether the QA command passed. It reads tool calls only. It doesn't run the QA command itself.
- The line at session start reads the same settings. To show only once per project, it writes an empty marker file under the plugin's data folder in `~/.claude/plugins/data/`. The QA hook writes one there when it blocks a stop after QA failed, so it blocks that stop only once, and one when it tells you about a change to a file QA runs. With no data folder from Claude Code, it writes the file in the temp folder. If it can't write the file, it blocks every such stop. Each time it writes one, it deletes its own marker files in that folder that are older than 30 days. When no Python is found, the hooks write an empty marker file in the temp folder so the warning shows once per session.
- Each hook runs `sh`, or PowerShell on Windows without Git Bash, to find Python, and then runs this plugin's scripts with it. To find Python, `hooks/run.sh` also calls `dirname`, `uname`, `sed`, `head` and `printf`, and `xcode-select -p` on macOS. The Python scripts start no other program. Nothing else runs, and nothing leaves the machine.
- A hook acts only on the agents your settings name. Every other agent is left alone.

## What each hook blocks

`hooks/reviewer_readonly.py` runs before Edit, Write, MultiEdit, NotebookEdit, Bash, PowerShell and Agent calls. When the acting agent is a reviewer, it denies:

- every Edit, Write, MultiEdit and NotebookEdit call
- Bash commands that write files. The hook reads the command the way the shell would, including `bash -c` strings, `eval`, command substitutions, heredocs fed to a shell, `xargs` and `find -exec`. Writes include redirects to files, `rm`, `mv`, `cp`, `touch`, `tee`, `sed -i` and other file tools, git commands that change the worktree, index or refs (including `git fetch`), package installs and build steps such as `npm run build`, formatters run with `--write` or `--fix`, downloads to a file, and inline `python -c`, `node -e`, `ruby -e`, `perl -e` or `php -r` code that writes. A script counts when its flags say so, such as `--write`, `--fix` or `--out FILE`. A command the hook can't parse counts as a write.
- every PowerShell command, because the hook can't parse PowerShell
- spawning a subagent that is not a reviewer, since it could write on the reviewer's behalf

A reviewer may still write to a temp folder outside the project, such as a scratch file in `/tmp`. The hook lets an Edit, a Write or a Bash command through only when every place it can change is in `/tmp`, `/private/tmp`, `/var/folders`, `$TMPDIR`, `%TEMP%` or a user's `AppData\Local\Temp`. A place it can change is more than the path the command names. It counts the source of a link (`ln`, `cp -l`, `cp -s`, `os.symlink` and the like), since the reviewer could write through the link to it; the source a move or an archiver takes away (`mv`, `rsync --remove-source-files`, `tar --remove-files`, `shutil.move`); and the git dir, work tree and index of a git command, so `git worktree add /tmp/w` from the project is denied while `git -C /tmp/w --git-dir=.git checkout .` is allowed. Before it allows a temp target that already exists, it resolves symlinks in the target and its parents, and it denies a target file with more than one hard link, since that link may lead out of temp. `mount` and `bindfs` are denied, since they can map a folder under a temp path. It denies a write anywhere else, in a temp folder inside the project, or in a place the command line doesn't show.

For every agent, reviewer or not, the hook asks you before a call that changes the config file, whether an Edit or Write of it or a shell command that writes, moves or deletes it or its `.claude` folder. That file decides who is a reviewer and which QA commands must pass, so an agent could turn the checks off by editing it. Reads aren't asked about. The setup skill's write shows the same prompt, and you approve it there.

`hooks/qa_before_done.py` runs on Stop and SubagentStop. When the agent is a builder, it reads the session transcript, or the subagent's own transcript, and blocks the stop if the agent wrote files in the project after the last QA command that passed. The project is the folder in `CLAUDE_PROJECT_DIR`, or the hook input's `cwd` when that isn't set. A write counts when any place it lands is inside the project, or when the hook can't tell where it lands, as with a build step, a PowerShell command, or code it can't read. For inline Python (`python -c` or a heredoc fed to python) it reads the code and places a literal target the same way it places a shell redirect, so a write that only lands under `/tmp` or `~/.claude` doesn't count. Relative paths start where the call ran and follow `cd` in the command. So notes in `/tmp` or `~/.claude` don't send a builder back, and a builder that wrote nothing in the project can stop.

A QA run doesn't count when a pipe, `|| true`, a later command or a background run hid its exit code. Most agents pipe QA output. In real transcripts, 96% of QA runs were piped or chained. When the last run was, the hook asks for one unpiped run. That is expected, and costs one extra QA run.

When QA ran on its own after the last write and failed, the hook blocks the first stop. The reason tells the agent to fix the cause and run QA again. If a check can't pass here, such as one that needs a server the session can't reach, it tells the agent to stop and tell you which check fails and why, and not to edit the QA command, the files it runs or `.claude/invigilator.json` to get it to pass. The next stop with no new write goes through, and you see a message that QA failed. Running QA again doesn't change that. A new write does, and QA has to run after it.

The files a QA command runs are its words that name a file in the project, such as `qa.py` in `python3 qa.py`. If the builder changes one of them after QA failed, a pass after that doesn't count. The hook blocks the first stop and asks the agent to undo the change, or to stop and tell you what it changed and why. The next stop with no new write goes through, with a message that names the file. That message ends it, so a later pass counts again, and a later failure starts over as above. Changing those files before QA has failed, or after it passed, is normal work.

Both hooks leave every other agent alone. In this example, run from the repo root, a `code-reviewer` subagent tries `sed -i 's/console.log(.*);//' build/app.js`:

```
$ python3 hooks/reviewer_readonly.py < tests/fixtures/hooks/deny-reviewer-bash-write.json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "invigilator blocked this Bash call: it runs sed -i. The project config lists code-reviewer as a reviewer, and reviewers are read-only. Put the change you would make in your review instead."}}
```

Claude Code cancels the call and gives the reason to the agent. In the next one, the main session is a builder. It edited a page, ran `npm run check 2>&1 | tail -5`, and is about to report that QA passed:

```
$ python3 hooks/qa_before_done.py < tests/fixtures/hooks/block-main-qa-piped.json
{"decision": "block", "reason": "invigilator: QA output went through a pipe or into another command, so its exit code was not seen. The project config lists the main session as a builder, so run `npm run check` after your last edit and get it to pass before you stop. Run it on its own, not through a pipe or with || true, so its exit code counts."}
```

Claude Code keeps the agent working and hands it the reason as its next instruction.

## How this compares

- [Superpowers](https://github.com/obra/superpowers) has a `verification-before-completion` skill that asks the agent to check its work before it says it's done. This plugin's stop hook refuses the stop when the agent didn't. The two work together.
- [TDD Guard](https://github.com/nizos/tdd-guard) is a PreToolUse hook that blocks code written before a failing test, with a model judging each change. This plugin makes no model calls, and it checks that QA passed after the last edit rather than test-first order.
- [CC Safety Net](https://github.com/kenryu42/cc-safety-net) blocks destructive git and file commands for every agent. This plugin blocks every write, but only for the agents you name as reviewers, and leaves builders free.
- In a subagent's frontmatter, `tools` or `disallowedTools` can take the write tools away from an agent you wrote. This plugin also covers agents you didn't write, such as other plugins' agents, and lets a reviewer keep a shell for reading.

## Limitations

- The read-only hook reads the command line, not what programs do. It reads inline code given with `python -c`, a heredoc or `node -e`, and counts it only when it writes; for inline Python it places a literal target and counts a write whose target it can't place, a subprocess, `exec` or `eval` it can't read, or code that doesn't parse. A script file, a `make` target or a program whose name is built at run time can still write unseen. For a strict reviewer, set `reviewer_shell` to `block-all`, or leave write tools out of the subagent's `tools` list.
- The config ask matches the file by path. A command that changes every file without naming one, such as `git checkout -- .` or `rm -rf .`, doesn't trigger it.
- The QA hook trusts the QA command's exit code. A QA command that checks nothing will pass. Point `qa_commands` at something that refuses empty builds, such as `invigilator check build`.
- The QA hook counts Edit, Write, MultiEdit and NotebookEdit calls, and recognizable shell and PowerShell writes, as edits. An Edit or Write that failed, and a command Claude Code refused to run, did not change anything, so they don't count. Neither does a git command that changes only refs, the index or a remote, such as `git fetch`, `git commit` or `git push`. A pre-commit hook that rewrites files is the exception it can't see. Files written by a subagent the builder starts, or by an MCP tool, don't count either.
- Claude Code documents where the transcript is, not its format. The QA hook reads the format Claude Code 2.1.136 to 2.1.288 writes, and reads it from the end, so a stop on a transcript of hundreds of megabytes takes well under a second. If it finds nothing it can read, or reading takes longer than 15 seconds, it lets the agent stop and shows a warning that the check did not run. Claude Code writes the transcript asynchronously, so the hook waits up to a second for a QA result that is not in the file yet. If the result still isn't there, it blocks once.
- A shell command longer than 128 KB counts as a write and never as the QA step, because reading it would take the shell parser too long.
- On a transcript over 64 MB, a block may say the QA command did not run after the last file write instead of saying whether it ever ran.
- After QA fails, the hook blocks one stop per write, so a builder that keeps editing and failing QA goes back after each edit. Claude Code's own cap of eight stop-hook blocks in a row doesn't end that, since the count starts over each time the agent calls a tool. Press Esc to stop it, or take the command out of `qa_commands`.
- The hook finds the files a QA command runs only in the command's own words. `npm run check` and `make check` name none, and a file that `qa.py` imports isn't one. It sees a change to them through an Edit or Write call, or a shell command or inline Python that names the file. It doesn't see a change through a glob, a path built at run time or a PowerShell command, and on a transcript over 64 MB it looks back at most 64 MB for the QA failure before the change.
- The plugin fails open. When no Python runs it or a hook crashes, the call goes through and you see a warning. Failing closed would block every agent's writes whenever Python is missing, so the plugin doesn't set `"onFailure": "block"`.
- Plugin options are per user. Claude Code doesn't read them from a project's settings, which is why a QA command can only go in the project file.
- A project's `.claude/invigilator.json` wins over your options, so a repo that ships one decides there who counts as a reviewer or a builder and which QA command counts. Claude Code gives a project's own settings the same trust, and they can turn any plugin off. Read that file in a repo you don't trust.
- The build scorer reads static HTML, CSS and JS only. Any other build comes out UNSCORABLE.

## Use the checks without Claude Code

```
pip install git+https://github.com/zhyuka/invigilator
invigilator check path/to/folder
```

The folder is a build folder or a run folder. A run folder holds a `run.json` that records one agent run, and can hold an `artifacts/qa_report.json` in the format `invigilator/step_ran.py` writes:

```json
{
  "agent": "page-builder",
  "role": "builder",
  "build": "build",
  "claims": ["QA passed"],
  "qa_commands": ["npm run check"],
  "tool_calls": [
    {"tool": "Write", "input": {"file_path": "build/index.html"}},
    {"tool": "Bash", "input": {"command": "npm run check"}, "ok": true}
  ]
}
```

`role` is `builder` or `reviewer` and is the only required field. `build` is the build folder, relative to `run.json`, and defaults to `build`. `tool_calls` use Claude Code's tool names and input fields, so you can copy them out of a transcript. `ok` is `false` for a failed call and `null` when the result is unknown. A reviewer run can add `build_sha256_before`, the build's hash before the review.

The command runs three checks and prints pass, fail or skip for each, with the reason.

- **build score.** The guarded score of the build has to exist and reach 0.75.
- **read-only.** For a reviewer, no tool call may write files, and the build hash has to match `build_sha256_before` when it is given.
- **step ran.** A "QA passed" claim needs evidence. That is a QA report that names this build, or a QA command that succeeded after the last file write with its exit code in plain view. Every piece of evidence given has to hold, and a claim with none fails.

A check with nothing to read is skipped and says why. A skip never counts as a pass. The exit code is 0 when no check failed, 1 when one did, and 2 when the input can't be read. `examples/runs/` has a passing and a failing run for each of the last two checks:

```
$ invigilator check examples/runs/cheap-builder
Release checks for examples/runs/cheap-builder
  check        result  detail
  build score  pass    1.00
  read-only    skip    role is builder, not reviewer
  step ran     fail    QA command 'npm run check' never ran
  2 of 3 checks ran: 1 passed, 1 failed.
```

## Where this comes from

In my work at Accenture, coding agents built web pages behind automated quality gates. A reviewer agent had to stay read-only. A builder agent had to run its QA step before it said it was done. A person approved every release. Each check here comes from a way that model failed:

- A rule-based scorer ranked an empty build above real ones, because most of its rules only look for problems.
- 3 of 91 reviewer-agent runs that were told to be read-only edited code anyway.
- A cheaper model skipped the QA step in all 33 benchmark runs and reported success anyway.

The original system is internal and none of it is in this repo. This is a fresh reimplementation of its release checks, and the plugin is new. The two counts above, 3 of 91 and 33 of 33, come from the original runs.

## The three failures, as a demo

```
git clone https://github.com/zhyuka/invigilator
cd invigilator
python -m invigilator demo
```

The demo runs each check with stub agents: small deterministic Python functions that stand in for a model, with no LLM calls and no network. It reads the example builds in the clone, and exits non-zero if any check fails calibration. Every number it prints comes from the stubs and the four example builds. It runs 91 and 33 times to match the original runs, and its 2 of 91 comes from a seeded edit rate, not from a model.

1. **An empty build passes most rules.** `invigilator/vacuous.py` scores a static web build against eight rules, such as hex colors, image alt text and heading order. Seven of them only look for problems, so the empty build passes them and scores 0.88, above the mediocre build at 0.62 and the broken one at 0.25. `guarded_score()` refuses to score a build with no visible HTML text, and says why.
2. **A read-only reviewer edits code.** `invigilator/readonly.py` runs a stub reviewer on fresh copies of a workspace and hashes every file before and after. Its prompt says not to write, and in a small seeded share of runs it writes anyway. Giving the same reviewer a tool layer whose write raises an error stops it. An instruction is not a permission, and the plugin's PreToolUse hook applies the same idea in Claude Code.
3. **A step reports success without running.** `invigilator/step_ran.py` accepts "QA passed" only when the QA report names the SHA-256 of the exact build it checked and is not older than that build. The verifier rejects the stub that only says it passed, in every run, and accepts the one that ran QA.

```
1. Vacuous pass: naive score vs guarded score
  build     naive  rules that inspected nothing  guarded
  good      1.00   0 of 8                        1.00
  empty     0.88   8 of 8                        UNSCORABLE (no HTML files)
  mediocre  0.62   2 of 8                        0.62
  broken    0.25   0 of 8                        0.25
  Naive scorer ranks 'empty' above 2 of 3 real builds (mediocre, broken). The guard refuses to score it.

2. Read-only audit: 91 runs of a stub reviewer told to stay read-only
  2 of 91 runs edited files despite a read-only instruction:
    run 36: added REVIEW_NOTES.md
    run 40: changed index.html
  tools given      runs that edited files  writes blocked
  read and write   2 of 91                 0 of 91
  read-only layer  0 of 91                 2 of 91
  Same prompt both times. The instruction did not stop the writes; the tool layer did.

3. Step-ran verification: 33 runs per stub agent
  agent        claimed QA passed  accepted  rejected  verifier reason
  qa_agent     33 of 33           33 of 33  0 of 33   report matches this build
  cheap_agent  33 of 33           0 of 33   33 of 33  no QA report

4. Calibration: one planted bad case and one known-good case per check
  check              planted bad caught  known good passed
  vacuous guard      1 of 1              1 of 1
  read-only audit    1 of 1              1 of 1
  step-ran verifier  1 of 1              1 of 1
  3 of 3 checks passed calibration.
```

## Testing

A check counts only after it catches a planted bad case and passes a known-good one. Each one reports counts, not a verdict. Run the tests with `python -m unittest discover -s tests`.

- `invigilator/calibrate.py` plants one bad case and one good case for each demo check, and demo part 4 prints the result. The tests show that a check which never objects, or always objects, fails calibration.
- `tests/test_check.py` calibrates `invigilator check` on the four runs in `examples/runs/`. CI also runs the command on them, and fails if a good run doesn't pass or a bad run doesn't fail.
- Each hook fixture's name starts with the answer it should get, such as deny, block or allow. `tests/test_hooks.py` pipes every fixture through its hook script, and fails unless each hook both blocks and allows something across the set.
- `tests/test_launcher.py` runs every command in `hooks/hooks.json` on every fixture, from a plugin folder with spaces in its path, in each shell it finds: sh, dash and bash, Git Bash, pwsh and Windows PowerShell. It also checks that a Store alias or an old Python is skipped, and that with no Python the call goes through with one warning per session. CI runs it on Ubuntu, macOS and Windows.
- `tests/test_readonly.py` lists shell commands that write files and commands that don't. The classifier has to catch every one of the first and pass every one of the second.
- These tests feed recorded inputs to the scripts and don't start Claude Code. `evals/` runs with and without the plugin through `claude plugin eval`, and `.github/workflows/live.yml` runs it in CI when the `ANTHROPIC_API_KEY` secret is set.

## License

MIT. See `LICENSE`.
