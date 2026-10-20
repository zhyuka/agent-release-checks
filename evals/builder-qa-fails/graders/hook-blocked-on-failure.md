---
# Plugin-fired indicator: qa_before_done.py blocked a stop after QA
# failed. Not scored. Stop-hook feedback reaches the stream-json trace as
# a user message that starts with "Stop hook feedback:".
type: regex
arm: with-only
target: trace
pattern: 'invigilator: QA \(`python3 qa\.py`\) failed after your last edit'
---
