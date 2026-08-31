#!/usr/bin/env python3
"""Dedicated regression suite for the conflict-detection layer
(tools/memory_conflict.py), built on top of tools/memory_runtime.py.

Standalone, self-contained, no pytest — matches tests/test_memory_runtime.py's
own convention: run directly (`python tests/test_memory_conflict.py`),
collects a problems list, prints PASS/FAILED, exits 0/1.

Covers the 11 requested adversarial scenarios plus two extras (duplicate-
reference collapsing, and cycle-membership precision — a note can share a
cycle with the anchor without ever being incorrectly paired against it if no
direct edge connects them). Every vault here is a fresh temp directory built
from tests/fixtures/vaults/build_fixtures.py's base_files() scaffolding, same
as test_memory_runtime.py, so this suite stays correct even if other fixture
sets are renumbered.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
MAIN_FIXTURES = REPO / "tests" / "fixtures" / "vaults"

sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(MAIN_FIXTURES))

import memory_runtime as rt  # noqa: E402
import memory_conflict as mc  # noqa: E402
import build_fixtures as bf  # noqa: E402

PROBLEMS = []


def check(label: str, condition: bool, detail: str = ""):
    if not condition:
        PROBLEMS.append("%s%s" % (label, (": " + detail) if detail else ""))


def note(memory_status=None, extra="", title="Note", project="personal"):
    lines = ["---", "status: active", "project: %s" % project, "type: reference"]
    if memory_status:
        lines.append("memory_status: %s" % memory_status)
    if extra:
        lines.append(extra.strip())
    lines += ["---", "# %s" % title, ""]
    return "\n".join(lines)


def build_vault(tmp: Path, name: str, files: dict) -> Path:
    root = tmp / name
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return root


def file_hashes(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def find(conflicts, path_a, path_b):
    """Locate the conflict entry for an unordered pair, regardless of which
    side memory_a/memory_b ended up sorted to."""
    for c in conflicts:
        paths = {c.memory_a.note_path, c.memory_b.note_path}
        if paths == {path_a, path_b}:
            return c
    return None


def check_common_shape(conflicts):
    for c in conflicts:
        check("shape: category is one of the five", c.category in mc.CATEGORIES, "got %r" % c.category)
        check("shape: confidence is a declared value", c.confidence in mc.CONFIDENCE_VALUES, "got %r" % c.confidence)
        check("shape: both canonical paths present", bool(c.memory_a.note_path) and bool(c.memory_b.note_path))
        check("shape: both lifecycle states present (status_track non-empty)",
              bool(c.memory_a.status_track) and bool(c.memory_b.status_track))
        check("shape: detection_method present", bool(c.detection_method))
        check("shape: recommended_action present", bool(c.recommended_action))
        check("shape: evidence present", bool(c.evidence))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # ============================================================
        # 1 — current vs candidate contradiction (also exercises
        #     path-qualified resolution — scenario 7)
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/pref.md"] = note("current", "last_confirmed: 2026-08-29", "Pref Current")
        files["00 - Inbox/pref.md"] = note("candidate", "source: inferred\nconfidence: low", "Pref Guess")
        v1 = build_vault(tmp, "v1", files)
        rt1 = rt.MemoryRuntime(v1)
        rep1 = mc.detect_conflicts(rt1, "09 - Resources/pref")
        check("1. path-qualified query resolves (status=resolved)", rep1.status == "resolved", "got %r" % (rep1.status,))
        c1 = find(rep1.conflicts, "09 - Resources/pref.md", "00 - Inbox/pref.md")
        check("1. current-vs-candidate pair found", c1 is not None, "got %r" % ([x.memory_a.note_path for x in rep1.conflicts],))
        check("1. current-vs-candidate -> potentially_conflicting",
              c1 is not None and c1.category == "potentially_conflicting", "got %r" % (c1,))
        check("1. correct detection_method",
              c1 is not None and c1.detection_method == "identity-collision-current-vs-candidate", "got %r" % (c1,))
        check_common_shape(rep1.conflicts)

        # ============================================================
        # 2 — current vs superseded contradiction (no declared link)
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/fact.md"] = note("current", "last_confirmed: 2026-08-29", "Fact Current")
        files["00 - Inbox/fact.md"] = note("superseded", title="Fact Old, No Link")
        v2 = build_vault(tmp, "v2", files)
        rt2 = rt.MemoryRuntime(v2)
        rep2 = mc.detect_conflicts(rt2, "09 - Resources/fact")
        c2 = find(rep2.conflicts, "09 - Resources/fact.md", "00 - Inbox/fact.md")
        check("2. current-vs-superseded (no link) -> potentially_conflicting",
              c2 is not None and c2.category == "potentially_conflicting", "got %r" % (c2,))
        check("2. correct detection_method",
              c2 is not None and c2.detection_method == "identity-collision-current-vs-superseded-no-link",
              "got %r" % (c2,))
        check_common_shape(rep2.conflicts)

        # ============================================================
        # 3 — two current conflicting memories
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/dup.md"] = note("current", "last_confirmed: 2026-08-29", "Dup A")
        files["00 - Inbox/dup.md"] = note("current", "last_confirmed: 2026-08-29", "Dup B")
        v3 = build_vault(tmp, "v3", files)
        rt3 = rt.MemoryRuntime(v3)
        rep3 = mc.detect_conflicts(rt3, "09 - Resources/dup")
        c3 = find(rep3.conflicts, "09 - Resources/dup.md", "00 - Inbox/dup.md")
        check("3. two-current -> potentially_conflicting", c3 is not None and c3.category == "potentially_conflicting",
              "got %r" % (c3,))
        check("3. correct detection_method", c3 is not None and c3.detection_method == "identity-collision-both-current",
              "got %r" % (c3,))
        check_common_shape(rep3.conflicts)

        # ============================================================
        # 4 — two candidates conflicting
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/guess.md"] = note("candidate", "source: inferred\nconfidence: low", "Guess A")
        files["00 - Inbox/guess.md"] = note("candidate", "source: inferred\nconfidence: low", "Guess B")
        v4 = build_vault(tmp, "v4", files)
        rt4 = rt.MemoryRuntime(v4)
        rep4 = mc.detect_conflicts(rt4, "09 - Resources/guess")
        c4 = find(rep4.conflicts, "09 - Resources/guess.md", "00 - Inbox/guess.md")
        check("4. two-candidates -> potentially_conflicting", c4 is not None and c4.category == "potentially_conflicting",
              "got %r" % (c4,))
        check("4. correct detection_method",
              c4 is not None and c4.detection_method == "identity-collision-two-candidates", "got %r" % (c4,))
        check_common_shape(rep4.conflicts)

        # ============================================================
        # 5 — unrelated memories sharing keywords: must cap at `related`,
        #     never escalate to potentially_conflicting/confirmed_conflict
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/apple-a.md"] = note("current", title="Apple A") + "\nThe quokka eats apples in the afternoon.\n"
        files["00 - Inbox/apple-b.md"] = note("current", title="Apple B") + "\nA different quokka story about apples entirely.\n"
        v5 = build_vault(tmp, "v5", files)
        rt5 = rt.MemoryRuntime(v5)
        rep5 = mc.detect_conflicts(rt5, "apples")
        c5 = find(rep5.conflicts, "00 - Inbox/apple-a.md", "00 - Inbox/apple-b.md")
        check("5. shared-keyword pair found", c5 is not None, "got %r" % (rep5.conflicts,))
        check("5. shared-keyword -> related, never higher",
              c5 is not None and c5.category == "related", "got %r" % (c5,))
        check("5. correct detection_method", c5 is not None and c5.detection_method == "shared-keyword", "got %r" % (c5,))
        check_common_shape(rep5.conflicts)

        # ============================================================
        # 6/7 — same-stem decoys + path-qualified resolution: the declared
        # path target must be the one actually analyzed, never the decoy
        # that happens to be `current`. Mirrors memory_runtime's own 3b.
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/required-source.md"] = note("current", "last_confirmed: 2026-08-29", "Decoy Current")
        files["09 - Resources/required-source.md"] = note("superseded", title="Declared Target Superseded")
        v6 = build_vault(tmp, "v6", files)
        rt6 = rt.MemoryRuntime(v6)
        rep6 = mc.detect_conflicts(rt6, "09 - Resources/required-source")
        check("6/7. path-qualified query resolves to specific pair",
              {c_.memory_a.note_path for c_ in rep6.conflicts} | {c_.memory_b.note_path for c_ in rep6.conflicts}
              == {"00 - Inbox/required-source.md", "09 - Resources/required-source.md"}
              if rep6.conflicts else False,
              "got %r" % (rep6.conflicts,))
        c6 = find(rep6.conflicts, "00 - Inbox/required-source.md", "09 - Resources/required-source.md")
        declared_side = c6.memory_a if c6 and c6.memory_a.note_path == "09 - Resources/required-source.md" else (c6.memory_b if c6 else None)
        decoy_side = c6.memory_a if c6 and c6.memory_a.note_path == "00 - Inbox/required-source.md" else (c6.memory_b if c6 else None)
        check("6/7. declared (superseded) target correctly labeled, decoy never substituted for it",
              declared_side is not None and declared_side.status_track == "superseded", "got %r" % (c6,))
        check("6/7. decoy correctly labeled current on its own side",
              decoy_side is not None and decoy_side.status_track == "current", "got %r" % (c6,))
        check("6/7. security: resolve() itself lands on the declared path, not the decoy",
              rt6.resolve("09 - Resources/required-source").note_path == "09 - Resources/required-source.md")
        check_common_shape(rep6.conflicts)

        # ============================================================
        # 8 — ambiguous identity: bare stem shared by 2+ notes, no path
        # qualifier -> fails closed into insufficient_evidence, never a guess
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/twin.md"] = note("current", "last_confirmed: 2026-08-29", "Twin A")
        files["02 - Projects/twin.md"] = note("current", "last_confirmed: 2026-08-29", "Twin B")
        v8 = build_vault(tmp, "v8", files)
        rt8 = rt.MemoryRuntime(v8)
        rep8 = mc.detect_conflicts(rt8, "twin")
        check("8. ambiguous identity -> status=ambiguous", rep8.status == "ambiguous", "got %r" % (rep8.status,))
        check("8. ambiguous candidates lists both paths",
              set(rep8.candidates) == {"00 - Inbox/twin.md", "02 - Projects/twin.md"}, "got %r" % (rep8.candidates,))
        check("8. exactly one pair reported for 2 ambiguous candidates", len(rep8.conflicts) == 1,
              "got %d" % len(rep8.conflicts))
        check("8. ambiguous pair -> insufficient_evidence, never a guess",
              rep8.conflicts and rep8.conflicts[0].category == "insufficient_evidence", "got %r" % (rep8.conflicts,))
        check("8. correct detection_method",
              rep8.conflicts and rep8.conflicts[0].detection_method == "ambiguous-identity", "got %r" % (rep8.conflicts,))
        check_common_shape(rep8.conflicts)

        # ============================================================
        # 9 — duplicate references: a note matched via multiple retrieval
        # methods must never be paired against itself, and a genuinely
        # isolated note (no siblings) produces zero conflicts, not a
        # self-pair.
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/zephyrnote.md"] = note("current", "last_confirmed: 2026-08-29", "Zephyrnote") + "\nzephyrnote zephyrnote.\n"
        files["02 - Projects/zephyrnote.md"] = note("candidate", "source: inferred\nconfidence: low", "Zephyrnote Sibling")
        v9 = build_vault(tmp, "v9", files)
        rt9 = rt.MemoryRuntime(v9)
        rep9 = mc.detect_conflicts(rt9, "zephyrnote", methods=("filename", "text"))
        check("9. duplicate-method match collapses to exactly one pair (no self-pair)",
              len(rep9.conflicts) == 1, "got %d: %r" % (len(rep9.conflicts), rep9.conflicts))
        check("9. no conflict entry ever pairs a note with itself",
              all(c.memory_a.note_path != c.memory_b.note_path for c in rep9.conflicts))

        files_lonely = bf.base_files()
        files_lonely["00 - Inbox/lonely.md"] = note("current", "last_confirmed: 2026-08-29", "Lonely") + "\nlonely lonely.\n"
        v9b = build_vault(tmp, "v9b", files_lonely)
        rt9b = rt.MemoryRuntime(v9b)
        rep9b = mc.detect_conflicts(rt9b, "lonely", methods=("filename", "text"))
        check("9b. isolated note matched via multiple methods -> zero conflicts, never a self-pair",
              rep9b.conflicts == (), "got %r" % (rep9b.conflicts,))
        check_common_shape(rep9.conflicts)

        # ============================================================
        # 10 — conflict involving a disputed memory: must fail closed into
        # insufficient_evidence regardless of what the lifecycle states
        # would otherwise suggest.
        # ============================================================
        files = bf.base_files()
        old_bullet = (
            "- **`memory_status`** — `active` (a current, working fact) | `stale` "
            "(was current, not recent) | `archived` (a fact no longer operative, kept for history)."
        )
        files["VAULT-INDEX.md"] = files["VAULT-INDEX.md"].replace(bf.MEMORY_STATUS_BULLET, old_bullet)
        files["00 - Inbox/disputed-thing.md"] = note("active", title="Disputed Thing")
        files["09 - Resources/disputed-thing.md"] = note("current", "last_confirmed: 2026-08-29", "Disputed Thing Current")
        v10 = build_vault(tmp, "v10", files)
        rt10 = rt.MemoryRuntime(v10)
        check("10. fixture actually produces disputed vocabulary (sanity check)",
              "active" in rt10.vault.disputed_terms, "got %r" % (rt10.vault.disputed_terms,))
        rep10 = mc.detect_conflicts(rt10, "09 - Resources/disputed-thing")
        c10 = find(rep10.conflicts, "09 - Resources/disputed-thing.md", "00 - Inbox/disputed-thing.md")
        check("10. disputed pair -> insufficient_evidence (fails closed, never guesses)",
              c10 is not None and c10.category == "insufficient_evidence", "got %r" % (c10,))
        check("10. correct detection_method", c10 is not None and c10.detection_method == "disputed-vocabulary",
              "got %r" % (c10,))
        check_common_shape(rep10.conflicts)

        # ============================================================
        # 11 — detection never mutates the vault
        # ============================================================
        before = file_hashes(v6)
        mc.detect_conflicts(rt6, "09 - Resources/required-source")
        mc.detect_conflicts(rt6, "required-source")
        mc.detect_conflicts(rt6, "nonexistent-thing")
        mc.detect_conflicts(rt6, "")
        after = file_hashes(v6)
        check("11. detect_conflicts never mutates the vault", before == after,
              "changed: %r" % ({k for k in before if before.get(k) != after.get(k)},))

        # ============================================================
        # EXTRA — cycle precision: a note sharing a cycle with the anchor
        # is only ever paired with it if a DIRECT edge connects them; cycle
        # *membership* alone is never enough (4-cycle: X->Y->Z->W->X; X's
        # only direct neighbors are Y and W, never Z).
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/x.md"] = note("current", 'supersedes: "[[y]]"', "X")
        files["00 - Inbox/y.md"] = note("current", 'supersedes: "[[z]]"', "Y")
        files["00 - Inbox/z.md"] = note("current", 'supersedes: "[[w]]"', "Z")
        files["00 - Inbox/w.md"] = note("current", 'supersedes: "[[x]]"', "W")
        v_cycle = build_vault(tmp, "v-cycle", files)
        rt_cycle = rt.MemoryRuntime(v_cycle)
        rep_cycle = mc.detect_conflicts(rt_cycle, "x")
        cxy = find(rep_cycle.conflicts, "00 - Inbox/x.md", "00 - Inbox/y.md")
        cxw = find(rep_cycle.conflicts, "00 - Inbox/x.md", "00 - Inbox/w.md")
        cxz = find(rep_cycle.conflicts, "00 - Inbox/x.md", "00 - Inbox/z.md")
        check("cycle: direct neighbor X<->Y -> confirmed_conflict",
              cxy is not None and cxy.category == "confirmed_conflict", "got %r" % (cxy,))
        check("cycle: direct neighbor X<->W -> confirmed_conflict",
              cxw is not None and cxw.category == "confirmed_conflict", "got %r" % (cxw,))
        check("cycle: correct detection_method",
              cxy is not None and cxy.detection_method == "supersession-cycle", "got %r" % (cxy,))
        check("cycle precision: non-adjacent cycle member Z is NEVER paired with anchor X",
              cxz is None, "got %r" % (cxz,))
        check_common_shape(rep_cycle.conflicts)

        # ============================================================
        # EXTRA — malformed frontmatter fails closed too
        # ============================================================
        files = bf.base_files()
        files["09 - Resources/ok-note.md"] = note("current", "last_confirmed: 2026-08-29", "Ok Note")
        files["00 - Inbox/ok-note.md"] = """---
