#!/usr/bin/env python3
"""Test harness for tools/audit_job_dependencies.py (Job dependency audit).

Pipeline:
  1. Rebuild every job fixture (tests/fixtures/jobs/build_job_fixtures.py).
  2. Run the auditor over each fixture (--today 2026-08-30, pinned) and
     compare vault_state/jobs_examined/jobs_blocked/error+warning ID
     multisets/required-info against tests/fixtures/jobs/manifest.yaml.
  3. For the one fixture with a show_optional_check (JJ), re-run with
     --show-optional and confirm the previously-silent finding appears.

Exits nonzero on any mismatch. Deterministic: error/warning buckets are
compared as an ID multiset (count preserved, order ignored); info stays a
presence-only subset check (info_required), plus an explicit
info_forbidden check where the fixture's whole point is default silence.
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
AUDITOR = REPO / "tools" / "audit_job_dependencies.py"
FIXTURES_DIR = REPO / "tests" / "fixtures" / "jobs"
OUT = FIXTURES_DIR / "vaults"
MANIFEST = FIXTURES_DIR / "manifest.yaml"
RUN_DATE = "2026-08-30"

sys.path.insert(0, str(FIXTURES_DIR))
import build_job_fixtures as bjf  # noqa: E402


def run_auditor(vault_dir: Path, boot: Path, show_optional: bool = False) -> dict:
    cmd = [sys.executable, str(AUDITOR), str(vault_dir), "--boot", str(boot),
           "--repo", str(REPO), "--today", RUN_DATE, "--json"]
    if show_optional:
        cmd.append("--show-optional")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO))
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("auditor produced non-JSON output for %s\nstdout=%s\nstderr=%s"
                           % (vault_dir, proc.stdout, proc.stderr))


def finding_ids(findings) -> set:
    return {f["id"] for f in findings}


def ids_multiset(findings) -> list:
    return sorted(Counter(f["id"] for f in findings).elements())


def check_fixture(fixture: dict, boot: Path) -> list[str]:
    problems = []
    name = "%s-%s" % (fixture["id"], fixture["name"])
    vault_dir = OUT / name
    try:
        report = run_auditor(vault_dir, boot)
    except Exception as exc:  # noqa: BLE001
        return ["%s: %s" % (name, exc)]

    def cmp(label, actual, expected):
        if actual != expected:
            problems.append("%s: %s %r != expected %r" % (name, label, actual, expected))

    cmp("vault_state", report["vault_state"], fixture["vault_state"])
    cmp("jobs_examined", report["jobs_examined"], fixture["jobs_examined"])
    cmp("jobs_blocked", report["jobs_blocked"], fixture["jobs_blocked"])
    cmp("errors", ids_multiset(report["errors"]), sorted(fixture["errors"]))
    cmp("warnings", ids_multiset(report["warnings"]), sorted(fixture["warnings"]))
    info_ids = finding_ids(report["information"])
    missing_info = [x for x in fixture.get("info_required", []) if x not in info_ids]
    if missing_info:
        problems.append("%s: required infos missing %r (have %s)" % (name, missing_info, sorted(info_ids)))
    forbidden_present = [x for x in fixture.get("info_forbidden", []) if x in info_ids]
    if forbidden_present:
        problems.append("%s: forbidden infos present (should be silent by default) %r" % (name, forbidden_present))

    if "show_optional_check" in fixture:
        report2 = run_auditor(vault_dir, boot, show_optional=True)
        info_ids2 = finding_ids(report2["information"])
        missing2 = [x for x in fixture["show_optional_check"].get("info_required", []) if x not in info_ids2]
        if missing2:
            problems.append("%s (--show-optional): required infos missing %r (have %s)" % (name, missing2, sorted(info_ids2)))
    return problems


def main():
    n = bjf.build_all()
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    boot = (FIXTURES_DIR / manifest["boot"]).resolve()
    problems = []
    for fixture in manifest["fixtures"]:
        problems += check_fixture(fixture, boot)

    print("job-dependency fixtures: %d" % n)
    for fixture in manifest["fixtures"]:
        name = "%s-%s" % (fixture["id"], fixture["name"])
        status = "PASS"
        for p in problems:
            if p.startswith(name + ":") or p.startswith(name + " ("):
                status = "FAIL"
                break
        print("  [%s] %s" % (status, name))

    print("\nTable-row coverage (mechanically checked by this harness):")
    print("  missing / superseded / candidate-under-claim / malformed declaration / stale /")
    print("  current PASS / operational-degraded-disclosed / supersession cycle / malformed target")
    print("  frontmatter / disputed vocabulary / Preferred-never-blocks / Optional-silent-by-default /")
    print("  unrelated-Job-isolation / non-Job Jobs-folder note")
    print("Not covered (by design, requires AI): 'two current notes contradict with no supersession'")
    print("  — semantic judgment, never converted into a heuristic. See tools/audit_job_dependencies.py docstring.")

    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nAll job-dependency fixture expectations met.")


if __name__ == "__main__":
    main()
