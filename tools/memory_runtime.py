#!/usr/bin/env python3
"""Memory Runtime — the orchestration layer between an AI/agent and the vault.

Coordinates the full RETRIEVE shape MEMORY_PROTOCOL.md already specifies:

    query
      v
    retrieval (tools/memory_retrieval.py)          <- "search phase"
      v
    candidate collection
      v
    canonical note loading (tools/validate-vault.py's Vault)
      v
    lifecycle/status validation                     <- "validation phase"
      v
    validated context

Candidate != trust. A retrieval result means "this note may be relevant" —
never "this note is current/valid/trusted." This module is where that
distinction gets enforced: every candidate memory_retrieval.py hands back is
re-read from LIVE Markdown and validated before this runtime returns it, and a
superseded/candidate/deprecated/disputed/cycle-member note is still returned —
correctly labeled `accepted: False` and why — never silently dropped and never
silently promoted to look current because it ranked highly. See
tests/test_memory_runtime.py's `test_ranking_cannot_grant_trust` for the
regression pinning this exact invariant.

Read-only in this phase, by construction: no method in this module opens a
file for writing, and none is planned until a future phase explicitly adds
one. `MemoryRuntime` is not an autonomous agent — it answers three questions
and does nothing else:

    resolve(identity)   -> WHERE is the one note this identity names?
                            (fail-closed: ambiguous/missing surfaced explicitly,
                            no lifecycle validation — pure identity)
    inspect(identity)    -> resolve(), then validate: is THAT note trustworthy
                            right now, and why (or why not)?
    retrieve(query, ...) -> search across all methods, validate every
                            candidate, return the full labeled list — accepted
                            AND rejected candidates both present.

Reuses, never re-implements: tools/validate-vault.py's Vault (discovery,
frontmatter parsing, vault-state/disputed-vocabulary detection),
tools/vault_identity.py (fail-closed identity resolution),
tools/audit_job_dependencies.py's compute_cycle_members() (supersession-cycle
membership — general graph traversal, not job-specific despite living in that
module), and tools/memory_retrieval.py (candidate search). No new lifecycle
model is invented here; the "accepted" trust computation below is the same
memory_status/disputed-vocabulary/cycle reasoning already established in
audit_job_dependencies.py's resolve_dependency(), restated in this module's
own vocabulary (accepted/status_track) because this runtime's callers ask a
different question ("is this trustworthy right now?") than a Job's Required
tier does ("does this satisfy a declared claim/window?") — same underlying
facts, different question, so not a duplicate of that function, a different
consumer of the same primitives.

No new dependency: standard library only. No network access. No write path.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "tools" / "validate-vault.py"
IDENTITY = REPO_ROOT / "tools" / "vault_identity.py"
JOB_AUDITOR = REPO_ROOT / "tools" / "audit_job_dependencies.py"
RETRIEVAL = REPO_ROOT / "tools" / "memory_retrieval.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Registered in sys.modules BEFORE exec: memory_retrieval.py's @dataclass
    # definitions need their module resolvable via sys.modules[__module__] to
    # evaluate `from __future__ import annotations` string annotations - an
    # unregistered dynamically-loaded module breaks that lookup (the other
    # tools in this repo never hit this because none of them use @dataclass).
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vv = _load("validate_vault", VALIDATOR)
vid = _load("vault_identity", IDENTITY)
ajd = _load("audit_job_dependencies", JOB_AUDITOR)
mr = _load("memory_retrieval", RETRIEVAL)

# Normal import, not _load(): tools/memory_index.py is a plain file already
# importable via tools/ being on sys.path (memory_index.py adds it too), and
# it deliberately no longer depends on this module (see its own v3.7.5
# import-comment for why) — the two used to have a one-way dependency the
# other direction (memory_index -> memory_runtime, for a shared vv
# reference), which would have made this import circular had it stayed that
# way. Added for the "Fix the Provenance Index Performance Boundary" ticket:
# MemoryRuntime(index=...) needs ValidatedIndex to amortize freshness
# checking across a runtime instance's whole lifetime instead of once per
# search() call.
import memory_index as mi_mod
# Same reasoning: tools/embedding_index.py has no dependency on this module
# either, so this import is safe and non-circular for the identical reason.
import embedding_index as ei_mod

RUNTIME_VERSION = "1.0.0"

# status_track values and their meaning, mirrored from MEMORY_PROTOCOL.md's
# memory_status vocabulary plus two runtime-specific tracks (disputed, cycle,
# malformed-frontmatter, untracked) that aren't memory_status VALUES but are
# real reasons a note can't be trusted right now.
ACCEPTED_TRACKS = {"current"}


@dataclass
class ValidatedContext:
    """Candidate + trust, kept visibly separate. `accepted` is the ONLY field
    that means "trustworthy right now" — every other field is provenance or
    diagnostic. A caller that reads `memory_status`/`status_track` without
    checking `accepted` is reading the label, not the verdict; both are
    exposed on purpose so a caller can explain *why*, not just *whether*."""
    note_path: str
    stem: str
    method: str
    all_methods: tuple
    query: str
    score: float | None
    excerpt: str | None
    ambiguous: bool
    memory_status: str | None
    status_track: str
    accepted: bool
    reason: str


@dataclass
class ResolveResult:
    """Pure identity resolution — no lifecycle validation. status is exactly
    one of "resolved" / "ambiguous" / "missing"; never a guess."""
    status: str
    identity: str
    note_path: str | None = None
    stem: str | None = None
    candidates: tuple = ()


@dataclass
class InspectResult:
    """resolve() plus validation, for exactly one identity."""
    status: str
    identity: str
    context: ValidatedContext | None = None
    candidates: tuple = ()


class MemoryRuntime:
    def __init__(self, vault_root, boot=None, repo_root=None, index=None,
                 embedding_backend=None, embedding_index=None):
        """`index` (added v3.7.5's "Fix the Provenance Index Performance
        Boundary" ticket, optional, default None — every existing caller is
        untouched): an already-built-or-loaded tools/memory_index.py
        MemoryIndex. If given, its freshness is checked EXACTLY ONCE, right
        here, against this instance's own freshly-discovered `self.vault` —
        the identical moment `self.vault.notes`, `self._cycle_members`, and
        `self._by_rel` all become fixed snapshots for this instance's entire
        life. The resulting ValidatedIndex is reused for every retrieve()
        call this instance ever makes, with zero re-hashing, for exactly the
        same reason `_cycle_members`/`_by_rel` are already never re-verified
        against disk after this point: a MemoryRuntime instance's view of
        the vault has always been a one-time snapshot, for everything it
        does, not a special case introduced for the index. If the vault
        changes on disk while this SAME instance stays alive without being
        reconstructed, retrieve() (and resolve()/inspect(), and everything
        else) are already reading the pre-change snapshot regardless of any
        index at all — construct a new MemoryRuntime to see new content, as
        was already true before this parameter existed."""
        root = Path(vault_root)
        if not root.is_dir():
            raise ValueError("vault path is not a directory: %s" % vault_root)
        if repo_root:
            vv.REPO_ROOT = Path(repo_root).resolve()
            vv.P3_ENABLED = True
        self.vault = vv.Vault(root, Path(boot) if boot else None)
        import time
        self.vault.t0 = time.time()
        self.vault.discover()
        self.vault.detect_state()
        self._cycle_members = ajd.compute_cycle_members(self.vault)
        self._by_rel = {n["rel"]: n for n in self.vault.notes}
        self._validated_index = mi_mod.ValidatedIndex(index, self.vault) if index is not None else None
        self._embedding_backend = embedding_backend
        self._embedding_index = embedding_index
        # ValidatedEmbeddingIndex (added v3.7.5 Phase 11, with MEASURED
        # justification): benchmarking found embedding_index.is_fresh_for()
        # alone consumed 28-56% of total semantic query time (N=100..5000,
        # the fraction GROWS with vault size) — not negligible, the same
        # shape of finding that justified ValidatedIndex above for the
        # lexical path. The ONE freshness check happens here, at the same
        # construction-time snapshot moment as everything else on this
        # instance, and is reused for every retrieve() call this instance
        # ever makes.
        self._validated_embedding_index = (
            ei_mod.ValidatedEmbeddingIndex(embedding_index, self.vault, embedding_backend)
            if embedding_index is not None and embedding_backend is not None else None
        )

    # ------------------------------------------------------------- resolve
    def resolve(self, identity: str) -> ResolveResult:
        """Fail-closed identity resolution — WHERE is the note, nothing about
        whether it should be trusted. Never mutates the vault."""
        target, ambiguous = vid.resolve_identity(self.vault, identity)
        if ambiguous:
            return ResolveResult(status="ambiguous", identity=identity,
                                  candidates=tuple(sorted(n["rel"] for n in ambiguous)))
        if target is None:
            return ResolveResult(status="missing", identity=identity)
        return ResolveResult(status="resolved", identity=identity, note_path=target["rel"], stem=target["stem"])

    # ------------------------------------------------------------- inspect
    def inspect(self, identity: str) -> InspectResult:
        """resolve(), then validate: is this specific note trustworthy right
        now, and why. Never mutates the vault."""
        r = self.resolve(identity)
        if r.status != "resolved":
            return InspectResult(status=r.status, identity=identity, candidates=r.candidates)
        note = self._by_rel[r.note_path]
        ctx = self._to_context(note, method="inspect", all_methods=("inspect",), query=identity,
                                score=None, excerpt=None, ambiguous=False)
        return InspectResult(status="resolved", identity=identity, context=ctx)

    # ------------------------------------------------------------ retrieve
    def retrieve(self, query: str, methods=None, limit=None) -> list:
        """search() for candidates, validate every one against LIVE
        Markdown, return the full labeled list in retrieval order — accepted
        and rejected candidates both present, each correctly labeled. Never
        filters a candidate out for being superseded/candidate/etc.; never
        mutates the vault."""
        candidates = mr.search(self.vault, query, methods=methods, limit=limit, validated_index=self._validated_index,
                                embedding_backend=self._embedding_backend, embedding_index=self._embedding_index,
                                validated_embedding_index=self._validated_embedding_index)
        out = []
        for c in candidates:
            note = self._by_rel.get(c.note_path)
            if note is None:
                continue  # defensive only; every Candidate.note_path came from vault.notes
            ctx = self._to_context(note, method=c.method, all_methods=c.all_methods, query=query,
                                    score=c.score, excerpt=c.excerpt, ambiguous=c.ambiguous)
            out.append(ctx)
        return out

    # ------------------------------------------------------------ internal
    def _to_context(self, note, *, method, all_methods, query, score, excerpt, ambiguous) -> ValidatedContext:
        memory_status, status_track, accepted, reason = self._validate(note)
        return ValidatedContext(
            note_path=note["rel"], stem=note["stem"], method=method, all_methods=tuple(all_methods),
            query=query, score=score, excerpt=excerpt, ambiguous=ambiguous,
            memory_status=memory_status, status_track=status_track, accepted=accepted, reason=reason,
        )

    def _validate(self, note):
        """The trust computation. Read-only: reads note["fm"]/note["meta"]
        (already parsed by Vault.discover()) plus vault-level state already
        computed at construction (disputed_terms, cycle membership) — never
        re-parses, never writes. Returns (memory_status_raw, status_track,
        accepted, reason)."""
        if note["fm"]["kind"] != "parsed":
            detail = "frontmatter missing" if note["fm"]["kind"] == "missing" else "frontmatter unparseable"
            return None, "malformed-frontmatter", False, detail

        ms_raw = note["meta"].get("memory_status")
        if isinstance(ms_raw, str) and ms_raw in self.vault.disputed_terms:
            return ms_raw, "disputed", False, (
                "memory_status=%r falls under disputed protocol vocabulary; not interpreted" % ms_raw)

        if note["stem"].lower() in self._cycle_members:
            return ms_raw, "cycle", False, (
                "note participates in a supersedes/superseded_by cycle — malformed, not a valid supersession")

        effective = ms_raw
        if isinstance(ms_raw, str) and ms_raw == "active" and self.vault.state != "legacy":
            effective = "current"

        if effective == "current":
            return ms_raw, "current", True, "memory_status: current" + (" (legacy `active`)" if ms_raw == "active" else "")
        if effective in ("superseded", "candidate", "uncertain", "deprecated"):
            return ms_raw, effective, False, "memory_status: %s — not accepted as current fact" % effective
        return None, "untracked", False, "memory_status absent — untracked/legacy, never equivalent to explicit current"


if __name__ == "__main__":
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(description="Memory Runtime — read-only retrieve/inspect/resolve over a vault.")
    ap.add_argument("vault")
    ap.add_argument("verb", choices=("retrieve", "inspect", "resolve"))
    ap.add_argument("query")
    ap.add_argument("--boot")
    ap.add_argument("--repo")
    ap.add_argument("--methods", help="comma-separated subset of: %s" % ",".join(mr.METHODS))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rt = MemoryRuntime(args.vault, boot=args.boot, repo_root=args.repo)
    methods = tuple(args.methods.split(",")) if args.methods else None

    def as_dict(o):
        if hasattr(o, "__dict__"):
            return {k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(o).items()}
        return o

    if args.verb == "retrieve":
        result = rt.retrieve(args.query, methods=methods, limit=args.limit)
        payload = [as_dict(r) for r in result]
    elif args.verb == "inspect":
        payload = as_dict(rt.inspect(args.query))
        if payload.get("context") is not None:
            payload["context"] = as_dict(payload["context"])
    else:
        payload = as_dict(rt.resolve(args.query))

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        if args.verb == "retrieve":
            print("Query: %r  Results: %d" % (args.query, len(payload)))
            for r in payload:
                trust = "ACCEPTED" if r["accepted"] else "not-accepted(%s)" % r["status_track"]
                flag = " [AMBIGUOUS IDENTITY]" if r["ambiguous"] else ""
                print("  [%s]%s %s — %s — %s" % (r["method"], flag, r["note_path"], trust, r["reason"]))
        elif args.verb == "inspect":
            print("Identity: %r  Status: %s" % (args.query, payload["status"]))
            if payload.get("context"):
                c = payload["context"]
                trust = "ACCEPTED" if c["accepted"] else "not-accepted(%s)" % c["status_track"]
                print("  %s — %s — %s" % (c["note_path"], trust, c["reason"]))
            if payload.get("candidates"):
                print("  candidates: %s" % ", ".join(payload["candidates"]))
        else:
            print("Identity: %r  Status: %s" % (args.query, payload["status"]))
            if payload.get("note_path"):
                print("  -> %s" % payload["note_path"])
            if payload.get("candidates"):
                print("  candidates: %s" % ", ".join(payload["candidates"]))
        sys.exit(0)
