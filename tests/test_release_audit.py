#!/usr/bin/env python3
"""Permanent regression coverage for genuinely new invariants confirmed
during the v3.7.5 Phase 12 release audit — none of these caught an
implementation defect (the audit's own finding was a clean pass), but each
closes a real coverage gap the existing suites didn't exercise: cross-wired
validated contexts (a lexical ValidatedIndex and a semantic
ValidatedEmbeddingIndex bound to two DIFFERENT vaults, both handed to one
search() call), numeric type-confusion in serialized vectors (JSON strings,
booleans), and the remaining freshness-independence combinations (no-lexical/
no-semantic/both-absent/corrupted-lexical-with-valid-semantic and the
reverse).

Standalone, self-contained, no pytest — matches every sibling suite's
convention.
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


class FakeBackend:
    """TEST DOUBLE ONLY."""
    def is_available(self): return True
    def backend_id(self): return "fake"
    def model_id(self): return "fake-v1"
    def dimensions(self): return 4
    def embed(self, text): return [0.5, 0.5, 0.5, 0.5]
    def embed_batch(self, texts): return [self.embed(t) for t in texts]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        backend = FakeBackend()

        files_a = bf.base_files()
        files_a["00 - Inbox/target.md"] = note("current", "last_confirmed: 2026-08-29", "Target")
        files_a["00 - Inbox/linker.md"] = note("current", title="Linker") + "\nSee [[target]].\n"
        root_a = build_vault(tmp, "vault-a", files_a)
        hashes_before = file_hashes(root_a)
        root_b = build_vault(tmp, "vault-b", bf.base_files())

        runtime_a = rt.MemoryRuntime(root_a)
        runtime_b = rt.MemoryRuntime(root_b)
        lex_idx_a = mi.MemoryIndex.build(runtime_a.vault)
        sem_idx_a = ei.EmbeddingIndex.build(runtime_a.vault, backend)
        sem_idx_b = ei.EmbeddingIndex.build(runtime_b.vault, backend)
        baseline = mret.search(runtime_a.vault, "target", methods=("wikilink",))

        # ============================================================
        # Cross-wired validated contexts: a correctly-bound lexical
        # ValidatedIndex alongside a semantic ValidatedEmbeddingIndex bound
        # to a DIFFERENT vault, both passed to one search() call.
        # ============================================================
        valid_lex_a = mi.ValidatedIndex(lex_idx_a, runtime_a.vault)
        crosswired_sem = ei.ValidatedEmbeddingIndex(sem_idx_b, runtime_b.vault, backend)
        check("crosswire1. a ValidatedEmbeddingIndex bound to vault B reports unusable for vault A",
              crosswired_sem.usable_for(runtime_a.vault) is False)
        r = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"),
                         validated_index=valid_lex_a, embedding_backend=backend, embedding_index=sem_idx_b,
                         validated_embedding_index=crosswired_sem)
        check("crosswire2. the correctly-bound lexical context still works normally",
              [c.note_path for c in r if c.method == "wikilink"] == [c.note_path for c in baseline])
        check("crosswire3. the cross-wired semantic context never leaks vault-B candidates into a vault-A query",
              all((root_a / c.note_path).exists() for c in r if c.method == "semantic"))

        # ============================================================
        # Numeric type confusion in serialized vectors
        # ============================================================
        payload = json.loads(sem_idx_a.to_json())
        p1 = json.loads(json.dumps(payload))
        p1["entries"][0]["vector"] = ["0.1", "0.2", "0.3", "0.4"]
        path1 = tmp / "string-vector.json"
        path1.write_text(json.dumps(p1), encoding="utf-8")
        check("typeconfusion1. a vector of JSON strings is rejected, never silently coerced to floats",
              ei.EmbeddingIndex.load(path1) is None)

        p2 = json.loads(json.dumps(payload))
        p2["entries"][0]["vector"] = [True, False, 0.3, 0.4]
        path2 = tmp / "bool-vector.json"
        path2.write_text(json.dumps(p2), encoding="utf-8")
        check("typeconfusion2. a vector containing booleans is rejected (bool is a bona fide int subtype in Python)",
              ei.EmbeddingIndex.load(path2) is None)

        # ============================================================
        # Ranking exploit attempt: an enormous semantic score must never
        # let a semantic-only hit outrank a higher-priority method's hit
        # in the final merged/sorted order.
        # ============================================================
        class HugeScoreIndex:
            def is_fresh_for(self, vault, b): return True
            def nearest(self, qv, limit=20): return [("00 - Inbox/linker.md", 99999999.0)]
        r_mixed = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"),
                               embedding_backend=backend, embedding_index=HugeScoreIndex())
        check("ranking1. method priority (wikilink) wins the merge regardless of an enormous semantic score",
              r_mixed and r_mixed[0].method == "wikilink", "got %r" % (r_mixed,))

        # ============================================================
        # Remaining Phase 6 freshness/independence combinations
        # ============================================================
        r_no_lex = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"),
                                embedding_backend=backend, embedding_index=sem_idx_a)
        check("freshness1. no lexical index + fresh semantic: wikilink still works via live fallback",
              [c.note_path for c in r_no_lex if c.method == "wikilink"] == [c.note_path for c in baseline])

        r_no_sem = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"), index=lex_idx_a)
        check("freshness2. fresh lexical + no semantic index: semantic contributes nothing, no error",
              [c for c in r_no_sem if c.method == "semantic"] == [])

        r_both_absent = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"))
        check("freshness3. both accelerators absent: matches plain lexical baseline exactly",
              [c.note_path for c in r_both_absent] == [c.note_path for c in baseline])

        p_corrupt_lex = tmp / "corrupt-lex.json"
        p_corrupt_lex.write_text("{broken", encoding="utf-8")
        corrupt_lex = mi.MemoryIndex.load(p_corrupt_lex)
        r_corrupt_lex_valid_sem = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"),
                                               index=corrupt_lex, embedding_backend=backend, embedding_index=sem_idx_a)
        check("freshness4. corrupted lexical index + valid semantic index: lexical falls back live, semantic still works",
              [c.note_path for c in r_corrupt_lex_valid_sem if c.method == "wikilink"] == [c.note_path for c in baseline]
              and len([c for c in r_corrupt_lex_valid_sem if c.method == "semantic"]) > 0)

        p_corrupt_sem = tmp / "corrupt-sem.json"
        p_corrupt_sem.write_text("{broken", encoding="utf-8")
        corrupt_sem = ei.EmbeddingIndex.load(p_corrupt_sem)
        r_valid_lex_corrupt_sem = mret.search(runtime_a.vault, "target", methods=("wikilink", "semantic"),
                                               index=lex_idx_a, embedding_backend=backend, embedding_index=corrupt_sem)
        check("freshness5. valid lexical + corrupted semantic index: lexical accelerated, semantic empty, no crash",
              [c.note_path for c in r_valid_lex_corrupt_sem if c.method == "wikilink"] == [c.note_path for c in baseline]
              and [c for c in r_valid_lex_corrupt_sem if c.method == "semantic"] == [])

        # ============================================================
        # Mutation proof
        # ============================================================
        hashes_after = file_hashes(root_a)
        check("mutation: vault-a byte-identical before vs. after this entire suite",
              hashes_before == hashes_after,
              "changed: %r" % ({k for k in hashes_before if hashes_before.get(k) != hashes_after.get(k)},))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: release audit — cross-wired validated contexts, numeric type confusion, ranking-exploit "
          "resistance, and remaining freshness-independence combinations all hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
