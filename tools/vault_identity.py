#!/usr/bin/env python3
"""Shared, dependency-free note-identity resolver.

Extracted 2026-08-30 from tools/audit_job_dependencies.py's resolve_job_target()
(the P0-1 fix) so a second consumer (tools/memory_retrieval.py, part of the
Memory Runtime / Retrieval Abstraction layer) doesn't carry an independent
copy of a security-relevant, fail-closed resolution algorithm — a third
divergent copy of the same logic (after validate-vault.py's buggy stem-only
original) is exactly the kind of duplicated functionality this module exists
to prevent. Behavior is unchanged from the original: audit_job_dependencies.py
now imports resolve_identity() instead of defining it locally, verified
behavior-identical via its full existing fixture suite (34/34 unchanged)
before and after the move.

Path-aware, fail-closed: a path-qualified identity (`folder/Note`) must match
a real note whose directory segments end with exactly that qualifier — no
same-stem fallback across paths, no first-match-wins on filesystem order. An
unqualified identity matches by stem alone; if more than one note shares that
identity (qualified or not), resolution fails closed as ambiguous rather than
silently picking one. See resolve_identity()'s own docstring for the full
mechanism and the P0-1 incident it fixes.

Deliberately NOT wired into the shared vv.Vault.resolve_link() in
validate-vault.py, which keeps its original stem-only behavior — see
resolve_identity()'s docstring for why a dedicated resolver is used instead of
changing the shared one everywhere it's called.
"""
from __future__ import annotations


def resolve_identity(vault: "object", raw_link: str):
    """Path-aware, fail-closed resolver for a note identity (a `[[wikilink]]`,
    a bare path, or a bare stem).

    P0-1 fix (2026-08-30, originally in tools/audit_job_dependencies.py):
    validate-vault.py's Vault.resolve_link() discards any path qualifier
    outright (`if "/" in raw: raw = raw.rsplit("/", 1)[-1]`) and matches
    purely by filename stem — the FIRST same-stem note found in path-sort
    order, silently, regardless of what path the link actually named. For a
    Job's Required tier that was a real integrity hole: a link explicitly
    qualified `[[09 - Resources/required-source]]` could resolve to an
    unrelated `00 - Inbox/required-source.md` that merely happened to sort
    first and happened to be `current`, producing a false PASS on a
    dependency that was never actually inspected. The same defect applies
    anywhere identity needs to be trusted, not merely located — which is
    exactly the Memory Runtime's "exact identity" and "filename" retrieval
    methods, hence this extraction.

    Every OTHER caller of resolve_link()/note_by_stem() in this repo
    (validate-vault.py's check_lifecycle/check_wikilinks/check_structural,
    audit_job_dependencies.py's own compute_cycle_members(), and
    audit_health_coverage.py's independent stem-only lookup in gate_l3()) is
    untouched by this fix — those call sites are exercised by dozens of
    already-green fixtures, none of which use a path-qualified link anywhere
    (confirmed by repo-wide grep before the original P0-1 fix), so reworking
    the shared resolver was and remains strictly higher-risk than a dedicated
    one used only where trusted identity resolution is actually needed.

    Returns (target_note_or_None, ambiguous_candidates). `ambiguous_candidates`
    is non-empty exactly when more than one note satisfies the declared
    identity — path-qualified or not — and the caller MUST fail closed in that
    case: never guess, never prefer a `current` sibling, never use recency or
    semantic similarity as a tie-breaker.

    Path-qualified (`raw_link` contains `/`): the qualifying segments must
    match a SUFFIX of the target's actual directory path, in order — the same
    disambiguation Obsidian itself performs for a partial-path wikilink. A
    declared path that does not correspond to any real note's location is
    MISSING, even when a same-stem note exists elsewhere — the exact declared
    target is used, or nothing is.

    Unqualified (`raw_link` has no `/`): stem match anywhere in the vault. A
    *genuinely* ambiguous unqualified name (two-or-more real notes sharing
    that exact stem) fails closed as ambiguous rather than silently picking
    whichever sorts first."""
    raw = raw_link.strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2]
    raw = raw.split("|")[0].strip()
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    if not raw:
        return None, []
    raw = raw.replace("\\", "/")
    segments = [seg for seg in raw.split("/") if seg]
    if not segments:
        return None, []
    want_stem = segments[-1]
    if want_stem.lower().endswith(".md"):
        want_stem = want_stem[:-3]
    want_dir = [s.lower() for s in segments[:-1]]

    matches = []
    for note in vault.notes:
        if note["stem"].lower() != want_stem.lower():
            continue
        if want_dir:
            note_dir = [p.lower() for p in note["dir_parts"]]
            if len(want_dir) > len(note_dir) or note_dir[len(note_dir) - len(want_dir):] != want_dir:
                continue
        matches.append(note)
    if len(matches) == 1:
        return matches[0], []
    if len(matches) == 0:
        return None, []
    return None, matches


def stem_matches(vault: "object", raw_link: str) -> list:
    """Every note sharing the given bare stem, anywhere in the vault, path
    ignored even if one was given — the deliberately looser sibling of
    resolve_identity(): a discovery lookup ("show me every note called
    this"), not an identity resolution ("give me the one note this names").
    Never fails closed — multiple results are its ordinary, expected output,
    not an error condition. Used by the Memory Runtime's "filename" retrieval
    method; resolve_identity() (path-aware, fail-closed) is used by its
    "exact" method. Deterministically ordered by vault-relative path."""
    raw = raw_link.strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2]
    raw = raw.split("|")[0].strip()
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    raw = raw.replace("\\", "/")
    want_stem = raw.rsplit("/", 1)[-1] if raw else ""
    if want_stem.lower().endswith(".md"):
        want_stem = want_stem[:-3]
    if not want_stem:
        return []
    matches = [n for n in vault.notes if n["stem"].lower() == want_stem.lower()]
    return sorted(matches, key=lambda n: n["rel"])
