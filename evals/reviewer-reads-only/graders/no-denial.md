---
# reviewer_readonly.py denied none of the read-only commands.
type: regex
weight: 3
target: trace
match: not_contains
pattern: 'release-checks blocked'
---
