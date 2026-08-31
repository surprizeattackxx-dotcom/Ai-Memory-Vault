#!/usr/bin/env python3
"""Conflict detection — a standalone, additive, read-only layer on top of
tools/memory_runtime.py's MemoryRuntime.

GOAL: given a target identity or a free-text query, surface pairs of notes
that might be in tension, WITHOUT ever deciding which one is true. This is
deliberately narrower than "detect contradictions" — MEMORY_PROTOCOL.md's own
Contradiction classification section requires reading and understanding note
CONTENT to tell "genuinely incompatible" apart from "compatible", "temporal
change", "contextual information", etc., and that judgment is explicitly
reserved for an agent running RESOLVE_CONFLICT, never a mechanical scanner
(see tools/audit_job_dependencies.py's own docstring: "Explicit non-goal, not
a bug" — the same principle this module extends, not revisits).

What this module CAN do deterministically, from metadata and structural
relationships alone:
    - read two notes' memory_status and compare them
    - check whether a supersedes/superseded_by link connects them, and
      whether it's clean, unreciprocated, or contradictory
    - check whether they participate in a supersession cycle together
      (MEMORY_PROTOCOL.md's Metadata section calls a cycle, or a same-
      direction pair with neither marked superseded, "genuinely incompatible
      claims" outright — that is protocol text, not an invented heuristic)
    - check whether they share an identity/stem with no declared relationship
      at all (the "same-stem decoy" pattern tools/memory_runtime.py's own
      test suite already exercises)
    - check whether either side's memory_status falls under disputed
      protocol vocabulary, or whether either side's frontmatter doesn't parse

What it can NEVER do: read two notes' prose and decide they assert
incompatible facts. That row of MEMORY_PROTOCOL.md's Job dependency policy
table stays REQUIRES_AI, exactly as documented in this repo already.

Five-category output, exactly this vocabulary, never blended:
    related                — some mechanical connection exists, no lifecycle
                              tension (a wikilink, clean history, or nothing
                              more than a shared search-keyword)
    compatible              — a KNOWN-safe relationship: a cleanly
                              reciprocated supersession pair, correctly
                              marked on both sides
    potentially_conflicting — mechanical evidence of tension (identity
                              collision with divergent lifecycle, an
                              unreciprocated supersession declaration) that a
                              PERSON should look at; never auto-resolved
    confirmed_conflict      — the one case MEMORY_PROTOCOL.md itself calls
                              genuinely incompatible outright: a supersession
                              cycle, or a same-direction pair with neither
                              side marked superseded
    insufficient_evidence   — disputed vocabulary, malformed frontmatter, or
                              an ambiguous identity that can't even be pinned
                              to a specific pair; ALWAYS the fail-closed
                              landing zone when evidence quality is compromised,
                              takes priority over every other category

Confidence reuses MEMORY_PROTOCOL.md's own `confidence` enum
(high|medium|low|unverified) rather than inventing a new scale.

This module NEVER:
    - writes to any file
    - changes a note's memory_status, supersedes/superseded_by, or any other
      field
    - deletes or supersedes anything
    - promotes a candidate
    - picks "the newest" (or any other heuristic) as truth
It only ever calls MemoryRuntime.resolve()/inspect() for trust judgments (the
one real trust computation in this codebase — see memory_runtime.py's own
docstring on why a second copy of that logic must never exist) and reuses
tools/vault_identity.py's resolve_identity()/stem_matches() and
tools/audit_job_dependencies.py's compute_cycle_members() rather than
re-deriving any of it. Standard library only. No network access. No file
opened for writing anywhere in this module.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import memory_runtime as _rt_mod  # noqa: E402  (provides .vv/.vid/.ajd/.mr, already loaded once)
from memory_runtime import MemoryRuntime  # noqa: E402

vid = _rt_mod.vid
ajd = _rt_mod.ajd
mret = _rt_mod.mr

CONFLICT_ENGINE_VERSION = "1.0.0"

CATEGORIES = ("confirmed_conflict", "potentially_conflicting", "insufficient_evidence", "compatible", "related")
_CATEGORY_ORDER = {c: i for i, c in enumerate(CATEGORIES)}
CONFIDENCE_VALUES = ("high", "medium", "low", "unverified")

NOT_EVALUATED = (
    "semantic contradiction between two notes' prose content (whether they assert "
    "incompatible facts) — requires AI judgment per MEMORY_PROTOCOL.md's Contradiction "
    "classification; this engine compares only metadata and structural relationships, "
    "never note bodies for meaning",
)


@dataclass
class ConflictMemory:
    """One side of a conflict pair. Same fields memory_runtime.py's
    ValidatedContext already exposes — `accepted` is still the only field
    that means "trustworthy right now"; everything else here is identity or
    provenance, never a second trust signal."""
    note_path: str
    stem: str
    memory_status: str | None
    status_track: str
    accepted: bool


@dataclass
class Conflict:
    """An auditable, read-only report of a relationship between two notes.
    Never a verdict on which one is true — `recommended_action` is always
    either "no action needed" or a request for a person/agent to look,
    never a resolution this module performed itself."""
    memory_a: ConflictMemory
    memory_b: ConflictMemory
    category: str                 # one of CATEGORIES, exactly
    confidence: str                # one of CONFIDENCE_VALUES, exactly
    detection_method: str
    evidence: str
    recommended_action: str


@dataclass
class ConflictReport:
    """Top-level result of detect_conflicts(). `status` describes whether the
    target/query itself could be pinned to specific note(s) at all —
    independent of what the conflicts list then says about those notes."""
    query: str
    status: str                    # "resolved" | "ambiguous" | "missing" | "empty"
    conflicts: tuple = field(default_factory=tuple)
    not_evaluated: tuple = NOT_EVALUATED
    candidates: tuple = ()          # populated only when status == "ambiguous"


# --------------------------------------------------------------- public API
def detect_conflicts(runtime: "MemoryRuntime", target_or_query: str, methods=None, limit=None) -> ConflictReport:
    """Given a target identity or a free-text query, return a ConflictReport.
    Never mutates `runtime.vault`. Never guesses: an ambiguous identity, a
    missing target, or an empty query all return explicit, distinct statuses
    rather than silently falling back to "everything" or "nothing"."""
    if target_or_query is None or not str(target_or_query).strip():
        return ConflictReport(query=target_or_query, status="empty", conflicts=())

    vault = runtime.vault
    target, ambiguous = vid.resolve_identity(vault, target_or_query)

    if ambiguous:
        cycle_members = ajd.compute_cycle_members(vault)
        conflicts = []
        for i, a in enumerate(ambiguous):
            for b in ambiguous[i + 1:]:
                conflicts.append(_ambiguous_pair(runtime, a, b))
        return ConflictReport(
            query=target_or_query, status="ambiguous",
            conflicts=tuple(_sorted(conflicts)),
            candidates=tuple(sorted(n["rel"] for n in ambiguous)),
        )

    if target is not None:
        anchors = (target,)
    else:
        candidates = mret.search(vault, target_or_query, methods=methods, limit=limit)
        if not candidates:
            return ConflictReport(query=target_or_query, status="missing", conflicts=())
        by_rel = {n["rel"]: n for n in vault.notes}
        anchors = tuple(by_rel[c.note_path] for c in candidates if c.note_path in by_rel)
        if not anchors:
            return ConflictReport(query=target_or_query, status="missing", conflicts=())

    cycle_members = ajd.compute_cycle_members(vault)
    pairs = _comparison_universe(vault, anchors)
    conflicts = [_classify_pair(runtime, vault, a, b, kind, cycle_members) for (a, b, kind) in pairs]
    return ConflictReport(query=target_or_query, status="resolved", conflicts=tuple(_sorted(conflicts)))


def _sorted(conflicts):
    return sorted(conflicts, key=lambda c: (_CATEGORY_ORDER[c.category], c.memory_a.note_path, c.memory_b.note_path))


# ------------------------------------------------------------- ref building
def _ref(runtime: "MemoryRuntime", note: dict) -> ConflictMemory:
    """The ONLY place this module computes trust for a note — by calling the
    runtime's own inspect(), never a second, independent computation. `note`
    always comes from vault.notes, so its "rel" always resolves to exactly
    itself (never ambiguous, never missing) — see vault_identity.py's
    contract for why a canonical rel path is always precise."""
    result = runtime.inspect(note["rel"])
    ctx = result.context
    return ConflictMemory(
        note_path=ctx.note_path, stem=ctx.stem,
        memory_status=ctx.memory_status, status_track=ctx.status_track, accepted=ctx.accepted,
    )


def _mk(ref_a, ref_b, category, confidence, detection_method, evidence, recommended_action) -> Conflict:
    lo, hi = sorted([ref_a, ref_b], key=lambda r: r.note_path)
    return Conflict(memory_a=lo, memory_b=hi, category=category, confidence=confidence,
                     detection_method=detection_method, evidence=evidence, recommended_action=recommended_action)


def _ambiguous_pair(runtime, a, b) -> Conflict:
    ref_a, ref_b = _ref(runtime, a), _ref(runtime, b)
    return _mk(
        ref_a, ref_b, "insufficient_evidence", "unverified", "ambiguous-identity",
        "identity matched more than one note with no path qualifier to disambiguate; "
        "cannot determine which note the query meant",
        "qualify the identity with a path (e.g. 'folder/%s') or resolve which note was "
        "meant before drawing any conclusion from either" % a["stem"],
    )


# ------------------------------------------------------- comparison universe
def _comparison_universe(vault, anchors) -> list:
    """Every (note_a, note_b, kind) pair worth classifying, for the given
    anchor set. `kind` is one of "stem" (identity collision), "supersession"
    (a declared supersedes/superseded_by edge), or "text" (anchors that
    matched the same free-text query with no structural relationship found —
    capped at `related` by _classify_pair, never promoted further). Dedup by
    unordered pair identity: a pair discovered via a stronger relationship
    (stem/supersession) is never also entered as a weaker "text" pair."""
    pairs = []
    seen_keys = set()

    def add(a, b, kind):
        if a["rel"] == b["rel"]:
            return
        key = frozenset([a["rel"], b["rel"]])
        if key in seen_keys:
            return
        seen_keys.add(key)
        pairs.append((a, b, kind))

    for anchor in anchors:
        for sib in vid.stem_matches(vault, anchor["stem"]):
            if sib["rel"] != anchor["rel"]:
                add(anchor, sib, "stem")

        for fld in ("supersedes", "superseded_by"):
            raw = anchor["meta"].get(fld)
            if isinstance(raw, str):
                tgt, amb = vid.resolve_identity(vault, raw)
                if tgt is not None and not amb:
                    add(anchor, tgt, "supersession")

        for note in vault.notes:
            if note["rel"] == anchor["rel"]:
                continue
            for fld in ("supersedes", "superseded_by"):
                raw = note["meta"].get(fld)
                if isinstance(raw, str):
                    tgt, amb = vid.resolve_identity(vault, raw)
                    if tgt is not None and not amb and tgt["rel"] == anchor["rel"]:
                        add(anchor, note, "supersession")

    if len(anchors) > 1:
        for i, a in enumerate(anchors):
            for b in anchors[i + 1:]:
                add(a, b, "text")

    return pairs


# -------------------------------------------------------- pair relationship
def _points_at(vault, src, dst, fld) -> bool:
    raw = src["meta"].get(fld)
    if not isinstance(raw, str):
        return False
    tgt, ambiguous = vid.resolve_identity(vault, raw)
    return tgt is not None and not ambiguous and tgt["rel"] == dst["rel"]


def _pair_relationship(vault, a, b) -> str:
    """"none" | "clean" | "unreciprocated" | "contradictory". Mirrors
    validate-vault.py's own _check_pairs()/_check_cycles() reasoning, scoped
    to one specific pair rather than a whole-vault scan — see
    MEMORY_PROTOCOL.md's Metadata > Circular supersession for why a
    same-direction pair with neither side marked superseded is "contradictory"
    even short of a formal graph cycle."""
    a_super_b = _points_at(vault, a, b, "supersedes")
    a_by_b = _points_at(vault, a, b, "superseded_by")
    b_super_a = _points_at(vault, b, a, "supersedes")
    b_by_a = _points_at(vault, b, a, "superseded_by")

    if not any((a_super_b, a_by_b, b_super_a, b_by_a)):
        return "none"
    if a_super_b and b_by_a and not a_by_b and not b_super_a:
        return "clean"
    if b_super_a and a_by_b and not b_by_a and not a_super_b:
        return "clean"
    if a_super_b and b_super_a:
        return "contradictory"
    if a_by_b and b_by_a:
        return "contradictory"
    return "unreciprocated"


def _dominated_note(vault, a, b):
    """For a "clean" pair, which note is the one that got superseded."""
    if _points_at(vault, a, b, "supersedes"):
        return b
    if _points_at(vault, b, a, "supersedes"):
        return a
    return None


# ----------------------------------------------------------- classification
def _classify_pair(runtime, vault, a, b, kind, cycle_members) -> Conflict:
    ref_a, ref_b = _ref(runtime, a), _ref(runtime, b)

    # Fail-closed checks first, unconditionally: compromised evidence always
    # lands in insufficient_evidence regardless of what else is detectable.
    if ref_a.status_track == "malformed-frontmatter" or ref_b.status_track == "malformed-frontmatter":
        return _mk(ref_a, ref_b, "insufficient_evidence", "unverified", "malformed-frontmatter",
                    "at least one note's frontmatter could not be parsed",
                    "fix the malformed frontmatter before any lifecycle judgment is possible")

    if ref_a.status_track == "disputed" or ref_b.status_track == "disputed":
        return _mk(ref_a, ref_b, "insufficient_evidence", "unverified", "disputed-vocabulary",
                    "memory_status meaning is disputed at the protocol-surface level for at least "
                    "one note (MEMORY_PROTOCOL.md > Backward compatibility & migration); not interpreted",
                    "reconcile the disputed protocol surfaces before judging this pair")

    rel = _pair_relationship(vault, a, b)
    a_in_cycle = a["stem"].lower() in cycle_members
    b_in_cycle = b["stem"].lower() in cycle_members

    if rel != "none" and a_in_cycle and b_in_cycle:
        return _mk(ref_a, ref_b, "confirmed_conflict", "high", "supersession-cycle",
                    "both notes participate in a supersedes/superseded_by cycle involving each "
                    "other — malformed, not a valid supersession (MEMORY_PROTOCOL.md > Metadata > "
                    "Circular supersession)",
                    "STOP: do not guess which side is current. Treat both as genuinely "
                    "incompatible and ask a person to reconcile.")

    if rel == "contradictory":
        return _mk(ref_a, ref_b, "confirmed_conflict", "high", "circular-declaration",
                    "both notes declare the same-direction supersession field pointing at each "
                    "other with neither marked memory_status: superseded — MEMORY_PROTOCOL.md's "
                    "Metadata section calls this genuinely incompatible outright, not a resolvable pair",
                    "STOP: do not guess which side is current. Ask a person to reconcile "
                    "(MEMORY_PROTOCOL.md > Metadata > Circular supersession).")

    if rel == "clean":
        dominated = _dominated_note(vault, a, b)
        if dominated is not None and dominated["meta"].get("memory_status") != "superseded":
            return _mk(ref_a, ref_b, "potentially_conflicting", "medium",
                        "supersession-link-without-superseded-status",
                        "a supersedes/superseded_by link connects these notes but the replaced "
                        "note is not marked memory_status: superseded",
                        "set memory_status: superseded on the replaced note, or confirm the "
                        "link is a mistake")
        return _mk(ref_a, ref_b, "compatible", "high", "clean-reciprocated-supersession",
                    "properly reciprocated supersession pair — normal preserved history, not a conflict",
                    "no action needed")

    if rel == "unreciprocated":
        return _mk(ref_a, ref_b, "potentially_conflicting", "medium", "unreciprocated-supersession",
                    "one note declares a supersedes/superseded_by relationship the other side "
                    "does not confirm (pair fields meant to be set together)",
                    "add the missing back-reference, or confirm the declared relationship isn't intended")

    # rel == "none": no declared relationship between these two notes at all.
    if kind == "stem":
        tracks = {ref_a.status_track, ref_b.status_track}
        if tracks == {"current"}:
            return _mk(ref_a, ref_b, "potentially_conflicting", "medium", "identity-collision-both-current",
                        "two notes share the same identity/stem and are both memory_status: "
                        "current, with no declared supersession between them",
                        "ask a person which is correct, or link one as supersedes/superseded_by the other")
        if "current" in tracks and "candidate" in tracks:
            return _mk(ref_a, ref_b, "potentially_conflicting", "medium",
                        "identity-collision-current-vs-candidate",
                        "a candidate shares identity/stem with a current fact and no "
                        "relationship is declared between them",
                        "confirm or discard the candidate per MEMORY_PROTOCOL.md > Candidate memory")
        if tracks == {"candidate"}:
            return _mk(ref_a, ref_b, "potentially_conflicting", "medium",
                        "identity-collision-two-candidates",
                        "two independent candidates share the same identity/stem with no "
                        "declared relationship between them",
                        "review both against MEMORY_PROTOCOL.md's independent-observation test "
                        "before either is promoted")
        if "current" in tracks and "superseded" in tracks:
            return _mk(ref_a, ref_b, "potentially_conflicting", "medium",
                        "identity-collision-current-vs-superseded-no-link",
                        "one note is current and another sharing its identity/stem is "
                        "superseded, but no supersedes/superseded_by link connects them — "
                        "unclear whether the superseded note replaced this one or something else entirely",
                        "add the missing link if related, or clarify these are unrelated "
                        "notes that happen to share a name")
        if ref_a.status_track != ref_b.status_track:
            return _mk(ref_a, ref_b, "potentially_conflicting", "low",
                        "identity-collision-divergent-lifecycle",
                        "two notes share the same identity/stem with different lifecycle "
                        "states and no declared relationship",
                        "confirm these are meant to be the same fact, and link or disambiguate them")
        return _mk(ref_a, ref_b, "related", "low", "identity-collision-same-lifecycle",
                    "two notes share the same identity/stem with the same lifecycle state and "
                    "no declared relationship — could be an intentional same-name note in a "
                    "different context, or an unnoticed duplicate",
                    "no action required unless these are meant to be the same fact")

    # kind == "text": anchors matched the same free-text query, nothing structural connects them
    return _mk(ref_a, ref_b, "related", "low", "shared-keyword",
                "matched the same query via keyword search only — no structural relationship detected",
                "no action needed — co-occurrence only")


# --------------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Detect potential memory conflicts (read-only).")
    ap.add_argument("vault")
    ap.add_argument("query")
    ap.add_argument("--boot")
    ap.add_argument("--repo")
    ap.add_argument("--methods")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rt = MemoryRuntime(args.vault, boot=args.boot, repo_root=args.repo)
    methods = tuple(args.methods.split(",")) if args.methods else None
    report = detect_conflicts(rt, args.query, methods=methods, limit=args.limit)

    def as_dict(o):
        if hasattr(o, "__dict__"):
            return {k: (list(v) if isinstance(v, tuple) else (as_dict(v) if hasattr(v, "__dict__") else v))
                    for k, v in vars(o).items()}
        return o

    payload = as_dict(report)
    payload["conflicts"] = [as_dict(c) for c in report.conflicts]

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("Query: %r  Status: %s  Conflicts: %d" % (args.query, report.status, len(report.conflicts)))
        for c in report.conflicts:
            print("  [%s/%s] %s <-> %s" % (c.category, c.confidence, c.memory_a.note_path, c.memory_b.note_path))
            print("      via %s: %s" % (c.detection_method, c.evidence))
            print("      -> %s" % c.recommended_action)
        if report.status == "ambiguous":
            print("  ambiguous candidates: %s" % ", ".join(report.candidates))
        sys.exit(0)
