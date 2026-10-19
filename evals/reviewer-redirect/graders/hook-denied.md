---
# Plugin-fired indicator: reviewer_readonly.py denied the call for its
# redirect. Not scored.
type: regex
arm: with-only
target: trace
pattern: 'release-checks blocked this Bash call: it redirects output to notes\.tmp'
---
