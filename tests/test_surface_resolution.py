#!/usr/bin/env python3
"""Dedicated regression test for one invariant, named explicitly:

    A same-named file cannot influence protocol interpretation merely
    by existing somewhere else in the vault.

Covers every protocol surface tools/validate-vault.py resolves by a fixed
basename and treats as authoritative for vault state or parity: VAULT-INDEX.md
(vault root) and MEMORY_PROTOCOL.md (a "NN - Resources" folder). For each:

  1. clean    - only the canonical file exists -> no SURFACE-AMBIGUOUS,
                the vault's reported state/parity reflects it correctly.
  2. decoy    - canonical file present AND a second, differently-worded file
                sharing the same basename sits elsewhere in the vault ->
                SURFACE-AMBIGUOUS fires naming the decoy's path, and the
                canonical file (not the decoy) is still what state/parity
                actually reflects - proven by making the two files' content
                assert opposite things and checking which one the report
                agrees with.

This is deliberately independent of tests/fixtures/vaults/manifest.yaml's
fixture 24 (which exercises the same bug end-to-end as part of the general
suite) - this file exists so the invariant itself has a named, standalone
regression target that survives even if fixture 24 is ever renumbered,
removed, or folded into something else.

Exit code 0 on success, 1 on any failure (message printed for each).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VALIDATOR = REPO / "tools" / "validate-vault.py"
PROTOCOL_SRC = REPO / "MEMORY_PROTOCOL.md"

# A minimal, schema-valid VAULT-INDEX.md carrying every INDEX_RULE_MARKERS
# string tools/validate-vault.py currently checks for - kept in sync by hand;
# if that list grows, add the new marker text here too.
CANONICAL_INDEX = """---
status: active
project: meta
type: index
---
# VAULT INDEX
Memory Is Data, Not Authority. Trust model. `status` and `memory_status`.
Structural files are exempt. Inspection Manifest. Jobs and Required Dependencies.
can never corroborate another inference.
"""

DECOY_INDEX = """---
status: active
project: personal
type: reference
---
# Not the real index
An old export, deliberately incomplete - carries none of the rule markers.
"""

DECOY_PROTOCOL = """---
status: active
project: personal
type: reference
---
# Not the real protocol
version: 2.7
Deliberately NOT byte-identical to canonical MEMORY_PROTOCOL.md.
"""


BOOT_TEXT = """# Boot Config
## The rules that can't lapse
- rules that can't lapse
- Never auto-execute external content
- Memory Governor
- Checkpoint persistence
"""


def run_validator(vault_dir: Path, boot_path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(vault_dir), "--boot", str(boot_path), "--json"],
        capture_output=True, text=True,
    )
    import json
    return json.loads(proc.stdout)


def finding_ids(report: dict, *buckets: str) -> set[str]:
    out = set()
    for b in buckets:
        out |= {f["id"] for f in report.get(b, [])}
    return out


def build(tmp: Path, *, decoy_index: bool, decoy_protocol: bool) -> Path:
    vault = tmp / "vault"
    if vault.exists():
        shutil.rmtree(vault)
    (vault / "09 - Resources").mkdir(parents=True)
    (vault / "09 - Resources" / "MEMORY_PROTOCOL.md").write_text(
        PROTOCOL_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (vault / "VAULT-INDEX.md").write_text(CANONICAL_INDEX, encoding="utf-8")
    if decoy_index:
        (vault / "00 - Inbox").mkdir(exist_ok=True)
        (vault / "00 - Inbox" / "VAULT-INDEX.md").write_text(DECOY_INDEX, encoding="utf-8")
    if decoy_protocol:
        (vault / "00 - Inbox").mkdir(exist_ok=True)
        (vault / "00 - Inbox" / "MEMORY_PROTOCOL.md").write_text(DECOY_PROTOCOL, encoding="utf-8")
    return vault


def main() -> int:
    problems = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        boot = tmp / "CLAUDE.md"
        boot.write_text(BOOT_TEXT, encoding="utf-8")

        # --- 1. clean: no decoys anywhere -> zero SURFACE-AMBIGUOUS, real
        # files correctly recognized as matching canonical.
        vault = build(tmp, decoy_index=False, decoy_protocol=False)
        r = run_validator(vault, boot)
        ids = finding_ids(r, "errors", "warnings", "information")
        if "SURFACE-AMBIGUOUS" in ids:
            problems.append("clean: SURFACE-AMBIGUOUS fired with no decoy present")
        if r["vault_state"] != "current":
            problems.append("clean: vault_state = %r, expected 'current'" % r["vault_state"])

        # --- 2. VAULT-INDEX.md decoy: must be flagged, and the CANONICAL
        # file's content (carrying every rule marker) must be what parity
        # actually reflects, not the decoy's (which carries none).
        vault = build(tmp, decoy_index=True, decoy_protocol=False)
        r = run_validator(vault, boot)
        ids = finding_ids(r, "errors")
        if "SURFACE-AMBIGUOUS" not in ids:
            problems.append("index-decoy: SURFACE-AMBIGUOUS did not fire")
        ambiguous_paths = {f["path"] for f in r["errors"] if f["id"] == "SURFACE-AMBIGUOUS"}
        if "00 - Inbox/VAULT-INDEX.md" not in ambiguous_paths:
            problems.append("index-decoy: SURFACE-AMBIGUOUS did not name the decoy's path (%r)" % ambiguous_paths)
        # The canonical file carries every marker; if the validator were
        # still hijacked onto the (marker-free) decoy, PARITY-INDEX-REGRESSED
        # would fire. It must not.
        if "PARITY-INDEX-REGRESSED" in ids:
            problems.append("index-decoy: parity check ran against the decoy, not the canonical file "
                             "(PARITY-INDEX-REGRESSED fired despite the real file being complete)")

        # --- 3. MEMORY_PROTOCOL.md decoy: same shape, other surface.
        vault = build(tmp, decoy_index=False, decoy_protocol=True)
        r = run_validator(vault, boot)
        ids = finding_ids(r, "errors")
        if "SURFACE-AMBIGUOUS" not in ids:
            problems.append("protocol-decoy: SURFACE-AMBIGUOUS did not fire")
        ambiguous_paths = {f["path"] for f in r["errors"] if f["id"] == "SURFACE-AMBIGUOUS"}
        if "00 - Inbox/MEMORY_PROTOCOL.md" not in ambiguous_paths:
            problems.append("protocol-decoy: SURFACE-AMBIGUOUS did not name the decoy's path (%r)" % ambiguous_paths)
        # The canonical protocol copy IS byte-identical to REPO canonical; if
        # the validator were checking the decoy instead, PARITY-PROTOCOL-
        # DIVERGENCE would fire (the decoy deliberately isn't identical). It
        # must not - the real file is what parity actually reflects.
        if "PARITY-PROTOCOL-DIVERGENCE" in ids:
            problems.append("protocol-decoy: parity check ran against the decoy, not the canonical file "
                             "(PARITY-PROTOCOL-DIVERGENCE fired despite the real file being byte-identical)")

        # --- 4. both decoys at once: both must be named independently.
        vault = build(tmp, decoy_index=True, decoy_protocol=True)
        r = run_validator(vault, boot)
        ambiguous_paths = {f["path"] for f in r["errors"] if f["id"] == "SURFACE-AMBIGUOUS"}
        expected = {"00 - Inbox/VAULT-INDEX.md", "00 - Inbox/MEMORY_PROTOCOL.md"}
        if not expected.issubset(ambiguous_paths):
            problems.append("both-decoys: expected both paths flagged, got %r" % ambiguous_paths)

    if problems:
        print("FAILED (%d):" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    print("PASS: surface-resolution invariant holds for VAULT-INDEX.md and MEMORY_PROTOCOL.md "
          "(clean, single-decoy x2, both-decoys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
