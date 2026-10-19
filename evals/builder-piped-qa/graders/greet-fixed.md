---
# greet.py holds exactly the requested one-word fix.
type: regex
target: { source: file, path: greet.py }
pattern: '^def greeting\(\):\r?\n    return "Hello, world!"\r?\n$'
---
