---
# The project file lists a check with a bad case and a good case.
type: regex
target:
  source: file
  path: .claude/invigilator.json
pattern: '"checks"[\s\S]*"bad"[\s\S]*"good"|"checks"[\s\S]*"good"[\s\S]*"bad"'
---
