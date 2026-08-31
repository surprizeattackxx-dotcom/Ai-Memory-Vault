#!/usr/bin/env python3
"""Retrieval abstraction — the "search phase" half of MEMORY_PROTOCOL.md's
RETRIEVE operation, implemented as a stable, dependency-free interface (this
module itself imports nothing beyond the standard library and its two sibling
scan modules) so an accelerator (lexical index, embeddings, a future graph
search — see ACCELERATION_LAYER.md) can be plugged in without changing this
module's callers or the Memory Runtime (tools/memory_runtime.py) that sits
above it. Semantic retrieval (v3.7.5 Phase 6) is one such accelerator,
already implemented as the "semantic" method below — dependency-free itself,
since it only ever calls duck-typed methods on whatever backend/index object
a caller injects (see `search()`'s own docstring on `embedding_backend`/
`embedding_index`). This module's own source stays free of even a mention of
any concrete ML/vector-store package name, by design and by regression test
(tests/test_memory_runtime.py) — a real backend implementation, wherever it
lives, is never named here.

Architectural principle (unchanged from MEMORY_PROTOCOL.md's RETRIEVE and
ACCELERATION_LAYER.md's contract — this module implements it, never redefines
it):

    Canonical Vault
        v
    Retrieval produces CANDIDATES        <-- this module
        v
    Validation/lifecycle checks determine TRUST   <-- tools/memory_runtime.py
        v
    Runtime returns validated memory context

Retrieval never establishes truth. A Candidate means "this note may be
relevant" — nothing here reads or reports memory_status, source, confidence,
or any other lifecycle field; that is deliberately out of scope for this
module and belongs entirely to the validation phase above it. A Candidate is
not, and must never become, a validated fact.

This module runs equally correctly with no accelerator at all — the mode
ACCELERATION_LAYER.md originally described, and still exactly what happens
when a caller passes none of `index`/`validated_index`/`embedding_backend`/
`embedding_index` — or with the lexical and/or semantic accelerators
injected. The four live-scan methods (exact, filename, wikilink, text)
operate directly on live Markdown via tools/validate-vault.py's Vault class
(discovery/frontmatter parsing) and tools/vault_identity.py (path-aware,
fail-closed identity resolution) rather than re-implementing either. No
dependency beyond the Python standard library. No network access. No file
access outside the vault the Vault class already discovered (every method
here operates only over `vault.notes`, never opens a path built from the
query string).

Retrieval methods, explicit and never conflated:
    exact     - the query names one note's identity precisely (a path, a
                path-qualified wikilink, or an unqualified stem with exactly
                one match anywhere in the vault). Fail-closed: 0 or 2+ matches
                is surfaced as zero or ambiguous candidates, never a guess.
    filename  - every note sharing a bare stem, path ignored even if one was
                given. Deliberately looser than `exact`: multiple results are
                its ordinary, expected output, not an error.
    wikilink  - every note that links TO the note the query identifies
                (inbound wikilink neighbors), walking the same [[...]] syntax
                validate-vault.py already parses.
    text      - simple, deterministic, case-insensitive keyword matching over
                note bodies (fenced code stripped, same as validate-vault.py's
                own wikilink scan) with an occurrence-count score. Not
                semantic. Never labeled or scored as if it were.
    semantic  - cosine similarity between the query's embedding and each
                note's precomputed embedding (see `_search_semantic` below,
                tools/embedding_backend.py, tools/embedding_index.py). A
                no-op unless a caller explicitly injects both an
                EmbeddingBackend and an EmbeddingIndex — no existing caller
                does, so this method contributes nothing by default. A
                similarity score is ranking metadata only; it carries no
                authority and cannot influence lifecycle acceptance (that
                remains exclusively tools/memory_runtime.py's job).

A future graph-search method is not implemented here — see
ACCELERATION_LAYER.md. Nothing in this module's shape prevents adding it: a
future accelerator implements the same Candidate contract and the same
`search(vault, query, methods=...)` signature; validation above it is
unaffected either way (ACCELERATION_LAYER.md invariant #7).

Determinism: results are never ordered by filesystem/rglob iteration order.
Sort key is explicit — (method priority, descending score, ascending
vault-relative path) — computed fresh on every call, never cached across
calls in a way that could leak stale ordering.

`index_snapshot_time` on every Candidate is always None here: this baseline
retriever reads live Markdown on every call, so there is no "snapshot" to
report — a future accelerator's non-None value is exactly what a caller uses
to tell "live" apart from "cached, possibly stale," per ACCELERATION_LAYER.md's
Interface shape section.
"""
from __future__ import annotations

