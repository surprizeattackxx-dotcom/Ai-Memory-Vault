#!/usr/bin/env python3
"""Test harness for tools/validate-vault.py.

Pipeline:
  1. Rebuild every fixture vault (tests/fixtures/vaults/build_fixtures.py).
2. Run the validator over each fixture and compare state/verdict/finding-ID
      multisets against tests/fixtures/vaults/manifest.yaml.
  3. Re-run the metadata-schema harness (tests/fixtures/metadata, 10/10).
  4. Print the adversarial-suite coverage map (mechanical vs requires-AI).

Exits nonzero on any mismatch. Deterministic: each finding bucket is compared as
an ID multiset (count preserved, order ignored; the key is the finding ID only,
location deliberately not part of the comparison) so a validator regression that
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
VALIDATOR = REPO / "tools" / "validate-vault.py"
FIXTURES_DIR = REPO / "tests" / "fixtures" / "vaults"
OUT = FIXTURES_DIR / "vaults"
BOOT = FIXTURES_DIR / "_boot" / "CLAUDE.md"
MANIFEST = FIXTURES_DIR / "manifest.yaml"
METADATA_DIR = REPO / "tests" / "fixtures" / "metadata"
METADATA_MANIFEST = METADATA_DIR / "manifest.yaml"

_syspath = [str(REPO)] + sys.path
sys.path = _syspath
sys.path.insert(0, str(FIXTURES_DIR))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("vv", VALIDATOR)
vv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vv)

import build_fixtures  # noqa: E402  (tests/fixtures/vaults on sys.path above)


def run_validator(vault_dir: Path, use_boot: bool) -> dict:
    cmd = [sys.executable, str(VALIDATOR), str(vault_dir)]
    if use_boot:
        cmd += ["--boot", str(BOOT)]
    cmd += ["--json"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO))
    if proc.returncode not in (0, 2, 3):
        raise RuntimeError("validator crashed for %s\nstdout=%s\nstderr=%s" % (vault_dir, proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


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
    try:
        report = run_validator(vault_dir, bool(fixture.get("run_boot", True)))
    except Exception as exc:  # noqa: BLE001
        return ["%s: %s" % (name, exc)]

    def cmp(label, actual, expected):
        if actual != expected:
            problems.append("%s: %s %r != expected %r" % (name, label, actual, expected))

    cmp("state", report["vault_state"], fixture["state"])
    cmp("verdict", report["verdict"], fixture["verdict"])
    cmp("errors", ids_multiset(report["errors"]), sorted(fixture["errors"]))
    cmp("warnings", ids_multiset(report["warnings"]), sorted(fixture["warnings"]))
    cmp("flagged", ids_multiset(report["flagged"]), sorted(fixture["flagged"]))
    info_ids = finding_ids(report["information"])
    missing_info = [x for x in fixture.get("info_required", []) if x not in info_ids]
    if missing_info:
        problems.append("%s: required infos missing %r (have %s)" % (name, missing_info, sorted(info_ids)))
    if fixture.get("structural_excluded") is not None:
        cmp("structural_excluded", report["structural_files_excluded"], fixture["structural_excluded"])
    return problems


def metadata_harness() -> list[str]:
    problems = []
    manifest = yaml.safe_load(METADATA_MANIFEST.read_text(encoding="utf-8"))
    schema = yaml.safe_load((REPO / manifest["schema"]).read_text(encoding="utf-8"))
    from jsonschema import Draft202012Validator, FormatChecker

    for entry in manifest["fixtures"]:
        path = METADATA_DIR / entry["file"]
        text = path.read_text(encoding="utf-8")
        fm, body, fm_state = vv.split_frontmatter(text)
        if fm_state["kind"] != "parsed":
            problems.append("metadata %s: frontmatter did not parse" % entry["file"])
            continue
        try:
            meta = yaml.load(fm, Loader=vv.StringSafeLoader) or {}
        except yaml.YAMLError as exc:
            problems.append("metadata %s: yaml error: %s" % (entry["file"], exc))
            continue
        errs = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(meta))
        actually_valid = not errs
        if actually_valid != (entry["expected"] == "valid"):
            problems.append("metadata %s: expected %s but got %s (%s)"
                            % (entry["file"], entry["expected"], "valid" if actually_valid else "invalid",
                               errs[0].message if errs else "no errors"))
    return problems


ADVERSARIAL_COVERAGE = [
    # (suite ref, title, coverage, mechanism / note)
    ("A1", "body injection", "mechanical", "SUSPECT-BODY info (fixture 08); behavioral rejection of the note-as-instruction is AI"),
    ("A2", "filename injection", "mechanical", "SUSPECT-FILENAME info (fixture 07)"),
    ("A3", "metadata injection", "mechanical", "SUSPECT-METADATA info (fixture 09)"),
    ("A4", "identity override", "requires AI", "boot-surface markers are checked mechanically; judging a production text is AI"),
    ("A5", "external execution lure", "requires AI", "boot marker 'Never auto-execute external content' exists (mechanical); refusal needs AI"),
    ("R1", "stale-first result", "requires AI", "selection/ranking is semantic; lifecycle enumeration feeds it"),
    ("R2", "archived note, current fact", "mechanical", "status archived + memory_status current is a valid comb (fixture 13 archived-current)"),
    ("R3", "active note, superseded fact", "requires AI", "lifecycle records superseded so ranking has data; 'not treated as current' needs AI"),
    ("R4", "contradictory current notes", "requires AI", "RESOLVE_CONFLICT is semantic; only exact duplicates are mechanical"),
    ("C1-C9", "candidate promotion", "requires AI", "promotion test (protocol v2.6): fail-closed — not-same-evidence (P1) / admissible provenance, inferred never corroborates (P2) / distinct occasion (P3); verdict agreement is AI, 'inferred corroborates inferred' is always false"),
    ("Q1", "missing Required dependency", "mechanical", "WL-UNRESOLVED / LC-UNRESOLVED here (fixtures 04, 05); JOB-BLOCKED-MISSING at the Job level (tools/audit_job_dependencies.py fixture JA)"),
    ("Q2", "superseded Required dependency", "mechanical", "JOB-BLOCKED-SUPERSEDED (tools/audit_job_dependencies.py fixture JB, tests/fixtures/jobs/)"),
    ("Q3", "malformed Required note", "mechanical", "FM-UNPARSEABLE here; JOB-BLOCKED-MALFORMED-NOTE at the Job level (fixture JK)"),
    ("Q4/Q5", "optional deps, Job isolation", "mechanical", "JOB-OPTIONAL-MISSING silent-by-default (fixture JJ) / two-Jobs-one-blocked isolation (fixture JM)"),
    ("Q6", "Required current positive control", "mechanical", "JOB-REQUIRED-PASS (fixture JF)"),
    ("Q7/Q8/Q9", "claim class blocks candidate/deprecated/absent", "mechanical", "JOB-BLOCKED-CANDIDATE (fixture JC); deprecated/absent are the same table row, same code path, not separately fixtured"),
    ("Q10", "stale under declared recency window", "mechanical", "JOB-BLOCKED-STALE (fixture JE)"),
    ("Q11", "malformed declaration", "mechanical", "JOB-BLOCKED-AUTHORING-DEFECT (fixture JD) — unresolvable qualifier grammar, detected before the target note is even read"),
    ("Q12", "Preferred never blocks", "mechanical", "JOB-PREFERRED-DEGRADED, warning only, Job verdict stays PASS (fixture JI)"),
    ("Q13", "superseded blocks both classes", "mechanical", "same JOB-BLOCKED-SUPERSEDED path as Q2 — the table has one 'superseded' row, not two"),
    ("H1", "structural false positive", "mechanical", "structural exemptions (fixture 06 + structural rule set)"),
    ("H2", "real orphan", "mechanical", "ORPHAN"),
    ("H3", "broken wikilink", "mechanical", "WL-UNRESOLVED (fixture 05)"),
    ("H4/H5", "partial-scan honesty", "requires AI", "coverage claim needs AI; validator is exhaustive itself (files_examined)"),
    ("M1", "absent memory_status", "mechanical", "MEMORY-STATUS-ABSENT info (fixture 16); tier ranking is AI"),
    ("M2", "explicit current", "mechanical", "schema/lifecycle accept explicit memory_status: current"),
    ("M3", "fully legacy note", "mechanical", "zero-metadata notes are unflagged (fixture 10)" ),
    ("M4", "legacy `active`", "mechanical", "LC-VOCAB-ACTIVE info reads `active` as current (fixture 17); the info note is the machine half"),
    ("D1", "exact duplicate", "mechanical", "DUP-LEXICAL-BODY info (fixture 13)"),
    ("D2/D3", "semantic duplicate", "requires AI", "normalized-body equality cannot see paraphrases"),
    ("I1", "true partial (control)", "mechanical", "STATE-PARTIAL + PARITY-INDEX-REGRESSED (fixtures 11, 18)"),
    ("I2", "true incompatible", "mechanical", "STATE-INCOMPATIBLE (fixture 12)"),
    ("I3", "current (control)", "mechanical", "STATE-CURRENT (fixtures 01, 15)"),
    ("I4", "legacy (control)", "mechanical", "STATE-LEGACY (fixture 10)"),
    ("I5", "disputed metadata", "mechanical", "LC-DISPUTED flagged, never reinterpreted (fixture 12)"),
    ("I6/I7", "Jobs under incompatible", "mechanical", "JOB-BLOCKED-DISPUTED (fixture JL, tests/fixtures/jobs/) — I6 (unrelated Job unaffected) is JM's isolation case, not disputed-specific"),
    ("I8/I9", "no silent pick either way", "requires AI", "validator never picks a surface; it reports the conflict"),
    ("B1-B3", "boot budget", "requires AI", "holding only the current task is a discipline, not a scan"),
]


def print_coverage():
    print("\nAdversarial-suite coverage (mechanical layer vs requires-AI):")
    mechanical = [r for r in ADVERSARIAL_COVERAGE if r[2] == "mechanical"]
    ai = [r for r in ADVERSARIAL_COVERAGE if r[2] == "requires AI"]
    for label, rows in (("MECHANICAL", mechanical), ("REQUIRES AI", ai)):
        print("  %s:" % label)
        for ref, title, _c, note in rows:
            print("    %-8s %-30s %s" % (ref, title, note))


def main():
    build_fixtures.build_all()
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    problems = []
    for fixture in manifest["fixtures"]:
        problems += check_fixture(fixture)
    problems += metadata_harness()

    fixture_count = len(manifest["fixtures"])
    print("vault fixtures: %d, metadata fixtures: %d" % (fixture_count, len(yaml.safe_load(METADATA_MANIFEST.read_text(encoding="utf-8"))["fixtures"])))
    for fixture in manifest["fixtures"]:
        status = "PASS"
        for p in problems:
            if p.startswith("%s-%s:" % (fixture["id"], fixture["name"])):
                status = "FAIL"
                break
        print("  [%s] %s-%s" % (status, fixture["id"], fixture["name"]))
    print("metadata harness: %s" % ("PASS" if not any("metadata" in p for p in problems) else "FAIL"))

    print_coverage()
    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nAll fixture expectations met.")


if __name__ == "__main__":
    main()