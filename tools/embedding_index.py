#!/usr/bin/env python3
"""Embedding index — the vector-storage half of the semantic retrieval
contract, structurally separate from tools/memory_index.py's MemoryIndex on
purpose (see that module's own docstring: never bolt vectors onto the
lexical index; two different concerns, two different modules).

STATUS: freshness/serialization machinery, usable with either a real or a
test-double backend. Building a REAL index requires an EmbeddingBackend whose
`is_available()` is True — tools/sentence_transformers_backend.py now ships
one (optional dependency, injected by the caller; see that module and
tools/embedding_backend.py). Every mechanism here was originally exercised
and proven correct using a deterministic TEST DOUBLE backend (see
tests/test_embedding_boundary.py) before the real backend existed, and both
are exercised today (tests/test_sentence_transformers_backend.py covers the
real one) — nothing about correctness, freshness, or fail-closed behavior was
ever deferred; this module itself still adds zero dependencies regardless of
which backend a caller supplies.

WHAT THIS INDEX STORES, and why each field exists:
    - vault_root, protocol_hash            same vault-identity binding as
                                            MemoryIndex, for the same reason
    - backend_id, model_id, dimensions     a vector is only ever comparable
                                            to another vector produced by the
                                            SAME backend and model at the SAME
                                            dimensionality — mismatched on any
                                            one of these three, the whole
                                            index is untrusted, exactly like a
                                            schema-version mismatch
    - schema_version                       this MODULE's own on-disk format
    - per-note: rel, content_hash, vector  the vector is a pure function of
                                            the note's exact bytes at the
                                            moment it was embedded; content_hash
                                            is what proves that correspondence
                                            still holds

WHAT THIS INDEX NEVER STORES (identical prohibition to MemoryIndex, extended
to vectors): memory_status, source, confidence, supersedes/superseded_by, any
other lifecycle field, a resolved wikilink target, an `accepted` bit, or
anything resembling a trust/confidence score. A similarity score computed
FROM a stored vector is retrieval metadata, produced at query time, never
persisted as if it were a fact about the note.

THE FRESHNESS MODEL: the identical binary, whole-snapshot gate
tools/memory_index.py's MemoryIndex.is_fresh_for() already uses, extended
with three additional identity checks (backend_id, model_id, dimensions) that
have no lexical-index equivalent because a lexical index has no notion of
"which model produced this." Any single mismatch — schema, vault, protocol,
backend, model, dimensions, or any note's content hash — untrusts the WHOLE
index for that call. No partial credit, no per-vector trust, matching
MemoryIndex's own reasoning: partial trust is not the design MemoryRuntime's
downstream validation is built to make safe (identity resolution and
lifecycle validation still run per-candidate, but candidate DISCOVERY itself
needs whole-snapshot completeness to be provably equivalent to a live scan;
see MemoryIndex's docstring for the full reasoning, unchanged here).

Standard library only. No network access. No write access to the vault.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from embedding_backend import EmbeddingBackend, cosine_similarity  # noqa: E402

EMBEDDING_SCHEMA_VERSION = "1.0.0"
PROTOCOL_REL_HINT = "MEMORY_PROTOCOL.md"


@dataclass
class EmbeddingEntry:
    """One note's vector plus exactly the facts needed to prove it still
    corresponds to that note's current content — no lifecycle field, no
    resolved link, no trust value."""
    rel: str
    content_hash: str            # sha256 of raw file bytes at embedding time
    vector: tuple                # tuple[float, ...], length == header.dimensions


@dataclass
class EmbeddingHeader:
    schema_version: str
    vault_root: str
    protocol_hash: str | None
    backend_id: str
    model_id: str
    dimensions: int
    note_count: int
    built_at: str                 # informational only, never a staleness authority


class EmbeddingIndex:
    """Disposable, rebuildable. Deleting it (or never building one) leaves
    every caller's behavior identical to no semantic retrieval at all — the
    identical disposability guarantee MemoryIndex already provides for
    lexical retrieval."""

    def __init__(self, header: EmbeddingHeader, entries: list):
        self.header = header
        self.entries = tuple(sorted(entries, key=lambda e: e.rel))
        self._by_rel = {e.rel: e for e in self.entries}
        # Precomputed ONCE, at construction (build() or load()), not per
        # query — profiling nearest() at N=5000 (v3.7.5 Phase 11 performance
        # audit) found each entry's own vector magnitude was being
        # recomputed from scratch on EVERY nearest() call, even though a
        # given entry's vector — and therefore its magnitude — never changes
        # for this index's whole lifetime. This produces IDENTICAL
        # similarity values to computing it fresh every time (see
        # nearest()'s docstring for the equivalence argument); it is a pure
        # exact optimization, never an approximation.
        self._magnitudes = {e.rel: math.sqrt(sum(x * x for x in e.vector)) for e in self.entries}

    # --------------------------------------------------------------- build
    @classmethod
    def build(cls, vault: "object", backend: "EmbeddingBackend", text_of=None) -> "EmbeddingIndex | None":
        """Returns None (never raises, never builds a partial/garbage index)
        if the backend is unavailable, misreports its own shape, or fails
        while embedding — a failed build is reported as "no index," exactly
        like a corrupted one, never a half-populated one silently returned
        as if it were complete.

        `text_of(note) -> str` lets the caller decide what text surface gets
        embedded (Phase 4's text-extraction boundary is a retrieval-policy
        decision, not something this storage module should hardcode) —
        defaults to the note's own full text (frontmatter included), matching
        the exact scan surface tools/memory_index.py's own build() already
        uses for the identical scope-mismatch reason documented there."""
        if text_of is None:
            text_of = lambda note: note["text"]
        try:
            if not backend.is_available():
                return None
            dims = backend.dimensions()
            if not isinstance(dims, int) or dims <= 0:
                return None
        except Exception:
            return None

        protocol_hash = None
        for note in vault.notes:
            if note["basename"] == PROTOCOL_REL_HINT and len(note["dir_parts"]) == 1 \
                    and note["dir_parts"][0].endswith("Resources"):
                protocol_hash = hashlib.sha256(note["path"].read_bytes()).hexdigest()
                break

        entries = []
        try:
            for note in sorted(vault.notes, key=lambda n: n["rel"]):
                raw_bytes = note["path"].read_bytes()
                content_hash = hashlib.sha256(raw_bytes).hexdigest()
                vector = tuple(float(x) for x in backend.embed(text_of(note)))
                if len(vector) != dims or not all(math.isfinite(x) for x in vector):
                    return None  # a backend that can't honor its own declared dimensionality/finiteness is not trusted
                entries.append(EmbeddingEntry(rel=note["rel"], content_hash=content_hash, vector=vector))
            backend_id = backend.backend_id()
            model_id = backend.model_id()
        except Exception:
            return None

        import time as _time
        header = EmbeddingHeader(
            schema_version=EMBEDDING_SCHEMA_VERSION, vault_root=str(vault.root.resolve()),
            protocol_hash=protocol_hash, backend_id=backend_id, model_id=model_id, dimensions=dims,
            note_count=len(entries), built_at=_time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()),
        )
        return cls(header, entries)

    def rebuild_is_identical(self, vault: "object", backend: "EmbeddingBackend") -> bool:
        """Idempotence check, identical philosophy to
        MemoryIndex.rebuild_is_identical(): compares every field except
        `built_at` (a wall-clock timestamp, explicitly non-authoritative —
        see EmbeddingHeader's own docstring), which will legitimately differ
        between two content-identical builds separated by real time. A real
        backend rebuild also costs a full re-embedding pass, so this is
        naturally more expensive to call than the lexical MemoryIndex's
        equivalent — callers should use it for verification/testing, not as
        a per-query check."""
        def _content_only(payload):
            return {k: v for k, v in payload.items() if k != "built_at"}
        fresh = EmbeddingIndex.build(vault, backend)
        if fresh is None:
            return False
        return _content_only(json.loads(self.to_json())) == _content_only(json.loads(fresh.to_json()))

    # ----------------------------------------------------------- freshness
    def is_fresh_for(self, vault: "object", backend: "EmbeddingBackend") -> bool:
        """Binary, all-or-nothing — identical philosophy to
        MemoryIndex.is_fresh_for(), extended with backend/model/dimension
        identity. Never raises."""
        try:
            if self.header.schema_version != EMBEDDING_SCHEMA_VERSION:
                return False
            if self.header.vault_root != str(vault.root.resolve()):
                return False
            if not backend.is_available():
                return False
            if self.header.backend_id != backend.backend_id() or self.header.model_id != backend.model_id():
                return False
            if self.header.dimensions != backend.dimensions():
                return False

            live_protocol_hash = None
            for note in vault.notes:
                if note["basename"] == PROTOCOL_REL_HINT and len(note["dir_parts"]) == 1 \
                        and note["dir_parts"][0].endswith("Resources"):
                    live_protocol_hash = hashlib.sha256(note["path"].read_bytes()).hexdigest()
                    break
            if self.header.protocol_hash != live_protocol_hash:
                return False

            live_hashes = {n["rel"]: hashlib.sha256(n["path"].read_bytes()).hexdigest() for n in vault.notes}
            indexed_hashes = {e.rel: e.content_hash for e in self.entries}
            return live_hashes == indexed_hashes
        except Exception:
            return False

    # -------------------------------------------------------------- lookup
    def nearest(self, query_vector: Sequence, limit: int = 10) -> list:
        """Returns [(rel, similarity), ...] sorted by descending similarity,
        deterministic tie-break by ascending rel. Never resolves identity,
        never reports a lifecycle field — a bare (path, number) pair, the
        minimum this contract allows (see embedding_backend.py's module
        docstring: this is candidate generation, nothing else). Malformed
        `query_vector` (wrong length vs this index's own dimensionality,
        non-finite values) yields [] rather than raising or comparing
        incomparable vectors.

        PERFORMANCE NOTE (v3.7.5 Phase 11): computes the query vector's own
        magnitude exactly ONCE for this whole call (not once per entry, as a
        naive per-pair `cosine_similarity()` call would), and reuses each
        entry's magnitude from `self._magnitudes` (precomputed once at
        construction — see __init__). This is mathematically IDENTICAL to
        calling `embedding_backend.cosine_similarity(query_vector, e.vector)`
        for every entry — same formula (dot product / product of
        magnitudes), same zero-magnitude-returns-0.0 edge case, same
        results, every time — just without redundantly recomputing the two
        magnitude terms that don't change within (query magnitude) or across
        (entry magnitude) calls. `embedding_backend.cosine_similarity()`
        itself is untouched and remains the simple, general-purpose
        reference implementation for any caller that isn't doing a
        whole-index scan."""
        if not isinstance(query_vector, (list, tuple)) or len(query_vector) != self.header.dimensions:
            return []
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in query_vector):
            return []
        query_mag = math.sqrt(sum(x * x for x in query_vector))
        scored = []
        if query_mag == 0.0:
            scored = [(e.rel, 0.0) for e in self.entries]  # matches cosine_similarity's own zero-magnitude rule exactly
        else:
            for e in self.entries:
                entry_mag = self._magnitudes[e.rel]
                if entry_mag == 0.0:
                    sim = 0.0
                else:
                    dot = sum(x * y for x, y in zip(query_vector, e.vector))
                    sim = dot / (query_mag * entry_mag)
                scored.append((e.rel, sim))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit] if limit is not None else scored

    def entry(self, rel: str):
        return self._by_rel.get(rel)

    # ------------------------------------------------------- (de)serialize
    def to_json(self) -> str:
        payload = {
            "schema_version": self.header.schema_version, "vault_root": self.header.vault_root,
            "protocol_hash": self.header.protocol_hash, "backend_id": self.header.backend_id,
            "model_id": self.header.model_id, "dimensions": self.header.dimensions,
            "note_count": self.header.note_count, "built_at": self.header.built_at,
            "entries": [{"rel": e.rel, "content_hash": e.content_hash, "vector": list(e.vector)}
                        for e in self.entries],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def save(self, path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "EmbeddingIndex | None":
        """Never raises. Every field's type is explicitly checked before use
        — including per-vector length/finiteness against the header's own
        declared dimensionality — treating the file as untrusted input,
        exactly like MemoryIndex.load()."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            required = {"schema_version", "vault_root", "protocol_hash", "backend_id", "model_id",
                        "dimensions", "note_count", "built_at", "entries"}
            if not required.issubset(payload.keys()):
                return None
            if not all(isinstance(payload[k], str) for k in
                       ("schema_version", "vault_root", "backend_id", "model_id", "built_at")):
                return None
            if payload["protocol_hash"] is not None and not isinstance(payload["protocol_hash"], str):
                return None
            if not isinstance(payload["dimensions"], int) or payload["dimensions"] <= 0:
                return None
            if not isinstance(payload["note_count"], int) or not isinstance(payload["entries"], list):
                return None

            dims = payload["dimensions"]
            entries = []
            seen_rels = set()
            for item in payload["entries"]:
                if not isinstance(item, dict):
                    return None
                if not {"rel", "content_hash", "vector"}.issubset(item.keys()):
                    return None
                if not isinstance(item["rel"], str) or not isinstance(item["content_hash"], str):
                    return None
                vec = item["vector"]
                if not isinstance(vec, list) or len(vec) != dims:
                    return None
                if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vec):
                    return None
                vec = [float(x) for x in vec]
                if not all(math.isfinite(x) for x in vec):
                    return None  # NaN/inf is malformed, never silently clamped or accepted
                if item["rel"] in seen_rels:
                    return None  # duplicate rel: malformed, reject the whole index (same rule as MemoryIndex.load())
                seen_rels.add(item["rel"])
                entries.append(EmbeddingEntry(rel=item["rel"], content_hash=item["content_hash"], vector=tuple(vec)))

            header = EmbeddingHeader(
                schema_version=payload["schema_version"], vault_root=payload["vault_root"],
                protocol_hash=payload["protocol_hash"], backend_id=payload["backend_id"],
                model_id=payload["model_id"], dimensions=dims, note_count=payload["note_count"],
                built_at=payload["built_at"],
            )
            return cls(header, entries)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


