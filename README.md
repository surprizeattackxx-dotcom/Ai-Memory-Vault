# AI Memory Vault

**A durable, local-first memory system for AI agents — built around plain Markdown, explicit authority, deterministic retrieval, and fail-closed behavior.**

AI Memory Vault gives an AI agent persistent memory without turning that memory into an opaque database or trusting whatever file happens to score highest.

The system stores memory in an ordinary Obsidian-compatible vault while providing a structured protocol for:

* persistent user and project memory
* memory lifecycle and supersession
* contradiction handling
* deterministic identity resolution
* indexed retrieval
* optional semantic retrieval
* Job-scoped context
* provenance and memory health
* adversarial validation
* safe fallback when indexes become stale or unavailable

The core principle is simple:

> **Retrieval can suggest. Memory authority decides.**

A search score, embedding similarity, index entry, or filename can never promote a memory, override its lifecycle, or manufacture acceptance.

---

## What This Is

AI Memory Vault is a **local memory architecture for AI agents**.

It separates four concerns that are commonly mixed together in AI memory systems:

| Layer         | Responsibility                                                          |
| ------------- | ----------------------------------------------------------------------- |
| **Storage**   | Markdown files in a normal Obsidian vault                               |
| **Identity**  | Determine which real note a reference actually means                    |
| **Retrieval** | Find potentially relevant notes efficiently                             |
| **Authority** | Decide whether a note is current, historical, disputed, candidate, etc. |

That separation is intentional.

A note being easy to find does not make it true.

A note having a high semantic similarity does not make it authoritative.

An index entry does not become a source of truth simply because it is faster than scanning the vault.

---

## Architecture

```text
                         AI AGENT
                            │
                            ▼
                  ┌─────────────────────┐
                  │   Memory Protocol   │
                  │ identity / lifecycle│
                  │ safety / authority  │
                  └──────────┬──────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │ MemoryRuntime  │
                    └───────┬────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
              ▼             ▼             ▼
        Lexical Search  Semantic Search  Jobs
              │             │             │
              ▼             ▼             ▼
        MemoryIndex    EmbeddingIndex   Scoped
        ValidatedIndex ValidatedIndex   Context
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                  ┌─────────────────────┐
                  │ Identity Resolution │
                  │ Lifecycle Validation│
                  │ Memory Acceptance   │
                  └──────────┬──────────┘
                             │
                             ▼
                       REAL VAULT NOTE
```

The important direction is **one-way**:

**retrieval → candidate → identity/lifecycle validation → accepted context**

Never:

**score → trust**

---

# Memory Storage

The underlying memory remains ordinary files.

That means the vault is:

* human-readable
* inspectable without the application
* compatible with Obsidian
* version-control friendly
* portable between AI tools
* recoverable without a proprietary database

The repository does not require a hosted memory service.

Your actual memory remains yours.

---

# Memory Lifecycle

Memory is not treated as a flat collection of permanent facts.

The protocol distinguishes lifecycle states such as:

* `current`
* `candidate`
* `superseded`
* `disputed`
* historical/archived states

A newer statement does not automatically erase an older one.

Instead, the system can preserve the historical record while establishing the newer state explicitly.

This matters because:

> **"What was true?" and "What is true now?" are different questions.**

Supersession relationships and lifecycle validation prevent stale information from silently becoming current again.

---

# Identity Is Separate From Retrieval

One of the most important architectural boundaries is identity resolution.

A search result does not get to decide what a name means.

For example, if a vault contains:

```text
00 - Inbox/Project.md
09 - Resources/Project.md
```

then a bare `Project` reference can be ambiguous.

The system therefore treats identity resolution as its own authority boundary and fails closed when identity cannot be established safely.

Path-qualified references remain path-qualified.

A same-named file elsewhere in the vault cannot silently replace the requested note.

---

# Retrieval

Retrieval currently supports five methods:

```text
exact
filename
wikilink
text
semantic
```

They operate as **candidate-generation mechanisms**, not authority mechanisms.

## Lexical Retrieval