import importlib.util
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "tools" / "validate-vault.py"
IDENTITY = REPO_ROOT / "tools" / "vault_identity.py"

_spec = importlib.util.spec_from_file_location("validate_vault", VALIDATOR)
vv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vv)

_id_spec = importlib.util.spec_from_file_location("vault_identity", IDENTITY)
vid = importlib.util.module_from_spec(_id_spec)
_id_spec.loader.exec_module(vid)

METHODS = ("exact", "filename", "wikilink", "text", "semantic")
METHOD_PRIORITY = {m: i for i, m in enumerate(METHODS)}

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


@dataclass
class Candidate:
    """A retrieval result. Means "this note may be relevant" — nothing about
    trust, currentness, or validity. See module docstring."""
    note_path: str                 # canonical, vault-relative path (provenance link)
    stem: str                      # note identity/stem
    method: str                    # primary/highest-priority contributing method
    all_methods: tuple             # every method that independently matched this note
    score: float | None            # the PRIMARY method's own score; never blended across methods — see
                                    # _merge_by_identity's docstring for why a cross-method max() was a latent bug
    matched_on: str                # what specifically matched, human-readable
    excerpt: str | None            # short text excerpt, when available
    ambiguous: bool                # True if this candidate came from an unresolvable identity clash
    index_snapshot_time: None = field(default=None)  # always None: live baseline retriever, no cache
    method_scores: dict = field(default_factory=dict)  # {method: its own score}, added for "semantic" — every
                                    # contributing method's score kept separate, never combined/normalized against
                                    # another method's scale. Ranking still uses `score`+method-priority only (see
                                    # search()'s sort key); this field is transparency, not a second ranking input.