status: active
project: personal
type: [broken
---
# Broken
"""
        v_mal = build_vault(tmp, "v-malformed", files)
        rt_mal = rt.MemoryRuntime(v_mal)
        rep_mal = mc.detect_conflicts(rt_mal, "09 - Resources/ok-note")
        c_mal = find(rep_mal.conflicts, "09 - Resources/ok-note.md", "00 - Inbox/ok-note.md")
        check("malformed frontmatter -> insufficient_evidence",
              c_mal is not None and c_mal.category == "insufficient_evidence", "got %r" % (c_mal,))
        check("malformed frontmatter correct detection_method",
              c_mal is not None and c_mal.detection_method == "malformed-frontmatter", "got %r" % (c_mal,))

        # ============================================================
        # EXTRA — clean, correctly reciprocated supersession is NOT a
        # conflict at all
        # ============================================================
        files = bf.base_files()
        files["00 - Inbox/old-fact.md"] = note("superseded", 'superseded_by: "[[new-fact]]"', "Old Fact")
        files["00 - Inbox/new-fact.md"] = note("current", 'supersedes: "[[old-fact]]"\nlast_confirmed: 2026-08-29', "New Fact")
        v_clean = build_vault(tmp, "v-clean", files)
        rt_clean = rt.MemoryRuntime(v_clean)
        rep_clean = mc.detect_conflicts(rt_clean, "new-fact")
        c_clean = find(rep_clean.conflicts, "00 - Inbox/old-fact.md", "00 - Inbox/new-fact.md")
        check("clean reciprocated supersession -> compatible, not a conflict",
              c_clean is not None and c_clean.category == "compatible", "got %r" % (c_clean,))
        check("clean supersession correct detection_method",
              c_clean is not None and c_clean.detection_method == "clean-reciprocated-supersession", "got %r" % (c_clean,))

        # ============================================================
        # EXTRA — never mutates: full re-check across every vault built in
        # this run, plus explicit assertion the module never opens a path
        # for writing.
        # ============================================================
        for label, root, runtime_obj, query in (
            ("v1", v1, rt1, "09 - Resources/pref"), ("v3", v3, rt3, "09 - Resources/dup"),
            ("v8", v8, rt8, "twin"), ("v_cycle", v_cycle, rt_cycle, "x"),
        ):
            b = file_hashes(root)
            mc.detect_conflicts(runtime_obj, query)
            a = file_hashes(root)
            check("mutation check (%s)" % label, b == a, "changed: %r" % ({k for k in b if b.get(k) != a.get(k)},))

        source_text = (TOOLS / "memory_conflict.py").read_text(encoding="utf-8")
        check("security: no method ever opens a path built directly from the query string (source check)",
              "open(" not in source_text and "write_text" not in source_text and "unlink" not in source_text)
        check("security: not_evaluated explicitly names semantic contradiction as out of scope",
              rep1.not_evaluated and "semantic" in rep1.not_evaluated[0])

        # ============================================================
        # Security: traversal-shaped queries never resolve, never escape
        # the vault, never crash.
        # ============================================================
        traversal_queries = ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\System32\\config\\SAM",
                              "..\\..\\..\\Windows", "..", "../"]
        for q in traversal_queries:
            target, ambiguous = mc.vid.resolve_identity(rt6.vault, q)
            check("security: exact identity resolution never resolves a traversal-shaped query %r" % q,
                  target is None and not ambiguous, "got %r" % ((target, ambiguous),))
            # A free-text hit on ordinary prose containing a word like "etc" is
            # expected (the `text` method is a legitimate keyword search, not
            # an identity resolver — same caveat test_memory_runtime.py's own
            # traversal test documents). What must never happen is a result
            # path escaping the vault root.
            rep_t = mc.detect_conflicts(rt6, q)
            for c_ in rep_t.conflicts:
                for path in (c_.memory_a.note_path, c_.memory_b.note_path):
                    resolved = (v6 / path).resolve()
                    check("security: every result for %r stays inside the vault root" % q,
                          str(resolved).startswith(str(v6.resolve())), "escaped to %r" % (resolved,))

    if PROBLEMS:
        print("FAILED (%d):" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  - %s" % p)
        return 1
    print("PASS: conflict detection — all 11 scenarios + duplicate-reference + cycle-precision "
          "+ malformed/clean extras + security checks hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
