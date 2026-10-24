---
type: llm
weight: 2
focus: last_message
---
The new median() returns `ordered[len(ordered) // 2]`, which is wrong for a list with an even number of values: median([1, 2, 3, 4]) gives 3 instead of 2.5.

PASS if the reply says median() is wrong, or doesn't average the two middle values, for an even number of values.
FAIL if the reply doesn't mention this problem, or says median() is correct.