def search(vault: "vv.Vault", query: str, methods=None, limit: int | None = None, index=None,
           validated_index=None, embedding_backend=None, embedding_index=None,
           validated_embedding_index=None) -> list[Candidate]:
    """Run the requested retrieval methods (default: all) and return a
    deterministically ordered, deduplicated candidate list. A missing/empty
    query returns [] immediately — never an error, never "everything".

    `index` (added v3.7.5 Phase 2, optional, default None — every existing
    call site is untouched and behaves exactly as before): an optional
    accelerator satisfying two duck-typed methods, `is_fresh_for(vault) ->
    bool` and `link_stem_candidates(stem) -> list[str]` (see
    tools/memory_index.py's MemoryIndex — this module deliberately does not
    import that module, to avoid a circular dependency, since MemoryIndex
    itself reuses this module's `_WORD_RE`). Freshness is re-verified on
    EVERY call when this parameter is used — the maximally-safe, per-call
    path, unchanged since Phase 2/3, kept exactly as-is for any caller that
    wants that guarantee on a one-off call rather than a shared session.

    `validated_index` (added v3.7.5's "Fix the Provenance Index Performance
    Boundary" ticket, optional, default None): a pre-validated context
    (tools/memory_index.py's ValidatedIndex) whose freshness was already
    confirmed ONCE, against this exact `vault` object, by its own
    constructor — never re-hashed again here. Duck-typed as `usable_for(vault)
    -> bool` and `link_stem_candidates(stem) -> list[str]`. This is NOT a
    weaker guarantee than `index=`'s per-call check — it is the identical
    one-time `is_fresh_for()` gate, amortized across every call sharing one
    ValidatedIndex instance, which is itself bound by Python object identity
    to the one `vv.Vault` snapshot it was checked against (see
    ValidatedIndex's own docstring for why identity, not path equality, is
    what makes reuse across a different/reconstructed vault impossible by
    construction rather than by an extra check here).

    Precedence when both are given: `validated_index` wins if `usable_for
    (vault)` is True; only then does `index`'s own per-call check get
    consulted as a secondary path. If neither yields a usable accelerator,
    or checking either raises for any reason (corrupt object, wrong shape,
    wrong vault, wrong schema): silently and completely fall back to the
    pre-v3.7.5 live scan, `_search_wikilink`. Live resolve_identity() is
    NEVER skipped, cached, or replaced by index data regardless of which
    path was used — see `_search_wikilink_indexed`'s own docstring for the
    exact guarantee, which is identical either way: `_search_wikilink_indexed`
    only ever calls `.link_stem_candidates(stem)` on whatever accelerator-like
    object it receives, so a ValidatedIndex and a raw MemoryIndex are
    interchangeable at that call site by construction, never by a special case.

    Only the "wikilink" method ever consults either accelerator parameter —
    the demonstrated O(N) resolve-per-match, O(N) notes-scanned bottleneck
    this accelerator targets. "exact"/"filename"/"text" are unaffected, on
    purpose — accelerating them is out of this change's scope.

    `embedding_backend` / `embedding_index` (semantic retrieval boundary):
    duck-typed as tools/embedding_backend.py's EmbeddingBackend and
    tools/embedding_index.py's EmbeddingIndex — this module deliberately
    does not import either, for the same reason it doesn't import
    tools/memory_index.py. Both default to None, in which case "semantic" is
    a no-op contributing zero raw hits. A real backend implementation does
    exist elsewhere in this repository (see tools/embedding_backend.py's own
    docstring for where), but it must be explicitly constructed and passed in
    by the caller; tools/embedding_backend.py's own NullEmbeddingBackend
    always reports itself unavailable and is what a caller gets by doing
    nothing. Every EXISTING caller (none of which pass these two parameters)
    therefore still gets byte-identical results to before "semantic" was
    added to METHODS. Any exception from either object at any
    point — availability check, embedding the query, or the nearest-neighbor
    lookup — is caught and treated as "no semantic candidates," never a
    crash and never a partial/guessed result. See `_search_semantic`'s own
    docstring for the exact candidate-generation contract."""
    if not query or not query.strip():
        return []
    methods = tuple(methods) if methods else METHODS
    unknown = set(methods) - set(METHODS)
    if unknown:
        raise ValueError("unknown retrieval method(s): %s (known: %s)" % (sorted(unknown), METHODS))

    accelerator = None
    if validated_index is not None:
        try:
            if bool(validated_index.usable_for(vault)):
                accelerator = validated_index
        except Exception:
            accelerator = None
    if accelerator is None and index is not None:
        try:
            if bool(index.is_fresh_for(vault)):
                accelerator = index
        except Exception:
            accelerator = None
    use_index = accelerator is not None
    index = accelerator  # _search_wikilink_indexed only ever calls .link_stem_candidates(); either type works

    raw_hits = []  # list of dict: note, method, score, matched_on, excerpt, ambiguous
    if "exact" in methods:
        raw_hits += _search_exact(vault, query)
    if "filename" in methods:
        raw_hits += _search_filename(vault, query)
    if "wikilink" in methods:
        wikilink_hits = None
        if use_index:
            try:
                wikilink_hits = _search_wikilink_indexed(vault, query, index)
            except Exception:
                # An index that lied about its own freshness (is_fresh_for()
                # returned True) but then fails/misbehaves during the actual
                # lookup must never crash the caller — fall back to the full
                # live scan exactly as if no index had been supplied at all.
                wikilink_hits = None
        raw_hits += wikilink_hits if wikilink_hits is not None else _search_wikilink(vault, query)
    if "text" in methods:
        raw_hits += _search_text(vault, query)
    if "semantic" in methods:
        raw_hits += _search_semantic(vault, query, embedding_backend, embedding_index, validated_embedding_index)

    merged = _merge_by_identity(raw_hits)
    merged.sort(key=lambda c: (METHOD_PRIORITY.get(c.method, len(METHODS)), -(c.score or 0.0), c.note_path))
    if limit is not None:
        merged = merged[:limit]
    return merged


