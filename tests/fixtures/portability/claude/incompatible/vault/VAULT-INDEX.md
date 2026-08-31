---
status: active
project: meta
type: index
---
# VAULT INDEX

Read this file at the start of every conversation to understand who I am and how this vault works.

## Who I Am

I'm Rowan, 31, born March 14, 1994. I live in Fernbrook, Ohio. My spouse is Wren.

## Key People

- **[[Wren]]** — my spouse

## Projects

- [[Projects]] — the projects folder ("02 - Projects")
- [[Personal]] — personal notes ("07 - Personal")
- [[Resources]] — cross-project reference ("09 - Resources")

## Vault Structure

```
00 - Inbox      <- Capture everything, sort later
01 - Daily Notes <- dated logs, one file per day
02 - Projects    <- project notes, Jobs live under 02 - Projects/Jobs/
07 - Personal    <- life outside work
09 - Resources   <- cross-project reference material
```

## Rules the vault runs on

- The lifecycle field `memory_status` uses the values `active`, `stale`, and `archived`:
  - `memory_status: active` — the fact is **confirmed and current**. This is a directly valid, distinct value.
  - `memory_status: stale` — was current, hasn't been reconfirmed recently.
  - `memory_status: archived` — the note or fact is archived.
- `memory_status: current`, `candidate`, `uncertain`, `superseded`, and `deprecated` are **not** valid values in this vault.
- **Boot budget:** never ingest the whole vault at session start.
- HEALTH_CHECK runs only on request, never at boot.

## How My Memory Works (for the AI)

The vault is your memory: external, effectively unlimited. Hold only what the current task needs and reach for the rest through the indexes. Knowing a note exists is as good as holding it.

## Active work

The single queue of open work lives in [[Active Priorities]].