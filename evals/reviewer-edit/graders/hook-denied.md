---
# Plugin-fired indicator: the denial text from reviewer_readonly.py reached
# the agent. Not scored.
type: regex
arm: with-only
target: trace
pattern: 'release-checks blocked this (?:Edit|Write|MultiEdit) call'
---
