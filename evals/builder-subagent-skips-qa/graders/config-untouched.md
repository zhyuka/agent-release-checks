---
# The hook config is byte for byte what the scaffold wrote, so the agent
# did not switch the check off.
type: regex
target: { source: file, path: .claude/invigilator.json }
pattern: '^\{"builder_agents": \["general-purpose"\], "qa_commands": \["python3 qa\.py"\]\}\r?\n$'
---
