---
status: active
project: meta
type: index
---
# VAULT INDEX

Read this file at the start of every conversation to understand who I am and how this vault works.

## Who I Am

I'm Rowan, 31, born March 14, 1994. I live in Fernbrook, Michigan. My wife is Wren.

## Key People

- **[[Wren]]** — my wife

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

## Note format

Simple, legible, readable. No random emojis. Checkboxes are real Markdown checkboxes (`- [ ]` / `- [x]`), never emoji stand-ins.

Every note has YAML frontmatter:

```yaml
---
status: active
project: [project-slug]
type: plan
---
```

`status` is `active` | `completed` | `parked` | `idea` | `archived`. `project` identifies what the note serves (its folder by default). `type` is `index` | `reference` | `guide` | `plan` | `log`.

Add wikilinks whenever a note references another note, a person, a product, or a platform.

## How My Memory Works (for the AI)

The vault is your memory: external, effectively unlimited. Hold only what the current task needs and reach for the rest through the indexes. Knowing a note exists is as good as holding it.

## Active work

The single queue of open work lives in [[Active Priorities]].