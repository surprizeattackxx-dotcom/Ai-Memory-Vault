---
status: active
project: meta
type: reference
name: migrate-vault-job
description: Migrate the vault forward per the staged procedure.
---
# Migrate Vault Job

## Purpose

Run the staged vault migration procedure.

## Context

Required:
- [[migration-checklist]] (claim) — the migration checklist note. If this note is missing or not `current`, the migration must **STOP and report BLOCKED** per the Job dependency policy; do not improvise the checklist.

## Steps

1. Resolve the Required tier. On BLOCK, stop this Job, name the dependency and the block reason, and do not substitute an invented checklist.
2. Otherwise follow the checklist.