---
manifest_type: health-check
scope: level1          # level1 | level2 | level3
scope_target: ""         # level2 only
start_time: 2026-08-29T00:00:00
completion_state: partial   # pass | partial | blocked
expected_files: 9
inspected_count: 3
skipped_count: 0
excluded_count: 0
checks_completed:
  - structure
  - frontmatter
  - wikilinks
  - metadata
  - upgrade-state
checks_not_completed: []
blocked_dependencies: [] # what was unavailable to the CHECK itself
scan_interrupted: true  # true forces partial
---

# Health Check Manifest

## Scope
Level 1 (structural) over the full vault; interrupted by a read budget.

## Exclusions
Structural files — exempt from index/orphan expectations:

## Inspected files
- [x] 02 - Projects/Projects.md
- [x] 07 - Personal/Personal.md
- [x] 07 - Personal/Wren.md

## Skipped files

## Findings
- [info] STATE-CURRENT — <vault>: state detection completed before the interruption

## Level evidence
- [ ] incomplete: scan_interrupted true; six files never examined

## Notes
No non-mechanical judgment was required this run.