# --------------------------------------------------------------- exact
def _search_exact(vault, query):
    target, ambiguous = vid.resolve_identity(vault, query)
    if ambiguous:
        return [
            {"note": n, "method": "exact", "score": None,
             "matched_on": "identity %r is ambiguous — %d notes share it" % (query, len(ambiguous)),
             "excerpt": None, "ambiguous": True}
            for n in ambiguous
        ]
    if target is None:
        return []
    return [{"note": target, "method": "exact", "score": None,
             "matched_on": "exact identity %r" % query, "excerpt": None, "ambiguous": False}]


# --------------------------------------------------------------- filename
def _search_filename(vault, query):
    matches = vid.stem_matches(vault, query)
    return [{"note": n, "method": "filename", "score": None,
             "matched_on": "filename/stem %r" % query, "excerpt": None, "ambiguous": False}
            for n in matches]


# --------------------------------------------------------------- wikilink
def _search_wikilink(vault, query):
    target, ambiguous = vid.resolve_identity(vault, query)
    if target is None:
        return []  # ambiguous or missing center note: nothing to walk from
    target_stem = target["stem"].lower()
    hits = []
    for note in vault.notes:
        if note is target:
            continue
        body = vv.strip_fenced_and_code(note["text"])
        for m in vv.WIKILINK_RE.finditer(body):
            linked, _amb = vid.resolve_identity(vault, m.group(1))
            if linked is not None and linked["stem"].lower() == target_stem:
                hits.append({"note": note, "method": "wikilink", "score": None,
                             "matched_on": "links to %r" % target["rel"], "excerpt": None, "ambiguous": False})
                break  # one hit per linking note, even if it links multiple times
    return hits


def _search_wikilink_indexed(vault, query, index):
    """Identical CONTRACT and identical inner logic to _search_wikilink —
    same fenced-code stripping, same WIKILINK_RE scan, same live
    vid.resolve_identity() call per match, same one-hit-per-note dedup, same
    hit dict shape. The ONLY difference: the outer loop runs over
    `index.link_stem_candidates(target_stem)` (notes whose body mentions a
    RAW [[...]] stem matching the target — a purely lexical fact) instead of
    every note in the vault. Because the caller (search()) only reaches here
    after `index.is_fresh_for(vault)` returned True — an exact, whole-vault
    content-hash match — this candidate set is PROVABLY identical to what a
    full live scan would find: is_fresh_for's guarantee is that the index's
    stored `outbound_link_stems` were extracted from these exact bytes, so
    there is no note the live scan would check that this candidate set could
    have missed. This is acceleration of WHERE to look, never a relaxation
    of WHAT counts as a match — identity resolution itself is never cached,
    never skipped, and never trusted from the index; a decoy sharing the
    target's stem is rejected here exactly as it would be by the live path,
    because the same fail-closed resolve_identity() call is what decides it,
    every time, against the live vault, not the index."""
    target, ambiguous = vid.resolve_identity(vault, query)
    if target is None:
        return []  # ambiguous or missing center note: nothing to walk from
    target_stem = target["stem"].lower()
    by_rel = {n["rel"]: n for n in vault.notes}
    hits = []
    for rel in index.link_stem_candidates(target_stem):
        note = by_rel.get(rel)
        if note is None or note is target:
            continue  # an index claiming a path the live vault doesn't have is never trusted; ignore, don't guess
        body = vv.strip_fenced_and_code(note["text"])
        for m in vv.WIKILINK_RE.finditer(body):
            linked, _amb = vid.resolve_identity(vault, m.group(1))
            if linked is not None and linked["stem"].lower() == target_stem:
                hits.append({"note": note, "method": "wikilink", "score": None,
                             "matched_on": "links to %r" % target["rel"], "excerpt": None, "ambiguous": False})
                break  # one hit per linking note, even if it links multiple times
    return hits


