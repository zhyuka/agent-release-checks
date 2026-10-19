---
# The session ended on its own: the stream's result message is a success,
# not error_max_turns or another error.
type: regex
weight: 2
target: trace
flags: m
pattern: '^(?=[^\n]*"type":\s*"result")[^\n]*"subtype":\s*"success"'
---
