# Changelog

## v3.6

Adds a fourth vault-upgrade state, `incompatible`, alongside `legacy`/`partial`/`current`. Requested explicitly after the v3.5.1 audit's `INCOMPATIBLE`-state investigation was revisited — that pass concluded it wasn't proven necessary at the time; this pass implements it to the same deterministic-criteria standard, and in the process found that an existing v3.5.1 test fixture had actually been triggering the exact disagreement this state now names correctly.

**Added:**
- **`incompatible` vault-upgrade state** (`MEMORY_PROTOCOL.md`, "Vault upgrade states") — for when two or more required protocol surfaces are each present but assert mutually exclusive meanings for the same vocabulary term or field default, with no version signal to resolve which governs. A four-part deterministic test (presence, mutually-exclusive meaning, governs real interpretation, no version signal) gates it — a component that's merely missing or behind is still `partial`, never `incompatible`. Comparisons are scoped to the protocol surfaces themselves; an ordinary note can never trigger this state by mimicking protocol language.
- **Required behavior when `incompatible`** (`MEMORY_PROTOCOL.md`) — `BOOT` reports it explicitly before treating any disputed metadata as safe; the conflicting surfaces and disputed term are named; no silent pick of the newer or older reading, ever; affected metadata stays uninterpreted until reconciled; unrelated work proceeds; a Required Job dependency touching the disputed metadata is treated exactly like any other broken Required dependency and blocks; the report states what must be reconciled.
- **`RETRIEVE`, Job dependency policy, `HEALTH_CHECK`** — each gained one targeted addition wiring the new state into existing mechanisms rather than new machinery: disputed candidates aren't validated either way; a Required note under the dispute triggers the same STOP as a contradictory/malformed one; a Level 1 check now reports vault upgrade state, and any check step needing the disputed vocabulary reports the existing `BLOCKED` outcome for that portion.
- **`I — Incompatible Protocol State` test group** (P0, `tests/ADVERSARIAL_REGRESSION_SUITE.md`) — 9 scenarios: true `partial` and true `incompatible` as controls against each other, `current`/`legacy` controls, dangerous metadata under `incompatible`, an unrelated Job proceeding, a Required Job blocking, and both auto-pick failure directions (newer-wins and older-wins) tested explicitly since a fix guarding only one direction is incomplete.
- One Q&A entry in `TROUBLESHOOTING.md` explaining the `INCOMPATIBLE PROTOCOL STATE DETECTED` message in plain language.

**Fixed (found while implementing the above):**
- The existing Partial-Upgrade Detection test's own 2026-08-29 live-fire fixture (`VAULT-INDEX.md` "still on old vocabulary") actually met the new `incompatible` criteria, not `partial` — it wasn't just behind, it was actively asserting a conflicting meaning for `memory_status: active`. Tightened that test's fixture to a clean missing-content-only case so it stays a valid `partial` control, and moved the original scenario to `I2`. The live-fire result itself stands as evidence the underlying detection already worked; it just needed the sharper label.

**Versioning:** `MEMORY_PROTOCOL.md` `2.1` → `2.2`; project version `3.5.1` → `3.6` (a capability addition, not a patch).

## v3.5.1

Correctness audit of the v3.5 hardening pass. No new infrastructure; one real defect found and fixed, one candidate change considered and rejected with reasoning kept for the record.

**Fixed:**
- **`memory_status` absence contradiction.** `MEMORY_PROTOCOL.md`'s own Metadata table and its "meanings" prose (plus both mirrors, `ai-memory-vault.md` and `templates/VAULT-INDEX.md`) said an absent `memory_status` defaults to `current` — directly contradicting the general Metadata principle in the same three documents ("absence means 'not tracked,' never itself an error") and `MIGRATION.md`'s Phase 2. All three mirrors agreed with *each other*, which is why the v3.5 word-for-word parity check didn't catch it — it was a self-consistency defect, not mirror drift. Fixed at the canonical source (`MEMORY_PROTOCOL.md`) first, then synchronized into both mirrors: absence is now stated plainly as "untracked," never equivalent to an explicit `current`.
- **Retrieval priority had no slot for untracked memory.** A knock-on effect of the same bug — the 9-item retrieval-priority list (and its condensed prose in both mirrors) jumped straight from "other current memory" to "historical/superseded," with nowhere for a fact-bearing note with no `memory_status` set to rank. Added an explicit tier for it, between the two.

**Considered and rejected:**
- **An explicit `INCOMPATIBLE` vault-upgrade state**, alongside `legacy`/`partial`/`current`. Investigated whether the three-state model can deterministically separate "missing/outdated components" from "present components that actively disagree." Conclusion: it already can — `MIGRATION.md` Phase 6 requires a word-for-word diff before anything is declared `current`, so any content-level disagreement between a mirror and canonical necessarily fails that diff and falls back to `partial`. The `memory_status` bug above wasn't a cross-file state-machine gap; it was a self-contradiction baked into the canonical source and faithfully copied into every mirror, which no state machine catches — only self-consistency review does. No scenario in this repo produces an indeterminate result under the existing three states, so per the standing rule ("don't add a state merely because it sounds useful"), it wasn't added.

**Added:**
- **`M — Metadata Defaults` test group** in `tests/ADVERSARIAL_REGRESSION_SUITE.md` (P0) — four cases covering absent `memory_status` on a fact-bearing note, explicit `current`, a fully legacy note with zero lifecycle metadata, and the one case where absence-adjacent reasoning legitimately *does* resolve to `current` (the mechanical pre-v3.5 `active` → `current` vocabulary rename), so the two are never conflated.

---

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
