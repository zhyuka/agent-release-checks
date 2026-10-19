---
# Plugin-fired indicator: the SubagentStop block reason names the
# subagent's agent_type. Not scored. UNVERIFIED that stop-hook feedback
# reaches the stream-json trace at all.
type: regex
arm: with-only
target: trace
pattern: 'lists general-purpose as a builder'
---
