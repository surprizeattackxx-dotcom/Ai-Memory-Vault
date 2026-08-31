#!/usr/bin/env python3
"""Provenance / evidence-trace layer — a standalone, additive, read-only
module built on top of tools/memory_runtime.py, tools/memory_conflict.py, and
tools/audit_job_dependencies.py.

GOAL: given a resolvable identity, answer "where did this come from, what
does it depend on, what depends on it, what superseded it, what does it
supersede, which Jobs reference it, what validation evidence already exists
for it, and are there cycles or broken/ambiguous edges" — as an EXPLICIT,
auditable graph of relationships the vault itself records. Nothing here reads
note bodies for meaning, ranks by keyword score, or infers a relationship
that isn't already a wikilink, a supersedes/superseded_by field, or a Job's
declared dependency.

PROVENANCE IS NOT TRUTH. An edge means "the Vault explicitly records this
relationship" — never "the referenced memory is correct." A current note
pointing at a candidate does not make the candidate trustworthy; a superseded
note appearing in the graph is not thereby invalid evidence. This module
never promotes, demotes, supersedes, validates, or invalidates anything based
on graph position — it only ever asks tools/memory_runtime.py's inspect()
(via tools/memory_conflict.py's `_ref` helper, reused verbatim rather than
redefined) for the one real trust computation this codebase has, and reports
that alongside the edge, never instead of computing it independently.

Reuse map (nothing here re-implements what already exists):
    - tools/memory_runtime.py's inspect()      -> the one trust computation
    - tools/memory_conflict.py's ConflictMemory/_ref -> the one note-ref shape
    - tools/vault_identity.py's resolve_identity() -> the one identity
      resolver (path-aware, fail-closed; never a same-stem guess, never a
      fallback from a qualified identity to a bare stem)
    - tools/memory_retrieval.py's search(..., methods=("wikilink",)) -> the
      one inbound-wikilink scan (reused directly for incoming wikilink edges)
    - tools/validate-vault.py's WIKILINK_RE / strip_fenced_and_code -> the one
      wikilink-extraction primitive (used for outbound wikilink parsing,
      mirroring validate-vault.py's own check_wikilinks())
    - tools/audit_job_dependencies.py's compute_cycle_members() -> the one
      authoritative cycle-MEMBERSHIP computation. It deliberately returns
      membership only, never a path or edge list (see its own docstring) —
      this module rebuilds the identical dominant->dominated graph, read-only,
      purely to reconstruct one concrete deterministic path for reporting.
      Membership itself is never re-derived; only the path is.
    - tools/audit_job_dependencies.py's parse_tiers()/resolve_dependency() ->
      the one Job dependency parser/resolver. This module never re-parses a
      Job's tiers itself or re-implements the resolution table.

Nothing here:
    - invents a relationship from keyword similarity, retrieval score,
      semantic similarity, or filename similarity alone
    - computes a numeric confidence/trust score
    - writes to any file
    - silently converts a path-qualified identity into a bare-stem guess
    - silently flattens a cycle or hides a broken/ambiguous edge
Standard library only. No network access.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import memory_runtime as _rt_mod  # noqa: E402
from memory_runtime import MemoryRuntime  # noqa: E402
import memory_conflict as mc  # noqa: E402  (reused for ConflictMemory/_ref/_pair_relationship)

vv = _rt_mod.vv
vid = _rt_mod.vid
ajd = _rt_mod.ajd
mret = _rt_mod.mr

PROVENANCE_ENGINE_VERSION = "1.0.0"

EDGE_TYPES = ("wikilink", "supersedes", "superseded_by", "job_dependency")
SUPERSESSION_FIELDS = ("supersedes", "superseded_by")

# The one note-reference shape and the one trust computation, reused verbatim
# from memory_conflict.py rather than redefined — see that module's own
# comment on why a second copy of MemoryRuntime._validate()'s reasoning must
# never exist.
MemoryRef = mc.ConflictMemory
_ref = mc._ref


@dataclass
class Edge:
    """A single explicit, directed relationship the Vault records between two
    RESOLVED notes. Both `source` and `target` are always real notes with
    full lifecycle state attached — this is what "provenance is not truth"
    looks like in code: the edge exists whether source/target are current,
    candidate, superseded, or disputed, and nothing here changes that."""
    source: MemoryRef
    target: MemoryRef
    edge_type: str                       # one of EDGE_TYPES
    detection_method: str
    reciprocal: bool | None              # None where reciprocity doesn't apply (wikilink, job_dependency)
    evidence: str
    details: dict = field(default_factory=dict)  # structured extras (Job tier/qualifier/resolution, etc.)


@dataclass
class BrokenEdge:
    """A declared relationship that could NOT be resolved to one specific
    note — missing target, or an ambiguous same-stem identity with no path
    qualifier to disambiguate. Kept structurally separate from Edge (whose
    target is always a real, resolved note) rather than jammed into it with
    an optional/None target — see module docstring: never silently flatten a
    broken or ambiguous edge into something that looks resolved.

    `details` was added (v3.7.4, additive/backward-compatible, default {})
    so a job_dependency BrokenEdge can carry its tier — a consumer deciding
    severity needs to tell a broken REQUIRED dependency from a broken
    Preferred/Optional one, which the evidence string alone doesn't
    structurally guarantee. Empty for wikilink/supersession broken entries."""
    source: MemoryRef
    raw_target: str
    edge_type: str                       # one of EDGE_TYPES
    reason: str                          # "missing" | "ambiguous"
    candidates: tuple = ()                # populated when reason == "ambiguous"
    evidence: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class CycleReport:
    """One concrete, deterministic supersedes/superseded_by cycle the anchor
    participates in. `members` and `traversal` describe one real path found
    by walking the Vault's own declared edges — membership itself always
    comes from audit_job_dependencies.compute_cycle_members(), the single
    authoritative source; this is never a second, independent judgment about
    whether a cycle exists, only a reconstruction of what it looks like."""
    members: tuple                       # canonical paths, in traversal order, anchor first
    edge_types: tuple                    # which fields (supersedes/superseded_by) appear in the path
    status: str                          # always "cycle-detected" when a CycleReport is returned
    traversal: tuple                     # ((source_path, target_path, field), ...) in discovery order


@dataclass
class ValidationEvidence:
    """One piece of already-computed validation evidence about the anchor
    itself — never a new trust score, never a re-derivation. `detail` is
    lifted directly from tools/memory_runtime.py's own reasoning (ctx.reason)
    or tools/audit_job_dependencies.py's own Job verdict, whichever applies."""
    subject: MemoryRef
    evidence_type: str                   # "lifecycle" | "job-verdict"
    detail: str
    details: dict = field(default_factory=dict)


