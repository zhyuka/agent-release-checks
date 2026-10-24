---
# The reviewer wasn't told the change works: no Agent prompt repeats the user's
# "came out right". A main session that reviews in place also passes this.
type: regex
target: trace
match: not_contains
pattern: '"name":\s*"Agent",\s*"input":\s*\{[^\n]*came out right'
---
