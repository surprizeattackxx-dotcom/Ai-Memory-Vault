# Migration Guide

How an existing vault moves to the current version of `MEMORY_PROTOCOL.md`, without a rebuild and without losing anything. This is the staged procedure `ai-memory-vault.md`'s upgrade-path branch points to — read it in full before touching a real vault, and follow the phases in order.

**The two rules that override everything below:** never rewrite the vault wholesale, and never blindly convert a value whose old meaning is ambiguous. When in doubt, flag it for the person instead of guessing.

---

## Vault states

Before doing anything, determine which state the vault is actually in — don't assume:

- **`legacy`** — no current-protocol metadata or rules detected at all (no `Resources/MEMORY_PROTOCOL.md`, no `memory_status`/`source`/`confidence` fields anywhere, `VAULT-INDEX.md`/`CLAUDE.md` don't carry the current operational sections). Fully functional as-is.
- **`partial`** — some current-protocol components exist, others don't, and the ones that don't are simply behind, not contradicting (e.g. `Resources/MEMORY_PROTOCOL.md` is current but `VAULT-INDEX.md`'s Memory Metadata section is an older version, or vice versa). **Report this explicitly as "PARTIAL UPGRADE DETECTED" and name the mismatched pieces** — never assume or claim the vault is fully current when it isn't.
- **`current`** — `MEMORY_PROTOCOL.md`, the boot file, and the root index are all synchronized with this version.
- **`incompatible`** — two or more of those surfaces are each present but assert genuinely conflicting meanings for the same vocabulary term or field default (not just staleness) — e.g. `MEMORY_PROTOCOL.md` treats `memory_status: active` as obsolete v3.4 vocabulary while `VAULT-INDEX.md`'s still-present Memory Metadata text describes `active` as a distinct, currently-valid value. Exact detection criteria and required behavior are in `MEMORY_PROTOCOL.md`'s "Detecting `incompatible`" / "Required behavior when `incompatible`". Never resolve this by guessing which surface is right — flag it for the person exactly like an ambiguous `archived` value in Phase 3 below.

Check by comparing `Resources/MEMORY_PROTOCOL.md`'s `version:` frontmatter (if present) against this repo's `MEMORY_PROTOCOL.md`, and by checking whether `VAULT-INDEX.md`/`CLAUDE.md` contain the sections this version added (Trust model, `status`/`memory_status` distinction, structural-file exemptions).

---

## Phase 1 — Non-destructive: add, don't rewrite

Copy in the current `Resources/MEMORY_PROTOCOL.md` and `Resources/Jobs/Memory Health Check.md`. Append (never overwrite) the current sections from `templates/VAULT-INDEX.md` and `templates/CLAUDE.md` onto the person's existing files. Nothing existing gets deleted or rewritten in this phase.

## Phase 2 — Metadata interpretation for existing notes

Existing notes without `memory_status` remain valid exactly as they are — their absence already means "not tracked," same as it always has. Don't add the field to a note just because you're passing through; only when you're touching that note for an unrelated reason anyway.

## Phase 3 — Vocabulary migration (only relevant if the vault used a pre-v3.5 `memory_status` vocabulary)

v3.5 renamed part of the vocabulary to stop it colliding with `status`: `active` → `current`, `stale` → `uncertain`, `archived` → `deprecated`. `candidate` and `superseded` are unchanged.

- `memory_status: active` → read as `current`. Mechanical, unambiguous, safe to reinterpret without asking.
- `memory_status: stale` → read as `uncertain`. Same reasoning, safe.
- **`memory_status: archived` needs a person's review, not a blind rename.** It could have meant either "the note was archived" (now naturally `status: archived`, with `memory_status` probably still `current` if the underlying fact holds) or "the fact itself stopped being current" (now `deprecated`) — and those aren't the same thing. Flag every pre-v3.5 `memory_status: archived` note found; do not convert it automatically. Present the flagged list to the person and let them say which each one meant.

## Phase 4 — Structural-file registration

Known structural paths (`VAULT-INDEX.md`, `Active Priorities.md`, `01 - Daily Notes/Daily Note Template.md`, `Resources/MEMORY_PROTOCOL.md`, everything under `templates/`) are exempt from orphan detection by path alone — nothing to do here. Don't force a wikilink into any of them just to satisfy an old orphan check; that was never the intent.

## Phase 5 — Protocol synchronization

Update, in this order: `MEMORY_PROTOCOL.md` (canonical) → `VAULT-INDEX.md` → `CLAUDE.md` → any templates → `ai-memory-vault.md`'s embedded copies → `schema/memory-metadata.schema.yaml`'s `x-protocol-version` (see `MEMORY_PROTOCOL.md`'s "Machine-readable schema"). The canonical file changes first; every other representation follows it — never the reverse, or you end up maintaining competing versions of the same rules again.

**This order is not a formality — skipping a step in it is exactly how a real drift happened.** A 2026-08-29 release audit found `MEMORY_PROTOCOL.md` had been advanced three sub-versions (v3.6.2–v3.6.4: the HEALTH_CHECK manifest requirement, the Job dependency declaration grammar, the candidate-promotion determinism test) while `VAULT-INDEX.md` and `CLAUDE.md` — the second and third items in this exact list — were never touched. Worse, the mechanical parity checker's own marker list had gone stale along with them, so it kept reporting the vault `current` throughout. Don't skip a step in this order because a change "doesn't seem template-relevant" — that's precisely the judgment call that produced the gap.

## Phase 6 — Validation

Before calling the vault `current`:

- Run a systematic diff between canonical and embedded/template text where word-for-word parity is required — not a visual spot-check.
- Confirm `schema/memory-metadata.schema.yaml`'s `x-protocol-version` matches `MEMORY_PROTOCOL.md`'s `version:` — a schema-version mismatch means the schema is stale relative to the protocol and must not be treated as authoritative until bumped and diffed (see `MEMORY_PROTOCOL.md`'s "Machine-readable schema").
- Confirm the vault isn't in an `incompatible` state (see `MEMORY_PROTOCOL.md`) before declaring it `current` — if it is, reconcile the conflicting surfaces first; never overwrite one side by guessing which was intended.
- Run the Memory Health Check (Level 1 at minimum) and confirm it reports cleanly, or that every finding is understood and expected.
- Confirm every flagged `memory_status: archived` note from Phase 3 has actually been reviewed, not just listed.
- Re-read `VAULT-INDEX.md`'s "Vault location" and structure map to confirm nothing drifted during the append steps.

Only after this phase does the vault's state become `current`.

---

## What migration never does

- Never deletes or moves a note the person didn't ask to have touched.
- Never rewrites a note's frontmatter in bulk to backfill new fields.
- Never guesses the meaning of an ambiguous legacy value — flags it instead.
- Never claims a `partial` state is `current`.
