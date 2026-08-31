# AI Memory Vault

A local-first, file-based memory system designed for **durable storage, deterministic retrieval, provenance, lifecycle safety, and adversarial validation**.

AI Memory Vault treats memory as data—not as an opaque chatbot feature. The vault is made of ordinary Markdown files with explicit metadata, while retrieval and validation layers provide increasingly powerful ways to find information without allowing search results, scores, indexes, or semantic similarity to become sources of truth.

## What It Is

AI Memory Vault is a structured memory vault built around four principles:

* **Files are the durable memory.**
* **Identity is resolved explicitly and fail-closed.**
* **Lifecycle state is authoritative and independent of retrieval.**
* **Accelerators may improve discovery, but can never manufacture truth.**

The system supports both traditional lexical retrieval and optional semantic retrieval using locally executed embeddings.

The architecture is intentionally layered so that faster or more sophisticated retrieval mechanisms can be added without changing what constitutes an accepted memory.

---

## Core Architecture

```text
                    ┌─────────────────────────┐
                    │      Markdown Vault      │
                    │                         │
                    │  Notes + Frontmatter    │
                    │  Metadata + Provenance   │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    Identity Authority   │
                    │                         │
                    │ vault_identity.py       │
                    │                         │
                    │ Fail-closed resolution  │
                    └────────────┬────────────┘
                                 │
                ┌────────────────┴────────────────┐
                ▼                                 ▼
      ┌─────────────────────┐          ┌─────────────────────┐
      │  Lexical Retrieval  │          │ Semantic Retrieval  │
      │                     │          │                     │
      │ exact               │          │ SentenceTransformer │
      │ filename            │          │ all-MiniLM-L6-v2    │
      │ wikilink            │          │ 384 dimensions      │
      │ text                │          │                     │
      └──────────┬──────────┘          └──────────┬──────────┘
                 │                                │
                 ▼                                ▼
        ┌─────────────────┐              ┌──────────────────┐
        │ MemoryIndex     │              │ EmbeddingIndex   │
        │ ValidatedIndex  │              │ Validated...     │
        └────────┬────────┘              └────────┬─────────┘
                 │                                │
                 └──────────────┬─────────────────┘
                                ▼
                    ┌─────────────────────────┐
                    │ Candidate Generation   │
                    │ + Method Ranking        │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │  MemoryRuntime.inspect  │
                    │  / lifecycle validation │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     Accepted Memory     │
                    └─────────────────────────┘
```

The important boundary is at the bottom:

> **Retrieval finds candidates. Validation decides what the system may accept.**

A higher semantic score cannot promote a superseded note.
A corrupted index cannot become authority.
A fabricated path cannot become a real memory.
A retrieval method cannot bypass identity resolution or lifecycle validation.

---

## Retrieval

`memory_retrieval.py` currently supports five retrieval methods:

1. **exact**
2. **filename**
3. **wikilink**
4. **text**
5. **semantic**

The lexical methods remain the deterministic baseline.

Semantic retrieval is additive: it can discover information by meaning even when the query shares little or no literal vocabulary with the note.

### Retrieval flow

```text
Query
  │
  ├── exact
  ├── filename
  ├── wikilink
  ├── text
  └── semantic
          │
          ▼
      Raw candidates
          │
          ▼
     Merge by identity
          │
          ▼
    Method-priority ranking
          │
          ▼
    MemoryRuntime validation
          │
          ▼
       Results
```

Semantic retrieval does **not** replace lexical retrieval.

---

## Semantic Retrieval

The shipped semantic backend uses:

* **Backend:** `sentence-transformers`
* **Model:** `sentence-transformers/all-MiniLM-L6-v2`
* **Dimensions:** 384
* **Similarity:** cosine similarity
* **Execution:** local/offline after the model has been downloaded
* **GPU requirement:** none

The semantic dependency is intentionally isolated from the core system.

The standard-library core can operate without semantic dependencies installed. The real backend is imported lazily and injected into retrieval rather than becoming a mandatory dependency of the entire vault.

### Install semantic support

```bash
pip install -r tools/requirements-semantic.txt
```

The first backend initialization may download the model if it is not already cached locally.

After the model is cached, retrieval operates locally without requiring a remote inference service.

### Important distinction

Semantic similarity is **ranking information only**.

It is never:

