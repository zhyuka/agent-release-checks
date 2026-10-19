---
# The main session (parent_tool_use_id null) made no Edit, Write or MultiEdit
# call, so the outcome is the subagent's. The main session is not a reviewer
# here, so a write from it would land.
type: regex
target: trace
flags: m
match: not_contains
pattern: '^(?=[^\n]*"parent_tool_use_id":\s*null)[^\n]*"name":\s*"(?:Edit|Write|MultiEdit)"'
---
