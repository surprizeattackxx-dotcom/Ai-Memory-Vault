#!/usr/bin/env python3
"""REAL IMPLEMENTATION (not a test double): an EmbeddingBackend backed by
`sentence-transformers` (CPU-only torch), model `all-MiniLM-L6-v2`.

This is the first backend this repository has ever depended on. The
dependency is OPTIONAL and ISOLATED to this one file: `sentence_transformers`
and `torch` are imported LAZILY, inside `__init__`, wrapped in try/except —
importing THIS MODULE never fails even if the packages aren't installed, and
nothing else in the codebase imports this module unconditionally (see
tools/embedding_backend.py's own docstring: a real backend lives outside the
stdlib-only core and is injected by whoever wants it). Core vault loading,
identity resolution, lifecycle validation, and lexical retrieval have zero
awareness this file exists and function identically whether or not it does.

Dependency manifest: tools/requirements-semantic.txt (optional extra, not
required for core operation — see that file's own header for exactly what
was installed and why).

Model: sentence-transformers/all-MiniLM-L6-v2, 384 dimensions, a small,
well-established general-purpose sentence embedding model with wide
community precedent for exactly this kind of task. Runs entirely locally
after the one-time download from HuggingFace on first use — no API key, no
per-query network call, no mandatory cloud dependency.

DETERMINISM: given the same model revision and the same input text, this
backend is deterministic for all practical purposes (the model runs in
eval() mode with no dropout — no intentional randomness anywhere in
inference). The one honest caveat: floating-point summation order in the
underlying BLAS/torch kernels can differ by a few ULPs across different CPU
architectures, thread counts, or torch/library versions, so bit-for-bit
identical vectors across different MACHINES are not guaranteed — only
"deterministic on one machine, one library version, one model revision,"
which is exactly the freshness identity tools/embedding_index.py already
binds to (backend_id + model_id + dimensions, plus the vault's own
content-hash gate) and exactly why cross-machine vector reuse was never a
goal of this design.

A second, SAME-machine caveat, confirmed empirically: `embed_batch()` and
repeated single-item `embed()` calls for the identical text are NOT bit-for-
bit identical to each other — batched transformer inference pads shorter
sequences to match the longest one in the batch, and the resulting attention-
mask interactions produce float32-epsilon-level differences (~1e-7) from the
unbatched result, even on the same machine, same process, same model. This
is a real, reproducible property of transformer batching, not a bug —
callers comparing vectors across the two call styles should use a small
tolerance, never exact equality (see tests/test_sentence_transformers_backend.py's
own `_close()` helper for the convention this repo uses).
"""
from __future__ import annotations

import math
import re
from typing import Sequence

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DECLARED_DIMENSIONS = 384
MAX_CHARS = 20_000  # defensive truncation, not rejection — see _prepare()'s docstring


