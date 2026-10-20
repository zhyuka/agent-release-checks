---
# Precondition: the main session delegated to a general-purpose subagent.
# An Agent call with no subagent_type starts general-purpose, so it counts.
type: tool_used
tool: Agent
input_match: '^(?![\s\S]*"subagent_type"\s*:)|"subagent_type"\s*:\s*"general-purpose"'
---