# --------------------------------------------------------------- text
def _search_text(vault, query):
    words = [w.lower() for w in _WORD_RE.findall(query)]
    if not words:
        return []
    hits = []
    for note in vault.notes:
        body = vv.strip_fenced_and_code(note["text"])
        # Word-boundary counting, not substring counting: a query word like
        # "sam" must not score a match on "sample"/"same"/"assam". Tokenize
        # the body into words the same way the query itself is tokenized, so
        # both sides use one consistent, deterministic definition of "word".
        body_words = Counter(w.lower() for w in _WORD_RE.findall(body))
        counts = Counter()
        for w in words:
            counts[w] = body_words[w]
        score = sum(counts.values())
        if score <= 0:
            continue
        excerpt = _first_matching_line(body, words)
        hits.append({"note": note, "method": "text", "score": float(score),
                     "matched_on": "keyword match: %s" % ", ".join(w for w in words if counts[w] > 0),
                     "excerpt": excerpt, "ambiguous": False})
    return hits


def _first_matching_line(body: str, words: list) -> str | None:
    for line in body.splitlines():
        low = line.lower()
        if any(w in low for w in words):
            stripped = line.strip()
            if stripped:
                return stripped[:200]
    return None


# --------------------------------------------------------------- semantic
def _search_semantic(vault, query, embedding_backend, embedding_index, validated_embedding_index=None):
    """Candidate generation contract (embedding-boundary ticket, Phase 6): a
    semantic backend contributes ONLY (note, similarity score) pairs in the
    exact same raw-hit dict shape every other _search_* function already
    produces — never a resolved identity, never a lifecycle field, never an
    `accepted` bit. Every returned hit's `note` comes straight from
    `vault.notes` (looked up by the EmbeddingIndex's own stored `rel`, which
    is only ever populated from `vault.notes` at build time — see
    tools/embedding_index.py — so there is no separate "resolve this path"
    step here, exactly as there is none for a `text` hit either), meaning
    every semantic hit flows through the IDENTICAL merge -> identity-neutral
    Candidate -> MemoryRuntime.inspect() pipeline as any other method with
    zero special-casing downstream.

    `validated_embedding_index` (added v3.7.5 Phase 11, optional): a
    pre-validated tools/embedding_index.py ValidatedEmbeddingIndex, whose
    freshness was already checked ONCE at construction against this exact
    vault+backend pair — measured to save 28-56% of per-query time at
    N=100..5000 (see the Phase 11 benchmark), the same amortization
    `_search_wikilink_indexed` already gets from ValidatedIndex. When usable
    (`usable_for(vault)` True), its cached validity is trusted and
    `embedding_index.is_fresh_for()` is never called at all for this query;
    otherwise this function falls back to the ordinary per-call
    `embedding_index.is_fresh_for(vault, embedding_backend)` check, exactly
    as if `validated_embedding_index` had never been passed.

    Fails closed, completely and silently, on: no backend, no index, backend
    unavailable, index stale/wrong-vault/wrong-schema/wrong-model, a raised
    exception embedding the query, a raised exception during nearest-
    neighbor lookup, or a malformed/empty result from either. None of these
    ever raise out of this function; all of them produce an empty candidate
    list, identical to "semantic retrieval wasn't requested at all." """
    if embedding_backend is None or embedding_index is None:
        return []
    try:
        if not embedding_backend.is_available():
            return []
        fresh = False
        if validated_embedding_index is not None:
            try:
                fresh = bool(validated_embedding_index.usable_for(vault))
            except Exception:
                fresh = False
        if not fresh:
            fresh = bool(embedding_index.is_fresh_for(vault, embedding_backend))
        if not fresh:
            return []
        query_vector = embedding_backend.embed(query)
        neighbors = embedding_index.nearest(query_vector, limit=20)
    except Exception:
        return []
    if not neighbors:
        return []

    by_rel = {n["rel"]: n for n in vault.notes}
    hits = []
    for rel, similarity in neighbors:
        note = by_rel.get(rel)
        if note is None:
            continue  # an index claiming a path the live vault doesn't have is never trusted; ignore, don't guess
        if not isinstance(similarity, (int, float)) or isinstance(similarity, bool) or not math.isfinite(similarity):
            # rejects NaN AND +-infinity — a real gap found during v3.7.5
            # Phase 11 adversarial testing: the original check
            # (`similarity == similarity`) only caught NaN, since `inf ==
            # inf` is True in Python — an index/backend returning an
            # infinite "similarity" silently passed through as an ordinary
            # candidate. math.isfinite() is the same non-finite check
            # already used consistently everywhere else in this codebase
            # (EmbeddingIndex.build()/load()/nearest()'s own query-vector
            # validation) — this brings _search_semantic in line with that
            # existing convention rather than a second, incomplete one.
            continue
        hits.append({"note": note, "method": "semantic", "score": float(similarity),
                     "matched_on": "semantic similarity: %.4f" % similarity, "excerpt": None, "ambiguous": False})
    return hits


