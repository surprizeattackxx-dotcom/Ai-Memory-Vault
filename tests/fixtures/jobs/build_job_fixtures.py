#!/usr/bin/env python3
"""Build deterministic vault fixtures for tools/audit_job_dependencies.py.

Idempotent: wipes vaults/ under this folder and rebuilds. Each fixture is one
small vault, built from tests/fixtures/vaults/build_fixtures.py's base_files()
(so state detection, parity, and boot behave exactly like the main vault
fixture set), plus a `09 - Resources/Jobs/` folder isolating exactly the one
dependency scenario under test from unrelated schema/lifecycle noise.

Shares the boot file at tests/fixtures/vaults/_boot/CLAUDE.md — no separate
boot copy, so there is nothing to drift out of sync.

Expected results are declared in manifest.yaml, consumed by
tests/run_job_dependency_audit.py. Run date for every fixture is fixed at
2026-08-30 (--today) so results never depend on wall-clock time.

JO-JU (path resolution) and JV-KC (multi-item tier parsing) were added
2026-08-30 as the boundary-oriented regression matrix for two P0 defects an
independent adversarial review found in v1.0.0 of the auditor — see
tools/audit_job_dependencies.py's module docstring and resolve_job_target()/
parse_tiers() docstrings for the exact mechanisms.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
OUT = BASE / "vaults"
MAIN_FIXTURES_DIR = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(MAIN_FIXTURES_DIR))
import build_fixtures as bf  # noqa: E402


def job_note(body: str) -> str:
    return """---
status: active
project: meta
type: guide
---
# Job

## Context (Required always, Preferred if it helps, Optional only on request)
%s
""" % body


def fixture_JA_missing():
    files = bf.base_files()
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[does-not-exist]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JB_superseded():
    files = bf.base_files()
    files["00 - Inbox/old-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: superseded
superseded_by: "[[new-fact]]"
---
# Old Fact
"""
    files["00 - Inbox/new-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[old-fact]]"
last_confirmed: 2026-08-29
---
# New Fact
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[old-fact]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JC_candidate_claim():
    files = bf.base_files()
    files["00 - Inbox/unconfirmed.md"] = """---
status: active
project: personal
type: reference
memory_status: candidate
source: inferred
confidence: low
---
# Unconfirmed
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[unconfirmed]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JD_malformed_declaration():
    files = bf.base_files()
    files["00 - Inbox/plain-current.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-08-29
---
# Plain Current
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[plain-current]] (claim, explicitly-confirmed)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JE_stale():
    files = bf.base_files()
    files["00 - Inbox/old-confirmation.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-01-01
---
# Old Confirmation
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[old-confirmation]] (claim, explicitly-confirmed: 7 days)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JF_current_pass():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-08-29
---
# Fresh Fact
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[fresh-fact]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JG_operational_disclosed():
    files = bf.base_files()
    files["00 - Inbox/legacy-fact.md"] = """---
status: active
project: personal
type: reference
---
# Legacy Fact (zero lifecycle metadata)
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[legacy-fact]]\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JH_cycle():
    files = bf.base_files()
    files["00 - Inbox/x-cycle.md"] = """---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[y-cycle]]"
---
# X Cycle
"""
    files["00 - Inbox/y-cycle.md"] = """---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[z-cycle]]"
---
# Y Cycle
"""
    files["00 - Inbox/z-cycle.md"] = """---
status: active
project: personal
type: reference
memory_status: current
supersedes: "[[x-cycle]]"
---
# Z Cycle
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[y-cycle]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JI_preferred_degraded():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-08-29
---
# Fresh Fact
"""
    files["00 - Inbox/old-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: superseded
superseded_by: "[[fresh-fact]]"
---
# Old Fact
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[fresh-fact]] (claim)\n**Preferred:** [[old-fact]] (claim)\n**Optional:** none"
    )
    return files


def fixture_JJ_optional_silent():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-08-29
---
# Fresh Fact
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[fresh-fact]] (claim)\n**Preferred:** none\n**Optional:** [[does-not-exist]]"
    )
    return files


def fixture_JK_malformed_target():
    files = bf.base_files()
    files["00 - Inbox/broken.md"] = """---
status: active
project: personal
type: [reference
memory_status: current
---
# Broken frontmatter (unclosed flow sequence)
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[broken]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JL_disputed():
    files = bf.base_files()
    old_bullet = (
        "- **`memory_status`** — `active` (a current, working fact) | `stale` "
        "(was current, not recent) | `archived` (a fact no longer operative, kept for history)."
    )
    files["VAULT-INDEX.md"] = files["VAULT-INDEX.md"].replace(bf.MEMORY_STATUS_BULLET, old_bullet)
    files["00 - Inbox/disputed.md"] = """---
