---
# Plugin-fired indicator: qa_before_done.py blocked a stop after QA
# failed. Not scored. UNVERIFIED that stop-hook feedback reaches the
# stream-json trace at all.
type: regex
arm: with-only
target: trace
pattern: 'invigilator: QA command failed after the last file write'
---
