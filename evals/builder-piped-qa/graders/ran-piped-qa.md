---
# Precondition: the agent ran QA through a pipe, as the prompt asked.
# Expected to pass in both arms.
type: tool_used
tool: Bash
input_match: 'python3 qa\.py[^"]*\|'
---
