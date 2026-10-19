# Agent release checks

[![tests](https://github.com/zhyuka/agent-release-checks/actions/workflows/tests.yml/badge.svg)](https://github.com/zhyuka/agent-release-checks/actions/workflows/tests.yml)

## What this is

Three release checks for software that coding agents build. One refuses to score a build that has nothing in it. One shows whether a reviewer agent wrote files. One accepts "QA passed" only with evidence that QA ran on the final build. You can run them as a demo with stub agents, as a command on your own build or run log, or as a Claude Code plugin whose hooks enforce two of them while agents work. I only trust a check after it catches a planted bad case and passes a known-good one, so each check here ships with both. Plain Python, no dependencies, tested on Python 3.9 to 3.13.

## Where this comes from

In my work at Accenture, coding agents built web pages behind automated quality gates. A reviewer agent had to stay read-only. A builder agent had to run its QA step before it said it was done. A person approved every release. Each check here comes from a way that model failed:

- A rule-based scorer ranked an empty build above real ones, because most of its rules only look for problems.
- 3 of 91 reviewer-agent runs that were told to be read-only edited code anyway.
- A cheaper model skipped the QA step in all 33 benchmark runs and reported success anyway.

The original system is internal and none of it is in this repo. This is a fresh reimplementation of its release checks. In the demo, each "agent" is a small deterministic Python function that stands in for a model. No LLM calls, no network. The Claude Code plugin is new too, and applies the same checks to real sessions through hooks.

The two counts above, 3 of 91 and 33 of 33, come from the original runs. Every number the demo prints comes from the stubs and the four example builds. The demo runs 91 and 33 times to match the original runs, and its 2 of 91 comes from a seeded edit rate, not from a model.

## Use it as a Claude Code plugin

The repo is a Claude Code plugin and a marketplace that lists it. In a Claude Code session:

```
/plugin marketplace add zhyuka/agent-release-checks
/plugin install agent-release-checks@zhyuka
```

From a shell, the same two steps are `claude plugin marketplace add zhyuka/agent-release-checks` and `claude plugin install agent-release-checks@zhyuka`. The hooks run `python3`, so Python 3.9 or later has to be on your PATH.

### Configure it

The hooks read `.claude/release-checks.json` in your project:

```json
{
  "reviewer_agents": ["*reviewer*"],
  "reviewer_shell": "block-writes",
  "builder_agents": ["main", "page-builder"],
  "qa_commands": ["npm run check"]
}
```

| Field | Default | What it does |
| --- | --- | --- |
| `reviewer_agents` | `["*reviewer*"]` | Agents that may not write files |
| `reviewer_shell` | `"block-writes"` | `block-writes` denies shell commands that write files. `block-all` denies every shell command |
| `builder_agents` | `["*builder*"]` | Agents that have to pass QA after their last edit before they stop |
| `qa_commands` | `[]` | Commands that count as the QA step. The QA hook stays off until you list one |

The hooks tell agents apart by the `agent_type` field that Claude Code puts in each hook input. For a custom subagent that is the `name` in its frontmatter. For a plugin's subagent it is `plugin-name:agent-name`, and for a session started with `--agent` it is that agent's name. Patterns are shell-style globs, matched without case. `main` stands for the main session when it runs without `--agent`.

With no config file the defaults apply. A file with bad JSON, an unknown field or a wrong type also falls back to the defaults, and every hook answer then carries a warning that names the problem.

### What each hook blocks

`hooks/reviewer_readonly.py` runs before Edit, Write, MultiEdit, NotebookEdit, Bash, PowerShell and Agent calls. When the acting agent is a reviewer, it denies:

- every Edit, Write, MultiEdit and NotebookEdit call
- Bash commands that write files, such as output redirects, `rm`, `mv`, `cp`, `touch`, `tee`, `sed -i`, git commands that change files, package installs and `find -delete`
- every PowerShell command, because the hook can't parse PowerShell
- spawning a subagent that is not a reviewer, since it could write on the reviewer's behalf

`hooks/qa_before_done.py` runs on Stop and SubagentStop. When the agent is a builder, it reads the session transcript, or the subagent's own transcript, and blocks the stop if the agent wrote files after the last QA command that passed. A QA run doesn't count when a pipe, `|| true`, a later command or a background run hid its exit code. A builder that wrote nothing can stop.

Both hooks leave every other agent alone.

### What a block looks like

The test fixtures are hook inputs shaped like the examples in Claude Code's hooks reference. Run these from the repo root. In the first, a `code-reviewer` subagent tries `sed -i 's/console.log(.*);//' build/app.js`:

```
$ python3 hooks/reviewer_readonly.py < tests/fixtures/hooks/deny-reviewer-bash-write.json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "release-checks blocked this Bash call: it runs sed -i. The project config lists code-reviewer as a reviewer, and reviewers are read-only. Put the change you would make in your review instead."}}
```

Claude Code cancels the call and gives the reason to the agent. In the second, the main session is a builder. It edited a page, ran `npm run check 2>&1 | tail -5`, and is about to report that QA passed:

```
$ python3 hooks/qa_before_done.py < tests/fixtures/hooks/block-main-qa-piped.json
{"decision": "block", "reason": "release-checks: QA output went through a pipe or into another command, so its exit code was not seen. The project config lists the main session as a builder, so run `npm run check` after your last edit and get it to pass before you stop. Run it on its own, not through a pipe or with || true, so its exit code counts."}
```

Claude Code keeps the agent working and hands it the reason as its next instruction.

The plugin also adds a `/agent-release-checks:release-check` skill that runs `release-checks check` on a folder and reports each result.

## Use it on your own build

```
pip install git+https://github.com/zhyuka/agent-release-checks
release-checks check path/to/folder
```

The folder is a build folder or a run folder. A run folder holds a `run.json` that records one agent run, and can hold an `artifacts/qa_report.json` in the format `release_checks/step_ran.py` writes:

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
$ release-checks check examples/runs/cheap-builder
Release checks for examples/runs/cheap-builder
  check        result  detail
  build score  pass    1.00
  read-only    skip    role is builder, not reviewer
  step ran     fail    QA command 'npm run check' never ran
  2 of 3 checks ran: 1 passed, 1 failed.
```

## The three failures, as a demo

```
git clone https://github.com/zhyuka/agent-release-checks
cd agent-release-checks
python -m release_checks demo
python -m unittest discover -s tests
```

The demo reads the example builds in the clone, and exits non-zero if any check fails calibration.

### 1. An empty build passes most rules

Most of the scorer's rules said "the build did not do X", and a build with no code never does X.

`release_checks/vacuous.py` scores a static web build against eight such rules, including hex colors, image alt text and heading order. The naive score is the share of rules passed. In the demo the empty build scores 0.88, above the mediocre build at 0.62 and the broken one at 0.25. All 8 rules inspected 0 items. Seven passed because they only look for problems and found none. The eighth, "a `<main>` landmark exists", is the only rule that asks for something to be there, so it fails, and 7 of 8 is 0.88.

`guarded_score()` refuses to score a build with no visible HTML text, and says why. `examples/builds/` holds the empty folder and three small sites. See demo part 1, or run `python -m unittest tests.test_vacuous`.

### 2. A read-only reviewer edits code

In the original runs the rule was in the prompt, and nothing enforced it.

`release_checks/readonly.py` runs a reviewer on fresh copies of a workspace and hashes every file before and after. It flags any run that edited, added or deleted a file. The stub reviewer's prompt says not to write, and in a small seeded share of runs it writes anyway. The fix gives the same reviewer a tool layer whose write raises an error. An instruction is not a permission. The plugin's PreToolUse hook does the same in Claude Code. The reviewer keeps its prompt, and the hook takes away the write.

See demo part 2, or run `python -m unittest tests.test_readonly`.

### 3. A step reports success without running

`release_checks/step_ran.py` accepts "QA passed" only with evidence. The QA report has to name the SHA-256 of the exact build it checked, and it can't be older than that build. One stub agent runs QA and writes the report. The cheaper stub just says it passed. The verifier rejects the second in every run and accepts the first.

See demo part 3, or run `python -m unittest tests.test_step_ran`.

### Demo output

Counts in parts 2 and 3 come from seeded stubs, not the original runs.

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

## How the checks are calibrated

A check counts only after it catches a planted bad case and passes a known-good one. Each one reports counts, not a verdict.

- `release_checks/calibrate.py` plants one bad case and one good case for each demo check, and demo part 4 prints the result. The tests show that a check which never objects, or always objects, fails calibration.
- `tests/test_check.py` calibrates `release-checks check` on the four runs in `examples/runs/`. CI also runs the command on them, and fails if a good run doesn't pass or a bad run doesn't fail.
- Each hook fixture's name starts with the answer it should get: deny, block or allow. `tests/test_hooks.py` pipes every fixture through its hook script, and fails unless each hook both blocks and allows something across the set.
- `tests/test_readonly.py` lists shell commands that write files and commands that don't. The classifier has to catch every one of the first and pass every one of the second.

## Limitations

- The read-only hook reads the command line, not what programs do. `python -c`, a script or a `make` target can write files without the hook seeing it. For a strict reviewer, set `reviewer_shell` to `block-all`, or leave write tools out of the subagent's `tools` list. The hook is the backstop for agents that have them.
- The QA hook trusts the QA command's exit code. A QA command that checks nothing will pass. Point `qa_commands` at something that refuses empty builds, such as `release-checks check build`.
- The QA hook counts Edit and Write calls and recognizable shell writes as edits. A build step like `npm run build` writes files without counting as one.
- Claude Code documents where the transcript is, not its format. The QA hook reads the format Claude Code writes today. If it finds nothing it can read, it lets the agent stop and shows a warning that the check did not run. Claude Code also writes the transcript asynchronously, so a QA result from just before a stop can be missing, and the hook then blocks once.
- Claude Code ends a turn after stop hooks block eight times in a row. `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` raises that cap. A builder that never runs QA can still stop once it is reached, and each refusal stays in the transcript.
- If `python3` is missing or a hook script crashes, Claude Code treats that as a non-blocking error and lets the call through. Setting `onFailure` to `block` would fail closed, but it needs Claude Code v2.1.295 or later and would block every agent's writes whenever Python is missing, so the plugin leaves it unset.
- Managed settings with `allowManagedHooksOnly` turn off plugin hooks, these included, unless the organization force-enables the plugin.
- The build scorer reads static HTML, CSS and JS only. Any other build comes out UNSCORABLE.
- The hook tests feed recorded inputs to the scripts. They don't start Claude Code.

## License

MIT. See `LICENSE`.
