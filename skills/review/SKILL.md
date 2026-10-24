---
name: review
description: Have the current change reviewed by a fresh reviewer that didn't write it and can't edit files, then report what it found. Use when asked to review a change, get a second opinion, or check work before it's called done.
argument-hint: "[files, commit range or what to review]"
---

A review counts when the reviewer didn't write the work and can't change it. You wrote this change, or saw the reasoning behind it, so don't review it yourself.

1. Collect what to review: what the change is meant to do, in the user's words, and the change itself, from $ARGUMENTS or from `git diff`, `git diff --staged` or a commit range. Leave out your own reasoning and any claim that it works. The reviewer should judge the work, not your account of it.

2. If you can start a subagent, start the `invigilator:reviewer` agent. The plugin keeps agents whose names match `*reviewer*` from writing files. Give it only:
   - what the change is meant to do, in the user's words
   - how to see the change: the diff command, the commit range or the file paths
   - "List the defects you find, most serious first, each with file:line, what's wrong and how you know. Say what you checked and what you couldn't."

3. If you can't start a subagent, don't review it in this chat. Write that prompt in one code block, with the diff pasted in when it's under 300 lines, and ask the user to open a new chat, paste it there, and bring the answer back.

4. Report the findings as the reviewer gave them. Don't drop or soften one because you disagree; put your note beside it. Don't fix them unless the user asks.