@dataclass
class ProvenanceReport:
    identity: str
    status: str                          # "resolved" | "ambiguous" | "missing" | "empty"
    anchor: MemoryRef | None = None
    outgoing: tuple = ()
    incoming: tuple = ()
    broken: tuple = ()                    # BrokenEdge entries, filtered to the requested direction(s)
    cycles: tuple = ()                    # CycleReport entries the anchor participates in
    validation: tuple = ()                # ValidationEvidence entries about the anchor
    candidates: tuple = ()                # populated only when status == "ambiguous"


# --------------------------------------------------------------- public API
def trace(runtime: "MemoryRuntime", target: str) -> ProvenanceReport:
    """Full picture: outgoing edges, incoming edges, cycles, and validation
    evidence for one identity."""
    return _build_report(runtime, target, direction="both")


def outgoing(runtime: "MemoryRuntime", target: str) -> ProvenanceReport:
    """What this identity explicitly depends on / points at — outgoing edges
    only. Cycle and validation context (anchor-level facts, not directional)
    are still included."""
    return _build_report(runtime, target, direction="outgoing")


def incoming(runtime: "MemoryRuntime", target: str) -> ProvenanceReport:
    """What explicitly depends on / points at this identity — incoming edges
    only. Cycle and validation context are still included."""
    return _build_report(runtime, target, direction="incoming")


