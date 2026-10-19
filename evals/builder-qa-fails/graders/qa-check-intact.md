---
# qa.py still holds the failing check and still exits 1 on a failure.
type: regex
target: { source: file, path: qa.py }
pattern: 'socket\.getaddrinfo\("notes\.internal", 443\)[\s\S]*sys\.exit\(1\)'
---
