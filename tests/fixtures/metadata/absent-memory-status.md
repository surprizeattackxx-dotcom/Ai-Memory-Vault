---
status: active
project: personal
type: reference
source: explicit
confidence: high
confidence_basis: "Fresh explicit statement, independently reconfirmed twice."
first_observed: 2026-08-01
last_confirmed: 2026-08-29
stability: stable
---
# Absent memory_status (must NOT read as current)

A fully tracked, fact-bearing note with provenance and confidence set — but NO `memory_status` field. Schema-valid: absence of the field is not an error. Semantically it is untracked/legacy, NEVER equivalent to `memory_status: current` (adversarial-suite M1). A consumer that resolves this note to the current tier on the strength of source/confidence alone has violated the protocol.