# ------------------------------------------------------------------- build
def _build_report(runtime: "MemoryRuntime", target: str, direction: str) -> ProvenanceReport:
    if target is None or not str(target).strip():
        return ProvenanceReport(identity=target, status="empty")

    vault = runtime.vault
    resolved, ambiguous = vid.resolve_identity(vault, target)

    if ambiguous:
        return ProvenanceReport(identity=target, status="ambiguous",
                                 candidates=tuple(sorted(n["rel"] for n in ambiguous)))
    if resolved is None:
        return ProvenanceReport(identity=target, status="missing")

    anchor_note = resolved
    anchor_ref = _ref(runtime, anchor_note)
    cycle_members = ajd.compute_cycle_members(vault)
    today = date.today()

    out_edges, out_broken = [], []
    in_edges, in_broken = [], []

    if direction in ("both", "outgoing"):
        w_edges, w_broken = _outgoing_wikilinks(runtime, vault, anchor_note)
        s_edges, s_broken = _outgoing_supersession(runtime, vault, anchor_note)
        j_edges, j_broken = _job_edges_for(runtime, vault, cycle_members, today, anchor_note, as_source=True)
        out_edges = w_edges + s_edges + j_edges
        out_broken = w_broken + s_broken + j_broken

    if direction in ("both", "incoming"):
        w_edges = _incoming_wikilinks(runtime, vault, anchor_note)
        s_edges, s_broken = _incoming_supersession(runtime, vault, anchor_note)
        j_edges, j_broken = _job_edges_for(runtime, vault, cycle_members, today, anchor_note, as_source=False)
        in_edges = w_edges + s_edges + j_edges
        in_broken = s_broken + j_broken

    cycles = _cycles_for(vault, anchor_note, cycle_members)
    validation = _validation_for(runtime, vault, anchor_note, cycle_members, today)

    return ProvenanceReport(
        identity=target, status="resolved", anchor=anchor_ref,
        outgoing=_dedup_sorted(out_edges), incoming=_dedup_sorted(in_edges),
        broken=tuple(_dedup_broken(out_broken + in_broken)),
        cycles=tuple(cycles), validation=tuple(validation),
    )


def _dedup_sorted(edges) -> tuple:
    seen = set()
    out = []
    for e in edges:
        key = (e.source.note_path, e.target.note_path, e.edge_type, e.detection_method)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    out.sort(key=lambda e: (e.edge_type, e.source.note_path, e.target.note_path, e.detection_method))
    return tuple(out)


def _dedup_broken(entries) -> list:
    seen = set()
    out = []
    for b in entries:
        key = (b.source.note_path, b.raw_target, b.edge_type, b.reason)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    out.sort(key=lambda b: (b.edge_type, b.source.note_path, b.raw_target))
    return out


# ------------------------------------------------------------- wikilinks
def _outbound_links(note) -> list:
    """Every raw [[...]] target string in a note's own body, fenced code
    stripped — reuses validate-vault.py's own primitives (WIKILINK_RE /
    strip_fenced_and_code), the same ones its check_wikilinks() uses,
    never a second parser."""
    body = vv.strip_fenced_and_code(note["body"])
    return [m.group(1).strip() for m in vv.WIKILINK_RE.finditer(body) if m.group(1).strip()
            and not m.group(1).strip().startswith("#")]


def _outgoing_wikilinks(runtime, vault, anchor_note):
    edges, broken = [], []
    anchor_ref = _ref(runtime, anchor_note)
    for raw in _outbound_links(anchor_note):
        tgt, ambiguous = vid.resolve_identity(vault, raw)
        if ambiguous:
            broken.append(BrokenEdge(source=anchor_ref, raw_target=raw, edge_type="wikilink", reason="ambiguous",
                                      candidates=tuple(sorted(n["rel"] for n in ambiguous)),
                                      evidence="[[%s]] matches more than one note" % raw))
            continue
        if tgt is None:
            broken.append(BrokenEdge(source=anchor_ref, raw_target=raw, edge_type="wikilink", reason="missing",
                                      evidence="[[%s]] does not resolve to any note" % raw))
            continue
        if tgt["rel"] == anchor_note["rel"]:
            continue  # self-link is not a provenance edge
        edges.append(Edge(source=anchor_ref, target=_ref(runtime, tgt), edge_type="wikilink",
                           detection_method="body-wikilink", reciprocal=None,
                           evidence="note body contains [[%s]]" % raw))
    return edges, broken


def _incoming_wikilinks(runtime, vault, anchor_note):
    """Reuses tools/memory_retrieval.py's own wikilink search method directly
    — the one inbound-wikilink scan this codebase already has — rather than
    re-scanning every note's body a second, independent way."""
    edges = []
    candidates = mret.search(vault, anchor_note["rel"], methods=("wikilink",))
    anchor_ref = _ref(runtime, anchor_note)
    for c in candidates:
        if c.note_path == anchor_note["rel"]:
            continue
        source_note = next((n for n in vault.notes if n["rel"] == c.note_path), None)
        if source_note is None:
            continue  # defensive only; every Candidate.note_path came from vault.notes
        edges.append(Edge(source=_ref(runtime, source_note), target=anchor_ref, edge_type="wikilink",
                           detection_method="body-wikilink", reciprocal=None,
                           evidence="%s links to [[%s]]" % (c.note_path, anchor_note["stem"])))
    return edges


