---
status: active
project: personal
type: reference
memory_status: current
source: observed
confidence: medium
last_confirmed: 2026-13-45
---
# Invalid date

`last_confirmed: 2026-13-45` — digit-shape matches `\d{4}-\d{2}-\d{2}` but is calendar-invalid (month 13, day 45). This must FAIL on format validation (`format: date`). A validator that does not enable format checking will not catch this — enabling format: date is a determinism requirement, not a choice.