# --------------------------------------------------------------- merge/dedup
def _merge_by_identity(raw_hits) -> list:
    """Score semantics (fixed for the "semantic" method's arrival — see the
    embedding-boundary contract ticket's Phase 4): `Candidate.score` is now
    the PRIMARY (highest-priority) contributing method's OWN score, never a
    cross-method `max()`. Before "semantic" existed, only "text" ever
    produced a non-None score, so `max()` was silently equivalent to "the
    text method's score" in every case that could actually happen — the
    latent bug was that a SECOND scored method would make `max()` compare
    two incomparable scales (a lexical keyword count against a cosine
    similarity) as if they meant the same thing. `method_scores` preserves
    every contributing method's own score, untouched and unblended, for a
    caller that wants the detail; RANKING still uses `score` + method
    priority only (search()'s sort key checks method priority BEFORE score,
    so a lower-priority method's score can never be compared against, or
    outrank, a higher-priority method's score — cross-scale comparison is
    avoided structurally, not by normalization)."""
    by_path = {}
    order = []
    for hit in raw_hits:
        rel = hit["note"]["rel"]
        if rel not in by_path:
            by_path[rel] = []
            order.append(rel)
        by_path[rel].append(hit)

    out = []
    for rel in order:
        group = by_path[rel]
        note = group[0]["note"]
        all_methods = tuple(sorted({h["method"] for h in group}, key=lambda m: METHOD_PRIORITY[m]))
        primary = all_methods[0]
        primary_hit = next(h for h in group if h["method"] == primary)
        method_scores = {h["method"]: h["score"] for h in group if h["score"] is not None}
        merged_score = primary_hit["score"]
        ambiguous = any(h["ambiguous"] for h in group)
        excerpt = next((h["excerpt"] for h in group if h["excerpt"]), None)
        out.append(Candidate(
            note_path=rel, stem=note["stem"], method=primary, all_methods=all_methods,
            score=merged_score, matched_on=primary_hit["matched_on"], excerpt=excerpt,
            ambiguous=ambiguous, method_scores=method_scores,
        ))
    return out


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Search a vault (candidates only — no lifecycle validation).")
    ap.add_argument("vault")
    ap.add_argument("query")
    ap.add_argument("--methods", help="comma-separated subset of: %s" % ",".join(METHODS))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.vault)
    if not root.is_dir():
        sys.exit("ERROR: vault path is not a directory: %s" % args.vault)
    v = vv.Vault(root, None)
    v.discover()
    methods = tuple(args.methods.split(",")) if args.methods else None
    results = search(v, args.query, methods=methods, limit=args.limit)

    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2, default=lambda o: list(o) if isinstance(o, tuple) else o))
    else:
        print("Query: %r  Candidates: %d" % (args.query, len(results)))
        for r in results:
            flag = " [AMBIGUOUS]" if r.ambiguous else ""
            print("  [%s%s] %s (score=%s) — %s" % (r.method, "+" + ",".join(m for m in r.all_methods if m != r.method) if len(r.all_methods) > 1 else "", r.note_path, r.score, r.matched_on))
            if r.excerpt:
                print("      %s" % r.excerpt)
