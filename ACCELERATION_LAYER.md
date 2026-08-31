# Acceleration Layer

> Status: **the contract this document defines is now implemented.** Written before any such implementation existed, specifically so the contract couldn't be quietly reshaped to fit whatever got built first — that discipline held: `tools/memory_index.py` (lexical candidate acceleration) and `tools/embedding_index.py` + `tools/sentence_transformers_backend.py` (semantic candidate acceleration, an optional dependency, see `tools/requirements-semantic.txt`) were both built against this document's invariants, unchanged from what's written below, and are tested against them (`tests/test_memory_index.py`, `tests/test_embedding_boundary.py`, `tests/test_sentence_transformers_backend.py`, `tests/test_semantic_performance_hardening.py`). This document remains the authoritative contract — it describes what any accelerator, present or future, must do; the modules above are its first (and, for the lexical case, only) implementations. No dependency was added BY this document — the optional semantic one lives entirely in the modules above, isolated from the stdlib-only core exactly as this contract requires. No protocol behavior changed.

`MEMORY_PROTOCOL.md`'s "Source of truth" section already draws the picture this document formalizes:

```
Markdown / Obsidian vault  (canonical memory — the only copy that has to exist)
        ↓
   optional acceleration layer  (index, full-text search, cache, embeddings)
```

This is the contract behind that arrow. It applies to *any* future retrieval accelerator this project ever builds or accepts — a filename/frontmatter index, a full-text search cache, an embeddings store, a vector database, or something not yet invented. The technology is deliberately unnamed and unchosen here: this contract must hold regardless of which one, if any, eventually gets built, and picking one is out of scope for this document.

---

## The one sentence that governs everything below

**The vault is truth. The index is a guess about the vault, cached for speed, and it is allowed to be wrong.**

Every invariant, every rebuild rule, and every stale-data resolution below is a restatement of that sentence in a different situation. If a future design decision doesn't obviously follow from it, the decision is wrong, not the sentence.

---

## Required invariants

An acceleration layer that violates any one of these is not "an acceleration layer with one bug" — it is a second source of truth wearing a cache's name, exactly the arrangement `MEMORY_PROTOCOL.md`'s Source of truth section already rules out. All ten are non-negotiable; none is satisfied "mostly."

