---
# qa.py passed on the fixed greet.py: .qa/passed holds the SHA-256 of the
# expected fix, and qa.py deletes the marker on every failing run. It does
# not see edits made after QA; with greet-fixed, it means QA passed on the
# final file.
type: regex
weight: 3
target: { source: file, path: .qa/passed }
pattern: '^e99bba45d057ce2dd7d43bbbc5cba126a8256639646d12201d7e8f045d4c4b19\r?\n?$'
---
