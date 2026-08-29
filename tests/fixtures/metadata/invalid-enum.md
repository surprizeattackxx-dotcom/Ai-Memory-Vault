---
status: active
project: personal
type: reference
memory_status: fresh
source: explicit
confidence: high
---
# Invalid enum

`memory_status: fresh` — the value `fresh` is not in the normative vocabulary (`candidate | current | superseded | uncertain | deprecated`). This must FAIL the schema on the memory_status enum rule. (No such value exists anywhere in MEMORY_PROTOCOL.md; it is invented here on purpose as a negative fixture.)