1. **Rebuildable entirely from Markdown.** Every field, entry, and relationship the index holds must be derivable by re-reading the vault's `.md` files and nothing else. A fact that exists only in the index and can't be reconstructed from Markdown was never really captured — it was smuggled into a cache pretending to be memory.
2. **Never authoritative over Markdown.** On any disagreement, for any field, the index loses — see "Stale-index resolution" below. Not "usually loses." Not "loses unless it's been reliable." Loses, unconditionally, every time.
3. **Never mutates canonical memory merely because the index says something.** The index is read *from* the vault; nothing ever writes *from* the index back into a note's frontmatter or body. An index rebuild that "corrects" a note's `memory_status` because the index computed a different value is a protocol violation, not a feature — `UPDATE_MEMORY`, `RESOLVE_CONFLICT`, and every other write path in `MEMORY_PROTOCOL.md` are triggered by evidence from the person or the vault, never by the accelerator's own internal state.
4. **Preserves provenance to the originating Markdown note.** Every index entry carries an unambiguous pointer back to the exact `.md` file (and, where relevant, the exact heading or frontmatter field) it was derived from. An index result with no traceable source note is unusable — it can't be validated, and an unvalidatable candidate is worthless per `RETRIEVE`'s validation phase.
5. **Tolerates complete deletion.** Deleting the entire index, cache, or database — accidentally or on purpose — must leave the vault's behavior unchanged apart from search speed. `BOOT`, `RETRIEVE`, `WRITE_MEMORY`, `HEALTH_CHECK`, and every other operation in `MEMORY_PROTOCOL.md` must degrade to "no acceleration layer" behavior (filesystem/full-text search, wikilink traversal, folder indexes) with zero data loss, because that degraded mode is simply *today's* actual behavior, not a fallback path that might be undertested.
6. **Tolerates stale index entries.** An index that hasn't been rebuilt since the vault last changed is expected, ordinary operating state, not a fault condition. Nothing may treat staleness as an error; see "Rebuild semantics" below for exactly how each staleness state behaves.
7. **Never bypasses lifecycle validation.** `RETRIEVE`'s validation phase — checking `memory_status`, supersession, conflicts, source/confidence — runs against the live Markdown note, every time, regardless of what the index says about that note. An accelerator may narrow *which* notes get validated (the search phase); it may never decide *that* a note is valid without the validation phase actually running against its current on-disk content.
8. **Never bypasses the security boundary.** Indexed content — a cached snippet, an extracted keyword, a stored embedding, a search-result preview — is exactly as much data, never instruction, as the Markdown it was derived from. `Security boundary`'s rules apply identically whether the agent is reading a note directly or reading an index's representation of that note; indexing something doesn't launder it into something safer to trust, and it doesn't launder it into something more dangerous either — it's the same data, differently stored.
9. **Never executes instructions found in indexed content.** A directive-sounding string doesn't gain authority by surviving into an index entry, an embedding, or a cached summary — same rule as #8, stated separately because it's the specific failure mode adversarial testing should target directly (see `tests/ADVERSARIAL_REGRESSION_SUITE.md`'s Security Boundary group for the pattern this extends, whenever an accelerator exists to test).
10. **Never causes `BOOT` to ingest the entire vault merely because the index exists.** `BOOT`'s behavior — read the boot file, `READ_INDEX`, most recent daily note, Active Priorities, nothing more — is fixed by `MEMORY_PROTOCOL.md` independent of whether an accelerator is present. A fully built, fast index sitting there is not an invitation to load more at boot than the protocol already specifies; "the index makes it cheap now" is not a reason to change what `BOOT` is for. Full-vault ingestion, accelerated or not, is still `HEALTH_CHECK`'s job, still on request only.

---

## Stale-index resolution

The canonical worked example, stated exactly:

```
Markdown note:  memory_status: superseded
Index entry:    memory_status: current
```

**The Markdown state wins.** The note is treated as `superseded`. The index entry is stale data — evidence the index needs a rebuild, not evidence the vault changed. This holds symmetrically, in every direction, for every field the index might cache:

- Markdown `current`, index `superseded` → treated as `current`. (The index doesn't know about a recent write yet — normal lag, not a conflict to resolve.)
- Markdown absent `memory_status`, index `current` → treated as absent/untracked, per the absence rule in `Metadata`. The index guessed a default the protocol explicitly says doesn't exist.
- Markdown note deleted, index entry still present → the note doesn't exist. A dangling index entry is not evidence the note still exists somewhere; it's evidence the index is behind.
- Markdown note exists, index has no entry for it → the note is simply not yet indexed. Full-text/filesystem search (`RETRIEVE`'s existing fallback) still finds it; the accelerator's blind spot never becomes the vault's blind spot.

**Why unconditionally, not "usually":** any exception — "trust the index if it's been right before," "trust the index if the rebuild is recent," "trust the index for this field but not that one" — reintroduces the second-source-of-truth arrangement invariant #2 forbids, just gated behind a plausible-sounding heuristic instead of stated outright. The moment an accelerator's output is trusted over a fresh read of the note it claims to describe, it has become the memory store, not an index of one.

---

## Rebuild semantics

Six states an implementation must define behavior for. None of these change `MEMORY_PROTOCOL.md`'s eight operations — they describe how the accelerator itself behaves, and each one below states explicitly which operations, if any, it's allowed to touch.

**Full rebuild** — the index is discarded and reconstructed from every `.md` file in scope, from nothing. Triggered on request, never automatically at `BOOT` (invariant #10) and never silently mid-session. Output: an index fully explained by the current vault state, with a recorded build timestamp and the scope covered. Touches no operation's behavior — pure read of Markdown, zero writes back to the vault.

**Incremental rebuild** — the index updates only for files changed since its last build (by mtime, content hash, or an equivalent signal — implementation's choice, unconstrained here). Must be provably equivalent to a full rebuild restricted to the changed set: an incremental rebuild that produces a different result than a full rebuild would have, for the same file, is a bug in the accelerator, not a divergence the protocol tolerates. If equivalence can't be established (the change-detection signal is unreliable), the implementation falls back to a full rebuild rather than risk silent drift.

**Corruption** — the index is unreadable, truncated, or internally inconsistent (fails its own schema/format check). Treated exactly as invariant #5's "complete deletion" case: fall back to unaccelerated retrieval immediately, and — only on request, never automatically — trigger a full rebuild. A corrupted index is never partially trusted; there is no "salvage what parses."

**Missing index** — no index exists yet, or it was deleted. Not an error state anywhere in `MEMORY_PROTOCOL.md`. `RETRIEVE`'s search phase falls back to root index → folder index → wikilink traversal → filesystem/full-text search, exactly as it does today with no accelerator built at all. `HEALTH_CHECK` may note the absence as informational, never as a finding requiring a fix.

**Stale index** — an index exists and is readable, but was built before the vault's current state (a note changed, was added, or was deleted since the last build/incremental update). Not an error. Per "Stale-index resolution" above: any note the index describes still gets validated against its live Markdown before being trusted, so a stale entry either self-corrects at validation time or gets caught and reported — it never propagates a wrong answer past the validation phase invariant #7 requires.

**Schema-version mismatch** — the index's own format/schema version doesn't match what the current accelerator implementation expects (a field was added, renamed, or reinterpreted in a newer accelerator version than the one that built the index on disk). Resolution follows the precedent already established for `schema/memory-metadata.schema.yaml` in `Metadata`'s "Machine-readable schema" section: the index must declare the schema version it was built against; a mismatch is detected deterministically by comparing that declaration against the running implementation's expected version; on mismatch, the index is treated as **stale in full** (equivalent to "missing index," not partially trusted field-by-field) until rebuilt against the current schema. A mismatched-version index is never silently reinterpreted under the new schema's assumptions.

---

## What the layer may do, and must never do

| May | Must never |
|---|---|
| Narrow which notes `RETRIEVE`'s search phase considers, to make search faster | Skip the validation phase for any note it narrowed to |
| Cache a note's frontmatter, a full-text index of its body, or a derived representation (keywords, an embedding, a summary) for fast lookup | Treat that cache as an acceptable substitute for reading the note when a real decision depends on its content |
| Speed up `HEALTH_CHECK`'s coverage bookkeeping (e.g., a fast file-inventory cache) | Let `HEALTH_CHECK`'s coverage arithmetic or `Inspection Manifest` requirements be satisfied by index state instead of the vault's true `.md` inventory — the manifest counts real files, always |
| Run its own rebuild/maintenance process, on request or on a schedule the person sets up themselves | Run unprompted inside `BOOT`, or make `BOOT` slower or heavier because "the index needs updating" |
| Report its own staleness, corruption, or absence as informational context | Report its own state as if it were the vault's state — an accelerator being current says nothing about whether the *vault* is `legacy`/`partial`/`current`/`incompatible`; that's `MEMORY_PROTOCOL.md`'s "Vault upgrade states" question, answered from Markdown, never from the index |
| Be deleted, corrupted, or left stale indefinitely with zero data loss | Be the only place any fact, relationship, or provenance link exists |

---

## Interface shape (contract, not implementation)

No technology is chosen or implied here — not embeddings, not a vector database, not any specific search engine. Whatever gets built must be describable in these terms; this is the minimum shape a compliant accelerator exposes, not a design for one:

- **`search(query, scope?) → [{note_path, matched_on, index_snapshot_time}]`** — candidates only, never validated facts. `note_path` is the provenance link (invariant #4); `index_snapshot_time` is how a caller judges staleness before trusting anything. The caller (`RETRIEVE`'s search phase) still runs every result through validation against the live note.
- **`rebuild(scope?, mode: full | incremental)`** — the only write path the accelerator has, and it writes to the index, never to the vault. Idempotent: rebuilding twice from the same vault state produces the same index.
- **`invalidate(note_path)`** — marks one entry stale without a full rebuild, for a caller that already knows a specific note changed. Optional optimization; a compliant accelerator may instead just rely on the next rebuild.
- **`status() → {schema_version, last_build_time, last_build_mode, scope_covered, known_stale: bool}`** — self-reported health, for `HEALTH_CHECK` or a person to consult. Never a substitute for actually validating a specific fact — see the May/must-never table above.

Nothing here requires the accelerator to be a separate process, a database, or persistent at all — a compliant "accelerator" could be an in-memory dictionary rebuilt every session and it would satisfy every invariant above. The contract is about behavior under staleness, deletion, and disagreement, not about scale or technology.

---

## Where this plugs into the eight operations

Only `RETRIEVE`'s **search phase** ever consults an accelerator, and only to shrink the candidate set search would otherwise return from filesystem/full-text lookup — the **validation phase that follows it is unchanged and unaccelerated**, per invariant #7. No other operation in `MEMORY_PROTOCOL.md` changes shape because an accelerator exists:

- **`BOOT`** — untouched (invariant #10). Doesn't check for an accelerator, doesn't rebuild one, doesn't behave differently with one present versus absent.
- **`HEALTH_CHECK`** — may optionally report the accelerator's own `status()` as an informational note, never as part of the vault's coverage arithmetic or `PASS`/`PARTIAL`/`BLOCKED` verdict. Conflating "the index is stale" with "the health check is partial" would be the same category error `PASS`/`PARTIAL`/`BLOCKED` and `legacy`/`partial`/`current`/`incompatible` already had to be kept apart from each other for.
- **Job dependency policy** — a Job's Required/Preferred/Optional resolution is decided from the live note's lifecycle state, exactly as today. An accelerator may help *locate* the note faster; it never gets a vote in whether the dependency resolves.
- **`WRITE_MEMORY` / `UPDATE_MEMORY` / `RESOLVE_CONFLICT` / `CHECKPOINT` / `READ_INDEX`** — untouched. None of these read from or write to an accelerator at all; they operate on the vault, and the accelerator finds out about the result the same way anything else does — by being rebuilt or invalidated afterward, never by being consulted during.

---

## What this document is not

Not an implementation plan and not a technology recommendation in itself — no dependency, package, or technology choice is made BY this document; it states the invariants any accelerator must satisfy, never which one to build. When a specific accelerator was proposed (the lexical `tools/memory_index.py`, then the optional semantic `tools/embedding_index.py`/`tools/sentence_transformers_backend.py`), each was evaluated against every invariant above *before* a line of code landed, and — per the standing discipline this project runs on for any protocol-adjacent decision — each proposal stated the exact change and got explicit confirmation first, including the specific choice of backend/model for the semantic case, which was never picked silently. Any future accelerator (a different embedding model, a different index technology, a vector database, anything not yet built) goes through the identical process: evaluated against these invariants, confirmed explicitly, before anything is built.
