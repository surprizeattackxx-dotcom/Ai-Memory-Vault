# Changelog

## v3.5

Hardening pass, driven by an adversarial architectural review of v3.4. No new infrastructure — every change is protocol clarity, deterministic behavior, or honest disclosure of an existing limitation.

**Fixed:**
- `status` and `memory_status` no longer share literal values (`active`, `archived`). `memory_status` vocabulary is now `candidate` \| `current` \| `superseded` \| `uncertain` \| `deprecated` — see `MIGRATION.md` for the mapping from the old vocabulary.
- `BOOT` and `HEALTH_CHECK` no longer implicitly contradict each other. `BOOT` is explicitly scoped to "operating rules and Job-scoped context, not a vault-wide scan"; `HEALTH_CHECK` is explicitly the operation that does the wider work, and only on request.
- A confirmed wording drift between `templates/VAULT-INDEX.md` and its embedded copy in `ai-memory-vault.md` (the `source` field's parenthetical examples) is corrected.
- A pre-existing wording-order difference on the daily-note-backfill line between `templates/CLAUDE.md` and its embedded copy is normalized.
- `MEMORY_PROTOCOL.md`'s frontmatter `version: 2.0` versus its body referring to itself as "v3.5" read as an internal inconsistency — caught by the partial-upgrade live-fire test below, not by review. Clarified as two intentionally separate counters (protocol-document version vs. project release version), not a drift.

**Added:**
- **Trust & Enforcement Boundary** section in `MEMORY_PROTOCOL.md` — states plainly that nothing in this system is technically enforced; every rule is a behavioral instruction the operating AI chooses to follow.
- **Job dependency policy** — a Required-tier note that's missing, `uncertain`, `superseded`, contradictory, or malformed now has one defined outcome: stop that Job, say why, don't substitute or guess. Scoped to the one Job, not the session.
- **`RETRIEVE`'s search/validation split** — raw search results have no authority; every candidate gets checked against its `memory_status` before use. Search-result ordering is explicitly called out as meaningless.
- **Structural-file exemption** — `VAULT-INDEX.md`, `Active Priorities.md`, the daily-note template, the protocol file, and everything under `templates/` are now explicitly exempt from orphan detection.
- **Formal definition of "independent observation"** for candidate promotion — with explicit counts-as / doesn't-count-as examples, closing the "two paraphrases of one statement" over-promotion risk.
- **Semantic duplicate limitation** — `MEMORY_PROTOCOL.md` and the Memory Health Check Job now state plainly that duplicate detection is lexical/heuristic, not semantic, and must never claim exhaustive coverage it didn't achieve.
- **Security boundary extended to filenames, paths, metadata, and wikilink targets** — not just note bodies. A file named to look like an instruction is still just a filename.
- **Tiered Memory Health Check** — Level 1 (Structural) / Level 2 (Targeted) / Level 3 (Exhaustive), each reporting `PASS` / `PARTIAL` / `BLOCKED` explicitly. A partial scan can no longer be reported as a clean pass.
- **`MIGRATION.md`** — the staged, non-destructive upgrade procedure, including explicit handling for a `partial` (mid-upgrade) vault state and the ambiguous-`archived` flag-for-review rule.
- **`tests/`** — an adversarial regression suite covering security boundary, retrieval, candidate promotion, Job dependencies, health-check honesty, duplicate detection, and partial-upgrade detection.
- **Portability caveat in `MEMORY_PROTOCOL.md`** — states explicitly that portability across agents is a design intent, not a verified property; only Claude-based agents have actually exercised this protocol so far.

**Consolidated:**
- `templates/CLAUDE.md`'s "rules that can't lapse" — the Memory Governor bullet and the boundary paragraph were tightened rather than left to grow, to offset the net rule count added this pass.

**Known limitations, stated rather than hidden (see the v3.4 adversarial review):**
- Nothing in this system is technically enforced; reliability depends entirely on the operating AI's own instruction-following.
- Retrieval and health checks are heuristic (index/search/wikilink-based); at very large vault sizes (roughly 1,000+ notes) exhaustive checks become impractical for an LLM to complete in one pass, and the protocol now requires that limitation to be disclosed (`PARTIAL`) rather than papered over.
- Portability to non-Claude agents is unverified.

---

## v3.4

First implementation of the memory protocol layer on top of the original v3.3 interview/build system. (Not logged at the time — reconstructed here for the record.)

**Added:**
- `MEMORY_PROTOCOL.md` — the original model-agnostic contract: memory classes (semantic/episodic/procedural/working), metadata (`memory_status`, `source`, `confidence`, `first_observed`, `last_confirmed`, `supersedes`/`superseded_by`, `stability`), the eight memory operations, the Memory Governor, contradiction classification, candidate-memory workflow, and the security boundary treating vault notes as data, not authority.
- `templates/JOB-MEMORY-HEALTH.md` — an opt-in, on-request Memory Health Check Job, shipped by default in every new vault.
- Job template upgraded from a flat "Boot chain" to Required/Preferred/Optional context tiers.
- Non-destructive upgrade path for pre-existing vaults, offering the new layer without forcing a rebuild.
- AGENT/HUMAN/RELATIONSHIP/MEMORY boundary named explicitly in `CLAUDE.md`.

**Deferred (not built, on purpose):** a standalone mechanical health-check script, embeddings, vector search, automated background maintenance, external databases.