status: active
project: personal
type: reference
memory_status: active
---
# Disputed
"""
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[disputed]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JM_unrelated_job_unaffected():
    files = bf.base_files()
    files["09 - Resources/Jobs/Job A.md"] = job_note(
        "**Required:** [[does-not-exist]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    files["00 - Inbox/fresh-fact.md"] = """---
status: active
project: personal
type: reference
memory_status: current
last_confirmed: 2026-08-29
---
# Fresh Fact
"""
    files["09 - Resources/Jobs/Job B.md"] = job_note(
        "**Required:** [[fresh-fact]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JN_not_a_job_index():
    files = bf.base_files()
    files["09 - Resources/Jobs/Jobs.md"] = """---
status: active
project: meta
type: index
---
# Jobs

Nothing here yet.
"""
    return files


def _note(memory_status=None, extra="", title="Note"):
    lines = ["---", "status: active", "project: personal", "type: reference"]
    if memory_status:
        lines.append("memory_status: %s" % memory_status)
    if extra:
        lines.append(extra.strip())
    lines += ["---", "# %s" % title, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- path resolution (P0-1)

def fixture_JO_exact_path_target():
    files = bf.base_files()
    files["09 - Resources/exact-target.md"] = _note("current", "last_confirmed: 2026-08-29", "Exact Target")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[09 - Resources/exact-target]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JP_decoy_current_declared_superseded():
    # The exact P0-1 pattern, as a permanent regression: an unrelated,
    # differently-located note shares the declared target's filename stem and
    # is current; the ACTUAL declared target (path-qualified) is superseded.
    # The Job must block on the real target, never the decoy.
    files = bf.base_files()
    files["00 - Inbox/dup-source.md"] = _note("current", "last_confirmed: 2026-08-29", "Dup Source (Inbox decoy — current)")
    files["09 - Resources/dup-source.md"] = _note("superseded", title="Dup Source (Resources — declared target, superseded)")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[09 - Resources/dup-source]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JQ_decoy_current_declared_candidate():
    files = bf.base_files()
    files["00 - Inbox/dup-cand.md"] = _note("current", "last_confirmed: 2026-08-29", "Dup Cand (Inbox decoy — current)")
    files["02 - Projects/dup-cand.md"] = _note("candidate", "source: inferred\nconfidence: low", "Dup Cand (Projects — declared target, candidate)")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[02 - Projects/dup-cand]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JR_ambiguous_unqualified():
    # Two real notes share one stem in different folders; the Job's declared
    # link is UNQUALIFIED (no path at all) -> genuinely ambiguous, must fail
    # closed rather than silently picking whichever sorts first.
    files = bf.base_files()
    files["00 - Inbox/dup-ambig.md"] = _note("current", "last_confirmed: 2026-08-29", "Dup Ambig (Inbox)")
    files["02 - Projects/dup-ambig.md"] = _note("current", "last_confirmed: 2026-08-29", "Dup Ambig (Projects)")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[dup-ambig]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JS_nested_path():
    files = bf.base_files()
    files["02 - Projects/Nested/nested-note.md"] = _note("current", "last_confirmed: 2026-08-29", "Nested Note")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[02 - Projects/Nested/nested-note]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JT_nonexistent_path():
    # A same-stem note exists (Inbox), but NOT at the declared path. The
    # declared path must be used exactly, or nothing — never a same-stem
    # fallback from elsewhere.
    files = bf.base_files()
    files["00 - Inbox/ghost.md"] = _note("current", "last_confirmed: 2026-08-29", "Ghost (real note, wrong location)")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[09 - Resources/ghost]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JU_unqualified_single_match():
    # Regression control: the one already-tested unqualified case (exactly
    # one same-stem note anywhere) must still resolve exactly as before.
    files = bf.base_files()
    files["00 - Inbox/solo.md"] = _note("current", "last_confirmed: 2026-08-29", "Solo")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[solo]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


# ------------------------------------------------------------ multi-item tier parsing (P0-2)

def fixture_JV_two_required_valid():
    files = bf.base_files()
    files["00 - Inbox/two-a.md"] = _note("current", "last_confirmed: 2026-08-29", "Two A")
    files["00 - Inbox/two-b.md"] = _note("current", "last_confirmed: 2026-08-29", "Two B")
    # single-line, middle-dot form (the shipped templates' own convention) —
    # confirms the fix didn't regress the case that happened to work before.
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[two-a]] (claim) · [[two-b]] (claim)\n**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JW_three_required_valid():
    files = bf.base_files()
    for n in ("a", "b", "c"):
        files["00 - Inbox/three-%s.md" % n] = _note("current", "last_confirmed: 2026-08-29", "Three %s" % n.upper())
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[three-a]] (claim)\n- [[three-b]] (claim)\n- [[three-c]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JX_first_valid_second_missing():
    # THE critical regression named explicitly in the P0-2 report.
    files = bf.base_files()
    files["00 - Inbox/valid-current.md"] = _note("current", "last_confirmed: 2026-08-29", "Valid Current")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[valid-current]] (claim)\n- [[missing-note]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JY_first_missing_second_valid():
    # Order independence: parsing must not stop after (or be thrown off by) a
    # failing item that comes FIRST.
    files = bf.base_files()
    files["00 - Inbox/valid-b.md"] = _note("current", "last_confirmed: 2026-08-29", "Valid B")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[missing-a]] (claim)\n- [[valid-b]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_JZ_mixed_tiers_multi_item():
    files = bf.base_files()
    files["00 - Inbox/req-1.md"] = _note("current", "last_confirmed: 2026-08-29", "Req 1")
    files["00 - Inbox/req-2.md"] = _note("current", "last_confirmed: 2026-08-29", "Req 2")
    files["00 - Inbox/pref-ok.md"] = _note("current", "last_confirmed: 2026-08-29", "Pref OK")
    files["00 - Inbox/pref-old.md"] = _note("superseded", extra='superseded_by: "[[pref-ok]]"', title="Pref Old")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[req-1]] (claim)\n- [[req-2]] (claim)\n"
        "**Preferred:**\n- [[pref-ok]] (claim)\n- [[pref-old]] (claim)\n"
        "**Optional:**\n- [[does-not-exist]]"
    )
    return files


def fixture_KA_blank_lines_between_items():
    files = bf.base_files()
    files["00 - Inbox/blank-a.md"] = _note("current", "last_confirmed: 2026-08-29", "Blank A")
    files["00 - Inbox/blank-b.md"] = _note("current", "last_confirmed: 2026-08-29", "Blank B")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n\n- [[blank-a]] (claim)\n\n- [[blank-b]] (claim)\n\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_KB_malformed_among_valid_siblings():
    files = bf.base_files()
    files["00 - Inbox/good-one.md"] = _note("current", "last_confirmed: 2026-08-29", "Good One")
    files["00 - Inbox/bad-one.md"] = _note("current", "last_confirmed: 2026-08-29", "Bad One")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[good-one]] (claim)\n- [[bad-one]] (claim, explicitly-confirmed)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_KC_four_failure_states_combined():
    files = bf.base_files()
    files["00 - Inbox/ok.md"] = _note("current", "last_confirmed: 2026-08-29", "Ok")
    files["00 - Inbox/super.md"] = _note("superseded", title="Super")
    files["00 - Inbox/cand.md"] = _note("candidate", "source: inferred\nconfidence: low", "Cand")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:**\n- [[miss]] (claim)\n- [[super]] (claim)\n- [[cand]] (claim)\n- [[ok]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


# ----------------------------------------------------- fenced / duplicate tiers (P0-3)

def fixture_KD_fenced_duplicate_after_real_missing():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = _note("current", "last_confirmed: 2026-08-29", "Fresh Fact")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[does-not-exist]] (claim)\n\n```md\n**Required:** [[fresh-fact]] (claim)\n```\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_KE_fenced_duplicate_before_real_missing():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = _note("current", "last_confirmed: 2026-08-29", "Fresh Fact")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "```md\n**Required:** [[fresh-fact]] (claim)\n```\n\n**Required:** [[does-not-exist]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_KF_duplicate_real_required_headers():
    files = bf.base_files()
    files["00 - Inbox/fresh-fact.md"] = _note("current", "last_confirmed: 2026-08-29", "Fresh Fact")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[fresh-fact]] (claim)\n\n**Required:** [[does-not-exist]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


def fixture_KG_fenced_preferred_optional_also_fake():
    # Regression item #9/#10: a single fenced worked-example block containing
    # fake **Required:**, **Preferred:**, AND **Optional:** lines together —
    # none of the three may leak into any real tier. The fenced notes don't
    # exist at all; if fencing ever failed for any one of the three labels,
    # this would surface as a spurious finding referencing a fake note, or a
    # duplicate-tier authoring defect, on an otherwise completely clean Job.
    files = bf.base_files()
    files["00 - Inbox/req-real.md"] = _note("current", "last_confirmed: 2026-08-29", "Req Real")
    files["00 - Inbox/pref-real.md"] = _note("current", "last_confirmed: 2026-08-29", "Pref Real")
    files["00 - Inbox/opt-real.md"] = _note("current", "last_confirmed: 2026-08-29", "Opt Real")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[req-real]] (claim)\n"
        "**Preferred:** [[pref-real]] (claim)\n"
        "**Optional:** [[opt-real]]\n\n"
        "Full worked example for reference, never a real declaration:\n\n"
        "```markdown\n"
        "**Required:** [[some-fenced-note]] (claim)\n"
        "**Preferred:** [[another-fenced-note]] (claim)\n"
        "**Optional:** [[yet-another-fenced-note]]\n"
        "```\n"
    )
    return files


def fixture_KH_duplicate_real_both_valid():
    # Two real Required headers where BOTH sets of dependencies independently
    # resolve fine — proves the duplicate-declaration authoring defect blocks
    # the Job on its own, not merely as a side effect of one half happening to
    # fail. Both notes' own PASS is still independently recorded.
    files = bf.base_files()
    files["00 - Inbox/valid-one.md"] = _note("current", "last_confirmed: 2026-08-29", "Valid One")
    files["00 - Inbox/valid-two.md"] = _note("current", "last_confirmed: 2026-08-29", "Valid Two")
    files["09 - Resources/Jobs/Job.md"] = job_note(
        "**Required:** [[valid-one]] (claim)\n\n**Required:** [[valid-two]] (claim)\n"
        "**Preferred:** none\n**Optional:** none"
    )
    return files


FIXTURES = [
    ("JA-missing", fixture_JA_missing),
    ("JB-superseded", fixture_JB_superseded),
    ("JC-candidate-claim", fixture_JC_candidate_claim),
    ("JD-malformed-declaration", fixture_JD_malformed_declaration),
    ("JE-stale", fixture_JE_stale),
    ("JF-current-pass", fixture_JF_current_pass),
    ("JG-operational-disclosed", fixture_JG_operational_disclosed),
    ("JH-cycle", fixture_JH_cycle),
    ("JI-preferred-degraded", fixture_JI_preferred_degraded),
    ("JJ-optional-silent", fixture_JJ_optional_silent),
    ("JK-malformed-target", fixture_JK_malformed_target),
    ("JL-disputed", fixture_JL_disputed),
    ("JM-unrelated-job-unaffected", fixture_JM_unrelated_job_unaffected),
    ("JN-not-a-job-index", fixture_JN_not_a_job_index),
    ("JO-exact-path-target", fixture_JO_exact_path_target),
    ("JP-decoy-current-declared-superseded", fixture_JP_decoy_current_declared_superseded),
    ("JQ-decoy-current-declared-candidate", fixture_JQ_decoy_current_declared_candidate),
    ("JR-ambiguous-unqualified", fixture_JR_ambiguous_unqualified),
    ("JS-nested-path", fixture_JS_nested_path),
    ("JT-nonexistent-path", fixture_JT_nonexistent_path),
    ("JU-unqualified-single-match", fixture_JU_unqualified_single_match),
    ("JV-two-required-valid", fixture_JV_two_required_valid),
    ("JW-three-required-valid", fixture_JW_three_required_valid),
    ("JX-first-valid-second-missing", fixture_JX_first_valid_second_missing),
    ("JY-first-missing-second-valid", fixture_JY_first_missing_second_valid),
    ("JZ-mixed-tiers-multi-item", fixture_JZ_mixed_tiers_multi_item),
    ("KA-blank-lines-between-items", fixture_KA_blank_lines_between_items),
    ("KB-malformed-among-valid-siblings", fixture_KB_malformed_among_valid_siblings),
    ("KC-four-failure-states-combined", fixture_KC_four_failure_states_combined),
    ("KD-fenced-duplicate-after-real-missing", fixture_KD_fenced_duplicate_after_real_missing),
    ("KE-fenced-duplicate-before-real-missing", fixture_KE_fenced_duplicate_before_real_missing),
    ("KF-duplicate-real-required-headers", fixture_KF_duplicate_real_required_headers),
    ("KG-fenced-preferred-optional-also-fake", fixture_KG_fenced_preferred_optional_also_fake),
    ("KH-duplicate-real-both-valid", fixture_KH_duplicate_real_both_valid),
]


def build_all():
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in FIXTURES:
        files = fn()
        vault_dir = OUT / name
        for path, content in files.items():
            target = vault_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    return len(FIXTURES)


if __name__ == "__main__":
    n = build_all()
    print("built %d job-dependency fixture vaults under %s" % (n, OUT))
