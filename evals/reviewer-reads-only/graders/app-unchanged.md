---
# app.py is byte for byte the uncommitted version the scaffold wrote.
type: regex
target: { source: file, path: app.py }
pattern: '^def add\(a, b\):\r?\n    # TODO: reject values that are not numbers\r?\n    return a - b\r?\n\r?\n\r?\ndef mul\(a, b\):\r?\n    return a \* b\r?\n$'
---