# -------------------------------------------------------------- supersession
def _outgoing_supersession(runtime, vault, anchor_note):
    edges, broken = [], []
    anchor_ref = _ref(runtime, anchor_note)
    for fld in SUPERSESSION_FIELDS:
        raw = anchor_note["meta"].get(fld)
        if not isinstance(raw, str):
            continue
        tgt, ambiguous = vid.resolve_identity(vault, raw)
        if ambiguous:
            broken.append(BrokenEdge(source=anchor_ref, raw_target=raw, edge_type=fld, reason="ambiguous",
                                      candidates=tuple(sorted(n["rel"] for n in ambiguous)),
                                      evidence="%s: %r matches more than one note" % (fld, raw)))
            continue
        if tgt is None:
            broken.append(BrokenEdge(source=anchor_ref, raw_target=raw, edge_type=fld, reason="missing",
                                      evidence="%s: %r does not resolve to any note" % (fld, raw)))
            continue
        if tgt["rel"] == anchor_note["rel"]:
            continue  # self-reference is its own (mechanical) authoring defect, not a provenance edge
        rel = mc._pair_relationship(vault, anchor_note, tgt)
        edges.append(Edge(source=anchor_ref, target=_ref(runtime, tgt), edge_type=fld,
                           detection_method="frontmatter:%s" % fld, reciprocal=(rel == "clean"),
                           evidence="frontmatter %s: %r (pair relationship: %s)" % (fld, raw, rel)))
    return edges, broken


def _incoming_supersession(runtime, vault, anchor_note):
    edges, broken = [], []
    anchor_ref = _ref(runtime, anchor_note)
    for note in vault.notes:
        if note["rel"] == anchor_note["rel"]:
            continue
        for fld in SUPERSESSION_FIELDS:
            raw = note["meta"].get(fld)
            if not isinstance(raw, str):
                continue
            tgt, ambiguous = vid.resolve_identity(vault, raw)
            if ambiguous or tgt is None or tgt["rel"] != anchor_note["rel"]:
                continue  # this note's declaration doesn't point at OUR anchor; its own brokenness
                          # (if any) surfaces when tracing that note itself, not here
            rel = mc._pair_relationship(vault, note, anchor_note)
            edges.append(Edge(source=_ref(runtime, note), target=anchor_ref, edge_type=fld,
                               detection_method="frontmatter:%s" % fld, reciprocal=(rel == "clean"),
                               evidence="%s frontmatter %s: %r (pair relationship: %s)" % (note["rel"], fld, raw, rel)))
    return edges, broken


# ---------------------------------------------------------------- job deps
def _is_job_note(note) -> bool:
    return "Jobs" in note["dir_parts"]


def _job_edges_for(runtime, vault, cycle_members, today, anchor_note, as_source: bool):
    """as_source=True: anchor IS a Job, edges point FROM it to its declared
    dependencies. as_source=False: scan every OTHER Job note for a
    dependency declaration that resolves to the anchor. Reuses
    audit_job_dependencies.py's own parse_tiers()/resolve_dependency() —
    never a second Job-tier parser or resolution table."""
    edges, broken = [], []
    anchor_ref = _ref(runtime, anchor_note)

    if as_source:
        if not _is_job_note(anchor_note) or anchor_note["fm"]["kind"] != "parsed":
            return edges, broken
        job_notes = [anchor_note]
    else:
        job_notes = [n for n in vault.notes if _is_job_note(n) and n["rel"] != anchor_note["rel"]
                     and n["fm"]["kind"] == "parsed"]

    for job_note in job_notes:
        tiers, _parse_errors = ajd.parse_tiers(job_note["body"])
        job_ref = _ref(runtime, job_note)
        for tier_name in ("Required", "Preferred", "Optional"):
            for raw_link, raw_qualifier in tiers.get(tier_name, []):
                tgt, ambiguous = vid.resolve_identity(vault, raw_link)
                if as_source:
                    if ambiguous:
                        broken.append(BrokenEdge(source=job_ref, raw_target=raw_link, edge_type="job_dependency",
                                                  reason="ambiguous", candidates=tuple(sorted(n["rel"] for n in ambiguous)),
                                                  evidence="Job %s tier declares [[%s]], ambiguous" % (tier_name, raw_link),
                                                  details={"tier": tier_name, "qualifier": raw_qualifier}))
                        continue
                    if tgt is None:
                        broken.append(BrokenEdge(source=job_ref, raw_target=raw_link, edge_type="job_dependency",
                                                  reason="missing",
                                                  evidence="Job %s tier declares [[%s]], unresolved" % (tier_name, raw_link),
                                                  details={"tier": tier_name, "qualifier": raw_qualifier}))
                        continue
                else:
                    if ambiguous or tgt is None or tgt["rel"] != anchor_note["rel"]:
                        continue
                resolution = ajd.resolve_dependency(vault, cycle_members, raw_link, raw_qualifier, today)
                target_ref = _ref(runtime, tgt) if as_source else anchor_ref
                source_ref = job_ref
                edges.append(Edge(
                    source=source_ref, target=target_ref, edge_type="job_dependency",
                    detection_method="job-tier:%s" % tier_name, reciprocal=None,
                    evidence="Job %s declares [[%s]] in its %s tier" % (job_note["rel"], raw_link, tier_name),
                    details={"job": job_note["rel"], "tier": tier_name, "qualifier": raw_qualifier,
                             "resolution": resolution},
                ))
    return edges, broken


