# Changelog

## v3.6.5

Fixes the P0 and P1 findings from a 2026-08-29 final adversarial release audit. No new capability — this is a correctness pass on gaps the audit found by actually running the tooling against the real vault and a fresh adversarial agent, not by re-reading documentation.

**Fixed (P0):**
- **`tools/validate-vault.py`'s parity checker gave a false `current`/`PASS` verdict on the real production vault.** `INDEX_RULE_MARKERS` (the hardcoded list it checks a vault's `VAULT-INDEX.md` against) was frozen at v3.5/v3.6-era content and never expanded across three subsequent sub-releases, so it kept reporting a stale `VAULT-INDEX.md` as fully synced. Root-caused to `templates/VAULT-INDEX.md` and `templates/CLAUDE.md` never having been updated for v3.6.2–v3.6.4 in the first place (see P1 below) — expanding the marker list without fixing the templates would have just made the check honest about a gap that still existed. Fixed both: synced the templates, then added 3 markers matching the new content. Re-verified against the real vault: now correctly reports `FAIL`/diverged instead of a false `PASS`.

**Fixed (P1):**
- **`templates/VAULT-INDEX.md` and `ai-memory-vault.md`'s embedded copy synced** for the v3.6.2/v3.6.3/v3.6.4 content that never made it there: a new "Jobs and Required Dependencies" section (the `(claim)`/`(claim, explicitly-confirmed: N days)` declaration grammar and table-driven resolution), a one-line Inspection Manifest / PASS-PARTIAL-BLOCKED mention in "How My Memory Works," and a sharpened Candidate Memory section stating the three-predicate promotion test explicitly, including the sharpest, most commonly-missed rule: `source: inferred` can never corroborate another inference, regardless of elapsed time. `templates/CLAUDE.md` needed no changes — confirmed by design precedent (it never carries this level of protocol detail, by intent, not by omission).
- **Circular supersession now has an explicit protocol rule.** `MEMORY_PROTOCOL.md`'s Metadata section and Job dependency policy table both state it directly: a `supersedes`/`superseded_by` cycle (or two notes each claiming to supersede the other with neither actually marked `superseded`) is malformed, not a resolvable state — treat both notes as genuinely incompatible, don't guess, ask. `tools/validate-vault.py`'s `LC-CYCLE` finding is now `error` severity (was `warning`, previously did not block verdict `PASS`) to match. Independently confirmed as a real gap by a fresh adversarial agent during the audit, which found the same absence in the protocol text and had to improvise a resolution from adjacent rules — that improvisation is now the canonical rule instead.
- **`MIGRATION.md` now documents schema-version-mismatch as a drift type**, added in Phase 5's sync order and Phase 6's validation checklist, alongside a note on the real incident above as the reason the sync order in Phase 5 isn't a formality.

