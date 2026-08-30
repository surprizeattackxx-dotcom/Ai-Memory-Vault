#!/usr/bin/env python3
"""Retrieval abstraction — the "search phase" half of MEMORY_PROTOCOL.md's
RETRIEVE operation, implemented as a stable, dependency-free interface so a
future accelerator (BM25, embeddings, graph search — see ACCELERATION_LAYER.md)
can be swapped in later without changing this module's callers or the Memory
Runtime (tools/memory_runtime.py) that sits above it.

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

This module IS the baseline "no accelerator installed" retrieval path
ACCELERATION_LAYER.md already describes as today's actual behavior — exact
identity lookup, filename/stem lookup, wikilink traversal, and full-text
search, all of it operating directly on live Markdown via
tools/validate-vault.py's Vault class (discovery/frontmatter parsing) and
tools/vault_identity.py (path-aware, fail-closed identity resolution) rather
than re-implementing either. No dependency beyond the Python standard library.
No network access. No file access outside the vault the Vault class already
discovered (every method here operates only over `vault.notes`, never opens a
path built from the query string).

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

Future methods (semantic, graph) are not implemented here — see
ACCELERATION_LAYER.md. Nothing in this module's shape prevents adding them:
a future accelerator implements the same Candidate contract and the same
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

METHODS = ("exact", "filename", "wikilink", "text")
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
    score: float | None            # relevance; None where the method has no notion of ranking
    matched_on: str                # what specifically matched, human-readable
    excerpt: str | None            # short text excerpt, when available
    ambiguous: bool                # True if this candidate came from an unresolvable identity clash
    index_snapshot_time: None = field(default=None)  # always None: live baseline retriever, no cache


def search(vault: "vv.Vault", query: str, methods=None, limit: int | None = None) -> list[Candidate]:
    """Run the requested retrieval methods (default: all) and return a
    deterministically ordered, deduplicated candidate list. A missing/empty
    query returns [] immediately — never an error, never "everything"."""
    if not query or not query.strip():
        return []
    methods = tuple(methods) if methods else METHODS
    unknown = set(methods) - set(METHODS)
    if unknown:
        raise ValueError("unknown retrieval method(s): %s (known: %s)" % (sorted(unknown), METHODS))

    raw_hits = []  # list of dict: note, method, score, matched_on, excerpt, ambiguous
    if "exact" in methods:
        raw_hits += _search_exact(vault, query)
    if "filename" in methods:
        raw_hits += _search_filename(vault, query)
    if "wikilink" in methods:
        raw_hits += _search_wikilink(vault, query)
    if "text" in methods:
        raw_hits += _search_text(vault, query)

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


# --------------------------------------------------------------- merge/dedup
def _merge_by_identity(raw_hits) -> list:
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
        scores = [h["score"] for h in group if h["score"] is not None]
        merged_score = max(scores) if scores else None
        ambiguous = any(h["ambiguous"] for h in group)
        excerpt = next((h["excerpt"] for h in group if h["excerpt"]), None)
        out.append(Candidate(
            note_path=rel, stem=note["stem"], method=primary, all_methods=all_methods,
            score=merged_score, matched_on=primary_hit["matched_on"], excerpt=excerpt,
            ambiguous=ambiguous,
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
