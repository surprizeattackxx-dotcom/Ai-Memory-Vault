#!/usr/bin/env python3
"""Dedicated regression suite for the Memory Runtime / Retrieval Abstraction
(tools/memory_retrieval.py, tools/memory_runtime.py, tools/vault_identity.py).

Standalone, self-contained, no pytest — matches tests/test_surface_resolution.py's
convention: run directly (`python tests/test_memory_runtime.py`), collects a
problems list, prints PASS/FAILED, exits 0/1.

Covers the required regression matrix (14 numbered scenarios) plus the key
architectural invariant: a highly ranked candidate cannot become "current"
merely because retrieval ranked it highly. Candidate != trust throughout.

Every vault here is a fresh temp directory, built from
tests/fixtures/vaults/build_fixtures.py's base_files() scaffolding (state
detection, parity, and boot behave exactly like the main vault fixture set) —
no dependency on any other fixture set's current contents, so this suite
stays correct even if fixtures elsewhere are renumbered or changed.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_retrieval as mr  # noqa: E402
import vault_identity as vid  # noqa: E402
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


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # 1/2 — exact canonical-path and exact filename retrieval
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/target-note.md"] = note("current", "last_confirmed: 2026-08-29", "Target Note")
        v1 = build_vault(tmp, "v1", files)
        runtime1 = rt.MemoryRuntime(v1)

        r = runtime1.resolve("09 - Resources/target-note")
        check("1. exact canonical-path retrieval", r.status == "resolved" and r.note_path == "09 - Resources/target-note.md",
              "got %r" % (r,))

        r2 = runtime1.resolve("target-note")
        check("2. exact filename (single match) retrieval", r2.status == "resolved" and r2.note_path == "09 - Resources/target-note.md",
              "got %r" % (r2,))

        # ============================================================
        # 3 — ambiguous same-stem retrieval fails closed, never a guess
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/dup-name.md"] = note("current", "last_confirmed: 2026-08-29", "Dup Inbox")
        files["02 - Projects/dup-name.md"] = note("current", "last_confirmed: 2026-08-29", "Dup Projects")
        v3 = build_vault(tmp, "v3", files)
        runtime3 = rt.MemoryRuntime(v3)
        r3 = runtime3.resolve("dup-name")
        check("3. ambiguous same-stem retrieval -> status=ambiguous", r3.status == "ambiguous", "got %r" % (r3,))
        check("3. ambiguous candidates lists both paths",
              set(r3.candidates) == {"00 - Inbox/dup-name.md", "02 - Projects/dup-name.md"}, "got %r" % (r3.candidates,))
        # security: same-stem decoy selection never silently picked
        rc3 = runtime3.retrieve("dup-name", methods=("exact",))
        check("security: no candidate silently marked non-ambiguous when 2+ share identity",
              all(c.ambiguous for c in rc3), "got %r" % ([(c.note_path, c.ambiguous) for c in rc3],))

        # security: path-qualified identity loss — decoy current, declared target superseded
        files = bf.base_files()
        files["00 - Inbox/required-source.md"] = note("current", "last_confirmed: 2026-08-29", "Decoy Current")
        files["09 - Resources/required-source.md"] = note("superseded", title="Declared Target Superseded")
        v3b = build_vault(tmp, "v3b", files)
        runtime3b = rt.MemoryRuntime(v3b)
        r3b = runtime3b.inspect("09 - Resources/required-source")
        check("security: path-qualified identity resolves to declared target, not same-stem decoy",
              r3b.status == "resolved" and r3b.context.note_path == "09 - Resources/required-source.md", "got %r" % (r3b,))
        check("security: declared (superseded) target correctly not accepted, decoy never substituted",
              r3b.context.accepted is False and r3b.context.status_track == "superseded", "got %r" % (r3b.context,))

        # ============================================================
        # 4 — deterministic ordering, independent of filesystem/rglob order
        # ============================================================
        files = bf.base_files()
        # Filenames chosen so alphabetical/filesystem order (b, m, z) differs
        # from the expected text-search score order (z scores highest).
        files["00 - Inbox/b-note.md"] = note("current", title="B Note") + "\nfindme once.\n"
        files["00 - Inbox/m-note.md"] = note("current", title="M Note") + "\nfindme findme twice twice.\n"
        files["00 - Inbox/z-note.md"] = note("current", title="Z Note") + "\nfindme findme findme three three three.\n"
        v4 = build_vault(tmp, "v4", files)
        runtime4a = rt.MemoryRuntime(v4)
        runtime4b = rt.MemoryRuntime(v4)  # independent second construction
        res4a = [c.note_path for c in runtime4a.retrieve("findme", methods=("text",))]
        res4b = [c.note_path for c in runtime4b.retrieve("findme", methods=("text",))]
        check("4. deterministic ordering: two independent runs agree", res4a == res4b, "got %r vs %r" % (res4a, res4b))
        check("4. deterministic ordering: score order, not filesystem/alphabetical order",
              res4a == ["00 - Inbox/z-note.md", "00 - Inbox/m-note.md", "00 - Inbox/b-note.md"], "got %r" % (res4a,))

        # ============================================================
        # 5 — full-text retrieval by body content only
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/prose.md"] = note("current", title="Some Note") + "\nThe quokka prefers shade in the afternoon.\n"
        v5 = build_vault(tmp, "v5", files)
        runtime5 = rt.MemoryRuntime(v5)
        r5 = runtime5.retrieve("quokka")
        check("5. full-text retrieval finds body-content-only match",
              any(c.note_path == "00 - Inbox/prose.md" and c.method == "text" for c in r5), "got %r" % (r5,))

        # ============================================================
        # 6/7 — superseded / candidate results remain correctly labeled
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/old-fact.md"] = note("superseded", 'superseded_by: "[[new-fact]]"', "Old Fact")
        files["00 - Inbox/new-fact.md"] = note("current", 'supersedes: "[[old-fact]]"\nlast_confirmed: 2026-08-29', "New Fact")
        files["00 - Inbox/guess.md"] = note("candidate", "source: inferred\nconfidence: low", "Guess")
        v6 = build_vault(tmp, "v6", files)
        runtime6 = rt.MemoryRuntime(v6)
        i_old = runtime6.inspect("old-fact")
        check("6. superseded result remains marked superseded",
              i_old.context.status_track == "superseded" and i_old.context.accepted is False, "got %r" % (i_old.context,))
        i_guess = runtime6.inspect("guess")
        check("7. candidate result remains marked candidate",
              i_guess.context.status_track == "candidate" and i_guess.context.accepted is False, "got %r" % (i_guess.context,))

        # ============================================================
        # 8 — retrieval never overrides canonical lifecycle state
        # ============================================================
        r8 = runtime6.retrieve("old fact", methods=("text",))
        old_hits = [c for c in r8 if c.note_path == "00 - Inbox/old-fact.md"]
        check("8. retrieval does not override canonical lifecycle state",
              bool(old_hits) and old_hits[0].status_track == "superseded" and old_hits[0].accepted is False,
              "got %r" % (old_hits,))

        # ============================================================
        # KEY INVARIANT — a highly ranked candidate cannot become "current"
        # merely because retrieval ranked it highly.
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/loud-candidate.md"] = (
            note("candidate", "source: inferred\nconfidence: low", "Loud Candidate")
            + "\ntrustme trustme trustme trustme trustme trustme trustme trustme.\n"
        )
        files["00 - Inbox/quiet-current.md"] = note("current", "last_confirmed: 2026-08-29", "Quiet Current") + "\ntrustme once.\n"
        v_inv = build_vault(tmp, "v-invariant", files)
        runtime_inv = rt.MemoryRuntime(v_inv)
        r_inv = runtime_inv.retrieve("trustme", methods=("text",))
        check("KEY INVARIANT setup: candidate note ranks first by score",
              r_inv and r_inv[0].note_path == "00 - Inbox/loud-candidate.md", "got %r" % ([c.note_path for c in r_inv],))
        top = r_inv[0] if r_inv else None
        check("KEY INVARIANT: top-ranked candidate is NOT accepted merely for ranking highly",
              top is not None and top.accepted is False and top.status_track == "candidate", "got %r" % (top,))

        # ============================================================
        # 9 — provenance is preserved
        # ============================================================
        prov = runtime6.retrieve("new fact", methods=("text",))
        hit = next((c for c in prov if c.note_path == "00 - Inbox/new-fact.md"), None)
        check("9. provenance fields present", hit is not None and all([
            hit.note_path, hit.method, hit.query == "new fact", hit.status_track, hit.reason,
        ]), "got %r" % (hit,))

        # ============================================================
        # 10 — missing query produces a clean empty result
        # ============================================================
        check("10a. empty-string query -> []", runtime6.retrieve("") == [])
        check("10b. whitespace-only query -> []", runtime6.retrieve("   ") == [])
        check("10c. None query -> [] (search layer)", mr.search(runtime6.vault, None) == [])

        # ============================================================
        # 11 — duplicate candidates deduplicated by canonical identity
        # ============================================================
        files = bf.base_files()
        # Stem AND body keyword both "zephyrnote" so one single-word query
        # legitimately matches via both the filename method (exact stem) and
        # the text method (body keyword) — a genuine multi-method hit on the
        # same note, not an artificially forced one.
        files["00 - Inbox/zephyrnote.md"] = note("current", "last_confirmed: 2026-08-29", "Zephyrnote") + "\nzephyrnote zephyrnote.\n"
        v11 = build_vault(tmp, "v11", files)
        runtime11 = rt.MemoryRuntime(v11)
        r11 = runtime11.retrieve("zephyrnote", methods=("filename", "text"))
        matches11 = [c for c in r11 if c.note_path == "00 - Inbox/zephyrnote.md"]
        check("11. duplicate candidates deduplicated to one entry", len(matches11) == 1, "got %d entries" % len(matches11))
        check("11. merged entry records every contributing method",
              matches11 and set(matches11[0].all_methods) >= {"filename", "text"}, "got %r" % (matches11,))

        # ============================================================
        # 12 — retrieval method is always explicit
        # ============================================================
        all_results = runtime6.retrieve("fact", methods=("text", "filename", "exact"))
        check("12. every result carries a non-empty explicit method",
              all(c.method in mr.METHODS for c in all_results), "got %r" % ([c.method for c in all_results],))

        # ============================================================
        # 13 — runtime never mutates the vault
        # ============================================================
        before = file_hashes(v6)
        runtime6.retrieve("fact")
        runtime6.inspect("old-fact")
        runtime6.resolve("guess")
        after = file_hashes(v6)
        check("13. runtime does not mutate the vault", before == after,
              "changed: %r" % ({k for k in before if before.get(k) != after.get(k)},))

        # ============================================================
        # 14 — runtime works without any accelerator installed
        # ============================================================
        banned = ("sentence_transformers", "torch", "transformers", "faiss", "chromadb",
                  "qdrant", "sqlite_vector")
        source_text = (TOOLS / "memory_retrieval.py").read_text(encoding="utf-8") \
            + (TOOLS / "memory_runtime.py").read_text(encoding="utf-8") \
            + (TOOLS / "vault_identity.py").read_text(encoding="utf-8")
        check("14. no forbidden accelerator dependency imported in source",
              not any(b in source_text for b in banned), "found a banned name in source")
        check("14. runtime operates with zero accelerator/index present (no extra setup needed)",
              bool(runtime6.retrieve("fact")))

        # ============================================================
        # Security: malicious query cannot cause file access outside the
        # vault. Note what this does and doesn't mean: the "text" method is a
        # legitimate freeform keyword search, so a traversal-shaped query is
        # allowed to score ordinary keyword hits against real vault content
        # (that's the feature working correctly, not a leak) — what must
        # NEVER happen is (a) identity-based methods (exact/filename)
        # resolving such a query to any note, and (b) any result path ever
        # pointing outside the vault root. Both are asserted directly rather
        # than assuming "empty result" is the safety property, which it is
        # not for a keyword search.
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "..\\..\\..\\Windows", "..", "../"]
        for q in traversal_queries:
            id_res = runtime6.retrieve(q, methods=("exact", "filename"))
            check("security: identity methods never resolve a traversal-shaped query %r" % q, id_res == [],
                  "got %r" % ([c.note_path for c in id_res],))
            all_res = runtime6.retrieve(q)
            for c in all_res:
                resolved = (v6 / c.note_path).resolve()
                check("security: every result for %r stays inside the vault root" % q,
                      str(resolved).startswith(str(v6.resolve())), "escaped to %r" % (resolved,))
        check("security: exact identity resolution refuses to leave the vault via a path-shaped query",
              runtime6.resolve("../../../etc/passwd").status == "missing")
        check("security: no method ever opens a path built directly from the query string (source check)",
              "open(" not in (TOOLS / "memory_retrieval.py").read_text(encoding="utf-8"))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: memory runtime / retrieval abstraction — all 14 scenarios + key invariant + security checks hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
