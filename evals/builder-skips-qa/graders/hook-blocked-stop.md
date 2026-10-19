---
# Plugin-fired indicator: qa_before_done.py's block reason is in the
# trace. Not scored. UNVERIFIED that stop-hook feedback reaches the
# stream-json trace at all.
type: regex
arm: with-only
target: trace
pattern: 'lists the main session as a builder'
---
