#!/usr/bin/env python3
"""Memory Index — the acceleration boundary ACCELERATION_LAYER.md already
specifies and tools/memory_retrieval.py's `Candidate.index_snapshot_time`
field has been reserved for since v3.7.0, now actually implemented.

STATUS: Phase 1 of v3.7.5 — the index data structure, its build/load/save,
and its freshness gate. NOT YET WIRED into tools/memory_retrieval.py (that is
Phase 2's seam, deliberately separate so this module can be tested completely
on its own before anything downstream depends on it).

THE ONE SENTENCE THAT GOVERNS EVERYTHING HERE (quoting ACCELERATION_LAYER.md
verbatim, because this module exists only to implement that sentence, never
to reinterpret it): "The vault is truth. The index is a guess about the
vault, cached for speed, and it is allowed to be wrong."

WHAT THIS INDEX STORES — and just as importantly, what it refuses to store:
    - a note's canonical path, lowercased stem, and lowercased directory
      parts (copied, not computed — purely structural, no judgment)
    - a content hash (sha256 of raw file bytes) and mtime, for staleness
    - every RAW, UNRESOLVED [[wikilink]] stem the note's body mentions
      (string extraction only — see "Why raw, never resolved" below)
    - every lowercase word the note's body contains (for text-search
      candidate narrowing — the same tokenization memory_retrieval.py's
      `_search_text` uses, reused directly so scores/excerpts computed from
      indexed candidates are identical to a live scan, never an approximation)
    - NEVER: memory_status, source, confidence, supersedes/superseded_by, or
      any other lifecycle field. NEVER a resolved wikilink target. NEVER an
      `accepted` bit. NEVER a score. This index has no opinion about trust,
      truth, or meaning — only about which files exist, what they're named,
      and what strings appear in them.

WHY RAW, NEVER RESOLVED: resolving a wikilink (deciding WHICH note `[[X]]`
points to) requires vault_identity.resolve_identity()'s fail-closed ambiguity
check, which is only correct when it sees every note sharing that stem,
vault-wide, at the moment of the query — not at the moment the index was
built. Caching a RESOLVED target would mean the index silently re-deciding
identity from stale state, exactly the "second source of truth" arrangement
ACCELERATION_LAYER.md's invariant #2 forbids. So this index caches only the
raw stem text extracted from `[[...]]` syntax — a purely lexical fact with
zero judgment in it — and identity resolution is re-run live, every time,
against the current vault, for every candidate the index's raw-stem lookup
surfaces. The index narrows WHERE to look; it never decides WHAT is there.

THE FRESHNESS MODEL — one binary gate, not a per-file patchwork: this index
is trusted for a query only when `is_fresh_for(vault)` proves EVERY one of
the following, all at once: the index's schema version matches this module's;
the index's recorded vault-root path and (if present) MEMORY_PROTOCOL.md
content hash match the live vault being queried; and the index's recorded
{path: content_hash} map is byte-identical to a fresh walk-and-hash of the
live vault RIGHT NOW — same file set, same hashes, nothing added, nothing
removed, nothing changed. Any single mismatch, any read/parse error, any
corruption, any wrong-vault or wrong-schema signal: the WHOLE index is
untrusted for that call. There is no partial-trust mode. This is stricter
than ACCELERATION_LAYER.md's per-file "stale entry self-corrects at
validation time" language strictly requires for narrowing-only uses (text/
wikilink candidate discovery, where an incomplete candidate set is a recall
problem, not a trust problem) — but IDENTITY resolution's ambiguity check
requires COMPLETE vault-wide visibility to be safe at all, so this module
picks the one gate that is safe for every use it supports, rather than two
different trust levels that would be easy to wire together wrong later.

Deliberately NOT covered by this file: wiring into memory_retrieval.py's
search() (Phase 2), a CLI (added once Phase 2 exists so there's something
worth invoking), and any use by memory_conflict/memory_provenance/
memory_health (Phase 3 explicitly excludes accelerating those layers'
semantics — they keep consuming the existing runtime primitives unchanged).

Standard library only. No network access. No write access to the vault —
every write this module performs targets its OWN index file, explicitly
given by the caller, always outside the vault's own file set.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

# Loads validate-vault.py directly (own copy) rather than borrowing
# tools/memory_runtime.py's already-loaded reference, as this module did
# through v3.7.5 Phase 1-3 — v3.7.5's Phase 2 amortization work needs
# memory_runtime.py to construct a ValidatedIndex (this module), and a
# memory_runtime -> memory_index -> memory_runtime import cycle is not
# resolvable at module load time. Loading validate-vault.py independently
# here (the identical importlib pattern tools/memory_retrieval.py already
# uses for the same reason) removes the only edge that could cycle; two
# independently-loaded copies of a pure-function/regex module are
# behaviorally identical, and nothing here relies on class identity across
# the copies (only vault.notes/vault.root attribute access, never
# isinstance(vault, vv.Vault)).
_vv_spec = importlib.util.spec_from_file_location("validate_vault", TOOLS_DIR / "validate-vault.py")
vv = importlib.util.module_from_spec(_vv_spec)
_vv_spec.loader.exec_module(vv)

import memory_retrieval as mret  # noqa: E402  (no dependency on memory_runtime/memory_index; safe, non-circular)

INDEX_SCHEMA_VERSION = "1.0.0"
PROTOCOL_REL_HINT = "MEMORY_PROTOCOL.md"  # matched by basename under any "NN - Resources" dir, same as validate-vault.py


@dataclass
class IndexEntry:
    """One note's purely-structural, purely-lexical facts — no lifecycle
    field, no resolved link, no score. Rebuildable entirely from the note's
    own bytes; nothing here is derived from any OTHER note."""
    rel: str
    stem: str                    # lowercased
    dir_parts: tuple             # lowercased, for path-qualified matching parity with vault_identity
    content_hash: str            # sha256 of raw file bytes
    mtime: float                 # advisory only, never a staleness authority on its own
    outbound_link_stems: tuple   # lowercased raw [[...]] stems this note's body mentions, deduped, sorted
    words: tuple                 # lowercased distinct words this note's body contains, sorted


@dataclass
class IndexHeader:
    schema_version: str
    vault_root: str               # resolved, absolute path string — binds the index to ONE vault
    protocol_hash: str | None     # sha256 of the vault's own MEMORY_PROTOCOL.md copy, if present
    note_count: int
    built_at: str                 # informational only; never used for a staleness decision


class MemoryIndex:
    """A disposable, rebuildable, read-only-once-built lookup structure.
    Deleting the on-disk file (or simply never building one) leaves every
    caller's behavior identical to today's, per ACCELERATION_LAYER.md
    invariant #5 — this class is never required for anything to function."""

    def __init__(self, header: IndexHeader, entries: list):
        self.header = header
        self.entries = tuple(sorted(entries, key=lambda e: e.rel))  # deterministic storage order, always
        self._by_rel = {e.rel: e for e in self.entries}
        self._by_stem = defaultdict(list)
        self._by_link_stem = defaultdict(list)
        self._by_word = defaultdict(list)
        for e in self.entries:
            self._by_stem[e.stem].append(e.rel)
            for s in e.outbound_link_stems:
                self._by_link_stem[s].append(e.rel)
            for w in e.words:
                self._by_word[w].append(e.rel)
        # Every bucket sorted once, at construction — lookups never need to
        # re-sort, and result order can never depend on dict/hash-seed
        # iteration order, insertion order, or which process built the index.
        for bucket in (self._by_stem, self._by_link_stem, self._by_word):
            for k in bucket:
                bucket[k].sort()

    # ------------------------------------------------------------- lookups
    def stem_candidates(self, stem: str) -> list:
        """Every rel whose OWN stem equals `stem` (case-insensitive) —
        candidate identity/filename matches. Purely lexical: never says
        whether the match is unique, ambiguous, or path-qualified-correct;
        that judgment is vault_identity.resolve_identity()'s alone, always
        re-run live against the current vault by the caller."""
        return list(self._by_stem.get(stem.lower(), ()))

    def link_stem_candidates(self, stem: str) -> list:
        """Every rel whose body mentions a RAW [[...]] pointing at `stem` —
        candidate inbound-wikilink sources. Never resolved; the caller must
        re-run vid.resolve_identity on each candidate's actual link text
        against the live vault to confirm it truly points at the intended
        note and not a same-stem decoy."""
        return list(self._by_link_stem.get(stem.lower(), ()))

    def word_candidates(self, word: str) -> list:
        """Every rel whose body contains `word` (case-insensitive, same
        word-boundary tokenization tools/memory_retrieval.py's `_search_text`
        already uses) — candidate text-search matches. Never a score."""
        return list(self._by_word.get(word.lower(), ()))

    def entry(self, rel: str):
        return self._by_rel.get(rel)

    # --------------------------------------------------------------- build
    @classmethod
    def build(cls, vault: "vv.Vault") -> "MemoryIndex":
        """Pure function of `vault.notes` (already discovered/parsed by the
        caller) — deterministic: the same vault contents always produce a
        byte-identical index. Reads nothing outside what `vault.discover()`
        already read; performs no additional filesystem access of its own
        beyond hashing each already-known note's own bytes."""
        import time as _time

        protocol_hash = None
        for note in vault.notes:
            if note["basename"] == PROTOCOL_REL_HINT and len(note["dir_parts"]) == 1 \
                    and note["dir_parts"][0].endswith("Resources"):
                protocol_hash = hashlib.sha256(note["path"].read_bytes()).hexdigest()
                break

        entries = []
        for note in sorted(vault.notes, key=lambda n: n["rel"]):
            raw_bytes = note["path"].read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            mtime = note["path"].stat().st_mtime
            # Scanned from note["text"] (the FULL file, frontmatter included) —
            # not note["body"] — because that is what memory_retrieval.py's
            # _search_wikilink()/_search_text() actually scan. A frontmatter
            # value like `supersedes: "[[x]]"` is a real [[...]] match to the
            # live scanner; indexing only the body would silently under-count
            # candidates relative to live for any note whose frontmatter
            # happens to contain wikilink-shaped text (found via the
            # supersession-cycle equivalence fixture during Phase 3 hardening
            # — see tests/test_memory_index.py's "cycle" equivalence case).
            scanned = vv.strip_fenced_and_code(note["text"])
            link_stems = sorted({_stem_of_raw_link(m.group(1)) for m in vv.WIKILINK_RE.finditer(scanned)
                                  if m.group(1).strip() and not m.group(1).strip().startswith("#")})
            words = sorted({w.lower() for w in mret._WORD_RE.findall(scanned)})
            entries.append(IndexEntry(
                rel=note["rel"], stem=note["stem"].lower(), dir_parts=tuple(p.lower() for p in note["dir_parts"]),
                content_hash=content_hash, mtime=mtime,
                outbound_link_stems=tuple(link_stems), words=tuple(words),
            ))

        header = IndexHeader(
            schema_version=INDEX_SCHEMA_VERSION, vault_root=str(vault.root.resolve()),
            protocol_hash=protocol_hash, note_count=len(entries),
            built_at=_time.strftime("%Y-%m-%dT%H:%M:%S", _time.gmtime()),
        )
        return cls(header, entries)

    def rebuild_is_identical(self, vault: "vv.Vault") -> bool:
        """Idempotence check: rebuilding from the same vault state produces
        the same LOGICAL index (ACCELERATION_LAYER.md's Interface shape
        section requires this of `rebuild()`) — compares every field except
        two that are explicitly documented as non-authoritative, point-in-
        time filesystem/wall-clock facts rather than index content:
        `built_at` (IndexHeader: "informational only; never used for a
        staleness decision") and each entry's `mtime` (IndexEntry: "advisory
        only, never a staleness authority on its own" — is_fresh_for() never
        consults it either, relying solely on content_hash). Both can
        legitimately differ between two builds of byte-identical file
        CONTENT if a file was merely touched (rewritten with the same bytes)
        between builds — that is real-world filesystem behavior, not an
        index defect, and comparing raw to_json() strings would wrongly fail
        idempotence on timestamps alone despite the actual content matching."""
        def _content_only(payload):
            out = {k: v for k, v in payload.items() if k != "built_at"}
            out["entries"] = [{k: v for k, v in e.items() if k != "mtime"} for e in out["entries"]]
            return out
        mine = _content_only(json.loads(self.to_json()))
        fresh = _content_only(json.loads(MemoryIndex.build(vault).to_json()))
        return mine == fresh

    # ----------------------------------------------------------- freshness
    def is_fresh_for(self, vault: "vv.Vault") -> bool:
        """The one, binary, all-or-nothing trust gate. Returns False (never
        raises) for anything short of an exact match — schema, vault
        identity, and a byte-for-byte {path: content_hash} equality against
        a fresh walk-and-hash of the live vault right now. No partial
        credit: one changed, added, or removed note is enough to distrust
        the whole index for this call."""
        try:
            if self.header.schema_version != INDEX_SCHEMA_VERSION:
                return False
            if self.header.vault_root != str(vault.root.resolve()):
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
        except OSError:
            return False

    # ------------------------------------------------------- (de)serialize
    def to_json(self) -> str:
        payload = {
            "schema_version": self.header.schema_version,
            "vault_root": self.header.vault_root,
            "protocol_hash": self.header.protocol_hash,
            "note_count": self.header.note_count,
            "built_at": self.header.built_at,
            "entries": [
                {"rel": e.rel, "stem": e.stem, "dir_parts": list(e.dir_parts), "content_hash": e.content_hash,
                 "mtime": e.mtime, "outbound_link_stems": list(e.outbound_link_stems), "words": list(e.words)}
                for e in self.entries
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def save(self, path) -> None:
        """The only file this module ever opens for writing — the caller's
        explicitly-given index path, always outside the vault's own note
        set (this module never writes into the vault)."""
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "MemoryIndex | None":
        """Never raises. Any read error, JSON error, type-confused field, or
        otherwise structurally malformed payload returns None — treated
        identically to "no index exists" by every caller, per
        ACCELERATION_LAYER.md's Corruption/Missing rebuild-semantics rows
        (both fall back to unaccelerated retrieval immediately, never a
        partial parse). Every field's TYPE is checked explicitly, not just
        its presence — `tuple("not-a-list")` silently succeeds in Python
        (it becomes a tuple of individual characters), so a bare
        `tuple(item[...])` would have accepted a type-confused field as
        valid data instead of rejecting it; every list/str/number field
        below is validated before use, on the assumption this file is
        untrusted input, never because our own code wrote it once."""
        try:
            raw = Path(path).read_text(encoding="utf-8")
            payload = json.loads(raw)
            required = {"schema_version", "vault_root", "protocol_hash", "note_count", "built_at", "entries"}
            if not required.issubset(payload.keys()):
                return None
            if not _is_str(payload["schema_version"]) or not _is_str(payload["vault_root"]) \
                    or not _is_str(payload["built_at"]) or not isinstance(payload["note_count"], int) \
                    or not (payload["protocol_hash"] is None or _is_str(payload["protocol_hash"])) \
                    or not isinstance(payload["entries"], list):
                return None

            entries = []
            seen_rels = set()
            for item in payload["entries"]:
                if not isinstance(item, dict):
                    return None
                item_keys = {"rel", "stem", "dir_parts", "content_hash", "mtime", "outbound_link_stems", "words"}
                if not item_keys.issubset(item.keys()):
                    return None
                if not (_is_str(item["rel"]) and _is_str(item["stem"]) and _is_str(item["content_hash"])
                        and isinstance(item["mtime"], (int, float)) and not isinstance(item["mtime"], bool)
                        and _is_str_list(item["dir_parts"]) and _is_str_list(item["outbound_link_stems"])
                        and _is_str_list(item["words"])):
                    return None
                if item["rel"] in seen_rels:
                    return None  # a well-formed index never claims the same canonical path twice;
                                 # two conflicting records for one rel is malformed data, not something
                                 # to silently resolve by picking a winner (section 7: "duplicate/
                                 # conflicting entries cannot silently win") — reject the whole index.
                seen_rels.add(item["rel"])
                entries.append(IndexEntry(
                    rel=item["rel"], stem=item["stem"], dir_parts=tuple(item["dir_parts"]),
                    content_hash=item["content_hash"], mtime=item["mtime"],
                    outbound_link_stems=tuple(item["outbound_link_stems"]), words=tuple(item["words"]),
                ))
            header = IndexHeader(
                schema_version=payload["schema_version"], vault_root=payload["vault_root"],
                protocol_hash=payload["protocol_hash"], note_count=payload["note_count"],
                built_at=payload["built_at"],
            )
            return cls(header, entries)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


class ValidatedIndex:
    """The amortization boundary (v3.7.5, "Fix the Provenance Index
    Performance Boundary"): a MemoryIndex whose freshness has already been
    confirmed against ONE EXACT `vv.Vault` object instance, reusable across
    many search() calls against that same instance without re-hashing every
    file on every call.

    THIS IS NOT A WEAKER FRESHNESS GUARANTEE — it is the identical
    `is_fresh_for()` gate, run exactly once and cached, for exactly as long
    as a `vv.Vault` snapshot is already treated as valid everywhere else in
    this codebase. tools/memory_runtime.py's MemoryRuntime already computes
    `_cycle_members`/`_by_rel` once at construction and never re-verifies
    either against disk again for the life of the instance — this class
    extends the identical, already-established pattern to the index,
    nothing more.

    WHY OBJECT IDENTITY, NOT PATH EQUALITY: `usable_for(vault)` checks
    `vault is self._vault` — a Python object-identity comparison, not
    "same vault_root string". Two different `vv.Vault` instances pointed at
    the same directory (e.g., a fresh MemoryRuntime constructed after a real
    edit-and-reload) are DIFFERENT objects, so a ValidatedIndex bound to the
    first is automatically, structurally unusable for the second — no
    additional invalidation logic is needed, because the wrong-vault case
    and the "reconstructed after mutation" case both fail the identity check
    for free. This is deliberately stricter than comparing `vault.root`
    paths, which a stale reference could still satisfy.

    WHAT THIS DOES NOT DO: it does not detect a file changing on disk WHILE
    a single vv.Vault/MemoryRuntime instance stays alive without being
    reconstructed — nothing in this codebase does that for anything else
    either (MemoryRuntime's own cycle-membership cache has the identical
    property), and building that would require exactly the filesystem
    watcher this project's own rules forbid. A ValidatedIndex is only ever
    as fresh as the vault snapshot it is bound to — never fresher, never
    staler, by construction."""

    def __init__(self, index, vault: "vv.Vault"):
        self._index = index
        self._vault = vault
        # The ONE check, ever, for this instance — exception-guarded exactly
        # like memory_retrieval.search()'s own per-call is_fresh_for() check,
        # so a malicious/buggy raw index passed to MemoryRuntime(index=...)
        # degrades this to "no index" rather than crashing MemoryRuntime
        # construction outright (found during Phase 6 adversarial testing —
        # this path was the one place in the whole boundary that wasn't yet
        # guarded, since it runs once at construction rather than per call).
        try:
            self.is_valid = index is not None and bool(index.is_fresh_for(vault))
        except Exception:
            self.is_valid = False

    def usable_for(self, vault: "vv.Vault") -> bool:
        return self.is_valid and vault is self._vault

    def link_stem_candidates(self, stem: str) -> list:
        return self._index.link_stem_candidates(stem)


def _is_str(v) -> bool:
    return isinstance(v, str)


def _is_str_list(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _stem_of_raw_link(raw: str) -> str:
    """Purely lexical stem extraction from a raw [[...]] target string — the
    same path/pipe/anchor stripping vault_identity.resolve_identity() does,
    stopped BEFORE the resolution step (no vault lookup, no ambiguity check,
    no filesystem access). This is intentionally a smaller, non-authoritative
    sibling of that function: it only ever feeds link_stem_candidates(), a
    narrowing hint that every caller re-validates through the real resolver."""
    raw = raw.strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2]
    raw = raw.split("|")[0].strip()
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    raw = raw.replace("\\", "/")
    stem = raw.rsplit("/", 1)[-1] if raw else ""
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem.lower()