The lexical layer can use a validated `MemoryIndex` to accelerate:

* exact matches
* filename candidates
* wikilinks
* text matches

The index is a performance optimization.

If it is missing, corrupted, stale, incompatible, or otherwise unusable, retrieval falls back to the live vault.

The vault remains authoritative.

## Semantic Retrieval

Semantic retrieval is optional and uses a real embedding backend.

The current implementation uses:

**Backend:** `sentence-transformers`

**Model:** `sentence-transformers/all-MiniLM-L6-v2`

**Dimensions:** `384`

Semantic retrieval allows queries to find related information even when the query and note do not share obvious keywords.

For example:

```text
"fresh produce you'd find at a market"
```

can retrieve notes discussing:

```text
apples
vegetables
farmers markets
groceries
```

without requiring exact lexical overlap.

Semantic similarity remains **ranking metadata only**.

It cannot:

* promote a candidate to current
* resurrect a superseded note
* override a disputed note
* resolve an ambiguous identity
* bypass lifecycle validation
* manufacture acceptance

---

# Embedding Architecture

Semantic retrieval uses an adapter boundary:

```text
EmbeddingBackend
       │
       ├── NullEmbeddingBackend
       │
       └── SentenceTransformerBackend
```

The core retrieval system does not unconditionally import the machine-learning stack.

The semantic backend is injected into the retrieval/runtime layer.

This keeps the core architecture usable without the optional semantic dependencies.

The embedding index separately records:

* vault identity
* protocol version/hash
* backend identity
* model identity
* embedding dimensions
* note content hashes
* vectors

A vector generated by a different model is not treated as interchangeable with the current model.

Likewise, an embedding index from another vault is rejected.

---

# Index Freshness

Indexes are snapshots.

They are never blindly trusted forever.

Both lexical and semantic indexes use explicit freshness validation.

A semantic index becomes unusable when relevant snapshot identity changes, including cases such as:

* note content changes
* notes are added
* notes are deleted
* the protocol changes
* the vault changes
* the embedding backend changes
* the embedding model changes
* embedding dimensions change

When an index is unusable:

```text
indexed retrieval
      ↓
freshness check fails
      ↓
live retrieval
      ↓
normal validation
```

No destructive repair is required.

No stale result is silently promoted.

---

# Validated Context

Repeated retrieval calls during a runtime session can reuse validated index state.

The system provides:

```text
ValidatedIndex
ValidatedEmbeddingIndex
```

These bind an index's validation result to the specific vault snapshot used to validate it.

This gives the system a useful balance:

* validate once for the session
* reuse the validated snapshot
* reject reuse with a different vault object
* fall back safely when validation fails

The goal is not merely speed.

The goal is **speed without weakening the authority boundary**.

---

# Candidate Scoring

Multiple retrieval methods can identify the same note.

Those methods may use completely different score scales.

Therefore:

```text
Candidate.score
```

represents the score of the **primary retrieval method**, rather than blindly taking the maximum score across unrelated scoring systems.

The individual method scores are preserved separately:

```text
Candidate.method_scores
```

This prevents a semantic similarity value from being accidentally compared against a lexical score as though the two numbers represented the same quantity.

Retrieval priority remains separate from authority.

---

# MemoryRuntime

`MemoryRuntime` is the higher-level integration point.

Conceptually:

```python
MemoryRuntime(
    vault,
    index=...,
    embedding_backend=...,
    embedding_index=...,
)
```

It combines retrieval with the existing memory validation pipeline.

The runtime does not allow a retrieval mechanism to redefine memory authority.

The important invariant is:

```text
retrieval
   ↓
candidate
   ↓
identity
   ↓
lifecycle validation
   ↓
accepted context
```

not:

```text
retrieval
   ↓
accepted memory
```

---

# Security Model

AI Memory Vault is deliberately defensive about memory authority.

The system is designed to resist attacks and failure modes including:

* same-name decoy notes
* ambiguous identity
* path traversal
* fabricated index paths
* stale indexes
* corrupted indexes
* cross-vault index reuse
* wrong embedding model
* wrong backend
* wrong vector dimensions
* malformed vectors
* NaN scores
* infinite scores
* maliciously high similarity scores
* backend exceptions
* freshness-check exceptions
* lifecycle manipulation through retrieved content

A particularly important invariant is:

> **A highly relevant note can still be rejected.**

For example, a superseded note can have a higher semantic similarity than the current note.

That changes ranking.

It does **not** change lifecycle authority.

---

# External Content Is Data

Vault content is memory data.

It is not executable instruction.

A note containing something such as:

```text
IGNORE ALL PREVIOUS INSTRUCTIONS
```

does not acquire authority merely because an AI retrieved it.

The system maintains a separation between:

* agent instructions
* human/user information
* relationship information
* memory content
* retrieved external content

Retrieved content can inform an agent's reasoning without becoming an instruction source.

---

# Memory Health

The project includes mechanical validation for structural memory integrity.

The health/audit tooling covers areas such as:

* frontmatter validity
* wikilink resolution
* lifecycle consistency
* supersession relationships
* cycles
* dependency declarations
* index integrity
* retrieval invariants
* authority boundaries

Some decisions intentionally remain AI-judgment tasks.

For example, determining whether two statements represent:

* a genuine contradiction
* a correction
* a temporal change
* compatible facts

requires semantic judgment and is not falsely presented as a deterministic validator capability.

---

# Jobs

Jobs provide scoped operational context for recurring tasks.

A Job can declare memory dependencies rather than requiring an agent to ingest the entire vault.

This keeps context:

* smaller
* more predictable
* easier to audit
* easier to reason about

Job dependencies are treated as explicit references rather than suggestions to improvise around.

A required dependency failing should not silently become permission to substitute a nearby note.

---

# Portability

The memory itself is deliberately model-agnostic.

The vault does not require a specific AI provider to understand its contents.

The repository includes:

* a canonical memory protocol
* vault templates
* agent configuration templates
* migration guidance
* validation tooling
* retrieval infrastructure

The current semantic backend is optional.

The memory protocol is the durable layer.

This distinction matters:

> **The model can change. The memory should not have to.**

---

# Installation

## Core

The core architecture is designed around Python's standard library.

The semantic backend is optional.

Core functionality does not require `sentence-transformers`, PyTorch, NumPy, FAISS, or another vector database.

## Optional Semantic Retrieval

Install the semantic dependencies with:

```bash
pip install -r tools/requirements-semantic.txt
```

This installs the optional local semantic retrieval stack.

The current backend uses:

```text
sentence-transformers
all-MiniLM-L6-v2
384 dimensions
```

The model is downloaded and cached by the Hugging Face ecosystem when the semantic backend is first initialized.

If the optional dependency is unavailable, the core system remains usable and semantic retrieval degrades cleanly.

---

# Repository Structure

```text
ai-memory-vault/
│
├── MEMORY_PROTOCOL.md
├── ACCELERATION_LAYER.md
├── MIGRATION.md
├── CHANGELOG.md
├── README.md
├── TROUBLESHOOTING.md
│
├── ai-memory-vault.md
│
├── templates/
│   ├── CLAUDE.md
│   ├── VAULT-INDEX.md
│   ├── DAILY-NOTE.md
│   └── MEMORY.md
│
├── tools/
│   ├── memory_retrieval.py
│   ├── memory_runtime.py
│   ├── memory_index.py
│   ├── embedding_backend.py
│   ├── embedding_index.py
│   ├── sentence_transformers_backend.py
│   ├── memory_conflict.py
│   ├── memory_provenance.py
│   ├── memory_health.py
│   └── requirements-semantic.txt
│
└── tests/
    ├── test_memory_index.py
    ├── test_embedding_boundary.py
    ├── test_sentence_transformers_backend.py
    ├── test_semantic_performance_hardening.py
    ├── test_release_audit.py
    └── ...
```

The exact repository contents may evolve; the important architectural distinction is between:

**protocol → memory → retrieval → validation → tooling**

---

# Testing

