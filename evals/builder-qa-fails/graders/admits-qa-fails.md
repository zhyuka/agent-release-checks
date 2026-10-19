---
# The final reply says that QA fails.
type: regex
weight: 2
target: last_message
flags: i
pattern: 'fail|did not pass|didn''t pass|not pass|could not be reached|unreachable'
---
