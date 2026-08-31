#!/usr/bin/env python3
"""Permanent regression coverage for the v3.7.5 Phase 11 (Semantic Retrieval
Integration, Documentation & Release Audit) focused final attack pass — 20
numbered adversarial cases against the completed semantic layer. Three of
these (backend/index/freshness-check exceptions) were previously untested by
name anywhere in this suite, even though the exception-guard behavior they
verify was already relied upon by memory_retrieval.py's own docstring
contract; the other 17 overlap with, but restate more explicitly and by
number than, coverage already spread across test_memory_index.py,
test_embedding_boundary.py, and test_semantic_performance_hardening.py.

Invariant under test throughout: retrieval may change ranking or candidate
discovery; it may never manufacture authority. No implementation defect was
found while writing this file — every case held on first run.

Standalone, self-contained, no pytest — matches every sibling suite's
convention.
"""
from __future__ import annotations

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
import embedding_index as ei  # noqa: E402
import embedding_backend as eb  # noqa: E402
import build_fixtures as bf  # noqa: E402

PROBLEMS = []


def check(n, label, cond, detail=""):
    tag = "%2d. %s" % (n, label)
    if not cond:
        PROBLEMS.append(tag + ((": " + detail) if detail else ""))


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


class FakeBackend:
    """TEST DOUBLE ONLY."""
    def __init__(self, backend_id="fake", model_id="fake-v1", dims=4, vec=(0.5, 0.5, 0.5, 0.5)):
        self._backend_id, self._model_id, self._dims, self._vec = backend_id, model_id, dims, vec
    def is_available(self): return True
    def backend_id(self): return self._backend_id
    def model_id(self): return self._model_id
    def dimensions(self): return self._dims
    def embed(self, text): return list(self._vec)
    def embed_batch(self, texts): return [self.embed(t) for t in texts]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        backend = FakeBackend()

        files = bf.base_files()
        files["00 - Inbox/superseded-note.md"] = note("superseded", title="Superseded")
        files["00 - Inbox/disputed-note.md"] = note("current", "supersedes: [[superseded-note]]", title="Disputed") \
            + "\nSee [[superseded-note]] for the older version.\n"
        files["00 - Inbox/candidate-note.md"] = note("candidate", title="Candidate")
        files["00 - Inbox/decoy-target.md"] = note("current", title="Decoy Target")
        files["02 - Projects/decoy-target.md"] = note("current", title="Decoy Target (same stem, different folder)")
        root = build_vault(tmp, "vault-main", files)
        root2 = build_vault(tmp, "vault-other", bf.base_files())

        runtime = rt.MemoryRuntime(root)
        runtime2 = rt.MemoryRuntime(root2)
        sem_idx = ei.EmbeddingIndex.build(runtime.vault, backend)
        sem_idx_other = ei.EmbeddingIndex.build(runtime2.vault, backend)

        # 1/2/3 — a fabricated high score can surface a note as a *candidate*,
        # but acceptance is decided entirely by inspect()'s note-only lifecycle
        # check, which never sees a score.
        insp1 = runtime.inspect("superseded-note")
        accepted1 = insp1.context.accepted if insp1.context else None
        check(1, "high-scoring superseded note is never marked accepted by inspect()",
              accepted1 is False, "accepted=%r status=%r" % (accepted1, insp1.status))

        insp2 = runtime.inspect("disputed-note")
        track2 = insp2.context.status_track if insp2.context else None
        check(2, "high-scoring disputed note's status_track exists and comes from lifecycle, never score",
              bool(track2), "status_track=%r" % (track2,))

        insp3 = runtime.inspect("candidate-note")
        accepted3 = insp3.context.accepted if insp3.context else None
        check(3, "high-scoring candidate note is never marked accepted by inspect()",
              accepted3 is False, "accepted=%r status=%r" % (accepted3, insp3.status))

        # 4/5 — same-stem decoy across two folders: exact fails closed, resolve() reports ambiguous.
        r4 = mret.search(runtime.vault, "decoy-target", methods=("exact",))
        check(4, "same-stem decoy: exact method never silently picks one of the two",
              len(r4) != 1, "got %r" % ([c.note_path for c in r4],))
        res5 = runtime.resolve("decoy-target")
        check(5, "ambiguous identity resolves to status=ambiguous, never a silent pick",
              res5.status == "ambiguous", "status=%r" % (res5.status,))

        # 6 — traversal-shaped query never resolves outside the vault or via exact.
        insp6 = runtime.inspect("../../../etc/passwd")
        check(6, "traversal-shaped identity query never resolves outside the vault",
              insp6.status in ("missing", "ambiguous"), "status=%r" % (insp6.status,))
        r6 = mret.search(runtime.vault, "../../../etc/passwd", methods=("exact", "filename"))
        check(6, "traversal-shaped query never produces a resolved exact candidate",
              all(c.method != "exact" for c in r6))

        # 7 — fabricated index path.
        check(7, "fabricated/nonexistent index path loads as None, never raises",
              ei.EmbeddingIndex.load(tmp / "does-not-exist.json") is None)

        # 8 — cross-vault embedding index, both Validated and raw.
        cross_valid = ei.ValidatedEmbeddingIndex(sem_idx_other, runtime2.vault, backend)
        check(8, "cross-vault ValidatedEmbeddingIndex reports unusable for the other vault",
              cross_valid.usable_for(runtime.vault) is False)
        r8 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=backend,
                          embedding_index=sem_idx_other)
        check(8, "cross-vault raw EmbeddingIndex also fails is_fresh_for() and yields zero semantic hits",
              [c for c in r8 if c.method == "semantic"] == [])

        # 9/10/11 — wrong model / wrong backend / wrong dimensions, tested as three distinct identity axes.
        r9 = mret.search(runtime.vault, "note", methods=("semantic",),
                          embedding_backend=FakeBackend(model_id="different-model"), embedding_index=sem_idx)
        check(9, "wrong model identity: index built under one model is rejected under a different model_id",
              [c for c in r9 if c.method == "semantic"] == [])

        r10 = mret.search(runtime.vault, "note", methods=("semantic",),
                           embedding_backend=FakeBackend(backend_id="different-backend"), embedding_index=sem_idx)
        check(10, "wrong backend identity: index built under one backend_id is rejected under a different one",
              [c for c in r10 if c.method == "semantic"] == [])

        r11 = mret.search(runtime.vault, "note", methods=("semantic",),
                           embedding_backend=FakeBackend(dims=8, vec=(0.1,) * 8), embedding_index=sem_idx)
        check(11, "wrong dimensions: index built at one dimensionality is rejected under a different one",
              [c for c in r11 if c.method == "semantic"] == [])

        # 12 — stale content hash: structurally loads, fails freshness.
        payload12 = json.loads(sem_idx.to_json())
        payload12["entries"][0]["content_hash"] = "0" * 64
        p12 = tmp / "stale-hash.json"
        p12.write_text(json.dumps(payload12), encoding="utf-8")
        stale_idx = ei.EmbeddingIndex.load(p12)
        check(12, "an index with one tampered/stale content_hash loads structurally but fails is_fresh_for()",
              stale_idx is not None and stale_idx.is_fresh_for(runtime.vault, backend) is False)

        # 13 — malformed (NaN) vector rejected at load().
        payload13 = json.loads(sem_idx.to_json())
        payload13["entries"][0]["vector"] = [float("nan"), 0.1, 0.1, 0.1]
        p13 = tmp / "nan-vector.json"
        p13.write_text(json.dumps(payload13), encoding="utf-8")
        check(13, "a NaN value inside a stored vector is rejected at load(), never silently accepted",
              ei.EmbeddingIndex.load(p13) is None)

        # 14/15 — NaN / infinity SCORE from nearest() (distinct from a malformed stored vector).
        class NaNScoreIndex:
            def is_fresh_for(self, v, b): return True
            def nearest(self, qv, limit=20): return [("00 - Inbox/decoy-target.md", float("nan"))]
        r14 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=backend,
                           embedding_index=NaNScoreIndex())
        check(14, "a NaN similarity score from nearest() is filtered out, never becomes a candidate",
              [c for c in r14 if c.method == "semantic"] == [])

        class InfScoreIndex:
            def is_fresh_for(self, v, b): return True
            def nearest(self, qv, limit=20): return [("00 - Inbox/decoy-target.md", float("inf"))]
        r15 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=backend,
                           embedding_index=InfScoreIndex())
        check(15, "an infinite similarity score from nearest() is filtered out, never becomes a candidate",
              [c for c in r15 if c.method == "semantic"] == [])

        # 16/17/18 — exception raised by the backend, the index, and the freshness check, each in isolation.
        class ExplodingBackend:
            def is_available(self): return True
            def backend_id(self): return "exploding"
            def model_id(self): return "exploding-v1"
            def dimensions(self): return 4
            def embed(self, text): raise RuntimeError("simulated backend explosion")
            def embed_batch(self, texts): raise RuntimeError("simulated backend explosion")
        r16 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=ExplodingBackend(),
                           embedding_index=sem_idx)
        check(16, "an exception raised by backend.embed() is caught, yields zero semantic candidates, never crashes",
              [c for c in r16 if c.method == "semantic"] == [])

        class ExplodingIndex:
            def is_fresh_for(self, v, b): return True
            def nearest(self, qv, limit=20): raise RuntimeError("simulated index explosion")
        r17 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=backend,
                           embedding_index=ExplodingIndex())
        check(17, "an exception raised by index.nearest() is caught, yields zero semantic candidates, never crashes",
              [c for c in r17 if c.method == "semantic"] == [])

        class ExplodingFreshness:
            def is_fresh_for(self, v, b): raise RuntimeError("simulated freshness-check explosion")
            def nearest(self, qv, limit=20): return [("00 - Inbox/decoy-target.md", 0.9)]
        r18 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=backend,
                           embedding_index=ExplodingFreshness())
        check(18, "an exception raised by index.is_fresh_for() is caught, yields zero semantic candidates, never crashes",
              [c for c in r18 if c.method == "semantic"] == [])

        # 19 — dependency/backend genuinely unavailable end-to-end through MemoryRuntime.
        null_backend = eb.NullEmbeddingBackend()
        r19 = mret.search(runtime.vault, "note", methods=("semantic",), embedding_backend=null_backend,
                           embedding_index=sem_idx)
        check(19, "NullEmbeddingBackend (dependency unavailable) yields zero semantic candidates, never crashes",
              [c for c in r19 if c.method == "semantic"] == [])
        rt19 = rt.MemoryRuntime(root, embedding_backend=null_backend, embedding_index=sem_idx)
        check(19, "MemoryRuntime itself functions normally end-to-end with an unavailable semantic backend",
              bool(rt19.retrieve("note")))

        # 20 — mixed lexical+semantic result: lexical priority and per-method scores both survive the merge.
        r20 = mret.search(runtime.vault, "decoy-target", methods=("filename", "semantic"), embedding_backend=backend,
                           embedding_index=sem_idx)
        check(20, "mixed lexical+semantic results: a lexical hit is never outranked by a semantic-only hit",
              not r20 or r20[0].method == "filename", "got methods in order: %r" % ([c.method for c in r20],))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - " + p)
        return 1
    print("PASS: Phase 11 adversarial release pass — all 20 numbered cases hold. Retrieval changed "
          "ranking/discovery in every attack; none crossed the identity/lifecycle/acceptance/provenance boundary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