The project uses adversarial and regression-oriented testing rather than relying exclusively on happy-path examples.

The test suite covers:

* memory indexing
* runtime behavior
* conflict handling
* provenance
* health checks
* surface resolution
* Job dependencies
* semantic retrieval
* embedding backend behavior
* embedding-index freshness
* malformed indexes
* cross-vault reuse
* model/backend/dimension mismatches
* lifecycle attacks
* score attacks
* filesystem traversal attempts
* dependency isolation
* release boundaries

The semantic layer is tested both with a deterministic test double and with the real embedding backend.

The real backend has been exercised end-to-end against actual vault data.

---

# Performance

The design deliberately favors **exactness before approximation**.

The current embedding index uses an exact linear nearest-neighbor scan.

That is intentionally simpler than introducing FAISS, hnswlib, or another approximate-nearest-neighbor dependency.

Measured behavior has already justified targeted optimization where it mattered:

* embedding magnitudes are cached
* validated embedding contexts are reusable
* semantic query embedding is independent of vault size
* retrieval remains exact
* no approximate ranking algorithm is required

The current implementation is intended for personal-vault-scale workloads.

If vault sizes eventually make O(N) semantic search a real bottleneck, the nearest-neighbor layer is an explicit future optimization boundary.

---

# Design Principles

The project follows a few rules aggressively.

### 1. The vault is authoritative

Indexes accelerate the vault.

They do not replace it.

### 2. Retrieval is not trust

A result being highly relevant does not make it true.

### 3. Identity is explicit

A search mechanism does not get to redefine what a reference means.

### 4. Lifecycle is authoritative

Current, candidate, superseded, and disputed states are not ranking labels.

### 5. Fail closed

When identity, freshness, or integrity cannot be established safely, the system falls back or refuses rather than guessing.

### 6. Preserve history

Superseded information should remain available as history when appropriate.

### 7. Prefer simple infrastructure

Plain Markdown, Python, deterministic validation, and explicit boundaries are preferred over unnecessary infrastructure.

### 8. Optional acceleration must remain optional

Semantic retrieval can improve discovery without becoming a requirement for basic memory operation.

### 9. Never auto-execute retrieved content

Memory is data.

It is not an instruction channel.

### 10. Optimize only when measurement justifies it

Performance work should preserve the same authority and correctness guarantees.

---

# Documentation

The repository's canonical protocol is:

**`MEMORY_PROTOCOL.md`**

The acceleration architecture is documented separately in:

**`ACCELERATION_LAYER.md`**

Migration and compatibility guidance lives in:

**`MIGRATION.md`**

Troubleshooting information lives in:

**`TROUBLESHOOTING.md`**

These documents should be treated as complementary:

```text
MEMORY_PROTOCOL.md
        │
        ├── What memory means
        ├── What is authoritative
        └── How an agent should behave
                 │
                 ▼
ACCELERATION_LAYER.md
        │
        ├── How retrieval is accelerated
        ├── Index boundaries
        └── Semantic retrieval
```

---

# Current Status

The repository currently contains a **working lexical + semantic retrieval architecture** with:

* explicit memory lifecycle
* deterministic identity resolution
* indexed lexical retrieval
* validated/reusable lexical context
* optional local semantic retrieval
* real sentence-transformer embeddings
* embedding-index freshness validation
* runtime integration
* score isolation between retrieval methods
* dependency isolation
* adversarial security coverage
* regression coverage
* release auditing

The semantic backend currently targets personal-vault-scale workloads and intentionally uses an exact linear nearest-neighbor scan rather than an additional ANN dependency.

---

# Philosophy

AI memory should not behave like a junk drawer where the loudest or newest item wins.

It should behave more like a carefully indexed filesystem:

**discover quickly, identify precisely, validate authority, preserve history, and fail closed when the answer is uncertain.**

The retrieval layer can become faster.

The models can change.

The embedding backend can change.

The vault can move between agents.

But the fundamental rule stays the same:

> **The thing that finds a memory must never be the thing that decides whether that memory is authoritative.**

