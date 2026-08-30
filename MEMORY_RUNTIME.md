# Memory Runtime

> Status: **read-only, phase 1.** Implements the interfaces below; adds no acceleration technology and no write path. `ACCELERATION_LAYER.md` remains the authoritative design contract for acceleration semantics — this document implements a small piece of it (the baseline, unaccelerated `search()` shape it already describes as today's actual behavior) and never restates or overrides any of its invariants. Where the two could be read as disagreeing, `ACCELERATION_LAYER.md` wins.

## What this is

`MEMORY_PROTOCOL.md`'s `RETRIEVE` operation already describes two phases: a **search phase** that returns candidate notes, and a **validation phase** that checks each candidate's `memory_status`, supersession, and conflicts against live Markdown before anything is trusted. This is the concrete, dependency-free implementation of that shape, sitting between the canonical Vault and an AI/agent caller:

```
Canonical Vault (tools/validate-vault.py's Vault — discovery, frontmatter)
        v
tools/vault_identity.py     — path-aware, fail-closed note-identity resolution
tools/memory_retrieval.py   — search phase: query -> Candidates
        v
tools/memory_runtime.py     — validation phase + orchestration: Candidates -> ValidatedContext
```

**Retrieval never establishes truth.** A `Candidate` means "this note may be relevant" — it carries no `memory_status`, no trust judgment, nothing about currentness. Only `memory_runtime.py`'s validation step, which re-reads the live note and checks it against the vault's actual lifecycle state (superseded/candidate/uncertain/deprecated/disputed/cycle-member/absent), decides whether a result is `accepted`. A highly-ranked candidate cannot become "current" merely because retrieval ranked it highly — see `tests/test_memory_runtime.py`'s key-invariant test.

## The three modules

- **`tools/vault_identity.py`** — `resolve_identity(vault, raw)`: given a path-qualified or bare identity, returns the one note it names, or fails closed (`None`, `[]` for missing; `None`, `[candidates]` for ambiguous) — never a same-stem guess. Also `stem_matches()`, the deliberately looser "every note with this filename" lookup. Originally written inside `tools/audit_job_dependencies.py` for its P0-1 fix; extracted here so a second consumer doesn't carry a second copy of a security-relevant algorithm.
- **`tools/memory_retrieval.py`** — `search(vault, query, methods=None, limit=None) -> list[Candidate]`. Four explicit, never-conflated methods: `exact` (fail-closed identity), `filename` (loose stem lookup, multiple results expected), `wikilink` (inbound-link graph, reusing `validate-vault.py`'s own `WIKILINK_RE`), `text` (deterministic, word-boundary keyword scoring — never claimed or scored as semantic). Deterministic ordering by an explicit sort key, never filesystem/rglob order. Duplicate hits on the same note (matched by more than one method) merge into one `Candidate` recording every contributing method, never a repeated entry.
- **`tools/memory_runtime.py`** — `MemoryRuntime(vault_root)` exposes `resolve(identity)` (identity only), `inspect(identity)` (resolve + validate one note), `retrieve(query, ...)` (search + validate every candidate). Every returned `ValidatedContext` carries full provenance: canonical path, retrieval method(s), the source query, `memory_status`, a `status_track` explaining *why*, and `accepted: bool` — the one field that means "trustworthy right now." Rejected candidates (superseded, candidate, disputed, cycle-member, untracked) are still returned, correctly labeled, never silently dropped or promoted.

All three: standard library only, no network access, no write method anywhere. `tests/test_memory_runtime.py` includes a static source check confirming no accelerator dependency is imported.

## What this is not

- Not an accelerator. There is no index, cache, or embedding store here — every call re-reads live Markdown. `index_snapshot_time` on every `Candidate` is always `None` for exactly this reason: nothing is cached, so there's no snapshot to report. A future accelerator implementing `ACCELERATION_LAYER.md`'s `search()` contract would populate that field, and this runtime's validation phase would still re-check the live note regardless of what the accelerator claims — that invariant doesn't change when one is added.
- Not a write path. `WRITE_MEMORY`, `UPDATE_MEMORY`, `RESOLVE_CONFLICT`, and `CHECKPOINT` are untouched — this phase only ever reads. Deleting a future accelerator's index (or never building one) leaves this runtime's behavior unchanged, per `ACCELERATION_LAYER.md` invariant #5 — it was never storing anything the index could have been standing in for.
- Not wired into `validate-vault.py`, `audit_health_coverage.py`, or `audit_job_dependencies.py`'s existing CLI behavior. Those tools work exactly as before; this is an additive capability, not a replacement.
- Not an autonomous agent. Three read-only questions — where is it, is it trustworthy, what matches — nothing else.

## Where semantic retrieval plugs in later

`memory_retrieval.py`'s `search()` signature and `Candidate` shape are the seam. A future `semantic` or `graph` method adds another `_search_*` function returning the same `Candidate` fields (with a genuine, honestly-labeled score this time) and a new entry in `METHODS` — `memory_runtime.py`'s validation phase, `MemoryRuntime`'s public API, and every existing caller are unaffected, because validation was never told to trust a method by name, only to re-check the note a method happened to point at. No `MEMORY_PROTOCOL.md` change is required for that addition, same as none was required for this one.
