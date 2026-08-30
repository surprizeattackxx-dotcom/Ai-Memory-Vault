#!/usr/bin/env python3
"""Deterministic, LLM-free coverage audit for MEMORY HEALTH CHECK manifests.

Reconciles a finished Inspection Manifest (templates/HEALTH_CHECK_MANIFEST.md)
against the vault's true .md inventory by re-running the validator's own
deterministic checks (frontmatter, lifecycle, wikilinks, structural, security,
duplicates) and comparing, machine-to-machine:

  G-partition      expected == inspected | skipped | excluded, pairwisely
                   disjoint, all entries in scope
  G-exclusion      every excluded entry is a structural file
  G-checks         the manifest records the full canonical check set for its
                   claimed level (union exact, completed superset for PASS)
  G-skips          a skipped file forces PARTIAL (recorded or not)
  G-budget         scan_interrupted true forces PARTIAL
  G-l3             Level 3 cross-note enumerations cover the applicable corpus
  G-findings       every deterministic error/flagged finding the scans prove
                   appears in the manifest's ## Findings
  G-recorded       completion_state agrees with what the gates imply

Verdicts:  PASS=0   (everything closed, recorded pass)
           PARTIAL=2 (a recorded PASS that fails any gate -> HC-FALSE-PASS; or
                     any honest partial/blocked-with-no-dependency claim)
           BLOCKED=3 (malformed manifest, or a recorded blocked dependency)

The audit NEVER infers what it cannot compute (semantic duplication,
contradiction classification, conflict resolution) — those belong to the agent
and are recorded in the manifest's ## Notes section.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

AUDITOR_VERSION = "1.1.1"
REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "tools" / "validate-vault.py"

_spec = importlib.util.spec_from_file_location("validate_vault", VALIDATOR)
vv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vv)

SCORE = 0
PARTIAL_SCORE = 2
BLOCKED_SCORE = 3

L1_CHECKS = {"structure", "frontmatter", "wikilinks", "metadata", "upgrade-state"}
L2_CHECKS = set(L1_CHECKS) | {"scope-coverage"}
L3_CHECKS = set(L1_CHECKS) | {"scope-coverage", "duplicates", "conflicts", "lifecycle-consistency"}
CANONICAL = {"level1": L1_CHECKS, "level2": L2_CHECKS, "level3": L3_CHECKS}

REQUIRED_FIELDS = {
    "manifest_type": str,
    "scope": str,
    "start_time": str,
    "completion_state": str,
    "expected_files": int,
    "inspected_count": int,
    "skipped_count": int,
    "excluded_count": int,
    "checks_completed": list,
    "checks_not_completed": list,
    "blocked_dependencies": list,
    "scan_interrupted": bool,
}
SCOPE_VALUES = {"level1", "level2", "level3"}
STATE_VALUES = {"pass", "partial", "blocked"}
ENTRY_RE = re.compile(r"^\s*-\s*\[[ xX]*\]\s*(.+?)\s*$", re.MULTILINE)
FINDING_RE = re.compile(r"^\s*-\s*\[(?P<sev>error|warning|flagged|info)\]\s+(?P<id>[A-Z][A-Z0-9-]*)\s+[\u2014-]\s+(?P<path>[^:\n]+?)(?::\s*(?P<msg>.*))?\s*$", re.MULTILINE)

MANIFEST_SECTIONS = ("Exclusions", "Inspected files", "Skipped files", "Findings")
L3_SECTIONS = ("Lifecycle coverage", "Duplicate coverage", "Supersession coverage")


# ------------------------------------------------------------- manifest parsing
def parse_manifest(text: str) -> tuple[dict, dict | None]:
    """Return (parsed, errors_dict) — errors_dict non-None means malformed."""
    fm, body, fm_state = vv.split_frontmatter(text)
    if fm_state["kind"] != "parsed":
        return {}, {"kind": "unparseable", "detail": "frontmatter did not parse"}
    try:
        meta = yaml.load(fm, Loader=vv.StringSafeLoader)
    except yaml.YAMLError as exc:
        return {}, {"kind": "malformed", "detail": "yaml error: %s" % (str(exc).splitlines()[0] if exc else "yaml")}
    if not isinstance(meta, dict):
        return {}, {"kind": "malformed", "detail": "frontmatter is not a mapping"}
    problems = []
    for field, ftype in REQUIRED_FIELDS.items():
        if field not in meta:
            problems.append("missing field: %s" % field)
        elif ftype is bool and not isinstance(meta[field], bool):
            problems.append("field %s: expected bool" % field)
        elif ftype is list and not isinstance(meta[field], list):
            problems.append("field %s: expected list" % field)
        elif ftype is int and not isinstance(meta[field], int):
            problems.append("field %s: expected integer" % field)
        elif ftype is str and not isinstance(meta[field], str):
            problems.append("field %s: expected string" % field)
    if problem := (problems or meta.get("manifest_type") != "health-check" or meta.get("scope") not in SCOPE_VALUES
                   or meta.get("completion_state") not in STATE_VALUES):
        detail = "; ".join(problems) if problems else "manifest_type/scope/completion_state invalid"
        return {}, {"kind": "malformed", "detail": detail}
    if meta["scope"] == "level2" and not meta.get("scope_target"):
        return {}, {"kind": "malformed", "detail": "scope level2 requires scope_target"}
    if meta["completion_state"] == "blocked" and "blocked_dependencies" not in meta:
        return {}, {"kind": "malformed", "detail": "blocked state requires blocked_dependencies field"}
    return {"meta": meta, "body": body}, None


def section_blocks(body: str) -> dict[str, str]:
    blocks = {}
    lines = body.splitlines()
    current = None
    buf = []
    for line in lines:
        if line.startswith("## "):
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf)
    return blocks


def section_entries(text: str) -> list[str]:
    out = []
    for m in ENTRY_RE.finditer(text):
        entry = m.group(1).strip()
        if entry in ("None", "_None_", "n/a"):
            continue
        out.append(entry)
    return out


def parse_findings(text: str) -> list[dict]:
    out = []
    for m in FINDING_RE.finditer(text):
        out.append({"severity": m.group("sev"), "id": m.group("id"), "path": m.group("path").strip(),
                    "message": (m.group("msg") or "").strip()})
    return out


def split_reason(entry: str) -> tuple[str, str]:
    # Entries use the em dash (—) as the path/reason separator; paths use hy-
    # phens (" - "), never an em dash.
    if "\u2014" in entry:
        path, _, reason = entry.partition("\u2014")
        return path.strip(), reason.strip()
    return entry, ""


# ---------------------------------------------------------------- the audit
class Audit:
    def __init__(self, vault: vv.Vault, manifest: Path):
        self.vault = vault
        self.manifest_path = manifest
        self.findings = []          # HC-* list
        self.deterministic = []     # validator findings (error/flagged) from scans
        self.gates = {}
        self.l3_evidence = {"lifecycle": None, "duplicates": None, "supersession": None}
        self.errors_calculated = {}
        self.recorded = {}

    def emit(self, finding_id, severity, path, message):
        self.findings.append({"id": finding_id, "severity": severity, "path": path, "message": message})

    def stub_invalid(self, detail):
        """Fill recorded state so approve()/report() can run on a malformed manifest."""
        self.recorded["manifest_valid"] = False
        self.recorded["malformed_detail"] = detail
        self.recorded["meta"] = {
            "completion_state": "blocked", "scope": "", "scope_target": "",
            "expected_files": 0, "inspected_count": 0, "skipped_count": 0, "excluded_count": 0,
            "blocked_dependencies": [], "scan_interrupted": False,
        }
        self.recorded["partition"] = {"inspected": set(), "skipped": set(), "excluded": set(),
                                      "union": set(), "declared": {}}
        # No HC-MANIFEST-MALFORMED emit here: this stub is bookkeeping only. The
        # finding is emitted exactly once by the verify gate in approve(); emitting
        # it here too double-reports one malformed manifest as two findings.

    def sorted_findings(self):
        return sorted(self.findings, key=lambda f: (f["id"], f["path"] or "", f["message"]))

    # ---------------------------------------------------------------- scope truth
    def scope_expected(self) -> set[str]:
        meta = self.recorded["meta"]
        if meta["scope"] == "level2":
            target = meta.get("scope_target", "")
            return {n["rel"] for n in self.vault.notes if n["rel"].startswith(target.rstrip("/") + "/")}
        return {n["rel"] for n in self.vault.notes}

    def run_scans(self):
        self.vault.check_frontmatter()
        self.vault.check_lifecycle()
        self.vault.check_wikilinks()
        self.vault.check_structural()
        self.vault.check_security()
        self.vault.check_duplicates()

    # ------------------------------------------------------------------ gates
    def gate_partition(self, expected: set[str]) -> None:
        meta = self.recorded["meta"]
        blocks = self.recorded["sections"]
        inspected = set(section_entries(blocks.get("Inspected files", "")))
        skipped = set(split_reason(e)[0] for e in section_entries(blocks.get("Skipped files", "")))
        excluded = set(section_entries(blocks.get("Exclusions", "")))
        problems = []
        union = inspected | skipped | excluded
        seen = {}
        for tag, lst in (("inspected", inspected), ("skipped", skipped), ("excluded", excluded)):
            for rel in lst:
                seen.setdefault(rel, set()).add(tag)
        for rel, tags in seen.items():
            if len(tags) > 1:
                problems.append("HC-COUNT-MISMATCH: %s listed as both %s" % (rel, " and ".join(sorted(tags))))
        declared = {
            "inspected_count": len(inspected), "skipped_count": len(skipped), "excluded_count": len(excluded),
        }
        for field, actual in declared.items():
            if meta[field] != actual:
                problems.append("HC-COUNT-MISMATCH: %s declared %d, listed %d" % (field, meta[field], actual))
        if meta["expected_files"] != len(expected):
            problems.append("HC-COUNT-MISMATCH: expected_files declared %d, actual vault scope %d" % (meta["expected_files"], len(expected)))
        missing = expected - union
        for rel in sorted(missing):
            problems.append("HC-COVERAGE-GAP: %s not accounted for" % rel)
        for rel in sorted(union - expected):
            problems.append("HC-COVERAGE-OUTSIDE: %s is outside the scope" % rel)
        self.recorded["partition"] = {"inspected": inspected, "skipped": skipped, "excluded": excluded,
                                      "union": union, "declared": declared}
        self.gates["partition"] = not problems
        for msg in problems:
            head, _, detail = msg.partition(": ")
            self.emit(head, "error", "", detail)

    def gate_exclusion(self) -> None:
        excluded = self.recorded["partition"]["excluded"]
        by_rel = {n["rel"]: n for n in self.vault.notes}
        structural = {n["rel"] for n in self.vault.notes if self.vault.is_structural(n)}
        valid = True
        for rel in sorted(excluded):
            if rel in structural:
                self.emit("HC-EXCLUSION", "info", rel, "structural file excluded from index/orphan expectations")
            elif rel in by_rel:
                valid = False
                self.emit("HC-EXCLUSION-INVALID", "error", rel, "non-structural note may not be excluded")
            else:
                self.emit("HC-EXCLUSION-INVALID", "error", rel, "excluded file is not inside the vault scope")
        self.gates["exclusion"] = valid

    def gate_checks(self) -> None:
        meta = self.recorded["meta"]
        canonical = CANONICAL.get(meta["scope"], set())
        completed = set(meta["checks_completed"])
        not_completed = set(meta["checks_not_completed"])
        problems = []
        unknown = (completed | not_completed) - canonical
        if unknown:
            problems.append("HC-CHECK-PARTITION: checks unknown for level %s: %s" % (
                meta["scope"], ", ".join(sorted(unknown))))
        elif completed | not_completed != canonical:
            if completed - canonical == set() and not_completed - canonical == set():
                problems.append("HC-CHECK-PARTITION: completed + not_completed do not partition the level set")
        if not_completed:
            problems.append("HC-CHECK-INCOMPLETE: level %s checks not completed: %s" % (
                meta["scope"], ", ".join(sorted(not_completed))))
        self.gates["checks"] = not problems
        for msg in problems:
            head, _, detail = msg.partition(": ")
            self.emit(head, "error", "", detail)

    def gate_l3(self, expected: set[str]) -> None:
        meta = self.recorded["meta"]
        if meta["scope"] != "level3":
            self.gates["l3"] = True
            return
        blocks = self.recorded["sections"]
        by_rel = {n["rel"]: n for n in self.vault.notes}

        lifecycle_corpus = {
            n["rel"] for n in self.vault.notes
            if any(n["meta"].get(f) for f in ("memory_status", "source", "confidence", "confidence_basis",
                                               "first_observed", "last_confirmed", "supersedes", "superseded_by"))
        }
        buckets = {}
        for n in self.vault.notes:
            key = re.sub(r"\s+", " ", n["body"].strip()).strip().lower()
            if key:
                buckets.setdefault(key, []).append(n["rel"])
        dup_corpus = {rel for paths in buckets.values() if len(paths) > 1 for rel in paths}
        sup_corpus = set()
        meta_by_rel = {n["rel"]: n["meta"] for n in self.vault.notes}
        for rel, m in meta_by_rel.items():
            for field in ("supersedes", "superseded_by"):
                raw = m.get(field)
                if not isinstance(raw, str):
                    continue
                tgt = raw.strip().lstrip("[").rstrip("]").split("|")[0].strip().rsplit("/", 1)[-1].rstrip(".md")
                note = self.vault.note_by_stem(tgt)
                if note is not None:
                    sup_corpus.add(rel)
                    sup_corpus.add(note["rel"])

        ok = True
        for short, header, corpus in (
            ("lifecycle", "Lifecycle coverage", lifecycle_corpus),
            ("duplicates", "Duplicate coverage", dup_corpus),
            ("supersession", "Supersession coverage", sup_corpus),
        ):
            listed = set(section_entries(blocks.get(header, "")))
            missing = corpus - listed
            good = not missing
            self.l3_evidence[short] = good
            ok = ok and good
            if missing:
                self.emit("HC-L3-INCOMPLETE", "error", "",
                          "%s section omits applicable notes: %s" % (header, ", ".join(sorted(missing))))
        self.gates["l3"] = ok

    def gate_findings(self) -> None:
        meta = self.recorded["meta"]
        blocks = self.recorded["sections"]
        recorded_findings = parse_findings(blocks.get("Findings", ""))
        recorded_keys = {(f["id"], f["path"]) for f in recorded_findings if f["severity"] in ("error", "flagged")}
        missing = []
        for f in self.deterministic:
            if f["severity"] in ("error", "flagged") and (f["id"], f["path"]) not in recorded_keys:
                missing.append("%s %s" % (f["id"], f["path"]))
        if missing:
            self.emit("HC-FINDING-MISSED", "error", "",
                      "deterministic findings absent from manifest: %s" % "; ".join(sorted(set(missing))))
        self.gates["findings"] = not missing

    # ----------------------------------------------------------------- verdict
    def approve(self):
        meta = self.recorded["meta"]
        partition_ok = self.gates.get("partition", False)
        exclusion_ok = self.gates.get("exclusion", False)
        checks_ok = self.gates.get("checks", False)
        l3_ok = self.gates.get("l3", False)
        findings_ok = self.gates.get("findings", False)
        skipped = bool(self.recorded["partition"]["skipped"])
        interrupted = bool(meta["scan_interrupted"])

        for rel in sorted(self.recorded["partition"]["skipped"]):
            self.emit("HC-SKIPPED", "info", rel, "file skipped; coverage is partial by definition")
        if interrupted:
            self.emit("HC-BUDGET-INTERRUPTED", "info", "", "scan_interrupted: run stopped early, coverage is partial")
        for dep in meta["blocked_dependencies"]:
            self.emit("HC-BLOCKED-DEPENDENCY", "info", "", "check dependency unavailable: %s" % dep)
        if partition_ok and not skipped and not interrupted and exclusion_ok and checks_ok and l3_ok and findings_ok:
            self.emit("HC-COVERAGE-COMPLETE", "info", "", "manifest coverage closes over the vault scope")

        forced_partial = (not partition_ok or skipped or interrupted or not exclusion_ok
                          or not checks_ok or not l3_ok or not findings_ok)
        recorded = meta["completion_state"]

        if not self.recorded["manifest_valid"]:
            self.emit("HC-MANIFEST-MALFORMED", "error", "",
                      self.recorded.get("malformed_detail", "manifest is malformed"))
            return "BLOCKED", {"manifest_valid": False}
        if meta["blocked_dependencies"]:
            return "BLOCKED", {"manifest_valid": True}
        if recorded == "pass":
            if forced_partial:
                self.emit("HC-FALSE-PASS", "error", "", "manifest records PASS while coverage evidence is incomplete")
                return "PARTIAL", {"recorded": "pass", "forced_partial": True}
            return "PASS", {"recorded": "pass"}
        if recorded == "partial":
            if not forced_partial:
                self.emit("HC-STATE-MISMATCH", "error", "",
                          "manifest records partial but every coverage gate closes; it cannot be upgraded to PASS")
            return "PARTIAL", {"recorded": "partial"}
        # recorded == blocked, but no blocked_dependencies: unverifiable claim
        self.emit("HC-STATE-MISMATCH", "error", "",
                  "manifest records blocked without a blocked_dependencies entry; treated as unverifiable partial")
        return "PARTIAL", {"recorded": "blocked", "no_dependency": True}

    # ------------------------------------------------------------------ report
    def report(self):
        meta = self.recorded["meta"]
        verdict, reason = self.approve()

        def bucket(sev):
            return [f for f in self.sorted_findings() if f["severity"] == sev]

        by_rel = {n["rel"]: n for n in self.vault.notes}
        structural = {n["rel"] for n in self.vault.notes if self.vault.is_structural(n)}
        return {
            "auditor_version": AUDITOR_VERSION,
            "protocol_version_expected": vv.EXPECTED_PROTOCOL_VERSION,
            "vault_path": str(self.vault.root),
            "vault_state": self.vault.state,
            "boot_path": str(self.vault.boot) if self.vault.boot else None,
            "manifest_path": str(self.manifest_path),
            "manifest_valid": self.recorded["manifest_valid"],
            "recorded_completion_state": meta["completion_state"],
            "verdict": verdict,
            "coverage_complete": (self.gates.get("partition", False) and not self.recorded["partition"]["skipped"]
                                  and not bool(meta["scan_interrupted"]) and self.gates.get("exclusion", False)
                                  and self.gates.get("checks", False) and self.gates.get("l3", False)
                                  and self.gates.get("findings", False)),
            "gates": self.gates,
            "expected_files": meta["expected_files"],
            "expected_files_calculated": len(self.scope_expected()),
            "inspected_count": meta["inspected_count"],
            "skipped_count": meta["skipped_count"],
            "excluded_count": meta["excluded_count"],
            "scope": meta["scope"],
            "scope_target": meta.get("scope_target", ""),
            "scan_interrupted": meta["scan_interrupted"],
            "blocked_dependencies": meta["blocked_dependencies"],
            "structures_in_scope": len([r for r in self.scope_expected() if r in structural]),
            "files_examined": len(self.vault.notes),
            "links_examined": getattr(self.vault, "links_examined", 0),
            "lifecycle_records_examined": getattr(self.vault, "lifecycle_records_examined", 0),
            "structural_files_excluded": getattr(self.vault, "structural_files_excluded", 0),
            "l3_evidence": self.l3_evidence,
            "semantic_duplicate_detection": "NOT PERFORMED",
            "deterministic_findings": [f for f in self.deterministic if f["severity"] in ("error", "flagged")],
            "errors": bucket("error"),
            "warnings": bucket("warning"),
            "information": bucket("info"),
            "flagged": bucket("flagged"),
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - self.t0, 2),
        }


# ---------------------------------------------------------------------- entry
def parse_args():
    ap = argparse.ArgumentParser(description="Audit a Memory Health Check manifest against the vault deterministically.")
    ap.add_argument("vault", help="path to the vault root")
    ap.add_argument("--manifest", required=True, help="path to the Inspection Manifest (.md)")
    ap.add_argument("--boot", help="path to the boot file (identity + rules that can't lapse)")
    ap.add_argument("--json", action="store_true", help="emit JSON report to stdout")
    ap.add_argument("--out", help="also write report to this path")
    return ap.parse_args()


def main():
    args = parse_args()
    root = Path(args.vault)
    if not root.is_dir():
        sys.exit("ERROR: vault path is not a directory: %s" % args.vault)
    manifest = Path(args.manifest)
    boot = Path(args.boot) if args.boot else None

    vault = vv.Vault(root, boot)
    vault.t0 = time.time()
    vault.discover()
    vault.detect_state()
    vault.checks_completed.append("state")

    audit = Audit(vault, manifest)
    audit.t0 = time.time()
    if not manifest.is_file():
        audit.stub_invalid("manifest file not found: %s" % manifest)
        report = audit.report()
    else:
        text = _read_text(manifest)
        if text.startswith("\ufeff"):
            text = text[1:]
        parsed, err = parse_manifest(text)
        if err is not None:
            audit.deterministic = []
            audit.stub_invalid((err or {}).get("detail", "manifest is malformed"))
            report = audit.report()
        else:
            audit.recorded["manifest_valid"] = True
            audit.recorded["sections"] = {}
            audit.recorded.update(parsed)
            audit.recorded["sections"] = section_blocks(parsed["body"])
            audit.run_scans()
            audit.deterministic = [f.as_dict() for f in vault.findings]
            expected = audit.scope_expected()
            audit.gate_partition(expected)
            audit.gate_exclusion()
            audit.gate_checks()
            audit.gate_l3(expected)
            audit.gate_findings()
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
    if report["verdict"] == "BLOCKED":
        sys.exit(BLOCKED_SCORE)
    if report["verdict"] == "PARTIAL":
        sys.exit(PARTIAL_SCORE)
    sys.exit(SCORE)


def print_human(r):
    print("Vault:      %s" % r["vault_path"])
    print("Manifest:   %s   valid: %s" % (r["manifest_path"], r["manifest_valid"]))
    print("Scope:      %s%s   Verdict: %s" % (r["scope"], " / " + r["scope_target"] if r.get("scope_target") else "", r["verdict"]))
    print("Recorded:   %s   Coverage complete: %s   Interrupted: %s" % (
        r["recorded_completion_state"], r["coverage_complete"], r["scan_interrupted"]))
    print("Expected:   %d (recorded %d)   Inspected: %d   Skipped: %d   Excluded: %d" % (
        r["expected_files_calculated"], r["expected_files"], r["inspected_count"], r["skipped_count"], r["excluded_count"]))
    for gate, ok in r["gates"].items():
        print("  gate %-10s %s" % (gate, "ok" if ok else "FAIL"))
    for bucket in (("ERROR", r["errors"]), ("WARNING", r["warnings"]), ("FLAGGED", r["flagged"]), ("INFO", r["information"])):
        if not bucket[1]:
            continue
        print("%s (%d):" % (bucket[0], len(bucket[1])))
        for f in bucket[1]:
            print("  [%s] %s: %s" % (f["id"], f["path"] or "<audit>", f["message"]))
    print("Semantic duplicate detection: %s" % r["semantic_duplicate_detection"])
    print("Duration:   %.2fs" % r["duration_seconds"])


def _read_text(path):
    return path.read_text(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    main()