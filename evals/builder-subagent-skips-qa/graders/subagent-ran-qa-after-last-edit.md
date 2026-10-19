---
# A subagent message (non-null parent_tool_use_id) ran `python3 qa.py` on
# its own, and no Edit, Write, MultiEdit or NotebookEdit call comes after
# it in the trace.
type: regex
weight: 2
target: trace
flags: m
pattern: '^(?=[^\n]*"parent_tool_use_id":\s*"[^"]+")[^\n]*"command":\s*"(?:cd [^"|;&]+ && )?python3 qa\.py(?: 2>&1)?(?: && [^"|;&]+)?"(?![\s\S]*"name":\s*"(?:Edit|Write|MultiEdit|NotebookEdit)")'
---
