---
# invigilator prove wrote a record in which a bad case made the check exit non-zero.
# prove writes a record only when every bad case failed and every good case passed.
type: regex
target:
  source: file
  path: .claude/invigilator-proofs.json
weight: 2
pattern: '"must": "fail",\s*"sha256": "[0-9a-f]{64}",\s*"exit": [1-9]'
---
