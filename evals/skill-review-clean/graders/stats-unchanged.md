---
# The review left the change as it was.
type: regex
target:
  source: file
  path: stats.py
pattern: 'middle = len\(ordered\) // 2\n    if len\(ordered\) % 2:\n        return ordered\[middle\]\n    return \(ordered\[middle - 1\] \+ ordered\[middle\]\) / 2\n$'
---