**Versioning:** `MEMORY_PROTOCOL.md` `2.6` → `2.7` (the circular-supersession rule; content change per the file's own version rule); `schema/memory-metadata.schema.yaml`'s `x-protocol-version` re-synced to `2.7` (no schema field/enum changed — Job dependency declarations and candidate-promotion predicates remain out-of-scope prose, per the existing `$comment`s); project version `3.6.4` → `3.6.5`.

**Regenerated:** all 23 vault fixtures and 11 health fixtures (dynamically sourced from canonical `MEMORY_PROTOCOL.md` by their build scripts) to pick up the version bump; `tests/fixtures/vaults/manifest.yaml`'s fixture 20 (`cycle`) expectation updated from `verdict: PASS` to `verdict: FAIL` with `LC-CYCLE` moved to `errors`; `tests/fixtures/vaults/build_fixtures.py`'s deliberate-divergence fixture (14) bumped its hardcoded mismatch forward one step (`2.7`→`2.8`) so it keeps testing `PARITY-PROTOCOL-VERSION` detection instead of accidentally matching canonical. Full suite re-run after every change: 23/23 vault fixtures + metadata harness PASS, 11/11 health fixtures + control matrix PASS.

**Known follow-up, not done here:** the real production vault (outside this repo) still needs the same `VAULT-INDEX.md`/`MEMORY_PROTOCOL.md` sync this pass gave the templates — confirmed by running the now-fixed validator against it directly, which correctly reports `FAIL`/diverged. Out of scope for a repo-only fix pass; flagged for the person to request explicitly.

## v3.6.4

Makes candidate promotion as deterministic as v3.6.3 made Job dependency resolution: "independent observation" stops being a qualitative standard and becomes a fail-closed, three-predicate **promotion test** — every predicate must be positively established, and any predicate that can't be shown (including "can't tell if this is a paraphrase") denies the observation. Two honest agents given the same notes must now read out the same promotion decision, and the hardest adversarial cases (restated inferences) fail on provenance alone, not on wording.

**Added:**
- **Promotion test** (`MEMORY_PROTOCOL.md` Candidate memory → "What counts as an independent observation") — (1) *not the same evidence, re-discovered*: no duplicate copies, no paraphrases however worded, no second note from the same source conversation/document, no re-extraction of one event; (2) *admissible provenance*: only `source: explicit`, `source: observed`, or `source: imported` with a genuinely independent external source named in `confidence_basis` can corroborate — **`source: inferred` can never corroborate** (an inference restated is still one inference), and `unknown`/`system` never count either; (3) *distinct occasion or date*: a separate conversation or behavioral occasion, shown by `first_observed`/`last_confirmed` separation or a documented occasion in `confidence_basis`. **Fail-closed default:** any unestablishable predicate fails the observation; the candidate stays `candidate`. The person's explicit confirmation remains an overriding promotion path, independent of the test.
- **No new machinery.** The decisive predicate for the hardest cases reads off `source` — an existing closed-enum schema field — so no evidence IDs, no `candidate_since`, no databases, no session-state files were introduced; `x-schema-version` stays `1.0.0`, and the schema's out-of-scope `$comment` for candidate promotion stays correct. The residual human-judgment question (is this actually a paraphrase?) is bounded by the fail-closed default: it can only deny, never promote.
- **6 new adversarial cases** (`tests/ADVERSARIAL_REGRESSION_SUITE.md`) — C4 (two genuinely independent observations — positive control), C5 (same observation copied twice), C6 (one event documented in two notes), C7 (explicit statement + independent later behavior), C8 (inference + restatement of the same inference — the provenance kill, and the case C3's wording-based framing alone could not decide), C9 (three notes from one original source, collapsing to one evidence context). Coverage-map row updated to `C1-C9` in `tests/run_vault_validator.py` (requires-AI: verdict agreement is behavioral, but "inferred never corroborates" is exactly the kind of non-negotiable a scan can still assert).

**Versioning:** `MEMORY_PROTOCOL.md` `2.5` → `2.6` (content change, per the file's own version rule, coupled with the schema's `x-protocol-version` re-sync to `2.6`); `x-schema-version` stays `1.0.0`; project version stays `3.6` — a protocol-contract addition, not a vault-state capability change.

## v3.6.3

Turns Job dependency resolution into a deterministic contract instead of a per-session judgment call: a Job may now qualify each dependency declaration, and every Required/Preferred/Optional × note-state combination resolves to a defined outcome from a single table — two honest agents given the same Job and the same vault must read out the same result.

**Added:**
- **Deterministic Job dependency policy** (`MEMORY_PROTOCOL.md` Job dependency policy section) — declaration grammar (`[[Note]]` operational, `[[Note]] (claim)` currentness-required, `[[Note]] (claim, explicitly-confirmed: N days)` currentness + explicit recency window) and a resolution table covering the states the old rule was silent on: candidate, deprecated, and absent `memory_status` now fail a `(claim)` declaration instead of being treated like an unexamined file; recency is only ever honored through an author-declared window (no silent default threshold); a malformed declaration is itself a block (`authoring defect`); superseded and ambiguous (contradicting current notes, no supersession link) block both classes; block reasons report in a fixed canonical order. Backward-compatible: an unqualified dependency on a pre-declaration Job resolves operational (permissive), so nothing existing turns red — only a Job that explicitly claims `(claim)`/recency opts into the stricter tiers.
- **`templates/JOB-MEMORY-HEALTH.md` updated** — Context documents the declaration syntax and points at the resolution table; procedure step 8 now resolves Jobs through the table (a `(claim)`/recency failure is `BLOCKED` with reason, an operational dependency on a degraded lifecycle note is a disclosure not a block, a malformed declaration flags as an authoring defect).
- **`ai-memory-vault.md` 4.7 reference synced** — the "Required means required" wording now reflects the deterministic contract scope (declaration syntax, table-driven resolution, block states + claim-class semantics, Job-scoped block).
- **8 new adversarial cases** (`tests/ADVERSARIAL_REGRESSION_SUITE.md`) — Q6 (Required `current` positive control), Q7 (candidate fail under claim), Q8 (deprecated fail under claim), Q9 (absent `memory_status` fail under claim, grandfathers under operational), Q10 (stale/recency-unverifiable fail under declared window), Q11 (malformed declaration is an authoring-defect block), Q12 (Preferred never blocks), Q13 (superseded blocks both classes). Coverage-map rows added in `tests/run_vault_validator.py` (all requires-AI — gating a Job is a behavioral decision; the validator's mechanical inputs behind each are cited).
- **Schema note** (`schema/memory-metadata.schema.yaml`) — `$comment` recording that Job dependency declarations are out-of-scope prose (a behavioral contract resolved against the vault, not frontmatter); no schema field, enum, or default encodes them.

**Versioning:** `MEMORY_PROTOCOL.md` `2.4` → `2.5` (content change, per the file's own version rule, coupled with the schema's `x-protocol-version` re-sync to `2.5`); project version stays `3.6` — a protocol-contract addition, not a vault-state capability change.

## v3.6.2

Makes the Memory Health Check's coverage claim mechanically checkable: every run now writes an Inspection Manifest recording file-by-file what was inspected/skipped/excluded and what was found, and a deterministic, LLM-free audit reconciles that manifest against the vault's true `.md` inventory. An incomplete run can no longer be described as a clean `PASS` — the arithmetic and the audit reject it.

**Added:**
- **Inspection Manifest** (`templates/HEALTH_CHECK_MANIFEST.md`) — the coverage record every health-check run writes, kept **outside** the vault scope it counts: machine-readable frontmatter (`scope`, `scope_target` for Level 2, `start_time`, `completion_state`, `expected_files`, the inspected/skipped/excluded counts, `checks_completed`/`checks_not_completed`, `blocked_dependencies`, `scan_interrupted`) plus body sections: exclusions, every inspected file, every skipped file with a reason, every finding (`- [severity] ID — path: message`), and — Level 3 only — the three cross-note enumeration sections (`## Lifecycle coverage`, `## Duplicate coverage`, `## Supersession coverage`).
- **`tools/audit_health_coverage.py`** — the deterministic coverage audit. Reuses the validator's own vault internals (inventory, frontmatter, lifecycle, wikilinks, structural checks) to reconcile a finished manifest against the machine truth: partition arithmetic (`expected == inspected ∪ skipped ∪ excluded`), the structural-exclusion rule, check-set completeness per level, findings reconciliation (a deterministic finding omitted from the manifest is `HC-FINDING-MISSED`), the Level 3 corpus enumeration supersets, and `completion_state`/`scan_interrupted`/`blocked_dependencies` agreement. A recorded `PASS` that fails any gate is reported `PARTIAL` (`HC-FALSE-PASS`) — the headline rule; a malformed/unreadable manifest is `BLOCKED`. Exit codes `0`/`2`/`3` for `PASS`/`PARTIAL`/`BLOCKED`. The audit never infers duplicates/semantics it can't compute — those still belong to the agent, recorded in the manifest's `## Notes`.
- **`tests/fixtures/health/` + `tests/run_health_coverage.py`** — 11 fixtures (`h01`–`h11`) spanning the verdict space: clean Level 1 `PASS` (zero skips), incomplete Level 3 with an omitted duplicate member (`HC-L3-INCOMPLETE`), a skipped file forcing `PARTIAL`, structural exclusions passing, budget interruption, a silent coverage gap, a blocked dependency, malformed manifest, an `HC-EXCLUSION-INVALID`, a findings-reconciliation miss — and **h08, the required forced simulated-incomplete run**: a manifest that claims `PASS` while listing 5 of 9 files, which the harness verifies comes back `PARTIAL` + `HC-FALSE-PASS`. Fixtures reuse the validator fixtures' base scaffold, so the two suites stay aligned.
- **HEALTH_CHECK rewrite in `MEMORY_PROTOCOL.md`** — the manifest requirement, deterministic coverage arithmetic, per-level evidence requirements (Level 3's enumeration supersets included), the `HC-FALSE-PASS` rejection rule, and the coverage audit as the mechanical backstop; the never-PASS-claim boundary in the Trust & Enforcement section now references it.
- **`templates/JOB-MEMORY-HEALTH.md` updated** — the manifest is the job's mandatory deliverable (new procedure step 15 with arithmetic check), each tier states its level-evidence requirement, and the honesty bar/report format now pin the manifest's `completion_state` to the report.
- **Backstop annotations** in `tests/ADVERSARIAL_REGRESSION_SUITE.md` — H4 (partial scan) and H5 (sampling masquerading as exhaustive), previously dependent on the agent's honesty alone, now note the mechanical coverage audit as the enforcement backstop.

**Versioning:** `MEMORY_PROTOCOL.md` `2.3` → `2.4` (content change, per the file's own version rule, coupled with the schema's `x-protocol-version` re-sync to `2.4`); project version stays `3.6` — a repo-tooling and protocol-evidence addition, not a vault-state capability change.

## v3.6.1

Adds the machine-readable metadata schema — the deterministic-verification foundation, with no AI in the validation loop.

**Added:**
- **`schema/memory-metadata.schema.yaml`** — a JSON Schema (draft 2020-12, serialized as YAML) mirroring `MEMORY_PROTOCOL.md`'s Metadata + Structural-files semantics: the exact normative field set (`status`, `project`, `type`, `memory_status`, `source`, `confidence`, `confidence_basis`, `first_observed`, `last_confirmed`, `stability`, `supersedes`, `superseded_by`, `memory_role`), enums, date/wikilink shapes, and the one lifecycle implication the protocol asserts (`superseded_by` ⇒ `memory_status: superseded`). Preserves the missing / explicit / legacy / invalid distinction — every memory-metadata field optional, `memory_status` with no default, absent never `current`. No field is invented; `candidate_since` is deliberately absent because the protocol doesn't define it.
- **`tests/fixtures/metadata/`** — machine-checkable fixtures (manifest + notes) covering valid current, valid legacy, candidate, superseded + superseding pair, explicit current, absent `memory_status`, invalid enum, invalid date, and invalid lifecycle relationship. These are deterministic schema inputs, distinct from the behavioral adversarial suite.
- **Schema statement in `MEMORY_PROTOCOL.md`** — where the schema lives, what's normative in it, that it never replaces the protocol, and that authority on disagreement is resolved by version comparison (`x-protocol-version` vs. `MEMORY_PROTOCOL.md`'s `version:`), with the protocol always winning.

**Versioning:** `MEMORY_PROTOCOL.md` `2.2` → `2.3` (content change per the file's own version rule); project version stays `3.6` — a repo-tooling addition, not a vault-state capability change, so the release-numbered build story is untouched.

**Known, deliberate gaps (documented in the schema header, resolved later by the validator):** cross-note invariants (supersedes/superseded_by pair completeness, link resolution, superseded-without-link warnings) are vault-level scans, not single-note constraints; date ordering (`first_observed` ≤ `last_confirmed`) is not protocol-asserted and not encoded.

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
