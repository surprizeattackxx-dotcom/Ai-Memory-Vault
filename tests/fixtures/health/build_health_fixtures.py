#!/usr/bin/env python3
"""Build deterministic fixtures for the Memory Health Check coverage audit.

Each fixture is (a) a small vault under `vaults/<id>/` and (b) an Inspection
Manifest under `manifests/<id>.md` sitting OUTSIDE the vault it counts, so the
manifest can never include itself in the .md inventory. A shared boot file
lives at `_boot/CLAUDE.md` (outside any vault), reused from the vault-fixtures
pool's own `_boot`.

The fixture set covers every coverage-gate the auditor enforces, including the
control that an incomplete scan recorded as `pass` is rejected outright
(h08-clean, HC-FALSE-PASS — that is the rule this whole layer exists to hold).

Expected results per fixture are declared in `manifest.yaml`, consumed by
tests/run_health_coverage.py.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
OUT = BASE / "vaults"
MANIFESTS = BASE / "manifests"
BOOT_DIR = BASE / "_boot"

sys.path.insert(0, str(BASE.parent / "vaults"))
import build_fixtures as bf  # noqa: E402  (for base_files and note constants)

PROJECTS_INDEX = """---
status: active
project: meta
type: index
---
# Projects

## Notes

- [[note-one]] — a sample project note.
- [[note-two]] — the current side of a supersession pair.
- [[legacy-note]] — the superseded side of that pair.
"""

NOTE_TWO = """---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[legacy-note]]"
---
# Note Two

The current fact; replaces legacy-note.
"""

LEGACY_NOTE = """---
status: active
project: personal
type: reference
memory_status: superseded
superseded_by: "[[note-two]]"
---
# Legacy Note

