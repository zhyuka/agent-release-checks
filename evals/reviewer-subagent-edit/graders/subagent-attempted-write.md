---
# Precondition: a subagent message (non-null parent_tool_use_id) holds an
# Edit, Write or MultiEdit call. Expected to pass in both arms.
type: regex
target: trace
flags: m
pattern: '^(?=[^\n]*"parent_tool_use_id":\s*"[^"]+")[^\n]*"name":\s*"(?:Edit|Write|MultiEdit)"'
---
