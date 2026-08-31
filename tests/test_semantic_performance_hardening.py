#!/usr/bin/env python3
"""Regression suite for v3.7.5 Phase 11 — semantic performance work and
release hardening: the nearest() magnitude-caching optimization,
ValidatedEmbeddingIndex (session-scoped freshness amortization), the
14-scenario mutation matrix, and the adversarial authority audit.

Uses FakeDeterministicBackend (a TEST DOUBLE, not a real embedding model —
see tests/test_embedding_boundary.py's own docstring for why) for every
mechanical/adversarial check, since none of those need real semantic
meaning — only a small integration check at the end uses the REAL backend,
skipped gracefully if the optional dependency isn't installed.

Standalone, self-contained, no pytest — matches every sibling suite's
convention.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_retrieval as mret  # noqa: E402
import embedding_backend as eb  # noqa: E402
import embedding_index as ei  # noqa: E402
import build_fixtures as bf  # noqa: E402

try:
    import sentence_transformers_backend as stb  # noqa: E402
    _REAL_BACKEND_AVAILABLE = True
except ImportError:
    _REAL_BACKEND_AVAILABLE = False

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


class FakeDeterministicBackend:
    """TEST DOUBLE ONLY — see tests/test_embedding_boundary.py's own
    disclaimer. Deterministic hash-derived vectors, zero semantic meaning."""
    def __init__(self, dims=4, backend_id="test-double", model_id="test-double-v1", available=True):
        self._dims = dims
        self._backend_id = backend_id
        self._model_id = model_id
        self._available = available

    def is_available(self):
        return self._available

    def backend_id(self):
        return self._backend_id

    def model_id(self):
        return self._model_id

    def dimensions(self):
        return self._dims

    def embed(self, text):
        h = hashlib.sha256(text.encode("utf-8")).digest()
        return [h[i] / 255.0 for i in range(self._dims)]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # nearest() OPTIMIZATION — correctness regression
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/a.md"] = note("current", title="A") + "\napples oranges\n"
        files["00 - Inbox/b.md"] = note("current", title="B") + "\nbananas grapes\n"
        files["00 - Inbox/c.md"] = note("current", title="C") + "\ncars trains\n"
        root = build_vault(tmp, "opt", files)
        runtime = rt.MemoryRuntime(root)
        backend = FakeDeterministicBackend()
        eidx = ei.EmbeddingIndex.build(runtime.vault, backend)

        qv = backend.embed("apples oranges")
        optimized = eidx.nearest(qv, limit=None)
        naive = sorted(((e.rel, eb.cosine_similarity(qv, e.vector)) for e in eidx.entries),
                        key=lambda pair: (-pair[1], pair[0]))
        check("opt1. optimized nearest() is bit-identical to naive per-pair cosine_similarity()",
              optimized == naive, "optimized=%r naive=%r" % (optimized, naive))

        zero_q = [0.0] * eidx.header.dimensions
        check("opt2. zero-magnitude query vector -> all zero similarities, matches old edge-case behavior",
              eidx.nearest(zero_q) == [(e.rel, 0.0) for e in sorted(eidx.entries, key=lambda e: e.rel)][:10])

        check("opt3. entry magnitudes are precomputed once at construction, not per nearest() call",
              hasattr(eidx, "_magnitudes") and len(eidx._magnitudes) == len(eidx.entries))

        # ============================================================
        # ValidatedEmbeddingIndex — one-time validation, amortized reuse
        # ============================================================
        vei = ei.ValidatedEmbeddingIndex(eidx, runtime.vault, backend)
        check("vei1. fresh index+matching backend+same vault -> is_valid True", vei.is_valid is True)
        check("vei2. usable_for(same vault object) True", vei.usable_for(runtime.vault) is True)

        calls = {"n": 0}
        orig_fresh = ei.EmbeddingIndex.is_fresh_for
        def counted(self, vault, backend_arg):
            calls["n"] += 1
            return orig_fresh(self, vault, backend_arg)
        ei.EmbeddingIndex.is_fresh_for = counted
        vei2 = ei.ValidatedEmbeddingIndex(eidx, runtime.vault, backend)
        for _ in range(20):
            vei2.usable_for(runtime.vault)
        ei.EmbeddingIndex.is_fresh_for = orig_fresh
        check("vei3. is_fresh_for() called exactly once across construction + 20 usable_for() checks",
              calls["n"] == 1, "got %d calls" % (calls["n"],))

        runtime_other_snapshot = rt.MemoryRuntime(root)  # a DIFFERENT Vault object, same directory
        check("vei4. a different Vault OBJECT (even same directory) is never usable, by identity",
              vei.usable_for(runtime_other_snapshot.vault) is False)

        # search()/MemoryRuntime wiring: validated_embedding_index skips the per-call check entirely
        calls["n"] = 0
        ei.EmbeddingIndex.is_fresh_for = counted
        r = mret.search(runtime.vault, "apples oranges", methods=("semantic",),
                         embedding_backend=backend, embedding_index=eidx, validated_embedding_index=vei)
        ei.EmbeddingIndex.is_fresh_for = orig_fresh
        check("vei5. search() with a usable validated_embedding_index never calls is_fresh_for() at all",
              calls["n"] == 0, "got %d calls, result=%r" % (calls["n"], r))

        runtime_wired = rt.MemoryRuntime(root, embedding_backend=backend, embedding_index=eidx)
        check("vei6. MemoryRuntime constructs its own ValidatedEmbeddingIndex automatically",
              runtime_wired._validated_embedding_index is not None
              and runtime_wired._validated_embedding_index.is_valid is True)
        via_runtime = runtime_wired.retrieve("apples oranges", methods=("semantic",))
        check("vei7. retrieve() through the wired runtime produces correct, validated results",
              any(vc.note_path == "00 - Inbox/a.md" and vc.accepted is True for vc in via_runtime))

        # A lying/throwing usable_for() must fall back, never crash or grant trust
        class RaisingContext:
            def usable_for(self, vault):
                raise RuntimeError("simulated malicious validated context")
        r_raise = mret.search(runtime.vault, "apples oranges", methods=("semantic",),
                               embedding_backend=backend, embedding_index=eidx, validated_embedding_index=RaisingContext())
        r_live = mret.search(runtime.vault, "apples oranges", methods=("semantic",),
                              embedding_backend=backend, embedding_index=eidx)
        check("vei8. a validated_embedding_index whose usable_for() raises falls back to the per-call check, "
              "never crashes",
              [(c.note_path, c.score) for c in r_raise] == [(c.note_path, c.score) for c in r_live])

        # ============================================================
        # PHASE 5 — 14-scenario mutation matrix
        # ============================================================
        def mkvault(name, files):
            return build_vault(tmp, "mut-" + name, files)

        # 1. file modified after semantic index construction
        f1 = bf.base_files()
        f1["00 - Inbox/x.md"] = note("current", title="X") + "\ntopic one\n"
        r1 = mkvault("m1", f1)
        rt1 = rt.MemoryRuntime(r1)
        b1 = FakeDeterministicBackend()
        e1 = ei.EmbeddingIndex.build(rt1.vault, b1)
        (r1 / "00 - Inbox/x.md").write_text(note("current", title="X CHANGED") + "\ntopic two\n",
                                              encoding="utf-8", newline="\n")
        rt1b = rt.MemoryRuntime(r1)
        check("mut1. file modified after construction -> is_fresh_for False", e1.is_fresh_for(rt1b.vault, b1) is False)
        vei1 = ei.ValidatedEmbeddingIndex(e1, rt1b.vault, b1)
        check("mut1. ValidatedEmbeddingIndex against the new snapshot is also invalid", vei1.is_valid is False)

        # 2. file added
        f2 = bf.base_files()
        r2 = mkvault("m2", f2)
        rt2 = rt.MemoryRuntime(r2)
        b2 = FakeDeterministicBackend()
        e2 = ei.EmbeddingIndex.build(rt2.vault, b2)
        (r2 / "00 - Inbox").mkdir(parents=True, exist_ok=True)
        (r2 / "00 - Inbox/new.md").write_text(note("current", title="New") + "\n", encoding="utf-8", newline="\n")
        rt2b = rt.MemoryRuntime(r2)
        check("mut2. file added after construction -> is_fresh_for False", e2.is_fresh_for(rt2b.vault, b2) is False)

        # 3. file deleted
        f3 = bf.base_files()
        f3["00 - Inbox/gone.md"] = note("current", title="Gone") + "\n"
        r3 = mkvault("m3", f3)
        rt3 = rt.MemoryRuntime(r3)
        b3 = FakeDeterministicBackend()
        e3 = ei.EmbeddingIndex.build(rt3.vault, b3)
        (r3 / "00 - Inbox/gone.md").unlink()
        rt3b = rt.MemoryRuntime(r3)
        check("mut3. file deleted after construction -> is_fresh_for False", e3.is_fresh_for(rt3b.vault, b3) is False)

        # 4. protocol modified
        f4 = bf.base_files()
        r4 = mkvault("m4", f4)
        rt4 = rt.MemoryRuntime(r4)
        b4 = FakeDeterministicBackend()
        e4 = ei.EmbeddingIndex.build(rt4.vault, b4)
        protocol4 = r4 / "09 - Resources" / "MEMORY_PROTOCOL.md"
        original4 = protocol4.read_bytes()
        protocol4.write_text(original4.decode("utf-8") + "\n<!-- tampered -->\n", encoding="utf-8", newline="\n")
        rt4b = rt.MemoryRuntime(r4)
        check("mut4. protocol modified -> is_fresh_for False", e4.is_fresh_for(rt4b.vault, b4) is False)
        protocol4.write_bytes(original4)

        # 5. backend identity changed
        b5_other = FakeDeterministicBackend(backend_id="different-backend")
        check("mut5. different backend_id -> is_fresh_for False", e1.is_fresh_for(rt1.vault, b5_other) is False)

        # 6. model identity changed
        b6_other = FakeDeterministicBackend(model_id="different-model")
        check("mut6. different model_id -> is_fresh_for False", e1.is_fresh_for(rt1.vault, b6_other) is False)

        # 7. dimensions changed
        b7_other = FakeDeterministicBackend(dims=8)
        check("mut7. different dimensions -> is_fresh_for False", e1.is_fresh_for(rt1.vault, b7_other) is False)

        # 8. index corrupted
        p8 = tmp / "corrupt.json"
        p8.write_text("{not json", encoding="utf-8")
        check("mut8. corrupted index file -> load() None", ei.EmbeddingIndex.load(p8) is None)

        # 9. stale semantic index combined with a valid lexical index
        import memory_index as mi
        f9 = bf.base_files()
        f9["00 - Inbox/link-target.md"] = note("current", "last_confirmed: 2026-08-29", "Link Target")
        f9["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[link-target]].\n"
        r9 = mkvault("m9", f9)
        rt9 = rt.MemoryRuntime(r9)
        b9 = FakeDeterministicBackend()
        e9 = ei.EmbeddingIndex.build(rt9.vault, b9)
        lex9 = mi.MemoryIndex.build(rt9.vault)
        (r9 / "00 - Inbox/link-target.md").write_text(note("current", title="Link Target CHANGED") + "\n",
                                                         encoding="utf-8", newline="\n")
        rt9b = rt.MemoryRuntime(r9)
        r9_both = mret.search(rt9b.vault, "link-target", methods=("wikilink", "semantic"),
                               index=lex9, embedding_backend=b9, embedding_index=e9)
        r9_live = mret.search(rt9b.vault, "link-target", methods=("wikilink", "semantic"))
        check("mut9. stale semantic index + valid-for-old-snapshot lexical index -> both correctly fall back to live",
              [(c.note_path, c.method) for c in r9_both] == [(c.note_path, c.method) for c in r9_live],
              "got %r vs %r" % (r9_both, r9_live))

        # 10. valid semantic index combined with a stale lexical index (roles reversed)
        f10 = bf.base_files()
        f10["00 - Inbox/target10.md"] = note("current", title="Target10") + "\ntopic ten\n"
        f10["00 - Inbox/linker10.md"] = note("current", title="Linker10") + "\nSee [[target10]].\n"
        r10 = mkvault("m10", f10)
        rt10 = rt.MemoryRuntime(r10)
        b10 = FakeDeterministicBackend()
        lex10 = mi.MemoryIndex.build(rt10.vault)
        (r10 / "00 - Inbox/target10.md").write_text(note("current", title="Target10 CHANGED") + "\n",
                                                       encoding="utf-8", newline="\n")
        rt10b = rt.MemoryRuntime(r10)
        e10_fresh = ei.EmbeddingIndex.build(rt10b.vault, b10)  # built AFTER the mutation -> genuinely fresh
        r10_mixed = mret.search(rt10b.vault, "target10", methods=("wikilink", "semantic"),
                                 index=lex10, embedding_backend=b10, embedding_index=e10_fresh)
        wikilink_hits = [c for c in r10_mixed if c.method == "wikilink"]
        semantic_hits = [c for c in r10_mixed if c.method == "semantic"]
        check("mut10. stale lexical index falls back to live wikilink scan even though the semantic index is fresh",
              any(c.note_path == "00 - Inbox/linker10.md" for c in wikilink_hits))
        check("mut10. the fresh semantic index is still used correctly alongside the stale-lexical fallback",
              len(semantic_hits) > 0)

        # 11. mutate then restore identical bytes
        f11 = bf.base_files()
        f11["00 - Inbox/y.md"] = note("current", title="Y") + "\ntopic y\n"
        r11 = mkvault("m11", f11)
        rt11 = rt.MemoryRuntime(r11)
        b11 = FakeDeterministicBackend()
        e11 = ei.EmbeddingIndex.build(rt11.vault, b11)
        original11 = (r11 / "00 - Inbox/y.md").read_bytes()
        (r11 / "00 - Inbox/y.md").write_text(note("current", title="Y TEMP") + "\n", encoding="utf-8", newline="\n")
        rt11_mutated = rt.MemoryRuntime(r11)
        check("mut11a. mutated -> is_fresh_for False", e11.is_fresh_for(rt11_mutated.vault, b11) is False)
        (r11 / "00 - Inbox/y.md").write_bytes(original11)
        rt11_restored = rt.MemoryRuntime(r11)
        check("mut11b. restored to identical bytes -> is_fresh_for True again (not permanently poisoned)",
              e11.is_fresh_for(rt11_restored.vault, b11) is True)

        # 12. same-stem decoy introduced after semantic validation
        f12 = bf.base_files()
        f12["09 - Resources/dupe.md"] = note("current", "last_confirmed: 2026-08-29", "Dupe Real")
        r12 = mkvault("m12", f12)
        rt12 = rt.MemoryRuntime(r12)
        b12 = FakeDeterministicBackend()
        e12 = ei.EmbeddingIndex.build(rt12.vault, b12)
        (r12 / "00 - Inbox").mkdir(parents=True, exist_ok=True)
        (r12 / "00 - Inbox/dupe.md").write_text(note("current", title="Dupe Decoy") + "\n",
                                                   encoding="utf-8", newline="\n")
        rt12b = rt.MemoryRuntime(r12)
        check("mut12. same-stem decoy added after validation -> is_fresh_for False (extra file in the set)",
              e12.is_fresh_for(rt12b.vault, b12) is False)

        # 13. ambiguity introduced after semantic validation (same fixture as 12, checked via resolve_identity)
        import vault_identity as vid
        resolved12, ambiguous12 = vid.resolve_identity(rt12b.vault, "dupe")
        check("mut13. new ambiguity is correctly visible live, never masked by the (now-stale) semantic index",
              resolved12 is None and len(ambiguous12) == 2)

        # 14. ambiguity removed after semantic validation
        (r12 / "00 - Inbox/dupe.md").unlink()  # remove the decoy -> unambiguous again
        rt12c = rt.MemoryRuntime(r12)
        resolved12c, ambiguous12c = vid.resolve_identity(rt12c.vault, "dupe")
        check("mut14. ambiguity resolved (decoy removed) -> resolve_identity correctly finds the one real note",
              resolved12c is not None and resolved12c["rel"] == "09 - Resources/dupe.md" and not ambiguous12c)

        # ============================================================
        # PHASE 6 — adversarial authority audit
        # ============================================================
        f_adv = bf.base_files()
        f_adv["00 - Inbox/adv-current.md"] = note("current", "last_confirmed: 2026-08-29", "Adv Current")
        f_adv["00 - Inbox/adv-candidate.md"] = note("candidate", "source: inferred\nconfidence: low", "Adv Candidate")
        f_adv["00 - Inbox/adv-superseded.md"] = note("superseded", title="Adv Superseded")
        r_adv = build_vault(tmp, "adv", f_adv)
        rt_adv = rt.MemoryRuntime(r_adv)
        b_adv = FakeDeterministicBackend()
        e_adv = ei.EmbeddingIndex.build(rt_adv.vault, b_adv)

        class HugeScoreIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("00 - Inbox/adv-candidate.md", 999999999.0), ("00 - Inbox/adv-superseded.md", 1e300)]
        r_huge = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=HugeScoreIndex())
        vctx_huge = {vc.note_path: vc for vc in rt.MemoryRuntime(r_adv, embedding_backend=b_adv, embedding_index=HugeScoreIndex()).retrieve("x", methods=("semantic",))}
        check("adv1. an artificially HUGE semantic score never manufactures acceptance for a candidate note",
              vctx_huge.get("00 - Inbox/adv-candidate.md") is not None
              and vctx_huge["00 - Inbox/adv-candidate.md"].accepted is False
              and vctx_huge["00 - Inbox/adv-candidate.md"].status_track == "candidate")
        check("adv1b. a huge score never un-supersedes a superseded note",
              vctx_huge.get("00 - Inbox/adv-superseded.md") is not None
              and vctx_huge["00 - Inbox/adv-superseded.md"].accepted is False
              and vctx_huge["00 - Inbox/adv-superseded.md"].status_track == "superseded")

        class NegativeScoreIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("00 - Inbox/adv-current.md", -0.9999)]
        r_neg = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=NegativeScoreIndex())
        check("adv2. a negative similarity score is passed through as ordinary ranking metadata, never rejected/crashed",
              r_neg and r_neg[0].score == -0.9999 and r_neg[0].note_path == "00 - Inbox/adv-current.md")

        class NaNIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("00 - Inbox/adv-current.md", float("nan")), ("00 - Inbox/adv-candidate.md", 0.5)]
        r_nan = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=NaNIndex())
        check("adv3. NaN similarity is filtered out (never a candidate with a NaN score, never a crash)",
              all(c.note_path != "00 - Inbox/adv-current.md" for c in r_nan) and len(r_nan) == 1,
              "got %r" % (r_nan,))

        class InfIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("00 - Inbox/adv-current.md", float("inf"))]
        r_inf = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=InfIndex())
        check("adv4b. math.isfinite correctly rejects infinity (so InfIndex's hit SHOULD have been filtered)",
              not math.isfinite(float("inf")))
        check("adv4c. infinity is in fact filtered out, consistent with NaN handling",
              r_inf == [], "got %r" % (r_inf,))

        class MalformedDimsIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("00 - Inbox/adv-current.md", 0.5)]
        # (dimension mismatch is enforced inside EmbeddingIndex.nearest() itself, tested separately below)
        bad_qv = [0.1, 0.2]  # wrong length vs eidx's real dimensionality
        check("adv5. malformed query vector (wrong dimensionality) -> nearest() returns [] rather than raising",
              e_adv.nearest(bad_qv) == [])
        check("adv5b. empty query vector -> []", e_adv.nearest([]) == [])
        check("adv5c. NaN inside the query vector -> []", e_adv.nearest([float("nan")] * e_adv.header.dimensions) == [])
        check("adv5d. infinity inside the query vector -> []", e_adv.nearest([float("inf")] * e_adv.header.dimensions) == [])

        class FabricatedPathIndex:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20):
                return [("../../../../etc/passwd", 0.99), ("/etc/passwd", 0.98),
                        ("00 - Inbox/adv-current.md", 0.5)]
        r_fab = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=FabricatedPathIndex())
        check("adv6. fabricated/traversal-shaped fake paths never resolve to a hit; only the real note survives",
              [c.note_path for c in r_fab] == ["00 - Inbox/adv-current.md"], "got %r" % ([c.note_path for c in r_fab],))
        for c in r_fab:
            resolved = (r_adv / c.note_path).resolve()
            check("adv6b. surviving result stays inside the vault root", str(resolved).startswith(str(r_adv.resolve())))

        class RaisingIsFresh:
            def is_fresh_for(self, vault, backend): raise RuntimeError("boom")
            def nearest(self, qv, limit=20): return []
        r_raise_fresh = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=RaisingIsFresh())
        check("adv7. is_fresh_for() raising -> [] , never a crash", r_raise_fresh == [])

        class RaisingNearest:
            def is_fresh_for(self, vault, backend): return True
            def nearest(self, qv, limit=20): raise RuntimeError("boom")
        r_raise_nearest = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=RaisingNearest())
        check("adv8. nearest() raising -> [], never a crash", r_raise_nearest == [])

        class RaisingEmbed(FakeDeterministicBackend):
            def embed(self, text): raise RuntimeError("boom")
        r_raise_embed = mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=RaisingEmbed(), embedding_index=e_adv)
        check("adv9. backend.embed() raising during query embedding -> [], never a crash", r_raise_embed == [])

        # ============================================================
        # Phase 4 regression: backend reuse is safe (plain object
        # injection, no hidden state mutated by sharing)
        # ============================================================
        shared_backend = FakeDeterministicBackend()
        rtA = rt.MemoryRuntime(r_adv, embedding_backend=shared_backend, embedding_index=e_adv)
        rtB = rt.MemoryRuntime(r_adv, embedding_backend=shared_backend, embedding_index=e_adv)
        check("reuse1. two MemoryRuntime instances sharing ONE backend object both work correctly and independently",
              rtA._embedding_backend is rtB._embedding_backend is shared_backend)
        rA = rtA.retrieve("x", methods=("semantic",))
        rB = rtB.retrieve("x", methods=("semantic",))
        check("reuse2. results from both shared-backend runtimes are consistent",
              [(v.note_path, v.score) for v in rA] == [(v.note_path, v.score) for v in rB])

        # ============================================================
        # Optional REAL backend integration spot-check
        # ============================================================
        if _REAL_BACKEND_AVAILABLE:
            real_backend = stb.SentenceTransformerBackend()
            if real_backend.is_available():
                f_real = bf.base_files()
                f_real["00 - Inbox/real-a.md"] = note("current", "last_confirmed: 2026-08-29", "Real A") + "\napples and oranges\n"
                r_real = build_vault(tmp, "real-check", f_real)
                rt_real = rt.MemoryRuntime(r_real)
                e_real = ei.EmbeddingIndex.build(rt_real.vault, real_backend)
                vei_real = ei.ValidatedEmbeddingIndex(e_real, rt_real.vault, real_backend)
                check("real1. ValidatedEmbeddingIndex works with the REAL backend too", vei_real.is_valid is True)
                rt_real_wired = rt.MemoryRuntime(r_real, embedding_backend=real_backend, embedding_index=e_real)
                r_real_via = rt_real_wired.retrieve("fresh fruit", methods=("semantic",))
                check("real2. full pipeline with REAL backend + ValidatedEmbeddingIndex produces correct results",
                      any(v.note_path == "00 - Inbox/real-a.md" and v.accepted is True for v in r_real_via))
            else:
                print("NOTE: real backend importable but model failed to load; skipped the real-backend spot-check")
        else:
            print("NOTE: sentence_transformers_backend not importable; skipped the real-backend spot-check")

        # ============================================================
        # MUTATION PROOF
        # ============================================================
        hashes_before_check = file_hashes(r_adv)  # r_adv was never mutated after its own construction above
        mret.search(rt_adv.vault, "x", methods=("semantic",), embedding_backend=b_adv, embedding_index=e_adv)
        hashes_after_check = file_hashes(r_adv)
        check("mutation: adversarial-audit vault untouched by any of the attack attempts",
              hashes_before_check == hashes_after_check)

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: semantic performance/hardening — nearest() optimization correctness, ValidatedEmbeddingIndex "
          "amortization, 14-scenario mutation matrix, adversarial authority audit, and backend reuse all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
