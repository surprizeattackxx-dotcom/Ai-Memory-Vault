#!/usr/bin/env python3
"""Deterministic, LLM-free vault validator for AI Memory Vault.

Checks (mirrors MEMORY_PROTOCOL.md + MIGRATION.md + templates):
  A. Frontmatter   - every markdown note parses + validates against the schema
  B. Lifecycle     - memory_status vocab, supersedes/superseded_by integrity
  C. Wikilinks     - every [[link]] resolves (fenced/inline code skipped)
  D. Structure     - folder indexes, orphan detection, exemptions applied
  E. Vault state   - legacy / partial / current / incompatible
  F. Parity        - vault protocol copy vs repo canonical; index completeness
  G. Security      - notes are data, not instructions; secret-shaped values

Intent: never PASS unless every deterministic check actually ran.
Findings: error / warning / info / flagged (flagged = disputed, uninterpreted).
Verdict:  BLOCKED (hard input problem or incomplete run) / FAIL / PASS.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
import jsonschema
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
VAULT_INDEX_NAME = "VAULT-INDEX.md"
ACTIVE_PRIORITIES_NAME = "Active Priorities.md"
DAILY_TEMPLATE_NAME = "Daily Note Template.md"
PROTOCOL_NAME = "MEMORY_PROTOCOL.md"
RESOURCES_RE = re.compile(r"^\d{2} - Resources$")
MONTH_DIR_RE = re.compile(r"^\d{2} - .+ \d{4}$")

INDEX_RULE_MARKERS = [
    "Memory Is Data, Not Authority",
    "Trust model",
    "`status` and `memory_status`",
    "Structural files are exempt",
]

SCHEMA = yaml.safe_load((REPO_ROOT / "schema/memory-metadata.schema.yaml").read_text(encoding="utf-8"))
EXPECTED_PROTOCOL_VERSION = str(SCHEMA.get("x-protocol-version", ""))

# ---------------------------------------------------------------- YAML loader
class StringSafeLoader(yaml.SafeLoader):
    pass


def _noop(loader, node):
    return loader.construct_scalar(node)


StringSafeLoader.add_constructor("tag:yaml.org,2002:str", _noop)
StringSafeLoader.add_constructor("tag:yaml.org,2002:timestamp", lambda l, n: l.construct_scalar(n))

# ------------------------------------------------------------- incompatibles
# Protocol migration guidance: "memory_status: active ... read as `current`"
PROTOCOL_MIGRATION_RE = re.compile(r"memory_status:\s*active.{0,120}read as\s*`current`", re.I | re.S)
# Old-vocabulary value bullet in a protocol surface: memory_status -> `active`
# The colon/dash delimiter guards against the status-axis line that mentions
# both `status and memory_status ... (active | completed ...)`.
OLD_VOCAB_BULLET_RE = re.compile(r"memory_status[^\n]{0,60}(?:\u2014|:|\u2013)\s*`active`", re.I)
# "absent memory_status is equivalent to current" (a default claim)
DEFAULT_COLLISION_RE = re.compile(
    r"(?:memory_status|field|absent)[^\n]{0,60}(?:defaults? to|default is|reads as|means|treated as)[^\n]{0,60}`?current`?"
)
DEFAULT_NEG_RE = re.compile(r"(?:never|not|no[ \n])")

FENCE_RE = re.compile(r"^```+", re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")

# --------------------------------------------------------------- security G
SUSPECT_FILENAME_RE = re.compile(r"(?i)ignore.{0,40}instruction")
SUSPECT_BODY_RE = [
    re.compile(r"(?im)^\s*IGNORE ALL PREVIOUS INSTRUCTIONS"),
    re.compile(r"(?i)\b(?:you are|i am) (?:now |hereby )?(?:jarvis|claude|opencode|an ai)\b"),
    re.compile(r"(?i)\b(?:disregard|ignore) (?:all |any )?previous (?:rules|instructions)\b"),
    re.compile(r"(?i)\bsystem[ -]?prompt\b"),
]
SUSPECT_META_RE = re.compile(r"(?i)(SYSTEM:|grant(?: me)? full (?:authority|access)|you are (?:now )?(?:jarvis|claude))")
SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|apikey|credential)")
SECRET_VALUE_RE = re.compile(r"(?i)(sk-|eyJ|AKIA|ghp_|gho_|ya29\.|AKIA[0-9A-Z]{16})")

# --------------------------------------------------------------- state (E)
CHECK_IDS = ["frontmatter", "lifecycle", "wikilinks", "structural", "state", "parity", "security", "duplicates"]
P3_ENABLED = False

REQUIRES_AI = [
    ("candidate promotion", "person-confirms or independent observation; mechanical scans cannot judge"),
    ("conflict classification / supersede-not-delete", "deciding which of correction/preference/temporal/historical/incompatible a change is"),
    ("retrieval-order ranking after search", "search-result ordering has no authority; selection is a semantic step"),
    ("memory health check honesty", "exhaustive-coverage claims require genuinely exhaustive reading"),
    ("boot-budget discipline", "holding only the current task and loading the rest on demand"),
    ("semantic duplicate detection", "same fact phrased differently is not detectable by normalized-body equality"),
    ("Jobs Required/QoS judgment", "is a dependency truly current, and does the block scope to one Job"),
    ("rejected-candidate handling", "a contradicted candidate is dropped, not archived - an intent-level act"),
    ("worth-remembering filter", "whether a fact earns memory at all is a judgment"),
    ("secret recognition", "arbitrary secrets cannot be enumerated; a validator only flags shapes"),
]


class Finding:
    __slots__ = ("id", "severity", "path", "message")

    def __init__(self, finding_id, severity, path, message):
        self.id = finding_id
        self.severity = severity
        self.path = path
        self.message = message

    def as_dict(self):
        return {"id": self.id, "severity": self.severity, "path": self.path, "message": self.message}

    def key(self):
        return (self.id, self.path or "", self.message)


class Vault:
    def __init__(self, root: Path, boot: Path | None):
        self.root = root.resolve()
        self.boot = boot
        self.notes = []          # list of dicts
        self.findings = []       # list[Finding]
        self.surfaces = {}
        self.disputed_terms = set()
        self.state = None
        self.checks_completed = ["discovery"]
        self.parity_checks = []

    # ------------------------------------------------------------- discovery
    def discover(self):
        for path in sorted(self.root.rglob("*.md")):
            rel = path.relative_to(self.root)
            parts = [p for p in rel.parts[:-1]] + [rel.name]
            meta = {}
            fm_state = {"kind": None, "error": None, "body": "", "found": False, "ok": True}
            text = _read_text(path)
            if text.startswith("\ufeff"):
                text = text[1:]
            frontmatter, body, fm_state = split_frontmatter(text)
            if fm_state["kind"] == "parsed":
                try:
                    meta = yaml.load(frontmatter, Loader=StringSafeLoader) or {}
                    if not isinstance(meta, dict):
                        fm_state = {"kind": "unparseable", "error": "frontmatter is not a mapping", "found": True, "body": body}
                except yaml.YAMLError as exc:
                    fm_state = {"kind": "unparseable", "error": str(exc).splitlines()[0] if exc else "yaml error", "found": True, "body": body}
            self.notes.append(
                {
                    "path": path,
                    "rel": "/".join(parts),
                    "stem": path.stem,
                    "basename": path.name,
                    "dir_parts": parts[:-1],
                    "text": text,
                    "body": body,
                    "meta": meta,
                    "fm": fm_state,
                }
            )

    # ----------------------------------------------------------- classifications
    def is_structural(self, note) -> bool:
        parts = note["dir_parts"]
        if note["basename"] in (VAULT_INDEX_NAME, ACTIVE_PRIORITIES_NAME, DAILY_TEMPLATE_NAME):
            return True
        if note["basename"] == PROTOCOL_NAME and parts and RESOURCES_RE.match(parts[-1]):
            return True
        if "templates" in parts:
            return True
        if note["meta"].get("memory_role") == "structural":
            return True
        return False

    def rel(self, path: Path) -> str:
        return "/".join(path.relative_to(self.root).parts)

    def in_legacy_zone(self, note) -> bool:
        # A self-declared museum folder (e.g. "Old Memory"): pre-protocol drags
        # are reported as information only, never as failures.
        return any(part == "Old Memory" for part in note["dir_parts"])

    def in_handoff_folder(self, note) -> bool:
        # The Handoff sub-folder runs its own documented sub-protocol
        # (Handoff.md), including a `type: task` vocabulary outside the global
        # note-type enum.
        return note["dir_parts"][:2] == ["09 - Resources", "Handoff"]

    def note_by_stem(self, stem: str):
        target = stem.lower()
        if target.endswith(".md"):
            target = target[:-3]
        for n in self.notes:
            if n["stem"].lower() == target:
                return n
        return None

    def resolve_link(self, raw: str):
        raw = raw.strip()
        if raw.startswith("[[") and raw.endswith("]]"):
            raw = raw[2:-2]
        raw = raw.split("|")[0].strip()
        if "/" in raw:
            raw = raw.rsplit("/", 1)[-1]
        if "#" in raw:
            raw = raw.split("#", 1)[0].strip()
        if not raw:
            return None
        return self.note_by_stem(raw)

    # ---------------------------------------------------------------- A: fm
    def check_frontmatter(self):
        for note in self.notes:
            fm = note["fm"]
            rel = note["rel"]
            if fm["kind"] == "missing":
                sev = "info" if (self.state == "legacy" or self.in_legacy_zone(note)) else "error"
                self.emit("FM-MISSING", sev, rel, "no YAML frontmatter")
            elif fm["kind"] == "unparseable":
                self.emit("FM-UNPARSEABLE", "error", rel, "frontmatter does not parse: %s" % fm["error"])
            elif fm["kind"] == "parsed":
                errors = self._schema_errors(note)
                if self._disputed(note):
                    continue
                for err in errors:
                    sev = "info" if (self.state == "legacy" or self.in_legacy_zone(note)) else "error"
                    self.emit("SCHEMA-VIOLATION", sev, rel, err)

    def _schema_errors(self, note):
        meta = note["meta"]
        missing = [k for k in ("status", "project", "type") if k not in meta]
        ms = meta.get("memory_status")
        errors = []
        if missing:
            errors.append("required field missing: %s" % ", ".join(missing))
        if isinstance(ms, str) and ms in ("active", "stale", "archived"):
            return errors  # old-vocab values are reported by lifecycle, not the schema enum
        if self.in_handoff_folder(note) and meta.get("type") == "task":
            return errors  # Handoff sub-protocol vocabulary (see Handoff.md)
        try:
            Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker()).validate(meta)
            return errors
        except jsonschema.ValidationError as exc:
            errors.append("schema: %s" % exc.message)
            return errors

    def _disputed(self, note):
        v = note["meta"].get("memory_status")
        return isinstance(v, str) and v in self.disputed_terms

    # ---------------------------------------------------------------- B: lifecycle
    def check_lifecycle(self):
        lifecycle_records = 0
        by_stem = {n["stem"].lower(): n for n in self.notes}
        edges = []  # (src_note, tgt_note, field)
        for note in self.notes:
            if self.in_legacy_zone(note):
                continue  # lifecycle contract never applied to pre-protocol drags
            if self._disputed(note):
                self.emit("LC-DISPUTED", "flagged", note["rel"],
                          "memory_status='%s' falls under disputed vocabulary; not interpreted" % note["meta"].get("memory_status"))
                continue
            meta = note["meta"]
            ms = meta.get("memory_status")
            if ms and isinstance(ms, str):
                lifecycle_records += 1
                if self.state != "legacy" and ms in ("active", "stale", "archived"):
                    if ms == "active":
                        self.emit("LC-VOCAB-ACTIVE", "info", note["rel"], "legacy value `active` reads as `current` (v3.5 migration)")
                    elif ms == "stale":
                        self.emit("LC-VOCAB-STALE", "info", note["rel"], "legacy value `stale` reads as `uncertain` (v3.5 migration)")
                    elif ms == "archived":
                        self.emit("LC-VOCAB-ARCHIVED", "error", note["rel"], "legacy value `archived` needs a person to decide current vs deprecated")
            if (meta.get("source") or meta.get("confidence") or meta.get("confidence_basis")) and "memory_status" not in meta:
                self.emit("MEMORY-STATUS-ABSENT", "info", note["rel"], "source/confidence tracked but memory_status untracked")
            for field in ("supersedes", "superseded_by"):
                raw = meta.get(field)
                if not isinstance(raw, str):
                    continue
                lifecycle_records += 1
                target = raw.strip()
                resolved = self.resolve_link(target)
                target_stem = target.lstrip("[").rstrip("]").split("|")[0].strip().rsplit("/", 1)[-1].rstrip(".md")
                if resolved is None:
                    self.emit("LC-UNRESOLVED", "error", note["rel"], "%s -> %s does not exist" % (field, target))
                    continue
                if note["stem"].lower() == resolved["stem"].lower():
                    self.emit("LC-SELF-REF", "error", note["rel"], "%s points at itself" % field)
                    continue
                edges.append((note, resolved, field))
            if ms == "superseded" and not (meta.get("supersedes") or meta.get("superseded_by")):
                self.emit("LC-SUPERSEDED-NO-LINK", "warning", note["rel"],
                          "memory_status: superseded but no supersedes/superseded_by link")
        self.lifecycle_records_examined = lifecycle_records
        self._check_pairs(edges, by_stem)
        self._check_cycles(edges)

    def _check_pairs(self, edges, by_stem):
        seen = set()
        for src, tgt, field in edges:
            if src is tgt:
                continue  # self-reference is its own error (LC-SELF-REF)
            mirror = "superseded_by" if field == "supersedes" else "supersedes"
            tgt_meta = tgt["meta"]
            back = tgt_meta.get(mirror)
            if isinstance(back, str) and src["stem"].lower() in back.lower():
                continue
            key = frozenset([src["stem"], tgt["stem"]])
            if key in seen:
                continue
            seen.add(key)
            self.emit("LC-PAIR-UNRECIPROCATED", "warning", src["rel"],
                      "%s -> %s without %s back-reference (pair fields meant to be set together)"
                      % (src["stem"], tgt["stem"], mirror))

    def _check_cycles(self, edges):
        # Graph only the forward (supersedes) direction. A reciprocated pair
        # (A supersedes B while B superseded_by A) is the intended mirror, not a
        # cycle, so superseded_by edges must not be added to the graph.
        from collections import defaultdict
        graph = defaultdict(list)
        for src, tgt, field in edges:
            if field == "supersedes":
                graph[src["stem"].lower()].append(tgt["stem"].lower())
        WHITE, GRAY, BLACK = 0, 1, 2
        color = defaultdict(int)

        def dfs(name):
            color[name] = GRAY
            for nxt in graph.get(name, []):
                if color[nxt] == GRAY:
                    return True
                if color[nxt] == WHITE and dfs(nxt):
                    return True
            color[name] = BLACK
            return False

        for s, t, _ in edges:
            if color[s["stem"].lower()] == WHITE:
                if dfs(s["stem"].lower()):
                    self.emit("LC-CYCLE", "warning", s["rel"], "cycle detected in supersedes/superseded_by chain")
                    return

    # ---------------------------------------------------------------- C: wikilinks
    def check_wikilinks(self):
        links_examined = 0
        for note in self.notes:
            body = strip_fenced_and_code(note["body"])
            for m in WIKILINK_RE.finditer(body):
                raw = m.group(1).strip()
                if not raw:
                    self.emit("WL-MALFORMED", "warning", note["rel"], "empty wikilink target")
                    continue
                if raw.startswith("#"):
                    continue  # intranote heading anchor (#Heading), not a note link
                links_examined += 1
                if self.resolve_link(raw) is None:
                    self.emit("WL-UNRESOLVED", "error", note["rel"], "[[%s]] does not resolve to any note" % raw)
        self.links_examined = links_examined

    # ---------------------------------------------------------------- D: structural
    def check_structural(self):
        legacy = self.state == "legacy"
        sev = "info" if legacy else "warning"
        inbound = defaultdict(set)  # stem -> set(linker stems)
        for note in self.notes:
            for m in WIKILINK_RE.finditer(strip_fenced_and_code(note["text"])):
                tgt = self.resolve_link(m.group(1))
                if tgt is not None:
                    inbound[tgt["stem"].lower()].add(note["stem"].lower())
        folders = defaultdict(list)
        for note in self.notes:
            folders[tuple(note["dir_parts"])].append(note)
        structural_count = 0
        for note in self.notes:
            if self.is_structural(note):
                structural_count += 1
        self.structural_files_excluded = structural_count

        for folder_key, notes in sorted(folders.items()):
            if not folder_key:
                continue
            folder_path = self.root.joinpath(*folder_key).resolve()
            basename = folder_key[-1]
            exempt_folder = folder_key[0] == "00 - Inbox"
            legacy_zone = any(part == "Old Memory" for part in folder_key)
            transient_folder = folder_key[0] == "09 - Resources" and len(folder_key) >= 2 and folder_key[1] == "Handoff"
            is_month_dir = folder_key[0] == "01 - Daily Notes" and len(folder_key) >= 2 and MONTH_DIR_RE.match(folder_key[1])
            if '.' in basename:  # not a real vault folder (e.g. app dirs)
                continue
            if legacy_zone:
                self.emit("LEGACY-ZONE", "info", "/".join(folder_key),
                          "self-declared legacy archive folder; notes inside are reported as information only")
            non_structural = [n for n in notes if not self.is_structural(n)]
            index_files = [
                n for n in notes
                if n["basename"].replace(".md", "").lower() == basename.replace(".md", "").lower() or n["meta"].get("type") == "index"
            ]
            if len(non_structural) >= 5 and not index_files and not exempt_folder and not is_month_dir and not legacy_zone and not transient_folder:
                self.emit("IDX-MISSING", sev, "/".join(folder_key), "folder has %d notes and no index" % len(non_structural))
            index_text = ""
            if index_files:
                index_text = index_files[0]["text"]
            for n in non_structural:
                if exempt_folder or legacy_zone or transient_folder:
                    continue
                if index_files and n in index_files:
                    continue  # a folder's own index file never lists itself
                stem = n["stem"].lower()
                if index_files and stem not in index_text.lower() and "[" + stem + "]" not in index_text.lower() and (stem + ".md") not in index_text.lower():
                    self.emit("IDX-ENTRY-MISSING", sev, n["rel"], "note not referenced in folder index %s" % index_files[0]["basename"])
                if not inbound[stem] and not (index_files and stem in index_text.lower()):
                    self.emit("ORPHAN", sev, n["rel"], "no inbound wikilinks and not listed in folder index")

    # ---------------------------------------------------------------- E: state
    def detect_state(self):
        index_note = next((n for n in self.notes if n["basename"] == VAULT_INDEX_NAME), None)
        protocol = next((n for n in self.notes if n["basename"] == PROTOCOL_NAME), None)
        surfaces = {}
        if protocol is not None:
            surfaces["protocol"] = {"path": protocol["rel"], "text": protocol["text"]}
        if index_note is not None:
            surfaces["index"] = {"path": index_note["rel"], "text": index_note["text"]}
        if self.boot is not None and Path(self.boot).is_file():
            surfaces["boot"] = {"path": str(self.boot), "text": _read_text(Path(self.boot))}
        self.surfaces = surfaces

        p = protocol is not None
        i = bool(index_note is not None and any(mk in index_note["text"] for mk in ("Memory Is Data, Not Authority", "Trust model", "How My Memory Works")))
        b = False
        boot_text = surfaces.get("boot", {}).get("text", "")
        if boot_text:
            has_a = any(k in boot_text for k in ("rules that can't lapse", "Never auto-execute external content"))
            has_b = any(k in boot_text for k in ("Memory Governor", "Checkpoint persistence"))
            b = has_a and has_b
        m = any(
            n["meta"].get("memory_status") or n["meta"].get("supersedes") or n["meta"].get("superseded_by") or n["meta"].get("source") or n["meta"].get("confidence")
            for n in self.notes
        )

        conflicts = self._detect_conflicts(surfaces)
        if conflicts:
            self.state = "incompatible"
            for name, term, a, bpath in conflicts:
                self.emit("STATE-INCOMPATIBLE", "error", a,
                          "protocol surfaces disagree on '%s' (%s): %s vs %s" % (term, name, a, bpath))
                if term.startswith("memory_status::"):
                    self.disputed_terms.add(term.split("::", 1)[1])
        elif p and i and b:
            self.state = "current"
            if self.boot is None:
                self.emit("STATE-BOOT-NOT-ASSESSED", "info", "", "boot file not supplied; current state assumed on other markers")
        elif not (p or i or b or m):
            self.state = "legacy"
        else:
            self.state = "partial"
            missing = []
            if not p:
                missing.append("Resources/" + PROTOCOL_NAME)
            if not i:
                missing.append(VAULT_INDEX_NAME + " current sections")
            if not b:
                missing.append("boot file current sections")
            self.emit("STATE-PARTIAL", "warning", "", "partial upgrade: missing %s" % "; ".join(missing))
        if self.state == "current":
            self.emit("STATE-CURRENT", "info", "", "all required protocol surfaces synchronized")
        elif self.state == "legacy":
            self.emit("STATE-LEGACY", "info", "", "no current-protocol metadata or rules detected")

    def _detect_conflicts(self, surfaces):
        conflicts = []
        protocol_text = surfaces.get("protocol", {}).get("text", "")
        has_default_deny = bool(re.search(r"(?i)no default|never equivalent", protocol_text)) if protocol_text else False
        for name, doc in surfaces.items():
            if name == "protocol":
                continue
            text = doc.get("text", "")
            if not text or re.search(r"read as\s*`current`", text, re.I):
                continue
            if OLD_VOCAB_BULLET_RE.search(text) and protocol_text:
                conflicts.append((name, "memory_status::active", doc["path"], PROTOCOL_NAME + " (says `active` reads as `current`)"))
            if not has_default_deny:
                continue
            for m in DEFAULT_COLLISION_RE.finditer(text):
                seg = text[max(0, m.start() - 25):m.end()]
                if DEFAULT_NEG_RE.search(seg):
                    continue
                conflicts.append((name, "memory_status-absence-default", doc["path"], PROTOCOL_NAME + " (says absent has no default)"))
                break
        return conflicts

    # ---------------------------------------------------------------- F: parity
    def check_parity(self):
        completed = []
        self.parity_checks = []
        protocol = next((n for n in self.notes if n["basename"] == PROTOCOL_NAME), None)
        if protocol is not None:
            vault_text = norm_crlf(protocol["text"])
            canon_text = norm_crlf(_read_text(REPO_ROOT / PROTOCOL_NAME))
            if vault_text != canon_text:
                self.emit("PARITY-PROTOCOL-DIVERGENCE", "error", protocol["rel"], "vault protocol copy differs from repo canonical MEMORY_PROTOCOL.md")
                self.parity_checks.append({"check": "p1", "surface": protocol["rel"], "status": "diverged", "detail": "copy differs from repo canonical"})
            else:
                self.parity_checks.append({"check": "p1", "surface": protocol["rel"], "status": "match", "detail": "byte-identical to repo canonical"})
            v_ver = protocol_version(protocol["text"])
            if v_ver and EXPECTED_PROTOCOL_VERSION and v_ver != EXPECTED_PROTOCOL_VERSION:
                self.emit("PARITY-PROTOCOL-VERSION", "error", protocol["rel"],
                          "protocol version %s (expected %s)" % (v_ver, EXPECTED_PROTOCOL_VERSION))
                self.parity_checks.append({"check": "p1_version", "surface": protocol["rel"], "status": "diverged",
                                           "detail": "version %s, expected %s" % (v_ver, EXPECTED_PROTOCOL_VERSION)})
            elif v_ver:
                self.parity_checks.append({"check": "p1_version", "surface": protocol["rel"], "status": "match", "detail": "version %s" % v_ver})
        elif self.state in ("legacy", "partial"):
            self.parity_checks.append({"check": "p1", "surface": PROTOCOL_NAME, "status": "not-applicable",
                                       "detail": "vault %s; protocol parity not required" % self.state})
        else:
            completed.append("parity_p1_missing_protocol")
            self.parity_checks.append({"check": "p1", "surface": PROTOCOL_NAME, "status": "missing",
                                       "detail": "current vault has no protocol copy"})
        completed.append("parity_p1")
        index_note = next((n for n in self.notes if n["basename"] == VAULT_INDEX_NAME), None)
        if index_note is not None and self.state != "legacy":
            text = index_note["text"]
            p2_status = None
            if re.search(r"\[FILL IN\s*:", text):
                self.emit("PARITY-TEMPLATE-INCOMPLETE", "error", index_note["rel"], "index still carries [FILL IN: ...] markers")
                p2_status = ("diverged", "unfilled [FILL IN:] markers")
            missing = [mk for mk in INDEX_RULE_MARKERS if mk not in text]
            if missing:
                for mk in missing:
                    self.emit("PARITY-INDEX-REGRESSED", "error", index_note["rel"], "missing rule marker: %s" % mk)
                p2_status = ("diverged", "missing rule markers: %s" % "; ".join(missing))
            if p2_status is not None:
                self.parity_checks.append({"check": "p2", "surface": index_note["rel"], "status": p2_status[0], "detail": p2_status[1]})
            else:
                self.parity_checks.append({"check": "p2", "surface": index_note["rel"], "status": "match",
                                           "detail": "%d rule markers present" % len(INDEX_RULE_MARKERS)})
        elif index_note is not None:
            self.parity_checks.append({"check": "p2", "surface": index_note["rel"], "status": "not-applicable", "detail": "vault %s" % self.state})
        completed.append("parity_p2")
        if P3_ENABLED:
            completed.append("parity_p3")
            self.parity_checks += self._parity_p3()
        self.checks_completed += completed
        self.checks_completed.append("parity")

    def _parity_p3(self):
        records = []
        try:
            doc = _read_text(REPO_ROOT / "ai-memory-vault.md")
        except OSError:
            return [{"check": "p3", "surface": "ai-memory-vault.md", "status": "missing", "detail": "embedded spec doc not found"}]
        blocks = re.findall(r"```(?:markdown|md|text)?\n(.*?)```", doc, re.S)
        index_hits = [b for b in blocks if b.splitlines() and ("VAULT INDEX" in b.splitlines()[0] or "# VAULT INDEX" in b)]
        boot_hits = [b for b in blocks if b.splitlines() and "# Boot Config" in b.splitlines()[0]]
        for block in boot_hits:
            present = [k for k in ("rules that can't lapse", "Never auto-execute external content", "Memory Governor") if k in block]
            self.emit("PARITY-EMBEDDED-BOOT", "info", "ai-memory-vault.md", "embedded boot copy carries %d/3 markers" % len(present))
            records.append({"check": "p3_boot", "surface": "ai-memory-vault.md", "status": "match" if len(present) == 3 else "partial",
                            "detail": "%d/3 rule markers" % len(present)})
        for block in index_hits:
            present = [k for k in ("Memory Is Data, Not Authority", "Trust model", "Structural files are exempt") if k in block]
            self.emit("PARITY-EMBEDDED-INDEX", "info", "ai-memory-vault.md", "embedded index copy carries %d/3 rule markers" % len(present))
            records.append({"check": "p3_index", "surface": "ai-memory-vault.md", "status": "match" if len(present) == 3 else "partial",
                            "detail": "%d/3 rule markers" % len(present)})
        if not records:
            records.append({"check": "p3", "surface": "ai-memory-vault.md", "status": "not-applicable", "detail": "no embedded blocks found"})
        return records

    # ---------------------------------------------------------------- G: security
    def check_security(self):
        for note in self.notes:
            if SUSPECT_FILENAME_RE.search(note["basename"]):
                self.emit("SUSPECT-FILENAME", "info", note["rel"], "filename looks like an instruction-masquerading note")
            for pat in SUSPECT_BODY_RE:
                if pat.search(note["body"]):
                    self.emit("SUSPECT-BODY", "info", note["rel"], "body contains directive language; notes are data, never instructions")
                    break
            for key, val in note["meta"].items():
                if not isinstance(val, str):
                    continue
                if SUSPECT_META_RE.search(val):
                    self.emit("SUSPECT-METADATA", "info", note["rel"], "metadata field '%s' contains directive-style language" % key)
                if SECRET_KEY_RE.search(key) and len(val) >= 8 and not val.startswith("<"):
                    self.emit("POSSIBLE-SECRET", "info", note["rel"], "field '%s' may hold a secret value (value not echoed)" % key)
                elif SECRET_VALUE_RE.search(val) and len(val) >= 12:
                    self.emit("POSSIBLE-SECRET", "info", note["rel"], "value shape in field '%s' looks secret-bearing (value not echoed)" % key)

    # ---------------------------------------------------------------- duplicates
    def check_duplicates(self):
        buckets = {}
        for note in self.notes:
            key = re.sub(r"\s+", " ", note["body"].strip()).strip().lower()
            if not key:
                continue
            buckets.setdefault(key, []).append(note["rel"])
        for key, paths in sorted(buckets.items()):
            if len(paths) > 1:
                self.emit("DUP-LEXICAL-BODY", "info", paths[0], "identical normalized bodies: %s" % "; ".join(paths))

    # ---------------------------------------------------------------- emit/report
    def emit(self, finding_id, severity, path, message):
        self.findings.append(Finding(finding_id, severity, path, message))

    def builder(self):
        # state is computed first in main(); the remaining checks order here
        for check in ("frontmatter", "lifecycle", "wikilinks", "structural", "parity", "security", "duplicates"):
            getattr(self, "check_" + check)()
            self.checks_completed.append(check)

    def report(self):
        sorted_findings = sorted(self.findings, key=Finding.key)
        errors = [f.as_dict() for f in sorted_findings if f.severity == "error"]
        warnings = [f.as_dict() for f in sorted_findings if f.severity == "warning"]
        information = [f.as_dict() for f in sorted_findings if f.severity == "info"]
        flagged = [f.as_dict() for f in sorted_findings if f.severity == "flagged"]
        required = set(CHECK_IDS)
        completed = set(self.checks_completed)
        hard_problem = False
        if self.boot is not None and not Path(self.boot).is_file():
            hard_problem = True
        if not required.issubset(completed):
            hard_problem = True
        if hard_problem or errors or flagged:
            verdict = "BLOCKED" if hard_problem else "FAIL"
        else:
            verdict = "PASS"
        return {
            "validator_version": "1.0.0",
            "protocol_version_expected": EXPECTED_PROTOCOL_VERSION,
            "vault_path": str(self.root),
            "boot_path": str(self.boot) if self.boot else None,
            "vault_state": self.state,
            "verdict": verdict,
            "checks_completed": sorted(completed),
            "files_examined": len(self.notes),
            "links_examined": getattr(self, "links_examined", 0),
            "lifecycle_records_examined": getattr(self, "lifecycle_records_examined", 0),
            "structural_files_excluded": getattr(self, "structural_files_excluded", 0),
            "parity_checks": self.parity_checks,
            "semantic_duplicate_detection": "NOT PERFORMED",
            "checks_requiring_ai": REQUIRES_AI,
            "errors": errors,
            "warnings": warnings,
            "information": information,
            "flagged": flagged,
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - self.t0, 2),
        }


def split_frontmatter(text):
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].rstrip("\r\n") == "---":
        return "", text, {"kind": "missing", "error": None, "found": False, "body": text}
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            fm = "".join(lines[1:idx])
            body = "".join(lines[idx + 1:])
            return fm, body, {"kind": "parsed", "error": None, "found": True, "body": body}
    return "", text, {"kind": "unparseable", "error": "no closing ---", "found": True, "body": text}


def strip_fenced_and_code(text):
    lines = text.splitlines()
    out = []
    in_fence = False
    for line in lines:
        if FENCE_RE.search(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = INLINE_CODE_RE.sub("`", line)
        out.append(line)
    return "\n".join(out)


def norm_crlf(text):
    return text.replace("\r\n", "\n")


def _read_text(path):
    return path.read_text(encoding="utf-8", errors="replace")


def protocol_version(text):
    m = re.search(r"(?m)^version:\s*[\"']?([0-9.]+)", text)
    return m.group(1) if m else None


from collections import defaultdict  # noqa: E402


def parse_args():
    ap = argparse.ArgumentParser(description="Validate an AI Memory Vault structure deterministically.")
    ap.add_argument("vault", help="path to the vault root")
    ap.add_argument("--boot", help="path to the boot file (identity + rules that can't lapse)")
    ap.add_argument("--json", action="store_true", help="emit JSON report to stdout")
    ap.add_argument("--repo", help="repo root (defaults to validator's own repo)")
    ap.add_argument("--out", help="also write report to this path")
    return ap.parse_args()


def main():
    args = parse_args()
    root = Path(args.vault)
    if not root.is_dir():
        sys.exit("ERROR: vault path is not a directory: %s" % args.vault)
    if args.repo:
        canonical_root = Path(args.repo).resolve()
        if not (canonical_root / PROTOCOL_NAME).is_file():
            sys.exit("ERROR: --repo path lacks MEMORY_PROTOCOL.md: %s" % args.repo)
    boot = Path(args.boot) if args.boot else None
    global REPO_ROOT, P3_ENABLED
    if args.repo:
        REPO_ROOT = canonical_root
        P3_ENABLED = True

    vault = Vault(root, boot)
    vault.t0 = time.time()
    vault.discover()
    vault.detect_state()
    vault.checks_completed.append("state")
    vault.builder()
    report = vault.report()

    if args.json:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        text = json.dumps(report, indent=2, sort_keys=True)
        print(text)
    else:
        print_human(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if report["verdict"] == "BLOCKED":
        sys.exit(3)
    if report["verdict"] == "FAIL":
        sys.exit(2)
    sys.exit(0)


def print_human(r):
    print("Vault:      %s" % r["vault_path"])
    print("State:      %s   Verdict: %s" % (r["vault_state"], r["verdict"]))
    print("Files:      %d  Links: %d  Lifecycle: %d  Structural excluded: %d" % (
        r["files_examined"], r["links_examined"], r["lifecycle_records_examined"], r["structural_files_excluded"]))
    print("Checks:     %s" % ", ".join(r["checks_completed"]))
    for pc in r.get("parity_checks") or []:
        print("  parity [%s] %s: %s (%s)" % (pc["check"], pc["surface"], pc["status"], pc["detail"]))
    for bucket in (("ERROR", r["errors"]), ("WARNING", r["warnings"]), ("FLAGGED", r["flagged"]), ("INFO", r["information"])):
        if not bucket[1]:
            continue
        print("%s (%d):" % (bucket[0], len(bucket[1])))
        for f in bucket[1]:
            print("  [%s] %s: %s" % (f["id"], f["path"] or "<vault>", f["message"]))
    print("Semantic duplicate detection: %s" % r["semantic_duplicate_detection"])
    print("Duration:   %.2fs" % r["duration_seconds"])


if __name__ == "__main__":
    main()