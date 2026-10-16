#!/usr/bin/env python3
"""Guard rule-table single sourcing and agent doc consistency with the contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENT_DOCS = [ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]

SINGLE_SOURCE_TABLES = ("EVENT_STATUS_RULES", "HANDOFF_STATE_BY_CALLER_STATUS", "HANDOFF_DOMAIN_STATUSES")

# Phrases that documented superseded rules; they must never come back.
STALE_DOC_PATTERNS = (
    re.compile(r"python3 (?:-B )?scripts/"),
    re.compile(r"union covers the full delta"),
    re.compile(r"metadata, not a password", re.I),
    re.compile(r"метаданные, а не пароль"),
    re.compile(r"journal(?:\.py)? (?:upgrade|import-legacy|classify-legacy)\b"),
    re.compile(r"reopen --upgrade\b"),
    re.compile(r"Version 1 remains readable"),
    re.compile(r"append-timeline"),
    re.compile(r"record-handoff-state"),
    re.compile(r"record-lane-boundary"),
)

# Gate names retired by the unified gate vocabulary (7 contours). They may
# appear once per contour per document on a line carrying a «входит в» or
# «включает» annotation for readers of older journals; nowhere else.
RETIRED_GATE_NAMES = (
    "Architecture Contract Gate",
    "Architecture Design Mode",
    "Architecture Capability Router",
    "Architecture Context Propagation",
    "Architecture Execution Control",
    "Architecture Approval Gate",
    "Engineering Simplicity Gate",
    "Simplicity Scope Coverage",
    "Lane Boundary Evidence Gate",
    "Claim Evidence Gate",
    "Acceptance Criteria Traceability Gate",
    "Surface Evidence Gate",
    "Contract Negative Fixture Gate",
    "Mitigation Gate",
    "Resolution Gate",
    "Blocked Resolution Gate",
    "Delegation Trace Gate",
    "Handoff State Gate",
    "Mandatory Independent QA Review Gate",
    "Continuation Gate",
)

RETIRED_NAME_MARKERS = ("входит в", "включает")

AGENT_ROLE_DOCS = sorted((ROOT / "agents").glob("*.md"))

STATUS_DOC = ROOT / "references" / "traceable-runs.md"


def fail(errors, message):
    errors.append(message)


def check_single_source(errors, scripts=SCRIPTS):
    for table in SINGLE_SOURCE_TABLES:
        definitions = []
        pattern = re.compile(rf"^{table}\s*=", re.M)
        for path in scripts.glob("*.py"):
            if path.name.startswith("test-"):
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                definitions.append(path.name)
        if definitions != ["journal_structure.py"]:
            fail(errors, f"{table} must be defined exactly once in journal_structure.py, found: {definitions or 'nowhere'}")


def check_stale_doc_patterns(errors, docs=AGENT_DOCS):
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for pattern in STALE_DOC_PATTERNS:
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    fail(errors, f"{path.name}:{number}: stale pattern {pattern.pattern!r}: {line.strip()[:100]}")


def check_retired_gate_names(errors, docs=None):
    for path in docs if docs is not None else [*AGENT_DOCS, *AGENT_ROLE_DOCS]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(marker in line for marker in RETIRED_NAME_MARKERS):
                continue
            for name in RETIRED_GATE_NAMES:
                if name in line:
                    fail(errors, f"{path.name}:{number}: retired gate name {name!r} outside a «входит в» note: {line.strip()[:100]}")
                    break


def check_documented_vocabulary(errors, doc=STATUS_DOC):
    from journal_structure import CALLER_STATUSES, EVENT_STATUS_RULES, HANDOFF_DOMAIN_STATUSES
    text = doc.read_text(encoding="utf-8")
    for status in sorted(CALLER_STATUSES):
        if f"`{status}`" not in text:
            fail(errors, f"{doc.name} does not document caller status `{status}`")
    for stage in sorted(EVENT_STATUS_RULES):
        if f"`{stage}`" not in text and f"--stage {stage}" not in text and f"stage={stage}" not in text:
            fail(errors, f"{doc.name} does not document event stage `{stage}`")
    for status in sorted(HANDOFF_DOMAIN_STATUSES):
        if f"`{status}`" not in text:
            fail(errors, f"{doc.name} does not document handoff domain status `{status}`")


def main():
    errors = []
    check_single_source(errors)
    check_stale_doc_patterns(errors)
    check_retired_gate_names(errors)
    check_documented_vocabulary(errors)
    if errors:
        for error in errors:
            print(f"rule consistency: {error}", file=sys.stderr)
        return 1
    print("rule consistency checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
