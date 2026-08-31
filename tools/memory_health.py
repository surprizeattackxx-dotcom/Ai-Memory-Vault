#!/usr/bin/env python3
"""Memory health / coverage layer — a standalone, additive, read-only
diagnostic built entirely on top of tools/memory_runtime.py,
tools/memory_conflict.py, tools/memory_provenance.py, and
tools/audit_job_dependencies.py.

THIS IS NOT MEMORY_PROTOCOL.md's `HEALTH_CHECK` OPERATION, AND IT IS NOT A
REPLACEMENT FOR tools/audit_health_coverage.py. That existing tool audits
whether an AI-authored Inspection Manifest honestly reports its own coverage
of a `HEALTH_CHECK` run (a meta-honesty check on a report, per
MEMORY_PROTOCOL.md's HEALTH_CHECK section). This module does something
different and did not exist as an option when that machinery was designed: a
purely mechanical structural diagnostic over the runtime/conflict/provenance
layer — lifecycle coherence, broken/ambiguous references, cycles, conflicts,
Job dependency coverage — with no AI in the loop and no Inspection Manifest.
The two systems answer different questions and neither supersedes the other.

GOAL: answer "how healthy is the Vault's memory STRUCTURE?" — never "is this
memory TRUE?". A finding here means the Vault's own explicit metadata and
relationships are internally coherent (or aren't) — it never evaluates
whether the underlying claim is factually correct, and it never changes
anything to make an inconsistency go away.

This module NEVER:
    - writes to any file
    - promotes a candidate, supersedes a note, or changes any lifecycle field
    - computes a numeric "truth score" or an opaque AI confidence score
    - invents a universal staleness threshold (see _translate_job_result's
      BLOCK_STALE handling — the ONLY staleness judgment anywhere in this
      module reuses a Job's own explicit `explicitly-confirmed: N days`
      window; no note is ever called stale on the strength of raw age alone)
    - treats an isolated/unreferenced note as a defect (orphaned_memory is
      always severity "informational", action "no action")
    - treats reference count, retrieval score, or search ranking as evidence
      of trustworthiness
    - re-implements identity resolution, lifecycle evaluation, conflict
      classification, provenance traversal, Job dependency resolution, cycle
      detection, or wikilink parsing — every one of those is reused from the
      module that already owns it (see the reuse map below)

Reuse map:
    - tools/memory_runtime.py's inspect()              -> lifecycle authority
    - tools/memory_conflict.py's detect_conflicts()    -> conflict classification
    - tools/memory_provenance.py's trace()             -> edges/cycles/broken refs
    - tools/audit_job_dependencies.py's JobAudit,
      resolve_dependency()'s own {block,degraded,disclose,detail} shape
                                                        -> Job dependency resolution
    - tools/vault_identity.py's resolve_identity()     -> identity resolution
    - tools/memory_conflict.py's ConflictMemory/_ref   -> the one note-ref shape
      and the one trust computation, reused verbatim

Standard library only. No network access. No write path anywhere.
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
import memory_conflict as mc  # noqa: E402
import memory_provenance as mp  # noqa: E402

vv = _rt_mod.vv
vid = _rt_mod.vid
ajd = _rt_mod.ajd

HEALTH_ENGINE_VERSION = "1.0.0"

FINDING_IDS = (
    "missing_required_dependency", "ambiguous_identity", "broken_reference",
    "unresolved_provenance", "supersession_inconsistency", "cycle_detected",
    "conflicting_identity", "disputed_memory", "malformed_metadata",
    "orphaned_memory", "stale_memory", "insufficient_evidence",
)
SEVERITIES = ("blocking", "warning", "informational")
RECOMMENDED_ACTIONS = ("ask a person", "review metadata", "repair reference",
                        "reconcile relationship", "no action")

MemoryRef = mc.ConflictMemory
_ref = mc._ref


@dataclass
class HealthFinding:
    """One structural diagnostic result. `severity` and `recommended_action`
    are both drawn from small, fixed vocabularies (SEVERITIES /
    RECOMMENDED_ACTIONS) — never a free-form verdict, never anything
    resembling "delete"/"promote"/"demote"/"mark current"/"mark superseded"."""
    finding_id: str                      # one of FINDING_IDS
    severity: str                        # one of SEVERITIES
    subject: MemoryRef | None
    related: tuple = ()                   # related MemoryRef entries, where applicable
    detection_method: str = ""
    evidence: str = ""
    recommended_action: str = "no action"  # one of RECOMMENDED_ACTIONS
    details: dict = field(default_factory=dict)


@dataclass
class CoverageSummary:
    """Vault-wide structural coverage counts. Populated only for a
    vault-wide assessment (target=None) — a single-target assessment reports
    its findings directly rather than a one-note "coverage" summary, since
    coverage is inherently a vault-scope concept (see module docstring's
    GOAL and ticket section 5)."""
    notes_discovered: int = 0
    notes_valid_lifecycle: int = 0
    notes_malformed_metadata: int = 0
    current: int = 0
    candidate: int = 0
    superseded: int = 0
    uncertain: int = 0
    deprecated: int = 0
    untracked: int = 0
    disputed: int = 0
    cycle_participants: int = 0
    ambiguous_identity_groups: int = 0
    broken_references: int = 0
    jobs_examined: int = 0
    jobs_blocked: int = 0
    jobs_with_required: int = 0
    required_pass: int = 0
    required_blocked: int = 0
    required_ambiguous: int = 0
    required_missing: int = 0
    conflicts_confirmed: int = 0
    conflicts_potentially_conflicting: int = 0
    conflicts_compatible: int = 0
    conflicts_insufficient_evidence: int = 0
    conflicts_related: int = 0
    notes_no_inbound: int = 0
    notes_no_outbound: int = 0
    notes_with_provenance: int = 0
    notes_insufficient_evidence: int = 0
    details: dict = field(default_factory=dict)


@dataclass
class HealthReport:
    target: str | None
    status: str                          # "resolved" | "ambiguous" | "missing" | "empty" | "vault"
    subject: MemoryRef | None = None
    findings: tuple = ()
    coverage: CoverageSummary | None = None
    candidates: tuple = ()


# --------------------------------------------------------------- public API
def assess(runtime: "MemoryRuntime", target: str | None = None, show_optional: bool = False,
           today: date | None = None) -> HealthReport:
    """Full picture: findings (+ coverage, when target is None)."""
    return _build(runtime, target, show_optional, today)


def coverage(runtime: "MemoryRuntime", target: str | None = None, show_optional: bool = False,
             today: date | None = None) -> CoverageSummary | None:
    return _build(runtime, target, show_optional, today).coverage


def findings(runtime: "MemoryRuntime", target: str | None = None, show_optional: bool = False,
             today: date | None = None) -> tuple:
    return _build(runtime, target, show_optional, today).findings


# ------------------------------------------------------------------- build
def _build(runtime: "MemoryRuntime", target: str | None, show_optional: bool, today) -> HealthReport:
    vault = runtime.vault
    today = today or date.today()

    if target is None:
        return _assess_vault(runtime, vault, show_optional, today)

    if not str(target).strip():
        return HealthReport(target=target, status="empty")

    resolved, ambiguous = vid.resolve_identity(vault, target)
    if ambiguous:
        f = HealthFinding(
            finding_id="ambiguous_identity", severity="blocking", subject=None,
            detection_method="identity-resolution",
            evidence="identity %r matches %d notes with no path qualifier to disambiguate" % (target, len(ambiguous)),
            recommended_action="ask a person",
            details={"candidates": sorted(n["rel"] for n in ambiguous)},
        )
        return HealthReport(target=target, status="ambiguous",
                             candidates=tuple(sorted(n["rel"] for n in ambiguous)), findings=(f,))
    if resolved is None:
        return HealthReport(target=target, status="missing")

    subject_ref = _ref(runtime, resolved)
    fs = list(_findings_for_note(runtime, vault, resolved, subject_ref, ajd.compute_cycle_members(vault),
                                  show_optional, today))
    return HealthReport(target=target, status="resolved", subject=subject_ref, findings=tuple(_dedup(fs)))


def _dedup(findings_list) -> list:
    seen = set()
    out = []
    for f in findings_list:
        subj = f.subject.note_path if f.subject else None
        rel = tuple(sorted(r.note_path for r in f.related))
        key = (f.finding_id, subj, rel, f.detection_method, f.evidence)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    out.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.finding_id,
                             f.subject.note_path if f.subject else "", f.detection_method))
    return out


_SEVERITY_ORDER = {"blocking": 0, "warning": 1, "informational": 2}


# ------------------------------------------------------------ per-note pass
def _job_tier_raw_links(note) -> set:
    """Every raw [[...]] target string declared in ANY tier of this note, if
    it's a parsed Job — so the generic wikilink scan can exclude them (they
    are exclusively reported, tier-aware, by _job_findings_for_note; without
    this, an Optional dependency's broken wikilink would leak through as a
    generic warning-severity broken_reference regardless of tier or
    show_optional, defeating "Optional is silent by default")."""
    if "Jobs" not in note["dir_parts"] or note["fm"]["kind"] != "parsed":
        return set()
    tiers, _errors = ajd.parse_tiers(note["body"])
    return {raw_link for items in tiers.values() for raw_link, _q in items}


def _findings_for_note(runtime, vault, note, subject_ref, cycle_members, show_optional, today):
    job_links = _job_tier_raw_links(note)
    yield from _lifecycle_findings(runtime, note, subject_ref)
    yield from _provenance_findings(runtime, vault, note, subject_ref, job_links)
    yield from _conflict_findings(runtime, note, subject_ref)
    yield from _job_findings_for_note(runtime, vault, note, subject_ref, cycle_members, show_optional, today)


def _lifecycle_findings(runtime, note, subject_ref):
    ctx = runtime.inspect(note["rel"]).context
    if ctx.status_track == "malformed-frontmatter":
        yield HealthFinding(finding_id="malformed_metadata", severity="warning", subject=subject_ref,
                             detection_method="lifecycle-inspection", evidence=ctx.reason,
                             recommended_action="review metadata")
    elif ctx.status_track == "disputed":
        yield HealthFinding(finding_id="disputed_memory", severity="warning", subject=subject_ref,
                             detection_method="lifecycle-inspection", evidence=ctx.reason,
                             recommended_action="ask a person")
    elif ctx.status_track == "untracked":
        # MEMORY_PROTOCOL.md's Metadata section: absence means "not tracked",
        # never itself an error — informational only, never escalated.
        yield HealthFinding(finding_id="insufficient_evidence", severity="informational", subject=subject_ref,
                             detection_method="lifecycle-inspection", evidence=ctx.reason,
                             recommended_action="no action")
    # "current" / "superseded" / "candidate" / "uncertain" / "deprecated" /
    # "cycle" are all coherent lifecycle states on their own — "cycle" is
    # reported once, richly, from provenance below, never duplicated here.


def _provenance_findings(runtime, vault, note, subject_ref, job_links=frozenset()):
    report = mp.trace(runtime, note["rel"])
    for b in report.broken:
        if b.edge_type == "job_dependency":
            continue  # handled with full tier/severity context by _job_findings_for_note
        if b.edge_type == "wikilink" and b.raw_target in job_links:
            continue  # this raw link IS a Job-tier declaration; tier-aware handling owns it exclusively
        yield HealthFinding(
            finding_id="broken_reference", severity="warning", subject=subject_ref,
            detection_method="provenance:%s" % b.edge_type, evidence=b.evidence,
            recommended_action="repair reference",
            details={"raw_target": b.raw_target, "reason": b.reason, "candidates": list(b.candidates)},
        )
    non_job_broken = [b for b in report.broken
                       if b.edge_type != "job_dependency" and not (b.edge_type == "wikilink" and b.raw_target in job_links)]
    if non_job_broken:
        yield HealthFinding(
            finding_id="unresolved_provenance", severity="warning", subject=subject_ref,
            detection_method="provenance-rollup",
            evidence="%d broken/ambiguous reference(s) in this note's own declared relationships" % len(non_job_broken),
            recommended_action="repair reference",
        )
    for c in report.cycles:
        yield HealthFinding(
            finding_id="cycle_detected", severity="blocking", subject=subject_ref,
            related=tuple(_ref(runtime, n) for n in vault.notes if n["rel"] in c.members and n["rel"] != note["rel"]),
            detection_method="provenance-cycle-path",
            evidence="participates in a supersedes/superseded_by cycle: %s" % " -> ".join(c.members + (c.members[0],)),
            recommended_action="ask a person", details={"members": list(c.members), "edge_types": list(c.edge_types)},
        )
    if not report.outgoing and not report.incoming and not vault.is_structural(note):
        yield HealthFinding(
            finding_id="orphaned_memory", severity="informational", subject=subject_ref,
            detection_method="provenance-isolation",
            evidence="no inbound or outbound wikilink/supersession/Job-dependency edges found",
            recommended_action="no action",
        )


# detect_conflicts detection_method -> (finding_id, severity)
_CONFLICT_TO_FINDING = {
    "identity-collision-both-current": ("conflicting_identity", "warning"),
    "identity-collision-current-vs-candidate": ("conflicting_identity", "warning"),
    "identity-collision-two-candidates": ("conflicting_identity", "warning"),
    "identity-collision-divergent-lifecycle": ("conflicting_identity", "warning"),
    "identity-collision-current-vs-superseded-no-link": ("supersession_inconsistency", "warning"),
    "unreciprocated-supersession": ("supersession_inconsistency", "warning"),
    "supersession-link-without-superseded-status": ("supersession_inconsistency", "warning"),
    "disputed-vocabulary": ("disputed_memory", "warning"),
    "malformed-frontmatter": ("malformed_metadata", "warning"),
    "ambiguous-identity": ("ambiguous_identity", "warning"),
    # "supersession-cycle" / "circular-declaration" (confirmed_conflict) are
    # deliberately NOT translated here — tools/memory_provenance.py's cycle
    # detection already reports the same underlying fact, richer (full path
    # + every member), via _provenance_findings above. Reporting it a second
    # time from the conflict layer would duplicate one fact under two
    # findings; provenance is the single source for cycle_detected.
    # "identity-collision-same-lifecycle" (related) / "shared-keyword"
    # (related) / "clean-reciprocated-supersession" (compatible) are never
    # findings at all — a lexical or clean-history match is not a defect.
}


def _conflict_findings(runtime, note, subject_ref):
    report = mc.detect_conflicts(runtime, note["rel"])
    if report.status != "resolved":
        return
    for c in report.conflicts:
        mapping = _CONFLICT_TO_FINDING.get(c.detection_method)
        if mapping is None:
            continue
        finding_id, severity = mapping
        other = c.memory_b if c.memory_a.note_path == subject_ref.note_path else c.memory_a
        yield HealthFinding(
            finding_id=finding_id, severity=severity, subject=subject_ref, related=(other,),
            detection_method="conflict:%s" % c.detection_method, evidence=c.evidence,
            recommended_action="ask a person" if finding_id in ("conflicting_identity", "disputed_memory", "ambiguous_identity")
            else ("review metadata" if finding_id == "malformed_metadata" else "reconcile relationship"),
            details={"conflict_category": c.category, "conflict_confidence": c.confidence},
        )


# resolve_dependency()'s block-reason string -> tier-aware finding translation.
# Required tier gets the tier-specific, blocking vocabulary; Preferred/Optional
# get the generic, non-required vocabulary at warning/informational severity —
# ticket section 12: "Preferred/Optional failures must not be treated as
# Required failures."
_REQUIRED_BLOCK_TO_FINDING = {
    "authoring-defect": "malformed_metadata",
    "malformed": "malformed_metadata",
    "disputed": "disputed_memory",
    "ambiguous-cycle": "cycle_detected",
    "ambiguous-target": "ambiguous_identity",
    "missing": "missing_required_dependency",
    "superseded": "missing_required_dependency",
    "candidate": "missing_required_dependency",
    "uncertain": "missing_required_dependency",
    "deprecated": "missing_required_dependency",
    "status-absent": "missing_required_dependency",
    "stale": "stale_memory",
    "recency-unverifiable": "missing_required_dependency",
}
_NON_REQUIRED_BLOCK_TO_FINDING = {
    "authoring-defect": "malformed_metadata",
    "malformed": "malformed_metadata",
    "disputed": "disputed_memory",
    "ambiguous-cycle": "cycle_detected",
    "ambiguous-target": "ambiguous_identity",
    "missing": "broken_reference",
    "superseded": "broken_reference",
    "candidate": "broken_reference",
    "uncertain": "broken_reference",
    "deprecated": "broken_reference",
    "status-absent": "broken_reference",
    "stale": "stale_memory",
    "recency-unverifiable": "broken_reference",
}
_ACTION_BY_FINDING = {
    "missing_required_dependency": "ask a person", "ambiguous_identity": "ask a person",
    "broken_reference": "repair reference", "unresolved_provenance": "repair reference",
    "supersession_inconsistency": "reconcile relationship", "cycle_detected": "ask a person",
    "conflicting_identity": "ask a person", "disputed_memory": "ask a person",
    "malformed_metadata": "review metadata", "orphaned_memory": "no action",
    "stale_memory": "review metadata", "insufficient_evidence": "no action",
}


def _translate_job_result(job_ref, target_ref, tier_name, item, show_optional):
    """item is resolve_dependency()'s own {block, degraded, disclose, detail}
    shape (or an equivalent for a broken/ambiguous raw target) — reused
    verbatim, never re-derived. Returns a HealthFinding or None (clean PASS,
    or a silent Optional degradation when show_optional is False)."""
    block = item.get("block")
    degraded = item.get("degraded")
    disclose = item.get("disclose", False)
    reason = block or degraded
    if reason is None:
        return None  # clean PASS
    if tier_name != "Required" and not (block or disclose):
        return None
    if tier_name == "Optional" and not show_optional:
        return None

    table = _REQUIRED_BLOCK_TO_FINDING if tier_name == "Required" else _NON_REQUIRED_BLOCK_TO_FINDING
    finding_id = table.get(reason, "broken_reference")
    if tier_name == "Required" and block is not None:
        severity = "blocking"
    elif tier_name == "Preferred":
        severity = "warning"
    else:
        severity = "informational"
    return HealthFinding(
        finding_id=finding_id, severity=severity, subject=job_ref, related=(target_ref,) if target_ref else (),
        detection_method="job-tier:%s" % tier_name,
        evidence="%s tier dependency [[%s]] -> %s: %s" % (tier_name, item.get("link", "?"), reason, item.get("detail", "")),
        recommended_action=_ACTION_BY_FINDING.get(finding_id, "ask a person"),
        details={"tier": tier_name, "qualifier": item.get("qualifier"), "reason": reason},
    )


def _job_findings_for_note(runtime, vault, note, subject_ref, cycle_members, show_optional, today):
    is_job = "Jobs" in note["dir_parts"] and note["fm"]["kind"] == "parsed"

    if is_job:
        out = mp.outgoing(runtime, note["rel"])
        for e in out.outgoing:
            if e.edge_type != "job_dependency":
                continue
            item = dict(e.details.get("resolution", {}))
            item["link"] = e.evidence.split("[[", 1)[-1].split("]]", 1)[0] if "[[" in e.evidence else e.target.stem
            item["qualifier"] = e.details.get("qualifier")
            f = _translate_job_result(subject_ref, e.target, e.details.get("tier", "Required"), item, show_optional)
            if f:
                yield f
        for b in out.broken:
            if b.edge_type != "job_dependency":
                continue
            tier_name = b.details.get("tier", "Required")
            item = {"block": "ambiguous-target" if b.reason == "ambiguous" else "missing",
                     "link": b.raw_target, "qualifier": b.details.get("qualifier"), "detail": b.evidence}
            f = _translate_job_result(subject_ref, None, tier_name, item, show_optional)
            if f:
                yield f

    inc = mp.incoming(runtime, note["rel"])
    for e in inc.incoming:
        if e.edge_type != "job_dependency":
            continue
        item = dict(e.details.get("resolution", {}))
        item["link"] = note["stem"]
        item["qualifier"] = e.details.get("qualifier")
        f = _translate_job_result(e.source, subject_ref, e.details.get("tier", "Required"), item, show_optional)
        if f:
            yield f


# --------------------------------------------------------------- vault-wide
def _assess_vault(runtime, vault, show_optional, today) -> HealthReport:
    cycle_members = ajd.compute_cycle_members(vault)
    cov = CoverageSummary()
    all_findings = []

    for note in vault.notes:
        cov.notes_discovered += 1
        ctx = runtime.inspect(note["rel"]).context
        if ctx.status_track == "malformed-frontmatter":
            cov.notes_malformed_metadata += 1
        else:
            cov.notes_valid_lifecycle += 1
        bucket = {"current": "current", "superseded": "superseded", "candidate": "candidate",
                  "uncertain": "uncertain", "deprecated": "deprecated", "untracked": "untracked",
                  "disputed": "disputed"}.get(ctx.status_track)
        if bucket:
            setattr(cov, bucket, getattr(cov, bucket) + 1)
        if note["stem"].lower() in cycle_members:
            cov.cycle_participants += 1

        subject_ref = _ref(runtime, note)
        note_findings = list(_findings_for_note(runtime, vault, note, subject_ref, cycle_members, show_optional, today))
        all_findings.extend(note_findings)

        prov = mp.trace(runtime, note["rel"])
        if not prov.outgoing:
            cov.notes_no_outbound += 1
        if not prov.incoming:
            cov.notes_no_inbound += 1
        if prov.outgoing or prov.incoming:
            cov.notes_with_provenance += 1
        cov.broken_references += len([b for b in prov.broken if b.edge_type != "job_dependency"])
        if ctx.status_track == "untracked":
            cov.notes_insufficient_evidence += 1

        conf = mc.detect_conflicts(runtime, note["rel"])
        if conf.status == "resolved":
            for c in conf.conflicts:
                if c.category == "confirmed_conflict":
                    cov.conflicts_confirmed += 1
                elif c.category == "potentially_conflicting":
                    cov.conflicts_potentially_conflicting += 1
                elif c.category == "compatible":
                    cov.conflicts_compatible += 1
                elif c.category == "insufficient_evidence":
                    cov.conflicts_insufficient_evidence += 1
                elif c.category == "related":
                    cov.conflicts_related += 1

    # Conflict/cycle/vault-conflict counts above double-count each pair once
    # per side (querying A finds (A,B); querying B finds the same pair again)
    # by construction of the per-note loop — halve the pairwise tallies to
    # report unique relationships, never a per-note multiplicity.
    for field_name in ("conflicts_confirmed", "conflicts_potentially_conflicting", "conflicts_compatible",
                        "conflicts_insufficient_evidence", "conflicts_related"):
        setattr(cov, field_name, getattr(cov, field_name) // 2)

    audit = ajd.JobAudit(vault, today, show_optional=True)
    audit.run()
    cov.jobs_examined = len(audit.jobs)
    cov.jobs_blocked = sum(1 for j in audit.jobs if j["verdict"] == "BLOCKED")
    for job_result in audit.jobs:
        req = job_result["tiers"].get("Required", [])
        if req:
            cov.jobs_with_required += 1
        for item in req:
            if item["block"] is None:
                cov.required_pass += 1
            elif item["block"] == "ambiguous-target":
                cov.required_ambiguous += 1
            elif item["block"] == "missing":
                cov.required_missing += 1
            else:
                cov.required_blocked += 1

    # Ambiguous-identity GROUPS (distinct same-stem clusters), not raw
    # candidate counts — dedupe by frozenset of member paths.
    seen_groups = set()
    for note in vault.notes:
        _target, ambiguous = vid.resolve_identity(vault, note["stem"])
        if ambiguous:
            seen_groups.add(frozenset(n["rel"] for n in ambiguous))
    cov.ambiguous_identity_groups = len(seen_groups)

    deduped = _dedup(_collapse_cycles(all_findings))
    return HealthReport(target=None, status="vault", findings=tuple(deduped), coverage=cov)


def _collapse_cycles(findings_list) -> list:
    """Vault-wide only: every member of an N-node cycle independently
    discovers "the same" cycle via its own trace() call, each reporting
    itself as the subject — correct and non-duplicate for a single-target
    assess() (that note really is the subject), but N redundant findings for
    one underlying cycle in a vault-wide sweep. Collapse to one
    representative per unique member-set, chosen deterministically (the
    member whose path sorts first becomes the reported subject)."""
    by_members = {}
    out = []
    for f in findings_list:
        if f.finding_id != "cycle_detected":
            out.append(f)
            continue
        key = frozenset(f.details.get("members", ()))
        if key in by_members:
            continue
        by_members[key] = f
    out.extend(by_members.values())
    return out


# --------------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Assess Vault memory health/coverage (read-only).")
    ap.add_argument("vault")
    ap.add_argument("target", nargs="?", default=None)
    ap.add_argument("--boot")
    ap.add_argument("--repo")
    ap.add_argument("--today")
    ap.add_argument("--show-optional", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rt = MemoryRuntime(args.vault, boot=args.boot, repo_root=args.repo)
    run_date = date.fromisoformat(args.today) if args.today else date.today()
    report = assess(rt, args.target, show_optional=args.show_optional, today=run_date)

    def as_dict(o):
        if hasattr(o, "__dict__"):
            return {k: (list(v) if isinstance(v, tuple) else (as_dict(v) if hasattr(v, "__dict__") else v))
                    for k, v in vars(o).items()}
        return o

    def finding_dict(f):
        d = as_dict(f)
        d["subject"] = as_dict(f.subject) if f.subject else None
        d["related"] = [as_dict(r) for r in f.related]
        return d

    if args.json:
        payload = {
            "target": report.target, "status": report.status,
            "subject": as_dict(report.subject) if report.subject else None,
            "candidates": list(report.candidates),
            "findings": [finding_dict(f) for f in report.findings],
            "coverage": as_dict(report.coverage) if report.coverage else None,
        }
        print(json.dumps(payload, indent=2, default=str, sort_keys=True))
    else:
        print("Target: %r  Status: %s" % (args.target, report.status))
        if report.status == "ambiguous":
            print("  candidates: %s" % ", ".join(report.candidates))
        for sev, label in (("blocking", "BLOCKING"), ("warning", "WARNING"), ("informational", "INFORMATIONAL")):
            group = [f for f in report.findings if f.severity == sev]
            if not group:
                continue
            print("%s (%d):" % (label, len(group)))
            for f in group:
                subj = f.subject.note_path if f.subject else "(vault)"
                print("  [%s] %s — %s" % (f.finding_id, subj, f.evidence))
                print("      -> %s" % f.recommended_action)
        if report.coverage:
            print("COVERAGE:")
            for k, v in vars(report.coverage).items():
                if k == "details":
                    continue
                print("  %s: %s" % (k, v))
        sys.exit(0)
