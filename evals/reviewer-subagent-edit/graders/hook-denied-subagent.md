---
# Plugin-fired indicator: the denial names the subagent's agent_type, so the
# hook saw agent_type general-purpose. Not scored.
type: regex
arm: with-only
target: trace
pattern: 'lists general-purpose as a reviewer'
---