class ValidatedEmbeddingIndex:
    """The semantic-side equivalent of tools/memory_index.py's
    ValidatedIndex — added in v3.7.5 Phase 11 with MEASURED justification,
    not for symmetry: benchmarking the per-call `is_fresh_for()` gate found
    it consumed 28% of total semantic query time at N=100, rising to 56% at
    N=5000 (the fraction GROWS with vault size, since is_fresh_for() itself
    is an O(N) whole-vault hash pass) — the exact same shape of finding that
    justified ValidatedIndex on the lexical side, now confirmed on the
    semantic side too.

    Identical philosophy, extended with the two extra identity dimensions
    EmbeddingIndex's freshness gate already checks (backend_id/model_id/
    dimensions have no lexical equivalent): the ONE `is_fresh_for(vault,
    backend)` check happens exactly once, at construction, bound by Python
    object identity to both the exact `vv.Vault` instance AND the exact
    backend instance it was checked against — either one being a DIFFERENT
    object on a later call makes this context automatically, structurally
    unusable, no separate invalidation logic required. See
    tools/memory_index.py's ValidatedIndex docstring for the full argument
    (object identity vs. path/id-string equality, and why this doesn't
    detect a live mid-session file change any more than the lexical
    equivalent does, or than MemoryRuntime's own `_cycle_members`/`_by_rel`
    caches already don't)."""

    def __init__(self, embedding_index: "EmbeddingIndex", vault: "object", backend: "EmbeddingBackend"):
        self._index = embedding_index
        self._vault = vault
        self._backend = backend
        try:
            self.is_valid = (embedding_index is not None
                              and bool(embedding_index.is_fresh_for(vault, backend)))
        except Exception:
            self.is_valid = False

    def usable_for(self, vault: "object") -> bool:
        return self.is_valid and vault is self._vault

    def nearest(self, query_vector: Sequence, limit: int = 10) -> list:
        return self._index.nearest(query_vector, limit=limit)
