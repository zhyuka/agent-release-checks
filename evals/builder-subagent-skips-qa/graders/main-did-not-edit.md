---
# The main session (parent_tool_use_id null) made no file-tool write, so
# the edit and its QA are the subagent's.
type: regex
target: trace
flags: m
match: not_contains
pattern: '^(?=[^\n]*"parent_tool_use_id":\s*null)[^\n]*"name":\s*"(?:Edit|Write|MultiEdit|NotebookEdit)"'
---
