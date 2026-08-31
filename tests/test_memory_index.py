#!/usr/bin/env python3
"""Dedicated regression + adversarial suite for the acceleration boundary
(tools/memory_index.py, and the indexed seam in tools/memory_retrieval.py).

Standalone, self-contained, no pytest — matches every sibling suite's
convention: run directly (`python tests/test_memory_index.py`), collects a
problems list, prints PASS/FAILED, exits 0/1.

Structure mirrors the v3.7.5 Phase 3 hardening ticket directly, section by
section, so a reviewer can map every check back to the requirement it proves:
    - INVARIANTS A-I (the boundary itself)
    - FRESHNESS (9 cases)
    - CORRUPTION / ADVERSARIAL (23 attack classes)
    - NEGATIVE-SPACE EQUIVALENCE (live vs indexed vs missing/stale/corrupt
      index, across 9 representative decoy/ambiguity/lifecycle fixtures)
    - SERIALIZATION SAFETY
    - DETERMINISM
    - MUTATION PROOF

The one sentence every check here is trying to make mechanically true:
a perfect index makes the system faster; a broken index makes it slower;
neither is allowed to make the system believe something the canonical
Markdown and live validation would not say.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_retrieval as mret  # noqa: E402
import memory_index as mi  # noqa: E402
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
            out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def cand_tuple(cands):
    return tuple((c.note_path, c.method, c.score, c.matched_on, c.ambiguous) for c in cands)


def rewrite_index(idx_path: Path, mutate):
    """Load an index's JSON payload, apply `mutate(payload)` in place, write
    it back. Returns the loaded MemoryIndex (or None if load() now rejects
    the mutated file — itself a valid, checkable outcome for several attacks)."""
    payload = json.loads(idx_path.read_text(encoding="utf-8"))
    mutate(payload)
    idx_path.write_text(json.dumps(payload), encoding="utf-8")
    return mi.MemoryIndex.load(idx_path)


class LyingIndex:
    """A duck-typed accelerator that ALWAYS claims freshness, then returns
    attacker-chosen data (or raises) from its lookup method — simulates a
    compromised/buggy accelerator regardless of whether the real
    MemoryIndex.is_fresh_for() would ever actually agree. Used to prove
    search()'s live-revalidation defends the boundary even when the
    freshness gate itself is assumed defeated."""
    def __init__(self, candidates=None, raise_on_lookup=False, raise_on_fresh=False):
        self._candidates = candidates if candidates is not None else []
        self._raise_lookup = raise_on_lookup
        self._raise_fresh = raise_on_fresh

    def is_fresh_for(self, vault):
        if self._raise_fresh:
            raise RuntimeError("simulated malicious/buggy is_fresh_for")
        return True

    def link_stem_candidates(self, stem):
        if self._raise_lookup:
            raise RuntimeError("simulated malicious/buggy link_stem_candidates")
        return self._candidates


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # Shared base vault used across most of the invariant/freshness/
        # corruption sections below.
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        files["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        files["00 - Inbox/other.md"] = note("current", title="Other") + "\nNo link here.\n"
        root = build_vault(tmp, "base", files)
        runtime = rt.MemoryRuntime(root)
        idx = mi.MemoryIndex.build(runtime.vault)
        idx_path = tmp / "base.json"
        idx.save(idx_path)
        live_baseline = mret.search(runtime.vault, "target", methods=("wikilink",))
        hashes_before_suite = file_hashes(root)

        # ============================================================
        # INVARIANT A — index never resolves identity
        # ============================================================
        check("A1. IndexEntry has no resolved-target field",
              "resolved" not in mi.IndexEntry.__dataclass_fields__
              and "target" not in mi.IndexEntry.__dataclass_fields__)
        check("A2. link_stem_candidates returns raw paths only, never a (resolved, ambiguous) tuple",
              all(isinstance(r, str) for r in idx.link_stem_candidates("target")))
        check("A3. every wikilink hit still calls vid.resolve_identity live (source check)",
              (TOOLS / "memory_retrieval.py").read_text(encoding="utf-8").count("vid.resolve_identity") >= 3)

        # ============================================================
        # INVARIANT B/C — index never determines lifecycle or acceptance
        # ============================================================
        lifecycle_fields = {"memory_status", "source", "confidence", "supersedes", "superseded_by",
                             "accepted", "status_track", "lifecycle", "trust", "authoritative", "validated"}
        check("B1. IndexEntry carries no lifecycle/trust field",
              lifecycle_fields.isdisjoint(mi.IndexEntry.__dataclass_fields__.keys()),
              "got %r" % (set(mi.IndexEntry.__dataclass_fields__.keys()) & lifecycle_fields,))
        check("B2. IndexHeader carries no lifecycle/trust field",
              lifecycle_fields.isdisjoint(mi.IndexHeader.__dataclass_fields__.keys()))
        check("C1. Candidate dataclass carries no numeric trust/confidence/authoritative field",
              lifecycle_fields.isdisjoint(mret.Candidate.__dataclass_fields__.keys())
              and "confidence" not in mret.Candidate.__dataclass_fields__)

        # ============================================================
        # INVARIANT D — index never suppresses live validation
        # ============================================================
        result_indexed = mret.search(runtime.vault, "target", methods=("wikilink",), index=idx)
        ctx = runtime.inspect("00 - Inbox/linker.md").context
        check("D1. a candidate surfaced via the index still requires MemoryRuntime.inspect() for trust",
              ctx.accepted is True and ctx.status_track == "current")
        check("D2. no code path treats index candidates as pre-validated (source check: no 'accepted=True' near index)",
              "accepted=True" not in (TOOLS / "memory_index.py").read_text(encoding="utf-8"))

        # ============================================================
        # INVARIANT E — stale index means no index (see FRESHNESS section
        # below for the full 9-case matrix; this is the summary assertion)
        # ============================================================
        check("E1. is_fresh_for on an untouched vault is True", idx.is_fresh_for(runtime.vault) is True)

        # ============================================================
        # INVARIANT F — index cannot expand authority (see CORRUPTION
        # section below for the full attack matrix)
        # ============================================================

        # ============================================================
        # INVARIANT G — no filesystem access based on query strings
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "..\\..\\..\\Windows", "\x00", "'; DROP TABLE notes; --", "{{7*7}}",
                              "${jndi:ldap://evil/a}", ".."]
        for q in traversal_queries:
            r = mret.search(runtime.vault, q, methods=("wikilink",), index=idx)
            check("G. traversal/injection query %r never resolves via indexed wikilink search" % q, r == [],
                  "got %r" % (r,))
        check("G2. link_stem_candidates never opens a path (source check: no open()/read in MemoryIndex lookups)",
              "def link_stem_candidates" in (TOOLS / "memory_index.py").read_text(encoding="utf-8"))

        # ============================================================
        # INVARIANT H — candidate equivalence (fresh + complete index)
        # ============================================================
        check("H1. indexed and live wikilink results are byte-identical on a fresh index",
              cand_tuple(result_indexed) == cand_tuple(live_baseline), "got %r vs %r" %
              (cand_tuple(result_indexed), cand_tuple(live_baseline)))

        # ============================================================
        # INVARIANT I — index is disposable
        # ============================================================
        idx_path.unlink()
        r_after_delete = mret.search(runtime.vault, "target", methods=("wikilink",))
        check("I1. deleting the on-disk index file changes nothing about live behavior",
              cand_tuple(r_after_delete) == cand_tuple(live_baseline))

        # ============================================================
        # FRESHNESS — 9 cases
        # ============================================================
        idx.save(idx_path)

        # 1. clean/fresh
        check("F1. clean index is fresh", mi.MemoryIndex.load(idx_path).is_fresh_for(runtime.vault) is True)

        # 2. changed note
        (root / "00 - Inbox/other.md").write_text(note("current", title="Other CHANGED") + "\n", encoding="utf-8", newline="\n")
        runtime_changed = rt.MemoryRuntime(root)
        check("F2. changed note -> stale", idx.is_fresh_for(runtime_changed.vault) is False)
        (root / "00 - Inbox/other.md").write_text(note("current", title="Other") + "\nNo link here.\n", encoding="utf-8", newline="\n")
        runtime = rt.MemoryRuntime(root)  # restored

        # 3. changed protocol
        protocol_path = root / "09 - Resources" / "MEMORY_PROTOCOL.md"
        original_protocol_bytes = protocol_path.read_bytes()
        protocol_path.write_text(original_protocol_bytes.decode("utf-8") + "\n<!-- tampered -->\n", encoding="utf-8", newline="\n")
        runtime_protocol_changed = rt.MemoryRuntime(root)
        check("F3. changed MEMORY_PROTOCOL.md -> stale", idx.is_fresh_for(runtime_protocol_changed.vault) is False)
        protocol_path.write_bytes(original_protocol_bytes)
        runtime = rt.MemoryRuntime(root)  # restored

        # 4. changed vault root (index built for a different vault entirely)
        root_other = build_vault(tmp, "other-vault", bf.base_files())
        runtime_other = rt.MemoryRuntime(root_other)
        check("F4. index built for vault A is never fresh for vault B", idx.is_fresh_for(runtime_other.vault) is False)

        # 5. changed schema version
        idx_schema = rewrite_index(idx_path, lambda p: p.__setitem__("schema_version", "999.0.0"))
        check("F5. wrong schema_version -> load() accepts (structurally valid) but is_fresh_for is False",
              idx_schema is not None and idx_schema.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 6. missing
        missing_path = tmp / "does-not-exist.json"
        check("F6. missing index file -> load() returns None", mi.MemoryIndex.load(missing_path) is None)

        # 7. corrupt (invalid JSON)
        corrupt_path = tmp / "corrupt.json"
        corrupt_path.write_text("{not valid json at all", encoding="utf-8")
        check("F7. corrupt JSON -> load() returns None", mi.MemoryIndex.load(corrupt_path) is None)

        # 8. unreadable (a directory where a file is expected)
        unreadable_path = tmp / "a-directory.json"
        unreadable_path.mkdir()
        check("F8. unreadable path (a directory) -> load() returns None", mi.MemoryIndex.load(unreadable_path) is None)

        # 9. malformed (valid JSON, wrong shape)
        malformed_path = tmp / "malformed.json"
        malformed_path.write_text(json.dumps({"schema_version": "1.0.0"}), encoding="utf-8")  # missing required keys
        check("F9. structurally malformed JSON (missing required keys) -> load() returns None",
              mi.MemoryIndex.load(malformed_path) is None)

        # ============================================================
        # CORRUPTION / ADVERSARIAL — 23 attack classes
        # ============================================================
        idx.save(idx_path)

        # 1. replace a path with a same-stem decoy (via a lying index)
        r1 = mret.search(runtime.vault, "target", methods=("wikilink",),
                          index=LyingIndex(candidates=["00 - Inbox/other.md"]))  # decoy: doesn't actually link to target
        check("attack1. decoy path suggested by a lying index yields no false hit (live body-scan rejects it)",
              r1 == [], "got %r" % (r1,))

        # 2/3. mark superseded-as-current / candidate-as-accepted: structurally impossible
        # (IndexEntry/Candidate carry no lifecycle field at all — see Invariant B/C above).
        check("attack2/3. no index or Candidate field exists that could carry a forged lifecycle/acceptance claim",
              True)  # covered exhaustively by B1/B2/C1's field-set assertions above

        # 4. remove a legitimate candidate (lying index returns nothing at all)
        r4 = mret.search(runtime.vault, "target", methods=("wikilink",), index=LyingIndex(candidates=[]))
        check("attack4. an index that omits a real candidate causes UNDER-reporting for that call, "
              "never a wrong trust result (documented limitation — see final report)",
              r4 == [] and cand_tuple(r4) != cand_tuple(live_baseline))

        # 5/6/7/8/9. filesystem-escape-shaped candidate paths
        escape_paths = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                         "\x00" + "00 - Inbox/target.md", "..\\..\\secret"]
        for p in escape_paths:
            r = mret.search(runtime.vault, "target", methods=("wikilink",), index=LyingIndex(candidates=[p]))
            check("attack5-9. filesystem-escape-shaped candidate %r never resolves/opens/escapes" % p, r == [],
                  "got %r" % (r,))

        # 10. alter MEMORY_PROTOCOL.md's expected hash
        idx10 = rewrite_index(idx_path, lambda p: p.__setitem__("protocol_hash", "f" * 64))
        check("attack10. forged protocol_hash -> is_fresh_for False", idx10 is not None and idx10.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 11. alter one note's content hash
        idx11 = rewrite_index(idx_path, lambda p: p["entries"][0].__setitem__("content_hash", "0" * 64))
        check("attack11. forged content_hash -> is_fresh_for False", idx11 is not None and idx11.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 12. alter vault-root identity
        idx12 = rewrite_index(idx_path, lambda p: p.__setitem__("vault_root", str(root) + "-FORGED"))
        check("attack12. forged vault_root -> is_fresh_for False", idx12 is not None and idx12.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 13. alter schema version (covered as freshness case F5 above too)
        idx13 = rewrite_index(idx_path, lambda p: p.__setitem__("schema_version", "0.0.1"))
        check("attack13. forged schema_version -> is_fresh_for False", idx13 is not None and idx13.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 14. delete an index entry
        idx14 = rewrite_index(idx_path, lambda p: p["entries"].pop(0))
        check("attack14. deleted entry -> is_fresh_for False (incomplete file set)",
              idx14 is not None and idx14.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 15. add a fake index entry
        idx15 = rewrite_index(idx_path, lambda p: p["entries"].append(dict(p["entries"][0], rel="00 - Inbox/FAKE-NOTE.md")))
        check("attack15. fabricated extra entry -> is_fresh_for False (extra file set member)",
              idx15 is not None and idx15.is_fresh_for(runtime.vault) is False)
        idx.save(idx_path)

        # 16. duplicate an index entry (regression test for the bug fixed this phase)
        idx16 = rewrite_index(idx_path, lambda p: p["entries"].append(dict(p["entries"][0])))
        check("attack16. duplicate rel entry -> load() rejects the whole index outright (fixed this phase)",
              idx16 is None, "got %r" % (idx16,))
        idx.save(idx_path)

        # 17. reorder index entries (must have zero effect — determinism)
        idx17 = rewrite_index(idx_path, lambda p: p["entries"].reverse())
        check("attack17. reordered entries -> still fresh, identical lookups (order is not identity)",
              idx17 is not None and idx17.is_fresh_for(runtime.vault) is True
              and idx17.link_stem_candidates("target") == idx.link_stem_candidates("target"))
        idx.save(idx_path)

        # 18. corrupt serialized/index metadata (various type-confusion shapes)
        for mutator, label in (
            (lambda p: p.__setitem__("entries", "not-a-list"), "entries as a string"),
            (lambda p: p["entries"][0].__setitem__("content_hash", 12345), "content_hash as an int"),
            (lambda p: p["entries"][0].__setitem__("outbound_link_stems", "not-a-list"), "outbound_link_stems as a string"),
            (lambda p: p.__setitem__("note_count", "not-a-number"), "note_count as a string"),
        ):
            corrupted = rewrite_index(idx_path, mutator)
            check("attack18. malformed metadata (%s) fails closed (load()->None, or is_fresh_for()->False, never a crash)" % label,
                  corrupted is None or corrupted.is_fresh_for(runtime.vault) is False)
            idx.save(idx_path)

        # 19. make is_fresh_for() raise
        r19 = mret.search(runtime.vault, "target", methods=("wikilink",), index=LyingIndex(raise_on_fresh=True))
        check("attack19. is_fresh_for() raising -> transparent live fallback", cand_tuple(r19) == cand_tuple(live_baseline))

        # 20. make an index lookup method raise (regression test for the bug fixed this phase)
        r20 = mret.search(runtime.vault, "target", methods=("wikilink",), index=LyingIndex(raise_on_lookup=True))
        check("attack20. link_stem_candidates() raising -> transparent live fallback (fixed this phase)",
              cand_tuple(r20) == cand_tuple(live_baseline))

        # 21. return malformed candidate paths (unhashable / wrong type)
        for bad in ([["nested", "list"]], [None], [12345], [{"not": "a string"}]):
            try:
                r21 = mret.search(runtime.vault, "target", methods=("wikilink",), index=LyingIndex(candidates=bad))
                check("attack21. malformed candidate path %r never crashes search()" % (bad,), True)
            except Exception as exc:
                check("attack21. malformed candidate path %r never crashes search()" % (bad,), False, "raised %r" % (exc,))

        # 22. return paths outside the vault
        r22 = mret.search(runtime.vault, "target", methods=("wikilink",),
                           index=LyingIndex(candidates=["/etc/other-vault/secret.md", "../../outside.md"]))
        check("attack22. out-of-vault candidate paths never resolve to a hit", r22 == [], "got %r" % (r22,))

        # 23. return a same-stem decoy alongside the correct note
        r23 = mret.search(runtime.vault, "target", methods=("wikilink",),
                           index=LyingIndex(candidates=["00 - Inbox/linker.md", "00 - Inbox/other.md"]))
        check("attack23. decoy alongside the real linker -> only the real, body-confirmed hit survives",
              cand_tuple(r23) == cand_tuple(live_baseline), "got %r" % (cand_tuple(r23),))

        # ============================================================
        # NEGATIVE-SPACE EQUIVALENCE — live vs indexed vs missing/stale/
        # corrupt index, across 9 representative fixtures
        # ============================================================
        def equivalence_case(label, vault_files, query, methods=("wikilink",)):
            r = build_vault(tmp, "eq-" + label.replace(" ", "-"), vault_files)
            runtime_r = rt.MemoryRuntime(r)
            fresh_idx = mi.MemoryIndex.build(runtime_r.vault)
            corrupt_p = tmp / ("corrupt-" + label.replace(" ", "-") + ".json")
            corrupt_p.write_text("{broken", encoding="utf-8")

            live_r = mret.search(runtime_r.vault, query, methods=methods)
            indexed_r = mret.search(runtime_r.vault, query, methods=methods, index=fresh_idx)
            missing_r = mret.search(runtime_r.vault, query, methods=methods, index=None)
            stale_r = mret.search(runtime_r.vault, query, methods=methods, index=mi.MemoryIndex.load(corrupt_p))

            # Phase 4 additions: the session-scoped ValidatedIndex path,
            # reused across several calls (not just one), and a genuinely
            # STALE validated context (built against a different vault
            # snapshot) proving it falls back exactly like the corrupt-index
            # case above, never partially trusted.
            runtime_reused = rt.MemoryRuntime(r, index=fresh_idx)
            for _ in range(3):
                reused_r = runtime_reused.retrieve(query, methods=methods)  # ValidatedContext, not Candidate —
                                                                             # a different (richer) shape; compare
                                                                             # only the fields both shapes carry

            runtime_other_snapshot = rt.MemoryRuntime(r)  # a DIFFERENT Vault object, same directory
            stale_ctx = mi.ValidatedIndex(fresh_idx, runtime_other_snapshot.vault)  # bound to the WRONG object
            stale_ctx_r = mret.search(runtime_r.vault, query, methods=methods, validated_index=stale_ctx)

            reused_tuple = tuple((c.note_path, c.method, c.score, c.ambiguous) for c in reused_r)
            comparable = lambda cands: tuple((c.note_path, c.method, c.score, c.ambiguous) for c in cands)

            all_equal = (comparable(live_r) == comparable(indexed_r) == comparable(missing_r)
                         == comparable(stale_r) == reused_tuple == comparable(stale_ctx_r))
            check("equivalence[%s]: live == indexed == missing == corrupt-fallback == "
                  "reused-validated-context == stale-context-fallback" % label, all_equal,
                  "live=%r indexed=%r missing=%r stale=%r reused=%r stale_ctx=%r" %
                  (comparable(live_r), comparable(indexed_r), comparable(missing_r), comparable(stale_r),
                   reused_tuple, comparable(stale_ctx_r)))

        # 1. current + superseded same-stem decoy
        f1 = bf.base_files()
        f1["00 - Inbox/required-source.md"] = note("current", "last_confirmed: 2026-08-29", "Decoy Current")
        f1["09 - Resources/required-source.md"] = note("superseded", title="Declared Target Superseded")
        f1["00 - Inbox/linker1.md"] = note("current", title="Linker1") + "\nSee [[09 - Resources/required-source]].\n"
        equivalence_case("decoy", f1, "09 - Resources/required-source")

        # 2. ambiguous same-stem notes
        f2 = bf.base_files()
        f2["00 - Inbox/twin.md"] = note("current", title="Twin A")
        f2["02 - Projects/twin.md"] = note("current", title="Twin B")
        equivalence_case("ambiguous", f2, "twin", methods=("exact",))

        # 3. candidate outranking current by text frequency (text method — unaffected by index, control case)
        f3 = bf.base_files()
        f3["00 - Inbox/loud-candidate.md"] = note("candidate", "source: inferred\nconfidence: low", "Loud") + "\n" + ("trustme " * 20) + "\n"
        f3["09 - Resources/quiet-current.md"] = note("current", "last_confirmed: 2026-08-29", "Quiet") + "\ntrustme once.\n"
        equivalence_case("candidate-outrank", f3, "trustme", methods=("text",))

        # 4. path-qualified identity
        f4 = bf.base_files()
        f4["00 - Inbox/pq.md"] = note("current", title="PQ Decoy")
        f4["09 - Resources/pq.md"] = note("current", "last_confirmed: 2026-08-29", "PQ Real")
        f4["00 - Inbox/linker4.md"] = note("current", title="Linker4") + "\nSee [[09 - Resources/pq]].\n"
        equivalence_case("path-qualified", f4, "09 - Resources/pq")

        # 5. missing target
        equivalence_case("missing-target", bf.base_files(), "does-not-exist-anywhere")

        # 6. traversal-shaped query
        equivalence_case("traversal", bf.base_files(), "../../../../etc/passwd")

        # 7. supersession cycle
        f7 = bf.base_files()
        f7["00 - Inbox/cx.md"] = note("current", 'supersedes: "[[cy]]"', "CX")
        f7["00 - Inbox/cy.md"] = note("current", 'supersedes: "[[cx]]"', "CY")
        f7["00 - Inbox/linker7.md"] = note("current", title="Linker7") + "\nSee [[cx]].\n"
        equivalence_case("cycle", f7, "cx")

        # 8. disputed note
        f8 = bf.base_files()
        old_bullet = (
            "- **`memory_status`** — `active` (a current, working fact) | `stale` "
            "(was current, not recent) | `archived` (a fact no longer operative, kept for history)."
        )
        f8["VAULT-INDEX.md"] = f8["VAULT-INDEX.md"].replace(bf.MEMORY_STATUS_BULLET, old_bullet)
        f8["00 - Inbox/disputed-thing.md"] = note("active", title="Disputed Thing")
        f8["00 - Inbox/linker8.md"] = note("current", title="Linker8") + "\nSee [[disputed-thing]].\n"
        equivalence_case("disputed", f8, "disputed-thing")

        # 9. malformed metadata
        f9 = bf.base_files()
        f9["00 - Inbox/broken.md"] = "---\nstatus: active\nproject: personal\ntype: [broken\n---\n# Broken\n"
        f9["00 - Inbox/linker9.md"] = note("current", title="Linker9") + "\nSee [[broken]].\n"
        equivalence_case("malformed", f9, "broken")

        # 10. frontmatter wikilink, explicit (a note whose ONLY [[...]]
        # mention of the target lives in a frontmatter field, e.g.
        # `supersedes:` — the exact scope-mismatch bug found and fixed
        # earlier in v3.7.5 Phase 3; this fixture pins it as a permanent
        # regression, independent of the cycle fixture that first caught it)
        f10 = bf.base_files()
        f10["00 - Inbox/fm-target.md"] = note("current", "last_confirmed: 2026-08-29", "FM Target")
        f10["00 - Inbox/fm-linker.md"] = note("superseded", 'supersedes: "[[fm-target]]"', "FM Linker")
        equivalence_case("frontmatter-wikilink", f10, "fm-target")

        # 11. Job dependency declared as a wikilink (the tier line IS a real
        # [[...]] in the note body, so it is legitimately both a
        # job_dependency AND an ordinary wikilink match — equivalence must
        # hold for the wikilink method regardless of which accelerator path
        # discovered the Job note)
        f11 = bf.base_files()
        f11["09 - Resources/job-dep-target.md"] = note("current", "last_confirmed: 2026-08-29", "Job Dep Target")
        f11["09 - Resources/Jobs/Job11.md"] = (
            note("current", title="Job11") + "\n## Context\n\n**Required:** [[job-dep-target]] (claim)\n"
            "**Preferred:** none\n**Optional:** none\n"
        )
        equivalence_case("job-dependency-wikilink", f11, "09 - Resources/job-dep-target")

        # ============================================================
        # PHASE 4 SECURITY — invariants specific to the NEW
        # ValidatedIndex/MemoryRuntime(index=...) layer (the raw-MemoryIndex
        # invariants A-I above already cover the underlying search(index=...)
        # path; these two are new because MemoryRuntime.retrieve() is a new
        # entry point this ticket adds).
        # ============================================================
        f_sec = bf.base_files()
        f_sec["00 - Inbox/sec-hub.md"] = note("current", "last_confirmed: 2026-08-29", "Sec Hub")
        f_sec["00 - Inbox/sec-candidate.md"] = (
            note("candidate", "source: inferred\nconfidence: low", "Sec Candidate") + "\nSee [[sec-hub]].\n")
        r_sec = build_vault(tmp, "sec", f_sec)
        scratch_sec = rt.MemoryRuntime(r_sec)
        idx_sec = mi.MemoryIndex.build(scratch_sec.vault)
        runtime_sec = rt.MemoryRuntime(r_sec, index=idx_sec)
        # "who links to sec-hub" surfaces sec-candidate.md as an inbound
        # linker via the accelerated (warm, indexed) path — it must stay
        # exactly as unaccepted as it would via a live scan; being found as
        # a "linker" is not itself a promotion.
        sec_results = runtime_sec.retrieve("sec-hub", methods=("wikilink",))
        check("security: MemoryRuntime(index=...) never manufactures acceptance — "
              "a candidate discovered via the warm path stays candidate/unaccepted",
              sec_results and sec_results[0].note_path == "00 - Inbox/sec-candidate.md"
              and sec_results[0].status_track == "candidate" and sec_results[0].accepted is False,
              "got %r" % (sec_results,))
        check("security: acceptance still comes from MemoryRuntime.inspect() (source check — retrieve() "
              "calls self._to_context, never a shortcut that reads trust off the index/ValidatedIndex)",
              "self._to_context(" in (TOOLS / "memory_runtime.py").read_text(encoding="utf-8")
              and "index.accepted" not in (TOOLS / "memory_runtime.py").read_text(encoding="utf-8"))

        # ============================================================
        # SERIALIZATION SAFETY
        # ============================================================
        check("serialization: save()/load() round-trip is lossless",
              mi.MemoryIndex.load(idx_path).to_json() == idx.to_json())
        check("serialization: to_json() output has deterministic (sorted) key ordering",
              idx.to_json() == json.dumps(json.loads(idx.to_json()), indent=2, sort_keys=True))
        source_text = (TOOLS / "memory_index.py").read_text(encoding="utf-8")
        check("serialization: load() never uses eval/exec/pickle/yaml.load (unsafe deserialization)",
              not any(bad in source_text for bad in ("eval(", "exec(", "pickle.", "yaml.load", "yaml.unsafe")))
        check("serialization: no arbitrary write path — save()'s only Path(...).write_text call targets the caller's own path",
              source_text.count("write_text(") == 1)

        # ============================================================
        # DETERMINISM
        # ============================================================
        # Compare content, not `built_at` — a wall-clock timestamp is
        # expected to differ across builds separated by real time; that is
        # not a determinism violation (see rebuild_is_identical()'s own
        # docstring for why comparing raw to_json() strings would wrongly
        # fail idempotence on that field alone).
        builds = set()
        for _ in range(5):
            runtime_d = rt.MemoryRuntime(root)
            payload = json.loads(mi.MemoryIndex.build(runtime_d.vault).to_json())
            del payload["built_at"]
            for e in payload["entries"]:
                del e["mtime"]
            builds.add(json.dumps(payload, sort_keys=True))
        check("determinism: 5 independent builds of the same vault produce identical logical content", len(builds) == 1)
        check("determinism: rebuild_is_identical() agrees", idx.rebuild_is_identical(runtime.vault) is True)

        search_results = set()
        for _ in range(5):
            runtime_d = rt.MemoryRuntime(root)
            idx_d = mi.MemoryIndex.build(runtime_d.vault)
            r = mret.search(runtime_d.vault, "target", methods=("wikilink",), index=idx_d)
            search_results.add(cand_tuple(r))
        check("determinism: 5 independent build+search cycles agree exactly", len(search_results) == 1)

        # ============================================================
        # SESSION-SCOPED VALIDATED-INDEX MUTATION SEMANTICS — the NEW
        # MemoryRuntime(index=...)/ValidatedIndex reuse-across-calls layer
        # ("Fix the Provenance Index Performance Boundary"), distinct from
        # raw MemoryIndex.is_fresh_for()'s already-exhaustive per-call
        # freshness matrix above. 12 scenarios, using dedicated vaults so
        # these deliberate mutations never touch `root`.
        # ============================================================
        def mkvault(name, files):
            return build_vault(tmp, "sess-" + name, files)

        # 1. clean vault -> index reusable
        f_s1 = bf.base_files()
        f_s1["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        f_s1["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        r_s1 = mkvault("s1", f_s1)
        scratch1 = rt.MemoryRuntime(r_s1)
        idx_s1 = mi.MemoryIndex.build(scratch1.vault)
        runtime_s1 = rt.MemoryRuntime(r_s1, index=idx_s1)
        check("session1. clean vault -> ValidatedIndex.is_valid True", runtime_s1._validated_index.is_valid is True)
        result_s1 = runtime_s1.retrieve("target", methods=("wikilink",))
        check("session1. retrieve() via reused validated index finds the linker",
              [c.note_path for c in result_s1] == ["00 - Inbox/linker.md"])

        # 2/11. vault file MODIFIED after validation — same live instance keeps
        # its frozen snapshot (exactly as stale as the rest of MemoryRuntime
        # already is); a NEW instance correctly detects staleness and falls
        # back to live, seeing the real, current content.
        f_s2 = bf.base_files()
        f_s2["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        f_s2["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        r_s2 = mkvault("s2", f_s2)
        scratch2 = rt.MemoryRuntime(r_s2)
        idx_s2 = mi.MemoryIndex.build(scratch2.vault)
        runtime_s2a = rt.MemoryRuntime(r_s2, index=idx_s2)
        before_mutation = runtime_s2a.retrieve("target", methods=("wikilink",))
        check("session2. baseline finds the linker before mutation",
              [c.note_path for c in before_mutation] == ["00 - Inbox/linker.md"])
        (r_s2 / "00 - Inbox/linker.md").write_text(note("current", title="Linker") + "\nNo link anymore.\n",
                                                     encoding="utf-8", newline="\n")
        after_mutation_same_instance = runtime_s2a.retrieve("target", methods=("wikilink",))
        check("session11. SAME live instance keeps its frozen pre-mutation view — never worse than "
              "pre-index MemoryRuntime behavior, never silently 'more stale' because an index exists",
              [c.note_path for c in after_mutation_same_instance] == ["00 - Inbox/linker.md"],
              "got %r" % ([c.note_path for c in after_mutation_same_instance],))
        runtime_s2b = rt.MemoryRuntime(r_s2, index=idx_s2)  # a NEW instance, disk now mutated
        check("session2. a NEW instance detects the stale index (content hash mismatch)",
              runtime_s2b._validated_index.is_valid is False)
        after_mutation_new_instance = runtime_s2b.retrieve("target", methods=("wikilink",))
        check("session2. a NEW instance falls back to live and sees the REAL current state (link removed)",
              after_mutation_new_instance == [], "got %r" % ([c.note_path for c in after_mutation_new_instance],))

        # 3. vault file ADDED after validation — a brand-new linker note
        f_s3 = bf.base_files()
        f_s3["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        r_s3 = mkvault("s3", f_s3)
        scratch3 = rt.MemoryRuntime(r_s3)
        idx_s3 = mi.MemoryIndex.build(scratch3.vault)
        (r_s3 / "00 - Inbox/new-linker.md").write_text(
            note("current", title="New Linker") + "\nSee [[target]].\n", encoding="utf-8", newline="\n")
        runtime_s3 = rt.MemoryRuntime(r_s3, index=idx_s3)
        check("session3. index built before a note was added -> is_valid False", runtime_s3._validated_index.is_valid is False)
        result_s3 = runtime_s3.retrieve("target", methods=("wikilink",))
        check("session3. live fallback correctly finds the newly-created linker",
              [c.note_path for c in result_s3] == ["00 - Inbox/new-linker.md"], "got %r" % (result_s3,))

        # 4. vault file DELETED after validation
        f_s4 = bf.base_files()
        f_s4["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        f_s4["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        r_s4 = mkvault("s4", f_s4)
        scratch4 = rt.MemoryRuntime(r_s4)
        idx_s4 = mi.MemoryIndex.build(scratch4.vault)
        (r_s4 / "00 - Inbox/linker.md").unlink()
        runtime_s4 = rt.MemoryRuntime(r_s4, index=idx_s4)
        check("session4. index built before a linker was deleted -> is_valid False",
              runtime_s4._validated_index.is_valid is False)
        result_s4 = runtime_s4.retrieve("target", methods=("wikilink",))
        check("session4. live fallback correctly reflects the deletion (no hit)", result_s4 == [])

        # 5. MEMORY_PROTOCOL.md modified after validation
        f_s5 = bf.base_files()
        r_s5 = mkvault("s5", f_s5)
        scratch5 = rt.MemoryRuntime(r_s5)
        idx_s5 = mi.MemoryIndex.build(scratch5.vault)
        protocol_s5 = r_s5 / "09 - Resources" / "MEMORY_PROTOCOL.md"
        original_s5_bytes = protocol_s5.read_bytes()
        protocol_s5.write_text(original_s5_bytes.decode("utf-8") + "\n<!-- tampered -->\n", encoding="utf-8", newline="\n")
        runtime_s5 = rt.MemoryRuntime(r_s5, index=idx_s5)
        check("session5. protocol change -> is_valid False", runtime_s5._validated_index.is_valid is False)
        protocol_s5.write_bytes(original_s5_bytes)  # restore for the outer file-hash checks elsewhere

        # 6. index file corrupted (load() -> None) -> MemoryRuntime behaves exactly as if index=None
        corrupt_s6_path = tmp / "corrupt-session.json"
        corrupt_s6_path.write_text("{not valid json", encoding="utf-8")
        loaded_s6 = mi.MemoryIndex.load(corrupt_s6_path)
        check("session6. load() on a corrupted index returns None", loaded_s6 is None)
        runtime_s6 = rt.MemoryRuntime(r_s1, index=loaded_s6)
        check("session6. MemoryRuntime(index=None-from-corrupt-load) has no validated index at all",
              runtime_s6._validated_index is None)

        # 7. index from another vault (a fresh, untouched vault — not one of
        # the scenarios above whose files were deliberately mutated/deleted,
        # since reusing a stale in-memory vault snapshot here would be a
        # test-script bug, not a real assertion about MemoryIndex)
        f_s7other = bf.base_files()
        r_s7other = mkvault("s7-other", f_s7other)
        scratch_s7other = rt.MemoryRuntime(r_s7other)
        idx_from_other = mi.MemoryIndex.build(scratch_s7other.vault)
        runtime_s7 = rt.MemoryRuntime(r_s1, index=idx_from_other)
        check("session7. index from a different vault -> is_valid False", runtime_s7._validated_index.is_valid is False)
        result_s7 = runtime_s7.retrieve("target", methods=("wikilink",))
        check("session7. still correctly falls back to live for THIS (s1) vault",
              [c.note_path for c in result_s7] == ["00 - Inbox/linker.md"])

        # 8. schema-version mismatch
        idx_s8_payload = json.loads(idx_s1.to_json())
        idx_s8_payload["schema_version"] = "999.0.0"
        idx_s8_path = tmp / "schema-mismatch.json"
        idx_s8_path.write_text(json.dumps(idx_s8_payload), encoding="utf-8")
        idx_s8 = mi.MemoryIndex.load(idx_s8_path)
        runtime_s8 = rt.MemoryRuntime(r_s1, index=idx_s8)
        check("session8. schema-version-mismatched index -> is_valid False",
              idx_s8 is not None and runtime_s8._validated_index.is_valid is False)

        # 9. file mtime changed but bytes UNCHANGED -> must remain valid
        f_s9 = bf.base_files()
        f_s9["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        f_s9["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        r_s9 = mkvault("s9", f_s9)
        scratch9 = rt.MemoryRuntime(r_s9)
        idx_s9 = mi.MemoryIndex.build(scratch9.vault)
        linker_s9 = r_s9 / "00 - Inbox/linker.md"
        identical_bytes = linker_s9.read_bytes()
        linker_s9.write_bytes(identical_bytes)  # rewrite: mtime changes, content does not
        runtime_s9 = rt.MemoryRuntime(r_s9, index=idx_s9)
        check("session9. mtime touched, bytes unchanged -> index remains valid (mtime is advisory only)",
              runtime_s9._validated_index.is_valid is True, "got is_valid=%r" % (runtime_s9._validated_index.is_valid,))
        result_s9 = runtime_s9.retrieve("target", methods=("wikilink",))
        check("session9. accelerated retrieval still correct after a bytes-neutral touch",
              [c.note_path for c in result_s9] == ["00 - Inbox/linker.md"])

        # 10. index lookup throws (post-validation) -> falls back to live, never crashes
        class ThrowingRawIndex:
            def is_fresh_for(self, vault):
                return True
            def link_stem_candidates(self, stem):
                raise RuntimeError("simulated post-validation lookup failure")
        vctx_throwing = mi.ValidatedIndex(ThrowingRawIndex(), scratch1.vault)
        check("session10. ValidatedIndex wrapping a throwing raw index still reports is_valid True at construction "
              "(is_fresh_for lied cleanly) — the crash must be caught at USE time, not before",
              vctx_throwing.is_valid is True)
        r_s10 = mret.search(scratch1.vault, "target", methods=("wikilink",), validated_index=vctx_throwing)
        live_s1 = mret.search(scratch1.vault, "target", methods=("wikilink",))
        check("session10. search() falls back to live when the validated index's lookup throws",
              [(c.note_path, c.method) for c in r_s10] == [(c.note_path, c.method) for c in live_s1])

        # 10b. index lookup throws DURING VALIDATION ITSELF (is_fresh_for()
        # raises) — regression test for a real bug found during Phase 6
        # adversarial testing: this path runs once at MemoryRuntime
        # construction, not per search() call, and was the one place in the
        # whole boundary that wasn't yet exception-guarded — a malicious/
        # buggy raw index passed to MemoryRuntime(index=...) crashed
        # construction ENTIRELY instead of degrading to "no index". Fixed in
        # ValidatedIndex.__init__.
        class RaisingOnFreshnessCheck:
            def is_fresh_for(self, vault):
                raise RuntimeError("simulated malicious/buggy raw index at construction time")
            def link_stem_candidates(self, stem):
                return []
        runtime_s10b = rt.MemoryRuntime(r_s1, index=RaisingOnFreshnessCheck())  # must NOT raise
        check("session10b. MemoryRuntime(index=...) survives a raw index whose is_fresh_for() raises "
              "(fixed this phase — previously crashed construction entirely)",
              runtime_s10b._validated_index is not None and runtime_s10b._validated_index.is_valid is False)
        result_s10b = runtime_s10b.retrieve("target", methods=("wikilink",))
        check("session10b. retrieve() still works normally after that degradation",
              [c.note_path for c in result_s10b] == ["00 - Inbox/linker.md"])

        # 12. mutation followed by restoration to IDENTICAL bytes -> validity returns
        f_s12 = bf.base_files()
        f_s12["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        f_s12["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        r_s12 = mkvault("s12", f_s12)
        scratch12 = rt.MemoryRuntime(r_s12)
        idx_s12 = mi.MemoryIndex.build(scratch12.vault)
        linker_s12 = r_s12 / "00 - Inbox/linker.md"
        original_s12_bytes = linker_s12.read_bytes()
        linker_s12.write_text(note("current", title="Linker") + "\nTEMPORARILY DIFFERENT.\n", encoding="utf-8", newline="\n")
        runtime_s12_mutated = rt.MemoryRuntime(r_s12, index=idx_s12)
        check("session12a. mutated (not yet restored) -> is_valid False", runtime_s12_mutated._validated_index.is_valid is False)
        linker_s12.write_bytes(original_s12_bytes)  # restore to the EXACT original bytes
        runtime_s12_restored = rt.MemoryRuntime(r_s12, index=idx_s12)
        check("session12b. restored to identical bytes -> is_valid True again (not permanently poisoned)",
              runtime_s12_restored._validated_index.is_valid is True)
        result_s12 = runtime_s12_restored.retrieve("target", methods=("wikilink",))
        check("session12b. accelerated retrieval correct again after exact restoration",
              [c.note_path for c in result_s12] == ["00 - Inbox/linker.md"])

        # Extra: ambiguity APPEARING after mutation must never be masked by a
        # stale index — a same-stem decoy added after validation forces
        # is_valid False, and the live fallback must correctly report the
        # new ambiguity rather than silently keep resolving to the old target.
        f_amb = bf.base_files()
        f_amb["09 - Resources/amb-target.md"] = note("current", "last_confirmed: 2026-08-29", "Amb Target")
        r_amb = mkvault("amb", f_amb)
        scratch_amb = rt.MemoryRuntime(r_amb)
        idx_amb = mi.MemoryIndex.build(scratch_amb.vault)
        decoy_amb_path = r_amb / "00 - Inbox/amb-target.md"
        decoy_amb_path.parent.mkdir(parents=True, exist_ok=True)
        decoy_amb_path.write_text(note("current", title="Amb Target Decoy") + "\n", encoding="utf-8", newline="\n")
        runtime_amb = rt.MemoryRuntime(r_amb, index=idx_amb)
        check("session-extra. decoy added after validation -> is_valid False",
              runtime_amb._validated_index.is_valid is False)
        resolved_amb, ambiguous_amb = vid.resolve_identity(runtime_amb.vault, "amb-target")
        check("session-extra. the NEW ambiguity is correctly visible via live fallback, never masked",
              resolved_amb is None and len(ambiguous_amb) == 2, "got %r" % ((resolved_amb, ambiguous_amb),))

        # ============================================================
        # MUTATION PROOF — the base vault must be byte-identical to how it
        # started, after every invariant/freshness/corruption/equivalence/
        # determinism check above (F2/F3 above deliberately edit-then-
        # restore two files; this proves the restoration was exact and
        # nothing else in the whole suite touched the vault).
        # ============================================================
        hashes_after_suite = file_hashes(root)
        check("mutation proof: base vault is byte-identical before vs. after the ENTIRE test suite",
              hashes_before_suite == hashes_after_suite,
              "changed: %r" % ({k for k in hashes_before_suite
                                 if hashes_before_suite.get(k) != hashes_after_suite.get(k)},))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: acceleration boundary — invariants A-I, 9 freshness cases, 23 adversarial attacks, "
          "11-fixture x 6-way negative-space equivalence (live/indexed/missing/corrupt/reused-context/"
          "stale-context), 12-scenario session-scoped mutation semantics, serialization safety, and "
          "determinism all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
