---
# Precondition: the agent did try a file-tool write, so the case tested the
# hook rather than an agent that never tried. Expected to pass in both arms.
type: regex
target: trace
pattern: '"name":\s*"(?:Edit|Write|MultiEdit)"'
---
