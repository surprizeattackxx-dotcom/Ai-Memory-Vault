#!/usr/bin/env python3
"""Dedicated regression suite for the provenance/evidence-trace layer
(tools/memory_provenance.py), built on top of tools/memory_runtime.py,
tools/memory_conflict.py, and tools/audit_job_dependencies.py.

Standalone, self-contained, no pytest — matches test_memory_runtime.py's and
test_memory_conflict.py's own convention: run directly
(`python tests/test_memory_provenance.py`), collects a problems list, prints
PASS/FAILED, exits 0/1. Covers the 20 requested adversarial scenarios.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_provenance as mp  # noqa: E402
import build_fixtures as bf  # noqa: E402

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


def edge(edges, src, dst, edge_type=None):
    for e in edges:
        if e.source.note_path == src and e.target.note_path == dst and (edge_type is None or e.edge_type == edge_type):
            return e
    return None


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # 1/4 — direct wikilink provenance, outgoing direction
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/link-source.md"] = note("current", title="Link Source") + "\nSee [[link-target]] for details.\n"
        files["00 - Inbox/link-target.md"] = note("current", "last_confirmed: 2026-08-29", "Link Target")
        v1 = build_vault(tmp, "v1", files)
        rt1 = rt.MemoryRuntime(v1)
        rep1 = mp.outgoing(rt1, "00 - Inbox/link-source")
        check("1/4. status resolved", rep1.status == "resolved", "got %r" % (rep1.status,))
        e1 = edge(rep1.outgoing, "00 - Inbox/link-source.md", "00 - Inbox/link-target.md", "wikilink")
        check("1/4. direct wikilink edge found", e1 is not None, "got %r" % (rep1.outgoing,))
        check("1/4. detection_method is body-wikilink", e1 is not None and e1.detection_method == "body-wikilink")

        # ============================================================
        # 2 — multi-hop provenance via composition (A -> B -> C)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/hop-a.md"] = note("current", title="Hop A") + "\nSee [[hop-b]].\n"
        files["00 - Inbox/hop-b.md"] = note("current", title="Hop B") + "\nSee [[hop-c]].\n"
        files["00 - Inbox/hop-c.md"] = note("current", title="Hop C")
        v2 = build_vault(tmp, "v2", files)
        rt2 = rt.MemoryRuntime(v2)
        hop1 = mp.outgoing(rt2, "00 - Inbox/hop-a")
        first_hop_target = hop1.outgoing[0].target.note_path if hop1.outgoing else None
        check("2. first hop A->B resolved", first_hop_target == "00 - Inbox/hop-b.md", "got %r" % (first_hop_target,))
        hop2 = mp.outgoing(rt2, first_hop_target) if first_hop_target else None
        second_hop_target = hop2.outgoing[0].target.note_path if hop2 and hop2.outgoing else None
        check("2. second hop B->C resolved by chaining outgoing() calls",
              second_hop_target == "00 - Inbox/hop-c.md", "got %r" % (second_hop_target,))

        # ============================================================
        # 3 — incoming relationship
        # ============================================================
        rep3 = mp.incoming(rt1, "00 - Inbox/link-target")
        e3 = edge(rep3.incoming, "00 - Inbox/link-source.md", "00 - Inbox/link-target.md", "wikilink")
        check("3. incoming wikilink edge found", e3 is not None, "got %r" % (rep3.incoming,))
        check("3. outgoing is empty for an incoming() call", rep3.outgoing == ())

        # ============================================================
        # 5/6 — supersedes / superseded_by edges, both directions
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/old-fact.md"] = note("superseded", 'superseded_by: "[[new-fact]]"', "Old Fact")
        files["00 - Inbox/new-fact.md"] = note("current", 'supersedes: "[[old-fact]]"\nlast_confirmed: 2026-08-29', "New Fact")
        v5 = build_vault(tmp, "v5", files)
        rt5 = rt.MemoryRuntime(v5)
        rep5_new = mp.outgoing(rt5, "old-fact")
        e6 = edge(rep5_new.outgoing, "00 - Inbox/old-fact.md", "00 - Inbox/new-fact.md", "superseded_by")
        check("6. superseded_by edge found", e6 is not None, "got %r" % (rep5_new.outgoing,))
        check("6. reciprocal True for a clean pair", e6 is not None and e6.reciprocal is True, "got %r" % (e6,))

        rep5_old = mp.outgoing(rt5, "new-fact")
        e5 = edge(rep5_old.outgoing, "00 - Inbox/new-fact.md", "00 - Inbox/old-fact.md", "supersedes")
        check("5. supersedes edge found", e5 is not None, "got %r" % (rep5_old.outgoing,))
        check("5. reciprocal True for a clean pair", e5 is not None and e5.reciprocal is True, "got %r" % (e5,))

        rep5_incoming = mp.incoming(rt5, "old-fact")
        e5b = edge(rep5_incoming.incoming, "00 - Inbox/new-fact.md", "00 - Inbox/old-fact.md", "supersedes")
        check("5b. incoming(old-fact) shows new-fact's supersedes declaration",
              e5b is not None, "got %r" % (rep5_incoming.incoming,))

        # ============================================================
        # 7 — Job dependency provenance
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/Jobs/My Job.md"] = (
            note("current", title="My Job") + "\n## Context\n\n**Required:** [[dep-note]] (claim)\n"
            "**Preferred:** none\n**Optional:** none\n"
        )
        files["09 - Resources/dep-note.md"] = note("current", "last_confirmed: 2026-08-29", "Dep Note")
        v7 = build_vault(tmp, "v7", files)
        rt7 = rt.MemoryRuntime(v7)
        rep7_out = mp.outgoing(rt7, "09 - Resources/Jobs/My Job")
        j1 = edge(rep7_out.outgoing, "09 - Resources/Jobs/My Job.md", "09 - Resources/dep-note.md", "job_dependency")
        check("7. job_dependency outgoing edge found", j1 is not None, "got %r" % (rep7_out.outgoing,))
        check("7. tier/qualifier recorded in details",
              j1 is not None and j1.details.get("tier") == "Required" and j1.details.get("qualifier") == "claim",
              "got %r" % (j1.details if j1 else None,))
        check("7. resolution status present (reused from audit_job_dependencies, not re-derived)",
              j1 is not None and j1.details.get("resolution", {}).get("block") is None, "got %r" % (j1,))
        rep7_in = mp.incoming(rt7, "09 - Resources/dep-note")
        j2 = edge(rep7_in.incoming, "09 - Resources/Jobs/My Job.md", "09 - Resources/dep-note.md", "job_dependency")
        check("7. job_dependency incoming edge found (reverse direction)", j2 is not None, "got %r" % (rep7_in.incoming,))

        # ============================================================
        # 8/9 — candidate/superseded targets remain exactly what they are
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/hub.md"] = note("current", title="Hub") + "\nSee [[cand-target]] and [[old-target]].\n"
        files["00 - Inbox/cand-target.md"] = note("candidate", "source: inferred\nconfidence: low", "Cand Target")
        files["00 - Inbox/old-target.md"] = note("superseded", title="Old Target")
        v89 = build_vault(tmp, "v89", files)
        rt89 = rt.MemoryRuntime(v89)
        rep89 = mp.outgoing(rt89, "00 - Inbox/hub")
        e8 = edge(rep89.outgoing, "00 - Inbox/hub.md", "00 - Inbox/cand-target.md")
        e9 = edge(rep89.outgoing, "00 - Inbox/hub.md", "00 - Inbox/old-target.md")
        check("8. candidate target's status_track stays 'candidate', never promoted",
              e8 is not None and e8.target.status_track == "candidate" and e8.target.accepted is False,
              "got %r" % (e8,))
        check("9. superseded target's status_track stays 'superseded', never invalidated-or-hidden",
              e9 is not None and e9.target.status_track == "superseded" and e9.target.accepted is False,
              "got %r" % (e9,))
        check("8/9. source (current, accepted) is unaffected by what it links to",
              e8 is not None and e8.source.status_track == "current" and e8.source.accepted is True)

        # ============================================================
        # 10 — ambiguous same-stem target fails closed
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/twin.md"] = note("current", title="Twin A")
        files["02 - Projects/twin.md"] = note("current", title="Twin B")
        v10 = build_vault(tmp, "v10", files)
        rt10 = rt.MemoryRuntime(v10)
        rep10 = mp.trace(rt10, "twin")
        check("10. ambiguous -> status=ambiguous", rep10.status == "ambiguous", "got %r" % (rep10.status,))
        check("10. candidates lists both paths",
              set(rep10.candidates) == {"00 - Inbox/twin.md", "02 - Projects/twin.md"}, "got %r" % (rep10.candidates,))
        check("10. no edges computed for an ambiguous target (never a guess)",
              rep10.outgoing == () and rep10.incoming == () and rep10.anchor is None)

        # ============================================================
        # 11 — missing target
        # ============================================================
        rep11 = mp.trace(rt10, "does-not-exist-anywhere")
        check("11. missing -> status=missing", rep11.status == "missing", "got %r" % (rep11.status,))
        check("11. no edges for a missing target", rep11.outgoing == () and rep11.incoming == ())

        # ============================================================
        # 12 — path-qualified target resolves precisely despite a same-stem
        # decoy elsewhere (security: never silently fall back to the decoy)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/required-source.md"] = note("current", "last_confirmed: 2026-08-29", "Decoy Current")
        files["09 - Resources/required-source.md"] = note("superseded", title="Declared Target Superseded")
        v12 = build_vault(tmp, "v12", files)
        rt12 = rt.MemoryRuntime(v12)
        rep12 = mp.trace(rt12, "09 - Resources/required-source")
        check("12. path-qualified resolution lands on the declared target, not the decoy",
              rep12.status == "resolved" and rep12.anchor is not None
              and rep12.anchor.note_path == "09 - Resources/required-source.md", "got %r" % (rep12.anchor,))
        check("12. declared target's own lifecycle (superseded) is preserved, never swapped for the decoy's",
              rep12.anchor.status_track == "superseded", "got %r" % (rep12.anchor,))

        # ============================================================
        # 13/14 — cycle detection + non-adjacent-in-outgoing precision
        # (4-cycle: X -> Y -> Z -> W -> X via `supersedes`; X's only DIRECT
        # edges are to Y and W — Z must appear in the cycle's member list but
        # never as a direct outgoing/incoming edge of X)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/x.md"] = note("current", 'supersedes: "[[y]]"', "X")
        files["00 - Inbox/y.md"] = note("current", 'supersedes: "[[z]]"', "Y")
        files["00 - Inbox/z.md"] = note("current", 'supersedes: "[[w]]"', "Z")
        files["00 - Inbox/w.md"] = note("current", 'supersedes: "[[x]]"', "W")
        v_cycle = build_vault(tmp, "v-cycle", files)
        rt_cycle = rt.MemoryRuntime(v_cycle)
        rep_cycle = mp.trace(rt_cycle, "x")
        check("13. cycle detected and reported", len(rep_cycle.cycles) == 1, "got %r" % (rep_cycle.cycles,))
        cyc = rep_cycle.cycles[0] if rep_cycle.cycles else None
        check("13. cycle status is cycle-detected", cyc is not None and cyc.status == "cycle-detected", "got %r" % (cyc,))
        check("13. all four members present in the cycle path",
              cyc is not None and set(cyc.members) == {"00 - Inbox/x.md", "00 - Inbox/y.md",
                                                          "00 - Inbox/z.md", "00 - Inbox/w.md"},
              "got %r" % (cyc.members if cyc else None,))
        check("13. traversal is a deterministic, ordered edge list of length 4",
              cyc is not None and len(cyc.traversal) == 4, "got %r" % (cyc.traversal if cyc else None,))
        # X supersedes Y (outgoing: X dominates Y) and W supersedes X
        # (incoming: W dominates X) — Z is two hops away in either direction.
        direct_out = {e.target.note_path for e in rep_cycle.outgoing}
        direct_in = {e.source.note_path for e in rep_cycle.incoming}
        check("14. X's direct outgoing edge is exactly {Y}, never a fabricated direct edge to Z",
              direct_out == {"00 - Inbox/y.md"}, "got %r" % (direct_out,))
        check("14. X's direct incoming edge is exactly {W}, never a fabricated direct edge to Z",
              direct_in == {"00 - Inbox/w.md"}, "got %r" % (direct_in,))
        check("14. non-adjacent cycle member Z never appears as a direct edge of X",
              edge(rep_cycle.outgoing, "00 - Inbox/x.md", "00 - Inbox/z.md") is None
              and edge(rep_cycle.incoming, "00 - Inbox/z.md", "00 - Inbox/x.md") is None)

        # ============================================================
        # 15 — duplicate edge collapse (same wikilink twice in one body)
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/dup-src.md"] = note("current", title="Dup Src") + "\nSee [[dup-dst]]. Also see [[dup-dst]] again.\n"
        files["00 - Inbox/dup-dst.md"] = note("current", "last_confirmed: 2026-08-29", "Dup Dst")
        v15 = build_vault(tmp, "v15", files)
        rt15 = rt.MemoryRuntime(v15)
        rep15 = mp.outgoing(rt15, "00 - Inbox/dup-src")
        matches15 = [e for e in rep15.outgoing if e.target.note_path == "00 - Inbox/dup-dst.md"]
        check("15. duplicate wikilink to the same note collapses to exactly one edge",
              len(matches15) == 1, "got %d: %r" % (len(matches15), matches15))

        # ============================================================
        # 16 — deterministic ordering across independent runtime instances
        # ============================================================
        rt16a = rt.MemoryRuntime(v_cycle)
        rt16b = rt.MemoryRuntime(v_cycle)
        rep16a = mp.trace(rt16a, "x")
        rep16b = mp.trace(rt16b, "x")
        order_a = [(e.edge_type, e.source.note_path, e.target.note_path) for e in rep16a.outgoing + rep16a.incoming]
        order_b = [(e.edge_type, e.source.note_path, e.target.note_path) for e in rep16b.outgoing + rep16b.incoming]
        check("16. two independent runtime instances agree on edge ordering", order_a == order_b,
              "got %r vs %r" % (order_a, order_b))
        check("16. cycle traversal is identical across independent runs",
              rep16a.cycles[0].traversal == rep16b.cycles[0].traversal if rep16a.cycles and rep16b.cycles else False)

        # ============================================================
        # 17 — every returned path stays inside the vault root
        # ============================================================
        for report, root in ((rep1, v1), (rep5_new, v5), (rep7_out, v7), (rep_cycle, v_cycle)):
            for e in list(report.outgoing) + list(report.incoming):
                for p in (e.source.note_path, e.target.note_path):
                    resolved = (root / p).resolve()
                    check("17. edge path stays inside vault root: %r" % p,
                          str(resolved).startswith(str(root.resolve())), "escaped to %r" % (resolved,))

        # ============================================================
        # 18 — malicious/traversal-shaped identity: never resolves, never
        # crashes, never escapes; a note that DECLARES a traversal-shaped
        # wikilink is reported broken, never silently resolved
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "..\\..\\..\\Windows", "..", "../"]
        for q in traversal_queries:
            rep_t = mp.trace(rt1, q)
            check("18. traversal-shaped identity %r -> status=missing (identity-only, no free-text fallback)" % q,
                  rep_t.status == "missing", "got %r" % (rep_t.status,))

        files = bf.base_files()
        files["00 - Inbox/attacker-note.md"] = note("current", title="Attacker Note") + "\nSee [[../../../../etc/passwd]].\n"
        v18 = build_vault(tmp, "v18", files)
        rt18 = rt.MemoryRuntime(v18)
        rep18 = mp.outgoing(rt18, "00 - Inbox/attacker-note")
        check("18. traversal-shaped wikilink target produces a broken entry, never a resolved edge",
              rep18.outgoing == () and len(rep18.broken) == 1, "got outgoing=%r broken=%r" % (rep18.outgoing, rep18.broken))
        check("18. broken entry reason is 'missing', not silently swallowed",
              rep18.broken and rep18.broken[0].reason == "missing", "got %r" % (rep18.broken,))

        # ============================================================
        # 19 — provenance retrieval never mutates the Vault
        # ============================================================
        before = file_hashes(v_cycle)
        mp.trace(rt_cycle, "x")
        mp.outgoing(rt_cycle, "y")
        mp.incoming(rt_cycle, "z")
        mp.trace(rt_cycle, "nonexistent")
        mp.trace(rt_cycle, "")
        after = file_hashes(v_cycle)
        check("19. provenance retrieval never mutates the vault", before == after,
              "changed: %r" % ({k for k in before if before.get(k) != after.get(k)},))

        # ============================================================
        # 20 — retrieval score can never become evidence: no dataclass here
        # carries a score field at all, and lexical overlap alone (no
        # wikilink/supersession/job link) never produces an edge
        # ============================================================
        for obj in (mp.Edge, mp.BrokenEdge, mp.CycleReport, mp.ValidationEvidence):
            fields = obj.__dataclass_fields__
            check("20. %s carries no 'score' field anywhere in its shape" % obj.__name__, "score" not in fields,
                  "got fields %r" % (list(fields),))

        files = bf.base_files()
        files["00 - Inbox/kw-a.md"] = note("current", title="KW A") + "\nThe quokka eats apples in the afternoon.\n"
        files["00 - Inbox/kw-b.md"] = note("current", title="KW B") + "\nA different quokka story about apples entirely.\n"
        v20 = build_vault(tmp, "v20", files)
        rt20 = rt.MemoryRuntime(v20)
        rep20 = mp.trace(rt20, "00 - Inbox/kw-a")
        check("20. lexical overlap alone (no wikilink/supersession/job link) never creates an edge",
              edge(rep20.outgoing, "00 - Inbox/kw-a.md", "00 - Inbox/kw-b.md") is None
              and edge(rep20.incoming, "00 - Inbox/kw-b.md", "00 - Inbox/kw-a.md") is None
              and rep20.outgoing == () and rep20.incoming == (),
              "got outgoing=%r incoming=%r" % (rep20.outgoing, rep20.incoming))

        # ============================================================
        # Extra — full-repo-style mutation check across every vault built
        # ============================================================
        for label, root, runtime_obj, target in (
            ("v1", v1, rt1, "00 - Inbox/link-source"), ("v7", v7, rt7, "09 - Resources/Jobs/My Job"),
            ("v10", v10, rt10, "twin"), ("v-cycle", v_cycle, rt_cycle, "x"),
        ):
            b = file_hashes(root)
            mp.trace(runtime_obj, target)
            mp.outgoing(runtime_obj, target)
            mp.incoming(runtime_obj, target)
            a = file_hashes(root)
            check("mutation check (%s)" % label, b == a, "changed: %r" % ({k for k in b if b.get(k) != a.get(k)},))

        source_text = (TOOLS / "memory_provenance.py").read_text(encoding="utf-8")
        check("security: no method ever opens a path for writing (source check)",
              "write_text(" not in source_text and "unlink(" not in source_text
              and '"w")' not in source_text and "'w')" not in source_text)

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: provenance/evidence trace — all 20 scenarios hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