* a trust score
* an acceptance decision
* a lifecycle decision
* an identity decision
* provenance
* evidence that a memory is current

A semantically excellent match can still be rejected because the underlying note is superseded, disputed, malformed, ambiguous, or otherwise invalid.

---

## Indexes

AI Memory Vault supports persistent retrieval accelerators while keeping the vault files authoritative.

### `MemoryIndex`

The lexical index accelerates lexical candidate discovery.

Its validity is tied to the vault snapshot it was built from.

### `ValidatedIndex`

A `ValidatedIndex` binds an index to the exact vault object it was validated against for the lifetime of a runtime session.

This avoids repeatedly performing the same expensive freshness check while preserving the existing construction-time snapshot semantics.

### `EmbeddingIndex`

The semantic index stores:

```text
schema_version
vault_root
protocol_hash
backend_id
model_id
dimensions

entries:
    rel
    content_hash
    vector
```

Vectors are tied to:

* the vault
* the protocol
* the backend
* the model
* the vector dimensionality
* the source note's content hash

An incompatible index is rejected rather than silently reused.

### `ValidatedEmbeddingIndex`

Like `ValidatedIndex`, this provides session-scoped validation of an embedding index.

The semantic index remains an accelerator—not an authority.

---

## Candidate and Score Semantics

Retrieval methods may produce different kinds of scores.

Those scores are **not blindly combined**.

Each candidate retains:

```text
Candidate.score
Candidate.method_scores
```

`Candidate.score` represents the score belonging to the candidate's primary retrieval method.

`method_scores` preserves individual method scores separately.

This prevents a semantic similarity value from being accidentally compared as though it were numerically equivalent to a lexical score.

Method priority is evaluated before score, so a larger semantic number cannot automatically outrank a higher-priority lexical match.

---

## Identity and Lifecycle Safety

The most important architectural rule is that retrieval does not decide whether something is true.

### Identity

`vault_identity.resolve_identity()` remains the authoritative identity resolver.

It is responsible for resolving references against the actual vault and failing closed when identity is ambiguous or invalid.

Indexes do not resolve identity.

Semantic similarity does not resolve identity.

Scores do not resolve identity.

### Lifecycle

`MemoryRuntime.inspect()` / `_validate()` remains the acceptance authority.

Lifecycle state such as:

* current
* candidate
* superseded
* disputed
* malformed

is determined from authoritative note metadata and vault state—not from retrieval scores.

This means an adversarially high semantic score cannot resurrect a superseded memory.

---

## Freshness and Mutation Safety

Both lexical and semantic indexes use whole-snapshot freshness validation.

Relevant changes invalidate the corresponding accelerator, including changes to:

* note contents
* note creation/deletion
* `MEMORY_PROTOCOL.md`
* vault identity
* index schema
* semantic backend identity
* semantic model identity
* embedding dimensions

When an accelerator is unavailable, corrupted, incompatible, or stale, retrieval falls back to live vault discovery rather than trusting stale data.

The system therefore prefers:

```text
slower + authoritative
```

over:

```text
faster + potentially stale
```

---

## Security Model

The retrieval layer is deliberately treated as untrusted optimization infrastructure.

The release and adversarial test suites exercise cases including:

* same-stem decoy notes
* ambiguous identities
* superseded notes with extremely high scores
* fabricated index paths
* path traversal attempts
* cross-vault index reuse
* wrong backend/model/dimension combinations
* stale content hashes
* malformed vectors
* NaN and infinite similarity scores
* exception-raising backends
* exception-raising indexes
* corrupted indexes
* disappearing and appearing ambiguities
* mutation followed by restoration
* mixed lexical + semantic results

The security invariant is simple:

> **No retrieval accelerator may cross the identity, lifecycle, provenance, or acceptance boundary.**

---

## Performance

Semantic retrieval currently uses an exact **O(N) linear nearest-neighbor scan**.

The implementation precomputes stored-vector magnitudes once, at index construction, so repeated cosine calculations never recompute an invariant value — this produces results bit-identical to the unoptimized reference calculation, never an approximation.

Measured results: profiling found the per-call freshness check (`is_fresh_for()`) alone consumed **28–56% of total semantic query time**, rising as vault size grew from N=100 to N=5,000. `ValidatedEmbeddingIndex` eliminates that repeated cost by checking freshness once per session, at construction, rather than on every query.