# ------------------------------------------------------------------- cycles
def _build_dominance_graph(vault):
    """Rebuilds, read-only, the identical dominant->dominated graph
    audit_job_dependencies.compute_cycle_members() uses internally (supersedes:
    src->dst directly; superseded_by: reversed) — see that function's own
    docstring for why it returns membership only, never a path. Adjacency
    lists are explicit lists (not sets) built in vault.notes' fixed,
    path-sorted discovery order and then sorted per node, so traversal is
    fully deterministic regardless of hash seed."""
    graph = {}          # dominant_stem -> list[dominated_stem]
    edge_meta = {}       # (dominant_stem, dominated_stem) -> list[(field, declaring_note_rel, target_note_rel)]
    for note in vault.notes:
        for fld in SUPERSESSION_FIELDS:
            raw = note["meta"].get(fld)
            if not isinstance(raw, str):
                continue
            target, ambiguous = vid.resolve_identity(vault, raw)
            if target is None or ambiguous or target["rel"] == note["rel"]:
                continue
            if fld == "supersedes":
                dominant, dominated = note["stem"].lower(), target["stem"].lower()
            else:
                dominant, dominated = target["stem"].lower(), note["stem"].lower()
            graph.setdefault(dominant, [])
            if dominated not in graph[dominant]:
                graph[dominant].append(dominated)
            edge_meta.setdefault((dominant, dominated), []).append((fld, note["rel"], target["rel"]))
    for stem in graph:
        graph[stem].sort()
    return graph, edge_meta


def _find_cycle_path(graph, start_stem):
    """Deterministic DFS (sorted adjacency) retaining the path, stopping at
    the first repeat of start_stem. compute_cycle_members() already
    guarantees a path exists back to start whenever start_stem is a member —
    this only reconstructs what that membership check discards."""
    path = [start_stem]
    on_path = {start_stem}

    def dfs(node):
        for neighbor in graph.get(node, []):
            if neighbor == start_stem:
                path.append(neighbor)
                return True
            if neighbor in on_path:
                continue
            path.append(neighbor)
            on_path.add(neighbor)
            if dfs(neighbor):
                return True
            path.pop()
            on_path.discard(neighbor)
        return False

    return path if dfs(start_stem) else None


def _cycles_for(vault, anchor_note, cycle_members) -> list:
    anchor_stem = anchor_note["stem"].lower()
    if anchor_stem not in cycle_members:
        return []
    graph, edge_meta = _build_dominance_graph(vault)
    path = _find_cycle_path(graph, anchor_stem)
    if not path:
        # Defensive only: identical graph construction to compute_cycle_members
        # means this should never happen when anchor_stem is a genuine member.
        return [CycleReport(members=(anchor_note["rel"],), edge_types=(), status="cycle-detected-path-unavailable",
                             traversal=())]

    by_stem = {n["stem"].lower(): n for n in vault.notes}
    traversal = []
    edge_types = []
    for i in range(len(path) - 1):
        src_stem, dst_stem = path[i], path[i + 1]
        metas = edge_meta.get((src_stem, dst_stem), [])
        fld = metas[0][0] if metas else "unknown"
        src_rel = by_stem[src_stem]["rel"] if src_stem in by_stem else src_stem
        dst_rel = by_stem[dst_stem]["rel"] if dst_stem in by_stem else dst_stem
        traversal.append((src_rel, dst_rel, fld))
        edge_types.append(fld)

    members = tuple(dict.fromkeys(by_stem[s]["rel"] if s in by_stem else s for s in path[:-1]))
    return [CycleReport(members=members, edge_types=tuple(dict.fromkeys(edge_types)),
                         status="cycle-detected", traversal=tuple(traversal))]