The old fact, now superseded.
"""

# Wren and Bash get IDENTICAL bodies so the rich fixture carries a latent
# duplicate cluster (DUP-LEXICAL-BODY info) for Level 3 duplicate coverage.
MELENA_NOTE = bf.MELENA_NOTE
BASH_NOTE = MELENA_NOTE


def base_files() -> dict:
    return bf.base_files()


def rich_files() -> dict:
    files = bf.base_files()
    files["02 - Projects/Projects.md"] = PROJECTS_INDEX
    files["02 - Projects/note-two.md"] = NOTE_TWO
    files["02 - Projects/legacy-note.md"] = LEGACY_NOTE
    files["07 - Personal/Wren.md"] = MELENA_NOTE
    files["07 - Personal/Milo.md"] = BASH_NOTE
    return files


STRUCTURAL = [
    "VAULT-INDEX.md",
    "Active Priorities.md",
    "01 - Daily Notes/Daily Note Template.md",
    "09 - Resources/MEMORY_PROTOCOL.md",
]

L1_CHECKS = ["structure", "frontmatter", "wikilinks", "metadata", "upgrade-state"]
L3_CHECKS = L1_CHECKS + ["scope-coverage", "duplicates", "conflicts", "lifecycle-consistency"]

START = "2026-08-29T00:00:00"


def render_manifest(params: dict) -> str:
    fm = [
        "---",
        "manifest_type: health-check",
        "scope: %s          # level1 | level2 | level3" % params["scope"],
        'scope_target: "%s"         # level2 only' % params.get("scope_target", ""),
        "start_time: %s" % START,
        "completion_state: %s   # pass | partial | blocked" % params["completion_state"],
        "expected_files: %s" % params.get(
            "expected", len(params["inspected"]) + len(params.get("skipped", {})) + len(params["excluded"])),
        "inspected_count: %d" % len(params["inspected"]),
        "skipped_count: %d" % len(params.get("skipped", {})),
        "excluded_count: %d" % len(params["excluded"]),
        "checks_completed:",
    ]
    for c in params.get("checks_completed", L1_CHECKS):
        fm.append("  - %s" % c)
    fm.append("checks_not_completed: []")
    deps = params.get("blocked_dependencies", [])
    fm.append("blocked_dependencies: %s" % (str(deps) if deps else "[] # what was unavailable to the CHECK itself"))
    fm.append("scan_interrupted: %s  # true forces partial" % str(params.get("scan_interrupted", False)).lower())
    fm.append("---")
    lines = fm + ["", "# Health Check Manifest", "", "## Scope", params.get("scope_text", ""), ""]
    lines.append("## Exclusions")
    lines.append("Structural files — exempt from index/orphan expectations:")
    if params["excluded"]:
        for rel in params["excluded"]:
            lines.append("- [x] %s" % rel)
    lines.append("")
    lines.append("## Inspected files")
    for rel in params["inspected"]:
        lines.append("- [x] %s" % rel)
    lines.append("")
    lines.append("## Skipped files")
    for rel, reason in params.get("skipped", {}).items():
        lines.append("- [x] %s \u2014 %s" % (rel, reason))
    lines.append("")
    lines.append("## Findings")
    for f in params.get("findings", []):
        lines.append(f)
    lines.append("")
    lines.append("## Level evidence")
    lines.append(params.get("level_evidence", "- [x] partition complete; every file accounted for"))
    lines.append("")
    lines.append("## Notes")
    lines.append(params.get("notes", "No non-mechanical judgment was required this run."))
    lines.append("")
    for section, entries in params.get("l3_sections", {}).items():
        lines.append("## %s" % section)
        for rel in entries:
            lines.append("- [x] %s" % rel)
        lines.append("")
    return "\n".join(lines)


MALFORMED_H09 = """---
manifest_type: health-check
scope: [level1
completion_state: pass
expected_files: 9
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
blocked_dependencies: []
scan_interrupted: false
---

# Health Check Manifest

An agent that filled in the template wrongly (unclosed flow sequence).
"""


def fixture_h01_clean():
    files = base_files()
    all_9 = sorted(set(files))
    p = {
        "scope": "level1",
        "completion_state": "pass",
        "inspected": all_9,
        "skipped": {},
        "excluded": [],
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault; every file inspected.",
        "level_evidence": "- [x] partition complete (inspected == expected, 9/9) with zero skips and zero exclusions",
    }
    return files, render_manifest(p), p


def fixture_h02_l3_duplicates_omitted():
    files = rich_files()
    p = {
        "scope": "level3",
        "completion_state": "partial",
        "inspected": sorted(
            set(files) - set(STRUCTURAL)
        ),
        "skipped": {},
        "excluded": STRUCTURAL,
        "checks_completed": L3_CHECKS,
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 3 (lifecycle + duplicates + conflicts) over the full vault, 11 files.",
        "level_evidence": (
            "- [x] partition complete; lifecycle/supersession enumerated\n"
            "- [ ] duplicate coverage omitted one cluster member (see ## Duplicate coverage)"
        ),
        "notes": "Duplicate coverage below lists only one member of the Wren/Bash cluster; "
                 "the run moved on without comparing the other.",
        "l3_sections": {
            "Lifecycle coverage": ["02 - Projects/note-two.md", "02 - Projects/legacy-note.md"],
            "Duplicate coverage": ["07 - Personal/Wren.md"],
            "Supersession coverage": ["02 - Projects/note-two.md", "02 - Projects/legacy-note.md"],
        },
    }
    return files, render_manifest(p), p


def fixture_h03_skipped():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "partial",
        "expected": 9,
        "inspected": ["02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md", "07 - Personal/Milo.md"],
        "skipped": {"02 - Projects/Projects.md": "read budget exhausted before the run completed"},
        "excluded": STRUCTURAL,
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault.",
        "level_evidence": "- [x] partition complete; one file skipped with a recorded reason (forced partial)",
    }
    return files, render_manifest(p), p


def fixture_h04_exclusions_only():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "pass",
        "inspected": ["02 - Projects/Projects.md", "02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md", "07 - Personal/Milo.md"],
        "skipped": {},
        "excluded": STRUCTURAL,
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault; structural files excluded from index expectations.",
        "level_evidence": "- [x] partition complete (5 inspected + 4 excluded); every exclusion is structural",
    }
    return files, render_manifest(p), p


def fixture_h05_interrupted():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "partial",
        "expected": 9,
        "inspected": ["02 - Projects/Projects.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md"],
        "skipped": {},
        "excluded": [],
        "scan_interrupted": True,
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: state detection completed before the interruption"],
        "scope_text": "Level 1 (structural) over the full vault; interrupted by a read budget.",
        "level_evidence": "- [ ] incomplete: scan_interrupted true; six files never examined",
    }
    return files, render_manifest(p), p


def fixture_h06_gap():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "partial",
        "expected": 9,
        "inspected": ["02 - Projects/Projects.md", "02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md"],
        "skipped": {},
        "excluded": STRUCTURAL,
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault.",
        "level_evidence": "- [ ] incomplete: Milo.md never accounted for",
        "notes": "One file (Milo.md) was missed entirely; the run stopped before covering it.",
    }
    return files, render_manifest(p), p


def fixture_h07_blocked():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "blocked",
        "inspected": ["02 - Projects/Projects.md", "02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md", "07 - Personal/Milo.md"],
        "skipped": {},
        "excluded": STRUCTURAL,
        "blocked_dependencies": ["root index unreadable"],
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: already recorded before the dependency failed"],
        "scope_text": "Level 1 (structural) over the full vault; coverage was complete but the run was blocked.",
        "level_evidence": "- [x] partition complete; the run records a blocked dependency",
    }
    return files, render_manifest(p), p


def fixture_h08_clean_false_pass():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "pass",
        "expected": 9,
        "inspected": ["02 - Projects/Projects.md", "02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Wren.md", "07 - Personal/Milo.md"],
        "skipped": {},
        "excluded": [],
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault.",
        "level_evidence": "- [x] partition complete (5 inspected vs 9 expected)",
        "notes": "This run claims pass but only covered five files. It is the control that must be caught.",
    }
    return files, render_manifest(p), p


def fixture_h09_malformed():
    files = base_files()
    return files, MALFORMED_H09, {"scope": "level1", "completion_state": "pass"}


def fixture_h10_finding_missed():
    files = rich_files()
    files["02 - Projects/legacy-note.md"] = files["02 - Projects/legacy-note.md"].split("---", 2)[-1].lstrip("\n")
    p = {
        "scope": "level3",
        "completion_state": "partial",
        "inspected": sorted(set(files) - set(STRUCTURAL)),
        "skipped": {},
        "excluded": STRUCTURAL,
        "checks_completed": L3_CHECKS,
        # legacy-note.md lost its frontmatter (FM-MISSING, an error) but the
        # manifest records only the warning it saw — the error is deliberately
        # omitted so the auditor's findings-reconciliation must catch it.
        "findings": [
            "- [warning] LC-PAIR-UNRECIPROCATED \u2014 02 - Projects/note-two.md: note-two -> legacy-note without superseded_by back-reference (pair fields meant to be set together)",
            "- [info] DUP-LEXICAL-BODY \u2014 07 - Personal/Wren.md: identical normalized bodies: 07 - Personal/Milo.md",
            "- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized",
        ],
        "scope_text": "Level 3 (lifecycle + duplicates + conflicts) over the full vault, 11 files.",
        "level_evidence": "- [x] partition complete; lifecycle/supersession/duplicate corpora enumerated in full",
        "notes": "One frontmatter-stripped note (legacy-note.md) was misread as clean while recording the warning;",
        "l3_sections": {
            "Lifecycle coverage": ["02 - Projects/note-two.md"],
            "Duplicate coverage": ["07 - Personal/Wren.md", "07 - Personal/Milo.md"],
            "Supersession coverage": ["02 - Projects/note-two.md", "02 - Projects/legacy-note.md"],
        },
    }
    return files, render_manifest(p), p


def fixture_h11_exclusion_invalid():
    files = base_files()
    p = {
        "scope": "level1",
        "completion_state": "partial",
        "inspected": ["02 - Projects/Projects.md", "02 - Projects/note-one.md", "07 - Personal/Personal.md", "07 - Personal/Milo.md"],
        "skipped": {},
        "excluded": STRUCTURAL + ["07 - Personal/Wren.md"],
        "findings": ["- [info] STATE-CURRENT \u2014 <vault>: all required protocol surfaces synchronized"],
        "scope_text": "Level 1 (structural) over the full vault.",
        "level_evidence": "- [x] partition complete; one exclusion is not structural (flagged)",
        "notes": "Wren.md excluded to dodge an inspection; the audit must reject the exclusion.",
    }
    return files, render_manifest(p), p


FIXTURES = [
    ("h01-clean", fixture_h01_clean),
    ("h02-l3-duplicates-omitted", fixture_h02_l3_duplicates_omitted),
    ("h03-skipped", fixture_h03_skipped),
    ("h04-exclusions-only", fixture_h04_exclusions_only),
    ("h05-interrupted", fixture_h05_interrupted),
    ("h06-gap", fixture_h06_gap),
    ("h07-blocked", fixture_h07_blocked),
    ("h08-clean-false-pass", fixture_h08_clean_false_pass),
    ("h09-malformed", fixture_h09_malformed),
    ("h10-finding-missed", fixture_h10_finding_missed),
    ("h11-exclusion-invalid", fixture_h11_exclusion_invalid),
]


def build_all() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    BOOT_DIR.mkdir(parents=True, exist_ok=True)
    (BOOT_DIR / "CLAUDE.md").write_text(bf.BOOT_SRC.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    for name, fn in FIXTURES:
        files, manifest_text, _params = fn()
        vault_dir = OUT / name
        for path, content in files.items():
            target = vault_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        (MANIFESTS / (name + ".md")).write_text(manifest_text, encoding="utf-8", newline="\n")
    return len(FIXTURES)


if __name__ == "__main__":
    n = build_all()
    print("built %d health fixtures under %s" % (n, OUT))