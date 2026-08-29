---
manifest_type: health-check
scope: level1            # level1 | level2 | level3
scope_target: ""         # level2 only: folder / Job / memory class / note the run was scoped to
start_time: 2026-08-29T00:00:00
completion_state: pass   # pass | partial | blocked — must match the report's stated state
expected_files: 9        # true .md count in scope (the audit computes this independently)
inspected_count: 5
skipped_count: 0
excluded_count: 4
checks_completed:
  - structure
  - frontmatter
  - wikilinks
  - metadata
  - upgrade-state
checks_not_completed: []
blocked_dependencies: [] # what was unavailable to the CHECK itself (vault unreadable, root index unreadable, disputed vocabulary under an incompatible vault); empty unless the run was blocked
scan_interrupted: false  # true when the run stopped early (context/token/tool budget, told to stop) — forces partial
---

<!--
Inspection Manifest — the coverage record of one Memory Health Check run (see
MEMORY_PROTOCOL.md HEALTH_CHECK and templates/JOB-MEMORY-HEALTH.md).

The manifest is what makes a coverage claim mechanically checkable: it states,
file-by-file, what was inspected, what was skipped and why, what was excluded and
why, what was found, and which checks completed. A deterministic, LLM-free audit
(tools/audit_health_coverage.py in the ai-memory-vault repo) reconciles this
manifest against the vault's true .md inventory — it does NOT take the claims
here on faith.

Rules that keep the arithmetic honest:
- Keep this file OUTSIDE the vault scope it counts (beside the vault, or in the
  folder you ran the check from). If it lives inside the vault, it counts itself.
- `completion_state` must equal the report's stated state. A run that did not
  cover everything is `partial`, never `pass` — an incomplete run recorded as
  `pass` is rejected outright (HC-FALSE-PASS).
- A skipped file forces `partial`. Every skipped file needs a reason.
- Exclusions may only name structural files (VAULT-INDEX.md, Active
  Priorities.md, the daily-note template, Resources/MEMORY_PROTOCOL.md, anything
  under templates/, and anything tagged memory_role: structural). Exemption is
  from index/orphan expectations, not from frontmatter validity.
- Every deterministic finding the scans prove must appear in ## Findings with
  `- [error]` or `- [flagged]` severity. Omitting one is HC-FINDING-MISSED.
- Level 3 additionally requires the cross-note enumeration sections at the
  bottom to list every applicable file (see the section headers).
--> 

# Health Check Manifest

## Scope
Level 1 (structural) over the full vault, as of `start_time`.

## Exclusions
Structural files — exempt from index/orphan expectations; listed so the
arithmetic `inspected + skipped + excluded == expected` adds up. Their frontmatter
validity still counts (checked via the frontmatter/metadata checks):
- [x] VAULT-INDEX.md
- [x] Active Priorities.md
- [x] 01 - Daily Notes/Daily Note Template.md
- [x] 09 - Resources/MEMORY_PROTOCOL.md

## Inspected files
- [x] 02 - Projects/Projects.md
- [x] 02 - Projects/note-one.md
- [x] 07 - Personal/Personal.md
- [x] 07 - Personal/Wren.md
- [x] 07 - Personal/Milo.md

## Skipped files (reason required — each skipped file forces partial)
_None_ — or one line per skipped file:
`- [x] path/to/note.md — reason it could not be inspected`

## Findings
One line per finding: `- [severity] ID — path: message`
severity ∈ `info` | `warning` | `error` | `flagged`. Every `error` and `flagged`
finding the scans can prove must be listed here in full:
- [info] STATE-CURRENT — <vault>: all required protocol surfaces synchronized

## Level evidence
State what the level's evidence requirement was and whether it was met. The audit
enforces the file-level facts itself; this section is the agent's own attestation.
- [x] partition complete (inspected ∪ skipped ∪ excluded == expected, nothing double-counted)
- [x] zero skipped files
- [x] every exclusion is a structural file
- [x] all Level 1 checks recorded complete

## Notes
What the agent actually judged that a mechanical scan cannot: conflicts it
classified, candidates it surfaced, fixes it applied (only obviously-safe
structural fixes), anything the person should decide.

<!-- Level 3 only — delete these three sections on Level 1/2 runs.
     The audit verifies each list covers the complete applicable corpus:

## Lifecycle coverage
Every note carrying lifecycle metadata (memory_status / source / confidence /
confidence_basis / first_observed / last_confirmed / supersedes / superseded_by),
as actually cross-checked for consistency:
- [x] 02 - Projects/note-two.md

## Duplicate coverage
Every member of a latent duplicate cluster (identical normalized bodies), as
actually compared:
- [x] 07 - Personal/Wren.md
- [x] 07 - Personal/Milo.md

## Supersession coverage
Both endpoints of every supersedes / superseded_by edge, as actually verified
both directions:
- [x] 02 - Projects/note-two.md
- [x] 02 - Projects/legacy-note.md
-->