---
status: active
project: meta
type: reference
name: desktop-theme-job
description: Pick and apply the current desktop theme from validated vault memory.
---
# Desktop Theme Job

## Purpose

Decide and apply the user's current desktop theme, sourced from validated vault memory rather than assumption.

## Context

Required:
- [[theme-canonical]] (claim) — the current theme preference, `memory_status: current` required.

Preferred:
- [[theme-old]] (claim) — the replaced preference, useful for explaining history.

Optional:
- [[theme-candidate]] — a possibly-replacing palette under consideration.

## Steps

1. Resolve Required tier per the Job dependency policy in `09 - Resources/MEMORY_PROTOCOL.md`. If any Required dependency does not resolve to clear `current` with no dispute, **STOP this Job** and say which dependency blocked it.
2. With the Required tier satisfied, apply the recorded preference. If the user is weighing a replacement (see Optional tier), disclose it and ask before changing anything.
3. Checkpoint: note any outcome back into the vault and today's daily note.