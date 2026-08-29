---
status: active
project: personal
type: reference
memory_status: current
superseded_by: "[[superseding]]"
source: observed
confidence: high
---
# Invalid lifecycle relationship

Carries `superseded_by` while asserting `memory_status: current`. Per MEMORY_PROTOCOL.md, a note carrying `superseded_by` asserts the fact it records has been REPLACED — it must carry `memory_status: superseded` instead. The two claims flatly contradict, so this must FAIL the superseded_by -> superseded lifecycle rule.