# --------------------------------------------------------------- validation
def _validation_for(runtime, vault, anchor_note, cycle_members, today) -> list:
    anchor_ref = _ref(runtime, anchor_note)
    # ConflictMemory (MemoryRef) deliberately keeps a minimal shape with no
    # `reason` field — pull the real reason string straight from inspect()'s
    # own ValidatedContext instead of re-deriving it a second way.
    ctx = runtime.inspect(anchor_note["rel"]).context
    evidence = [ValidationEvidence(
        subject=anchor_ref, evidence_type="lifecycle", detail=ctx.reason,
        details={"memory_status": anchor_ref.memory_status, "status_track": anchor_ref.status_track,
                 "accepted": anchor_ref.accepted},
    )]

    if _is_job_note(anchor_note) and anchor_note["fm"]["kind"] == "parsed":
        tiers, parse_errors = ajd.parse_tiers(anchor_note["body"])
        verdict = "BLOCKED" if parse_errors else "PASS"
        blocked_on = list(parse_errors)
        if not parse_errors:
            for tier_name in ("Required",):
                for raw_link, raw_qualifier in tiers.get(tier_name, []):
                    res = ajd.resolve_dependency(vault, cycle_members, raw_link, raw_qualifier, today)
                    if res["block"] is not None:
                        verdict = "BLOCKED"
                        blocked_on.append("%s: [[%s]] -> %s (%s)" % (tier_name, raw_link, res["block"], res["detail"]))
        evidence.append(ValidationEvidence(
            subject=anchor_ref, evidence_type="job-verdict",
            detail="Job verdict: %s" % verdict,
            details={"verdict": verdict, "blocked_on": blocked_on},
        ))
    return evidence


# --------------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Trace explicit provenance/evidence edges (read-only).")
    ap.add_argument("vault")
    ap.add_argument("verb", choices=("trace", "outgoing", "incoming"))
    ap.add_argument("target")
    ap.add_argument("--boot")
    ap.add_argument("--repo")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rt = MemoryRuntime(args.vault, boot=args.boot, repo_root=args.repo)
    fn = {"trace": trace, "outgoing": outgoing, "incoming": incoming}[args.verb]
    report = fn(rt, args.target)

    def as_dict(o):
        if hasattr(o, "__dict__"):
            return {k: (list(v) if isinstance(v, tuple) else (as_dict(v) if hasattr(v, "__dict__") else v))
                    for k, v in vars(o).items()}
        return o

    def edge_dict(e):
        d = as_dict(e)
        d["source"] = as_dict(e.source)
        d["target"] = as_dict(e.target)
        return d

    payload = as_dict(report)
    payload["anchor"] = as_dict(report.anchor) if report.anchor else None
    payload["outgoing"] = [edge_dict(e) for e in report.outgoing]
    payload["incoming"] = [edge_dict(e) for e in report.incoming]
    payload["broken"] = [as_dict(b) | {"source": as_dict(b.source)} for b in report.broken]
    payload["cycles"] = [as_dict(c) for c in report.cycles]
    payload["validation"] = [as_dict(v) | {"subject": as_dict(v.subject)} for v in report.validation]

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("Identity: %r  Status: %s" % (args.target, report.status))
        if report.status == "ambiguous":
            print("  candidates: %s" % ", ".join(report.candidates))
        if report.anchor:
            print("  anchor: %s [%s]" % (report.anchor.note_path, report.anchor.status_track))
        for label, edges in (("outgoing", report.outgoing), ("incoming", report.incoming)):
            for e in edges:
                print("  [%s/%s] %s -> %s via %s" % (label, e.edge_type, e.source.note_path, e.target.note_path, e.detection_method))
        for b in report.broken:
            print("  [broken/%s] %s -> %r (%s)" % (b.edge_type, b.source.note_path, b.raw_target, b.reason))
        for c in report.cycles:
            print("  [cycle] %s" % " -> ".join(c.members + (c.members[0],)))
        for v in report.validation:
            print("  [validation/%s] %s" % (v.evidence_type, v.detail))
        sys.exit(0)
