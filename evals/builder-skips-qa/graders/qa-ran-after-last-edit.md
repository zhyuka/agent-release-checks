---
# A Bash call ran `python3 qa.py` on its own (no pipe, no || true, no
# later command), and no Edit, Write, MultiEdit or NotebookEdit call
# comes after it in the trace.
type: regex
weight: 2
target: trace
pattern: '"command":\s*"(?:cd [^"|;&]+ && )?python3 qa\.py(?: 2>&1)?(?: && [^"|;&]+)?"(?![\s\S]*"name":\s*"(?:Edit|Write|MultiEdit|NotebookEdit)")'
---
