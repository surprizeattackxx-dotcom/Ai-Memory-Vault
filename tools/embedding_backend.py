#!/usr/bin/env python3
"""Embedding backend contract — a backend-neutral interface for semantic
retrieval, defined WITHOUT choosing, requiring, or importing any actual
embedding model, ML runtime, or external dependency.

STATUS: contract, plus the one always-unavailable stub (`NullEmbeddingBackend`)
that needs no dependency at all. This file itself adds no dependency and
never will — see ACCELERATION_LAYER.md's own "no dependency is added by this
document" stance, extended to the embedding boundary. A real backend now
exists (tools/sentence_transformers_backend.py, optional dependency manifest
at tools/requirements-semantic.txt), but it lives OUTSIDE this stdlib-only
core, in its own file, and is INJECTED by whoever calls
tools/memory_retrieval.py's search() — never imported unconditionally here,
and never required for core operation. This is the "deferred + adapter"
architecture in its completed state: the shape was defined first; a real
implementation now fills it, without this contract file itself gaining a
single dependency.

Five responsibilities this project keeps deliberately separate — conflating
any two of them is exactly how an accelerator becomes a second authority:
    1. embedding generation   -> EmbeddingBackend (this file)
    2. vector storage          -> tools/embedding_index.py's EmbeddingIndex
    3. vector similarity math  -> a plain function, no backend needed at all
    4. semantic candidate gen  -> memory_retrieval.py's _search_semantic()
    5. identity / lifecycle /
       acceptance               -> vault_identity.py / memory_runtime.py,
                                    completely untouched by any of the above

WHAT AN EmbeddingBackend MUST NEVER DO: resolve identity, decide ambiguity,
read or report memory_status/lifecycle, compute `accepted`, or persist
anything about a note beyond its own vector. A backend answers exactly one
question — "what is the vector for this text?" — and nothing else.

Standard library only (typing.Protocol, no runtime dependency). No network
access. No file access of any kind — a backend embeds TEXT it is handed; it
never reads a path itself.
"""
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Structural (duck-typed) contract — matches this codebase's existing
    convention of never requiring `isinstance` against a concrete class
    (MemoryIndex/ValidatedIndex are consulted the same way, by method
    presence, not by type). `@runtime_checkable` only enables `isinstance`
    checks for TESTS that want to assert conformance; production code here
    never uses it to gate behavior — a backend that merely LOOKS right by
    `isinstance` but misbehaves at call time is handled the same way a
    misbehaving MemoryIndex already is: every call site that uses a backend
    is exception-guarded and falls back to "no semantic candidates," never
    to a crash and never to a guess."""

    def is_available(self) -> bool:
        """Cheap, side-effect-free check: can this backend actually produce
        embeddings right now (model loaded, API reachable, whatever it
        needs)? Callers must check this before relying on `embed()` — a
        backend is never assumed available merely because it was supplied."""
        ...

    def backend_id(self) -> str:
        """A stable string identifying the IMPLEMENTATION (e.g. "openai-api",
        "local-minilm", "test-double") — not the model. Part of an
        EmbeddingIndex's freshness identity: vectors from one backend are
        never assumed comparable to vectors from another."""
        ...

    def model_id(self) -> str:
        """A stable string identifying the specific MODEL/version producing
        vectors (e.g. "text-embedding-3-small", "all-MiniLM-L6-v2@1.0"). Two
        different models are never assumed to produce comparable vectors
        even if `backend_id()` matches."""
        ...

    def dimensions(self) -> int:
        """The fixed length of every vector this backend produces. An
        EmbeddingIndex whose stored dimensionality doesn't match this is
        never trusted, regardless of anything else matching."""
        ...

    def embed(self, text: str) -> Sequence[float]:
        """Return the embedding vector for one piece of text. May raise for
        any reason (unavailable, malformed input, backend failure) — every
        caller in this repo wraps this in a try/except and treats a raised
        exception identically to "no semantic candidates," never a crash."""
        ...

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Batch form of embed(), for backends where embedding many texts
        together is materially cheaper than one at a time. Must return
        vectors in the same order as `texts`. A backend with no real batching
        advantage may implement this as a plain per-item loop — callers never
        assume anything about HOW a backend batches, only that the output
        aligns positionally with the input."""
        ...


class NullEmbeddingBackend:
    """The only EmbeddingBackend this repository ships. Always unavailable,
    always raises if actually asked to embed — a caller that skips the
    `is_available()` check and calls `embed()` anyway gets a clear,
    unambiguous failure, exception-guarded by every call site, never a
    silent zero-vector or fabricated result standing in for a real one."""

    def is_available(self) -> bool:
        return False

    def backend_id(self) -> str:
        return "none"

    def model_id(self) -> str:
        return "none"

    def dimensions(self) -> int:
        return 0

    def embed(self, text: str) -> Sequence[float]:
        raise RuntimeError("NullEmbeddingBackend cannot embed — no real embedding backend is configured")

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise RuntimeError("NullEmbeddingBackend cannot embed — no real embedding backend is configured")


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Plain-Python vector similarity — deliberately NOT part of
    EmbeddingBackend (similarity math needs no model, no backend, no
    dependency; see this module's docstring on why the five responsibilities
    stay separate). Returns 0.0 for a zero-length or zero-magnitude vector
    rather than raising — a malformed/empty vector is a data problem for the
    caller (EmbeddingIndex) to reject before comparison, not something this
    pure function should crash over."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(y * y for y in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)
