---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[superseded]]"
source: explicit
confidence: high
first_observed: 2026-08-29
last_confirmed: 2026-08-29
---
# Superseding note (new side of a pair)

The new side of the same true replacement: it carries `supersedes` pointing at the note it replaced. Schema-valid on its own. Cross-note invariant (the target must carry a matching `superseded_by` back at this note) is a vault-level scan, not a single-note check — this fixture's counterpart `superseded.md` satisfies it.