class SentenceTransformerBackend:
    """A concrete EmbeddingBackend. Loading the model happens once, at
    construction — `is_available()` reports whether that succeeded, and
    every embedding call after a failed load raises cleanly rather than
    limping along with a partially-initialized model."""

    def __init__(self, model_id: str = MODEL_ID, device: str = "cpu"):
        self._model_id = model_id
        self._device = device
        self._model = None
        self._load_error = None
        self._resolved_revision = None
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_id, device=device)
            self._resolved_revision = _resolve_cached_revision(model_id)
        except Exception as exc:  # ImportError (package missing), OSError (no
            # network + no cache), or any other load-time failure — all
            # treated identically: no model, is_available() reports False,
            # every embed call raises a clear, specific error naming why.
            self._model = None
            self._load_error = exc

    # ------------------------------------------------------------ identity
    def is_available(self) -> bool:
        return self._model is not None

    def backend_id(self) -> str:
        return "sentence-transformers"

    def model_id(self) -> str:
        return self._model_id if self._resolved_revision is None else "%s@%s" % (self._model_id, self._resolved_revision)

    def dimensions(self) -> int:
        if self._model is not None:
            # sentence-transformers renamed this method (get_embedding_dimension,
            # deprecating get_sentence_embedding_dimension) between library
            # versions — try the current name first, fall back to the older
            # one, so this works across the version range without a warning.
            for method_name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
                method = getattr(self._model, method_name, None)
                if method is None:
                    continue
                try:
                    dims = method()
                    if isinstance(dims, int) and dims > 0:
                        return dims
                except Exception:
                    continue
        return DECLARED_DIMENSIONS

    # -------------------------------------------------------------- embed
    def _prepare(self, text) -> str:
        """Input-handling policy, explicit per the ticket's requirements:
        - non-string input: coerced via str(), never raises here (a
          malformed-type INPUT is a caller bug worth tolerating gracefully
          at this layer; the resulting embedding is still well-defined)
        - None: treated as the empty string
        - whitespace-only / empty: embedded as the empty string, not
          special-cased to a zero vector — the model's own embedding of ""
          is well-defined, deterministic, and avoids inventing a magic value
          that could collide oddly under cosine similarity
        - extremely long text: truncated to MAX_CHARS (20,000 characters) —
          a defensive limit against pathological memory/time cost from an
          absurd query string, not a meaningful modeling decision; documented
          as truncation, never silent data loss pretending to be complete
        - Unicode: passed through untouched; the model/tokenizer handles it
        - never touches the filesystem, never treats `text` as a path"""
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        text = text.strip()
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        return text

    def embed(self, text: str) -> Sequence[float]:
        if self._model is None:
            raise RuntimeError("SentenceTransformerBackend: model not loaded (%r)" % (self._load_error,))
        prepared = self._prepare(text)
        vector = self._model.encode(prepared, convert_to_numpy=True, show_progress_bar=False)
        result = [float(x) for x in vector]
        _validate_vector(result, self.dimensions())
        return result

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Batch policy, explicit: individual malformed-TYPE items are
        sanitized by `_prepare()` (never raise per-item — a non-string in a
        batch is coerced, exactly like a single embed() call). A genuine
        MODEL failure during the batch call fails the WHOLE batch, never
        partially — sentence-transformers' own `encode()` is a single
        vectorized call over the batch, so partial failure isn't a real
        outcome to model here; documented explicitly rather than pretended
        otherwise. Ordering is always preserved (encode() is positional)."""
        if self._model is None:
            raise RuntimeError("SentenceTransformerBackend: model not loaded (%r)" % (self._load_error,))
        prepared = [self._prepare(t) for t in texts]
        vectors = self._model.encode(prepared, convert_to_numpy=True, show_progress_bar=False)
        results = [[float(x) for x in row] for row in vectors]
        for r in results:
            _validate_vector(r, self.dimensions())
        return results


def _validate_vector(vec, expected_dims):
    if len(vec) != expected_dims:
        raise RuntimeError("SentenceTransformerBackend produced a vector of length %d, expected %d"
                            % (len(vec), expected_dims))
    if not all(math.isfinite(x) for x in vec):
        raise RuntimeError("SentenceTransformerBackend produced a non-finite value")


def _resolve_cached_revision(model_id: str):
    """Best-effort: read the actual snapshot commit hash HuggingFace's local
    cache resolved `model_id` to, so tools/embedding_index.py's `model_id`
    field can record precisely which model revision produced a given vector
    set, not just the model's bare name. Returns None (never raises) if the
    cache layout can't be read — this is provenance/reporting detail, never
    something a caller depends on for correctness."""
    try:
        from pathlib import Path
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        repo_folder = "models--" + model_id.replace("/", "--")
        for repo in cache_info.repos:
            if repo.repo_id == model_id or Path(repo.repo_path).name == repo_folder:
                for revision in repo.revisions:
                    return revision.commit_hash
    except Exception:
        return None
    return None