At personal-vault scale, this is currently sufficient.

If vaults become substantially larger, the next optimization target would be vectorized or approximate nearest-neighbor search. That would be a separate architectural decision rather than something silently introduced into the current core.

---

## Dependency Model

The core project remains intentionally lightweight.

Semantic retrieval is an optional dependency layer:

```text
Core
├── Python standard library
├── vault parsing
├── identity resolution
├── lifecycle validation
├── lexical retrieval
└── lexical index

Optional semantic layer
├── sentence-transformers
├── torch
├── numpy
└── local embedding model
```

The semantic backend is lazily imported and injected.

A caller that does not use semantic retrieval does not need to load the ML stack.

---

## Project Structure

Important components include:

```text
.
├── MEMORY_PROTOCOL.md
├── MEMORY_RUNTIME.md
├── ACCELERATION_LAYER.md
├── MIGRATION.md
├── CHANGELOG.md
│
├── tools/
│   ├── memory_retrieval.py
│   ├── memory_runtime.py
│   ├── memory_index.py
│   ├── embedding_backend.py
│   ├── embedding_index.py
│   ├── sentence_transformers_backend.py
│   ├── memory_health.py
│   ├── memory_conflict.py
│   ├── memory_provenance.py
│   └── requirements-semantic.txt
│
└── tests/
    ├── test_memory_index.py
    ├── test_memory_runtime.py
    ├── test_embedding_boundary.py
    ├── test_sentence_transformers_backend.py
    ├── test_semantic_performance_hardening.py
    ├── test_semantic_adversarial_release.py
    ├── test_release_audit.py
    ├── test_memory_health.py
    ├── test_memory_conflict.py
    ├── test_memory_provenance.py
    ├── test_surface_resolution.py
    └── ...
```

---

## Testing

The project uses both ordinary regression testing and adversarial testing.

The test surface covers:

* vault validation
* metadata/schema behavior
* identity resolution
* lifecycle validation
* provenance
* conflict handling
* lexical indexes
* semantic indexes
* backend behavior
* freshness and mutation
* cross-method ranking
* dependency isolation
* serialization integrity
* path safety
* exception handling
* release invariants

The current audited suite contains **13+ test/validator entry points**, including dedicated semantic and release-audit coverage.

A clean test run is a release requirement.

---

## Design Philosophy

AI Memory Vault deliberately separates four concerns:

### 1. Storage

Markdown files are the durable memory.

### 2. Discovery

Retrieval methods find possible matches.

### 3. Identity

The vault determines what a reference actually refers to.

### 4. Acceptance

Runtime validation determines whether the resulting memory is acceptable.

Those concerns should not collapse into one another.

A faster search algorithm should remain a faster search algorithm—not quietly become a new source of truth.

---

## Current Status

**Semantic retrieval: shipped and audited.**

The current architecture includes:

* deterministic lexical retrieval
* persistent lexical indexing
* validated lexical index reuse
* semantic retrieval
* local sentence-transformer embeddings
* persistent embedding indexes
* validated embedding-index reuse
* cross-backend/model/dimension freshness checks
* explicit per-method scoring
* adversarial semantic retrieval testing
* dependency isolation
* lifecycle and identity separation

The semantic layer is currently considered suitable for **local, personal-vault-scale use**.

Known limitations are documented rather than hidden:

* semantic nearest-neighbor search remains O(N)
* semantic duplicate detection is not currently part of `HEALTH_CHECK`
* some semantic judgments remain inherently AI-assisted rather than mechanically decidable
* optional semantic dependencies use the project's current dependency constraints

---

## Documentation

For the deeper contracts:

* `MEMORY_PROTOCOL.md` — normative memory rules and invariants
* `MEMORY_RUNTIME.md` — runtime and retrieval architecture
* `ACCELERATION_LAYER.md` — index and acceleration architecture
* `MIGRATION.md` — migration guidance
* `CHANGELOG.md` — historical changes and decisions

The protocol is the authority for memory semantics. Implementation details must conform to it.

---

## The Short Version

If you only remember one thing:

**The vault is the truth.**

Everything else exists to make finding that truth faster.

Lexical indexes are guesses.
Semantic vectors are guesses.
Scores are ranking metadata.
Caches are accelerators.
Retrieval produces candidates.

**Identity and lifecycle validation decide what actually counts.**
