#!/usr/bin/env python3
"""Deterministic, LLM-free Job Required/Preferred/Optional dependency auditor.

v1.1.0 (2026-08-30) fixed two P0 defects an independent adversarial review
found in v1.0.0, both against already-defined protocol semantics, neither
requiring a MEMORY_PROTOCOL.md change:
  P0-1  a path-qualified Required link (`[[folder/Note]]`) could silently
        resolve to a same-stem note in the WRONG folder — see
        resolve_job_target()'s docstring for the full mechanism and why a
        dedicated resolver was added instead of changing the shared one.
  P0-2  a tier written as a multi-line Markdown bullet list only had its
        FIRST item parsed — every dependency after it was silently invisible
        to the auditor, never able to block anything — see parse_tiers()'s
        docstring.
v1.2.0 (2026-08-30, same day) fixed a third P0 an independent adversarial
review found in v1.1.0, also an implementation defect against already-defined
semantics:
  P0-3  a `**Required:**`-shaped line inside a fenced Markdown code block
        (a documentation example, not a declaration) could be matched as a
        real tier header, and a genuine duplicate real header could silently
        overwrite the first tier's dependency set in the tiers dict — either
        one could mask a real missing/blocked dependency behind a false
        PASS. Fixed by making parse_tiers() fence-aware (fenced lines are
        blanked before header/heading matching) and by making a duplicate
        REAL header an explicit, fail-closed authoring defect
        (JOB-BLOCKED-AUTHORING-DEFECT) whose dependency sets are still both
        independently evaluated, never one overwriting the other. See
        parse_tiers()'s docstring for the exact mechanism.

MEMORY_PROTOCOL.md's "Job dependency policy" (v2.5+) states the resolution
table is deterministic: "given the same Job and the same vault state, any two
honest agents reach the same outcome. No improvised per-session judgment." No
tool in this repo mechanically verified that claim before this one — an
Ai-Memory-Vault lifecycle audit (2026-08-30) found `validate-vault.py` has
zero Job-aware logic (it never parses a Job's Context section at all), so a
superseded, candidate-under-claim, or malformed-declaration Required
dependency produced no finding whatsoever anywhere in the toolchain. This tool
closes that gap for every row of the table that is actually computable from
note frontmatter + a Job's own declared syntax + a run date.

Reuses tools/validate-vault.py's Vault class for discovery, frontmatter
parsing, and vault-state detection (incompatible/disputed vocabulary) rather
than re-implementing any of it — the same pattern audit_health_coverage.py
already established.

Job discovery: any note under a path segment literally named "Jobs" (the
protocol's own placement rule — Memory classes: "Job notes (Jobs/ inside a
project, or in Resources for cross-cutting work)"). Within such a note, a
"## Context" heading is NOT required to be matched literally (templates use
that exact heading, but MEMORY_PROTOCOL.md itself never mandates it) — what
this tool actually keys on is the three bold tier labels themselves
(`**Required:**`, `**Preferred:**`, `**Optional:**`), which the protocol's own
prose and every shipped template agree on. A Jobs/-folder note with none of
the three (e.g. a Jobs/ folder index) is reported informationally, not as an
error.

Dependency-item grammar parsed: a `[[wikilink]]`, optionally immediately
followed by a parenthesized qualifier — `(claim)` or
`(claim, explicitly-confirmed: <N> days)`. Non-wikilink Context items (a
backtick-quoted file path, a bare descriptive phrase like "this note, end to
end") are not dependencies under the protocol's own dependency table (which is
wikilink-keyed) and are silently skipped, not flagged. The `·` separator
between items is a template convention, not a protocol requirement, so this
tool never splits on it — it extracts every `[[...]]  (...)?`  occurrence in
the tier's own line directly, which is delimiter-agnostic.

Explicit non-goal, not a bug: the table's "Two current notes contradict with
no supersession between them" row requires semantic understanding of note
CONTENT and is never evaluated by this tool. Per MEMORY_PROTOCOL.md's own
REQUIRES_AI classification (mirrored in validate-vault.py), that judgment
belongs to an agent performing RESOLVE_CONFLICT, not a mechanical scanner —
turning it into a heuristic (e.g. flagging same-topic notes as "maybe
contradictory") would be exactly the kind of invented, unreliable semantics
the audit that produced this tool was told not to add. A Required dependency
resolves purely on ITS OWN note's state; this tool never inspects sibling
notes to guess at contradiction.

"Frontmatter malformed" (table row) is scoped narrowly to a YAML-parse
failure or fully absent frontmatter — not to schema-enum violations (those are
validate-vault.py's SCHEMA-VIOLATION concern, a different failure mode from
"malformed").

Findings: JOB-BLOCKED-* (Required, error) / JOB-PREFERRED-DEGRADED (warning,
never blocks) / JOB-REQUIRED-PASS, JOB-DEGRADED-DISCLOSED,
JOB-PREFERRED-PASS, JOB-OPTIONAL-MISSING, JOB-NOT-A-JOB (info).
Per-Job verdict: BLOCKED iff any Required dependency resolves BLOCK; else
PASS. Never substitutes a sibling or semantically similar note for a failed
dependency — a failed dependency is reported and nothing else runs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "tools" / "validate-vault.py"
IDENTITY = REPO_ROOT / "tools" / "vault_identity.py"

_spec = importlib.util.spec_from_file_location("validate_vault", VALIDATOR)
vv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vv)

_id_spec = importlib.util.spec_from_file_location("vault_identity", IDENTITY)
vid = importlib.util.module_from_spec(_id_spec)
_id_spec.loader.exec_module(vid)

AUDITOR_VERSION = "1.2.1"

TIER_HEADER_RE = re.compile(r"^\*\*(Required|Preferred|Optional):\*\*", re.MULTILINE)
SECTION_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
DEP_ITEM_RE = re.compile(r"\[\[([^\[\]]+)\]\]\s*(\(([^)]*)\))?")
CLAIM_WINDOW_RE = re.compile(r"^claim\s*,\s*explicitly-confirmed\s*:\s*(\d+)\s*days?$")

# Canonical block-reason order per MEMORY_PROTOCOL.md Job dependency policy
# Required-tier paragraph: "malformed declaration -> missing -> malformed ->
# disputed -> ambiguous -> superseded -> claim lifecycle -> staleness".
BLOCK_MALFORMED_DECL = "authoring-defect"
BLOCK_MISSING = "missing"
BLOCK_MALFORMED_NOTE = "malformed"
BLOCK_DISPUTED = "disputed"
BLOCK_AMBIGUOUS_CYCLE = "ambiguous-cycle"
BLOCK_SUPERSEDED = "superseded"
BLOCK_CANDIDATE = "candidate"
BLOCK_UNCERTAIN = "uncertain"
BLOCK_DEPRECATED = "deprecated"
BLOCK_STATUS_ABSENT = "status-absent"
BLOCK_STALE = "stale"
BLOCK_RECENCY_UNVERIFIABLE = "recency-unverifiable"
BLOCK_AMBIGUOUS_TARGET = "ambiguous-target"

BLOCK_FINDING_ID = {
    BLOCK_MALFORMED_DECL: "JOB-BLOCKED-AUTHORING-DEFECT",
    BLOCK_MISSING: "JOB-BLOCKED-MISSING",
    BLOCK_AMBIGUOUS_TARGET: "JOB-BLOCKED-AMBIGUOUS-TARGET",
    BLOCK_MALFORMED_NOTE: "JOB-BLOCKED-MALFORMED-NOTE",
    BLOCK_DISPUTED: "JOB-BLOCKED-DISPUTED",
    BLOCK_AMBIGUOUS_CYCLE: "JOB-BLOCKED-CYCLE",
    BLOCK_SUPERSEDED: "JOB-BLOCKED-SUPERSEDED",
    BLOCK_CANDIDATE: "JOB-BLOCKED-CANDIDATE",
    BLOCK_UNCERTAIN: "JOB-BLOCKED-UNCERTAIN",
    BLOCK_DEPRECATED: "JOB-BLOCKED-DEPRECATED",
    BLOCK_STATUS_ABSENT: "JOB-BLOCKED-STATUS-ABSENT",
    BLOCK_STALE: "JOB-BLOCKED-STALE",
    BLOCK_RECENCY_UNVERIFIABLE: "JOB-BLOCKED-RECENCY-UNVERIFIABLE",
}
DEGRADED_STATES = {"candidate": BLOCK_CANDIDATE, "uncertain": BLOCK_UNCERTAIN, "deprecated": BLOCK_DEPRECATED}


class Finding:
    __slots__ = ("id", "severity", "path", "message")

    def __init__(self, finding_id, severity, path, message):
        self.id = finding_id
        self.severity = severity
        self.path = path
        self.message = message

    def as_dict(self):
        return {"id": self.id, "severity": self.severity, "path": self.path, "message": self.message}


def compute_cycle_members(vault: "vv.Vault") -> set[str]:
    """Every note stem (lowercased) that participates in a supersedes/
    superseded_by cycle, general graph traversal (BFS reachability), no
    hard-coded depth — independent of validate-vault.py's own _check_cycles(),
    which only ever reports ONE node of a detected cycle and stops (confirmed
    by source read: it returns immediately after the first cycle found), so it
    cannot tell a Job-dependency check whether an arbitrary OTHER node is also
    a member of that same cycle."""
    graph = defaultdict(set)
    for note in vault.notes:
        meta = note["meta"]
        for field in ("supersedes", "superseded_by"):
            raw = meta.get(field)
            if not isinstance(raw, str):
                continue
            target = vault.resolve_link(raw)
            if target is None or target is note:
                continue
            src, dst = note["stem"].lower(), target["stem"].lower()
            if field == "supersedes":
                graph[src].add(dst)
            else:
                graph[dst].add(src)
    members = set()
    for start in list(graph.keys()):
        seen = set()
        stack = list(graph.get(start, ()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if node == start:
                members.add(start)
                break
            stack.extend(graph.get(node, ()))
    return members


def resolve_job_target(vault: "vv.Vault", raw_link: str):
    """Thin alias over tools/vault_identity.py's resolve_identity() — the
    path-aware, fail-closed resolver originally written here for the P0-1 fix
    (2026-08-30), extracted the same day into a shared module so
    tools/memory_retrieval.py (the Memory Runtime's retrieval layer) doesn't
    carry a second, independently-maintained copy of the same security-
    relevant algorithm. Behavior is byte-for-byte unchanged — see
    vault_identity.resolve_identity()'s own docstring for the full mechanism
    and the exact incident (a path-qualified Required link silently resolving
    to a same-stem note in the wrong folder) this fixes. Kept as a
    same-named wrapper, not just a bare `= vid.resolve_identity`, so this
    module's own docstrings and comments that still say "resolve_job_target"
    keep pointing at a real, callable name."""
    return vid.resolve_identity(vault, raw_link)


def parse_qualifier(raw_qualifier: str | None):
    """Return (declared_class, window_days, malformed: bool)."""
    if raw_qualifier is None:
        return "operational", None, False
    q = raw_qualifier.strip()
    if q == "":
        return None, None, True
    if q == "claim":
        return "claim", None, False
    m = CLAIM_WINDOW_RE.match(q)
    if m:
        return "claim_window", int(m.group(1)), False
    return None, None, True


def parse_tiers(body: str) -> tuple[dict[str, list[tuple[str, str | None]]], list[str]]:
    r"""tier name -> list of (raw_link_text, raw_qualifier_or_None) in document
    order.

    P0-2 fix (2026-08-30): the prior version matched `(.*)$` immediately after
    the tier's bold label, which — because `.` never crosses a newline even
    under re.MULTILINE — captured only whatever text happened to sit on that
    same physical line. For the shipped templates' own single-line, middle-dot
    convention (`**Required:** [[a]] (claim) · [[b]] (claim)`) that happened to
    work by accident. For a tier written as a multi-line Markdown bullet list —
    an extremely ordinary way to author more than a couple of dependencies —
    `\s*` swallowed the newline after the label, `(.*)$` then matched only the
    FIRST bullet line, and every subsequent bullet was silently outside the
    match entirely: never scanned, never resolved, never able to block a Job
    no matter what it named.

    Fix: each tier's span now runs from immediately after its own header to
    the start of the next tier header, the next Markdown heading (## etc.), or
    the end of the document — whichever comes first — and [[wikilink]]
    extraction scans that whole span regardless of line breaks. This parses
    single-line middle-dot lists and multi-line bulleted lists identically,
    with blank lines between items a no-op either way.

    Tier labels inside fenced code are literal examples, never declarations.
    A repeated real label is an authoring defect, but its dependencies are
    retained for independent evaluation: a malformed duplicate must not hide
    the earlier or later Required set behind a dict overwrite."""
    visible_lines = []
    fence_marker = None
    errors = []
    for line in body.splitlines(keepends=True):
        fence = FENCE_LINE_RE.match(line)
        if fence:
            marker = fence.group(1)[0]
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            visible_lines.append("\n" if line.endswith("\n") else "")
            continue
        visible_lines.append("\n" if fence_marker is not None and line.endswith("\n") else ("" if fence_marker is not None else line))
    if fence_marker is not None:
        errors.append("unclosed fenced code block")

    visible = "".join(visible_lines)
    headers = list(TIER_HEADER_RE.finditer(visible))
    heading_starts = [m.start() for m in SECTION_HEADING_RE.finditer(visible)]
    tiers = {}
    for i, m in enumerate(headers):
        tier = m.group(1)
        span_start = m.end()
        span_end = len(body)
        if i + 1 < len(headers):
            span_end = min(span_end, headers[i + 1].start())
        for h in heading_starts:
            if h > span_start:
                span_end = min(span_end, h)
                break
        rest = visible[span_start:span_end]
        items = [(dm.group(1), dm.group(3)) for dm in DEP_ITEM_RE.finditer(rest)]
        if tier in tiers:
            errors.append("duplicate **%s:** tier declaration" % tier)
            tiers[tier].extend(items)
        else:
            tiers[tier] = items
    return tiers, errors


def resolve_dependency(vault: "vv.Vault", cycle_members: set[str], raw_link: str, raw_qualifier: str | None, today: date):
    """Returns dict: {block: reason-key or None, degraded: state-name or None,
    disclose: bool}. block is None means PASS. Canonical first-applicable-wins
    order per MEMORY_PROTOCOL.md."""
    declared_class, window_days, decl_malformed = parse_qualifier(raw_qualifier)
    if decl_malformed:
        return {"block": BLOCK_MALFORMED_DECL, "degraded": None, "disclose": False,
                "detail": "unresolvable qualifier grammar: (%s)" % (raw_qualifier or "")}

    target, ambiguous = resolve_job_target(vault, raw_link)
    if ambiguous:
        candidates = ", ".join(sorted(n["rel"] for n in ambiguous))
        return {"block": BLOCK_AMBIGUOUS_TARGET, "degraded": None, "disclose": False,
                "detail": "[[%s]] matches more than one note, exact target undetermined: %s" % (raw_link, candidates)}
    if target is None:
        return {"block": BLOCK_MISSING, "degraded": None, "disclose": False,
                "detail": "[[%s]] does not resolve to any note" % raw_link}

    if target["fm"]["kind"] != "parsed":
        return {"block": BLOCK_MALFORMED_NOTE, "degraded": None, "disclose": False,
                "detail": "target frontmatter %s" % ("missing" if target["fm"]["kind"] == "missing" else "unparseable")}

    ms_raw = target["meta"].get("memory_status")
    if isinstance(ms_raw, str) and ms_raw in vault.disputed_terms:
        return {"block": BLOCK_DISPUTED, "degraded": None, "disclose": False,
                "detail": "memory_status='%s' falls under disputed protocol vocabulary" % ms_raw}

    if target["stem"].lower() in cycle_members:
        return {"block": BLOCK_AMBIGUOUS_CYCLE, "degraded": None, "disclose": False,
                "detail": "target participates in a supersedes/superseded_by cycle — malformed, not a valid supersession"}

    effective = ms_raw
    if isinstance(ms_raw, str) and ms_raw == "active" and vault.state != "legacy":
        effective = "current"

    if effective == "superseded":
        return {"block": BLOCK_SUPERSEDED, "degraded": None, "disclose": False, "detail": "memory_status: superseded"}

    if effective in ("current",):
        if declared_class == "claim_window":
            last = target["meta"].get("last_confirmed")
            if not isinstance(last, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", last):
                return {"block": BLOCK_RECENCY_UNVERIFIABLE, "degraded": None, "disclose": False,
                        "detail": "last_confirmed absent/unparseable; declared window explicitly-confirmed: %d days" % window_days}
            try:
                last_date = date.fromisoformat(last)
            except ValueError:
                return {"block": BLOCK_RECENCY_UNVERIFIABLE, "degraded": None, "disclose": False,
                        "detail": "last_confirmed not a valid calendar date: %s" % last}
            age = (today - last_date).days
            if age < 0 or age > window_days:
                return {"block": BLOCK_STALE, "degraded": None, "disclose": False,
                        "detail": "last_confirmed %s is %d day(s) old; window is %d day(s)" % (last, age, window_days)}
        return {"block": None, "degraded": None, "disclose": False, "detail": "memory_status: %s" % (ms_raw or "current")}

    if effective in DEGRADED_STATES:
        reason = DEGRADED_STATES[effective]
        if declared_class == "operational":
            return {"block": None, "degraded": effective, "disclose": True, "detail": "memory_status: %s (operational, used as-is)" % effective}
        return {"block": reason, "degraded": None, "disclose": False, "detail": "memory_status: %s fails a claim declaration" % effective}

    # absent memory_status
    if declared_class == "operational":
        return {"block": None, "degraded": "absent", "disclose": True, "detail": "memory_status absent (untracked/legacy, operational use)"}
    return {"block": BLOCK_STATUS_ABSENT, "degraded": None, "disclose": False, "detail": "memory_status absent; claim declaration requires explicit currentness"}


class JobAudit:
    def __init__(self, vault: "vv.Vault", today: date, show_optional: bool):
        self.vault = vault
        self.today = today
        self.show_optional = show_optional
        self.findings = []
        self.jobs = []

    def emit(self, finding_id, severity, path, message):
        self.findings.append(Finding(finding_id, severity, path, message))

    def run(self):
        cycle_members = compute_cycle_members(self.vault)
        job_notes = [n for n in self.vault.notes if "Jobs" in n["dir_parts"]]
        for note in job_notes:
            if note["fm"]["kind"] != "parsed":
                self.emit("JOB-NOT-A-JOB", "info", note["rel"], "under a Jobs/ folder but frontmatter did not parse; skipped")
                continue
            tiers, parse_errors = parse_tiers(note["body"])
            if not tiers and not parse_errors:
                self.emit("JOB-NOT-A-JOB", "info", note["rel"], "under a Jobs/ folder but no **Required:**/**Preferred:**/**Optional:** tier line found; not treated as a Job")
                continue
            job_result = {"path": note["rel"], "verdict": "PASS", "tiers": {}}
            for detail in parse_errors:
                self.emit("JOB-BLOCKED-AUTHORING-DEFECT", "error", note["rel"],
                          "Job tier declaration malformed: %s" % detail)
                job_result["verdict"] = "BLOCKED"
            for tier_name in ("Required", "Preferred", "Optional"):
                items = tiers.get(tier_name, [])
                tier_out = []
                for raw_link, raw_qualifier in items:
                    res = resolve_dependency(self.vault, cycle_members, raw_link, raw_qualifier, self.today)
                    tier_out.append({"link": raw_link, "qualifier": raw_qualifier, **res})
                    self._emit_for_tier(note["rel"], tier_name, raw_link, res, job_result)
                job_result["tiers"][tier_name] = tier_out
            self.jobs.append(job_result)

    def _emit_for_tier(self, path, tier_name, raw_link, res, job_result):
        blocked = res["block"] is not None
        if tier_name == "Required":
            if blocked:
                fid = BLOCK_FINDING_ID[res["block"]]
                self.emit(fid, "error", path, "[[%s]] Required — BLOCKED (%s): %s" % (raw_link, res["block"], res["detail"]))
                job_result["verdict"] = "BLOCKED"
            elif res["disclose"]:
                self.emit("JOB-DEGRADED-DISCLOSED", "info", path, "[[%s]] Required (operational) — PASS, disclosed: %s" % (raw_link, res["detail"]))
            else:
                self.emit("JOB-REQUIRED-PASS", "info", path, "[[%s]] Required — PASS: %s" % (raw_link, res["detail"]))
        elif tier_name == "Preferred":
            if blocked or res["disclose"]:
                reason = res["block"] or res["degraded"]
                self.emit("JOB-PREFERRED-DEGRADED", "warning", path, "[[%s]] Preferred — degraded (%s), non-blocking: %s" % (raw_link, reason, res["detail"]))
            else:
                self.emit("JOB-PREFERRED-PASS", "info", path, "[[%s]] Preferred — PASS: %s" % (raw_link, res["detail"]))
        else:  # Optional
            if (blocked or res["disclose"]) and self.show_optional:
                reason = res["block"] or res["degraded"]
                self.emit("JOB-OPTIONAL-MISSING" if res["block"] == BLOCK_MISSING else "JOB-OPTIONAL-DEGRADED", "info", path,
                          "[[%s]] Optional — degraded (%s), silent unless asked: %s" % (raw_link, reason, res["detail"]))

    def report(self):
        sorted_findings = sorted((f.as_dict() for f in self.findings), key=lambda f: (f["id"], f["path"], f["message"]))
        errors = [f for f in sorted_findings if f["severity"] == "error"]
        warnings = [f for f in sorted_findings if f["severity"] == "warning"]
        information = [f for f in sorted_findings if f["severity"] == "info"]
        return {
            "auditor_version": AUDITOR_VERSION,
            "vault_path": str(self.vault.root),
            "vault_state": self.vault.state,
            "run_date": self.today.isoformat(),
            "jobs_examined": len(self.jobs),
            "jobs_blocked": sum(1 for j in self.jobs if j["verdict"] == "BLOCKED"),
            "jobs": self.jobs,
            "errors": errors,
            "warnings": warnings,
            "information": information,
            "not_evaluated": [
                "Two current notes contradict with no supersession between them (semantic — requires AI; "
                "see MEMORY_PROTOCOL.md Job dependency policy table). A Required dependency is resolved purely "
                "from its own note's state; this tool never inspects sibling notes to infer contradiction."
            ],
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - self.t0, 2),
        }


def parse_args():
    ap = argparse.ArgumentParser(description="Audit Job Required/Preferred/Optional dependencies deterministically.")
    ap.add_argument("vault", help="path to the vault root")
    ap.add_argument("--boot", help="path to the boot file (identity + rules that can't lapse)")
    ap.add_argument("--repo", help="repo root (defaults to this tool's own repo)")
    ap.add_argument("--today", help="run date YYYY-MM-DD (defaults to system date; use for deterministic fixture runs)")
    ap.add_argument("--show-optional", action="store_true", help="also report degraded Optional dependencies (protocol: silent unless asked)")
    ap.add_argument("--json", action="store_true", help="emit JSON report to stdout")
    ap.add_argument("--out", help="also write report to this path")
    return ap.parse_args()


def main():
    args = parse_args()
    root = Path(args.vault)
    if not root.is_dir():
        sys.exit("ERROR: vault path is not a directory: %s" % args.vault)
    boot = Path(args.boot) if args.boot else None
    if args.repo:
        vv.REPO_ROOT = Path(args.repo).resolve()
        vv.P3_ENABLED = True
    today = date.fromisoformat(args.today) if args.today else date.today()

    vault = vv.Vault(root, boot)
    vault.t0 = time.time()
    vault.discover()
    vault.detect_state()
    vault.checks_completed.append("state")

    audit = JobAudit(vault, today, args.show_optional)
    audit.t0 = time.time()
    audit.run()
    report = audit.report()

    if args.json:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.exit(1 if report["jobs_blocked"] else 0)


def print_human(r):
    print("Vault:      %s   State: %s   Run date: %s" % (r["vault_path"], r["vault_state"], r["run_date"]))
    print("Jobs:       %d examined, %d blocked" % (r["jobs_examined"], r["jobs_blocked"]))
    for j in r["jobs"]:
        print("  [%s] %s" % (j["verdict"], j["path"]))
    for bucket in (("ERROR", r["errors"]), ("WARNING", r["warnings"]), ("INFO", r["information"])):
        if not bucket[1]:
            continue
        print("%s (%d):" % (bucket[0], len(bucket[1])))
        for f in bucket[1]:
            print("  [%s] %s: %s" % (f["id"], f["path"] or "<audit>", f["message"]))
    print("Not evaluated (requires AI): %d row(s) — see report JSON" % len(r["not_evaluated"]))
    print("Duration:   %.2fs" % r["duration_seconds"])


if __name__ == "__main__":
    main()
