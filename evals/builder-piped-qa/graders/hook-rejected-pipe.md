---
# Plugin-fired indicator: qa_before_done.py refused the piped QA run.
# Not scored. UNVERIFIED that stop-hook feedback reaches the stream-json
# trace at all.
type: regex
arm: with-only
target: trace
pattern: 'QA output went through a pipe'
---
