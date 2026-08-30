#!/usr/bin/env python3
"""Test harness for tools/audit_health_coverage.py (Memory Health Check audit).

Pipeline:
  1. Rebuild every health fixture (tests/fixtures/health/build_health_fixtures.py):
     a vault under vaults/<id>/ plus its Inspection Manifest under
     manifests/<id>.md (outside the counted vault).
  2. Run the auditor over each vault+manifest and compare verdict, exit code,
state, error/finding-ID multisets, gates, L3 evidence, and the independent
      recount (expected_files_calculated) against manifest.yaml.
  3. Print the HC control matrix (PASS / PARTIAL / BLOCKED / HC-FALSE-PASS).

Exits nonzero on any mismatch. Deterministic: each finding bucket is compared as
an ID multiset (count preserved, order ignored; the key is the finding ID only,
location deliberately not part of the comparison) so an auditor regression that
collapses distinct findings into one cannot silently pass. info stays a
presence-only subset check (`info_required`).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
AUDITOR = REPO / "tools" / "audit_health_coverage.py"
FIXTURES_DIR = REPO / "tests" / "fixtures" / "health"
OUT = FIXTURES_DIR / "vaults"
MANIFESTS = FIXTURES_DIR / "manifests"
BOOT = FIXTURES_DIR / "_boot" / "CLAUDE.md"
EXPECTATIONS = FIXTURES_DIR / "manifest.yaml"

_syspath = [str(REPO)] + sys.path
sys.path = _syspath
sys.path.insert(0, str(FIXTURES_DIR))

import build_health_fixtures as bhf  # noqa: E402  (tests/fixtures/health on sys.path)


def run_auditor(vault_dir: Path, manifest_path: Path) -> tuple[dict, int]:
    cmd = [sys.executable, str(AUDITOR), str(vault_dir), "--manifest", str(manifest_path),
           "--boot", str(BOOT), "--json"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO))
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("auditor produced non-JSON output for %s\nstdout=%s\nstderr=%s"
                           % (vault_dir, proc.stdout, proc.stderr))
    return report, proc.returncode


def finding_ids(findings) -> set:
    """Presence-only projection (for the info_required subset check)."""
    return {f["id"] for f in findings}


def finding_counts(findings) -> Counter:
    """ID multiset over a finding bucket; multiplicity is part of the contract."""
    return Counter(f["id"] for f in findings)


def ids_multiset(findings) -> list:
    """Sorted, multiplicity-preserving projection for exact bucket equality."""
    return sorted(finding_counts(findings).elements())


def check_fixture(fixture: dict) -> list[str]:
    problems = []
    name = "%s-%s" % (fixture["id"], fixture["name"])
    vault_dir = OUT / name
    manifest_path = MANIFESTS / (name + ".md")
    try:
        report, rc = run_auditor(vault_dir, manifest_path)
    except Exception as exc:  # noqa: BLE001
        return ["%s: %s" % (name, exc)]

    def cmp(label, actual, expected):
        if actual != expected:
            problems.append("%s: %s %r != expected %r" % (name, label, actual, expected))

    cmp("exit_code", rc, fixture["exit_code"])
    cmp("manifest_valid", report["manifest_valid"], fixture["manifest_valid"])
    cmp("vault_state", report["vault_state"], fixture["state"])
    cmp("scope", report["scope"], fixture["scope"])
    cmp("verdict", report["verdict"], fixture["verdict"])
    cmp("errors", ids_multiset(report["errors"]), sorted(fixture["errors"]))
    cmp("deterministic_errors", ids_multiset(report["deterministic_findings"]), sorted(fixture["deterministic_errors"]))
    cmp("coverage_complete", report["coverage_complete"], fixture["coverage_complete"])
    cmp("expected_files_calculated", report["expected_files_calculated"], fixture["expected"])
    for gate, val in fixture.get("gates", {}).items():
        cmp("gate.%s" % gate, report["gates"][gate], val)
    for sec, val in fixture.get("l3_evidence", {}).items():
        cmp("l3.%s" % sec, report["l3_evidence"][sec], val)
    info_ids = finding_ids(report["information"])
    missing_info = [x for x in fixture.get("info_required", []) if x not in info_ids]
    if missing_info:
        problems.append("%s: required infos missing %r (have %s)" % (name, missing_info, sorted(info_ids)))
    return problems


HC_MATRIX = [
    # (id, verdict, note)
    ("h01", "PASS", "clean full inspect"),
    ("h02", "PARTIAL", "L3 duplicate coverage incomplete -> HC-L3-INCOMPLETE"),
    ("h03", "PARTIAL", "skipped file forces partial"),
    ("h04", "PASS", "structural exclusions honoured"),
    ("h05", "PARTIAL", "scan_interrupted -> partial"),
    ("h06", "PARTIAL", "coverage gap"),
    ("h07", "BLOCKED", "recorded blocked dependency"),
    ("h08", "PARTIAL", "CONTROL: forged PASS caught (HC-FALSE-PASS)"),
    ("h09", "BLOCKED", "malformed manifest"),
    ("h10", "PARTIAL", "omitted deterministic finding caught (HC-FINDING-MISSED)"),
    ("h11", "PARTIAL", "invalid exclusion caught (HC-EXCLUSION-INVALID)"),
]


def print_matrix(problems):
    print("\nHealth-check coverage control matrix:")
    for fid, verdict, note in HC_MATRIX:
        failed = any(p.startswith(fid + "-") for p in problems)
        print("  [%s] %-5s %-60s %s" % ("FAIL" if failed else "ok", verdict, note,
                                        "PROBLEM" if failed else ""))


def main():
    n = bhf.build_all()
    expectations = yaml.safe_load(EXPECTATIONS.read_text(encoding="utf-8"))
    fixtures = expectations["fixtures"]
    problems = []
    for fixture in fixtures:
        problems += check_fixture(fixture)
    print("health fixtures: %d" % n)
    for fixture in fixtures:
        name = "%s-%s" % (fixture["id"], fixture["name"])
        status = "PASS"
        for p in problems:
            if p.startswith(name + ":"):
                status = "FAIL"
                break
        print("  [%s] %s (expected %s, exit %s)" % (status, name, fixture["verdict"], fixture["exit_code"]))
    print_matrix(problems)
    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nAll health-fixture expectations met.")


if __name__ == "__main__":
    main()