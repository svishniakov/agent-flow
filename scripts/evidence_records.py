"""Evidence Records parser and analyzer for Agent Flow project memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_TYPES = {
    "Attempt",
    "Success",
    "Failure",
    "Regression",
    "Architecture Attempt",
    "Architecture Failure",
    "Orchestration Failure",
}
ALLOWED_OUTCOMES = {"success", "failure", "regression", "rejected", "unknown"}
REQUIRED_FIELDS = [
    "Type",
    "Problem class",
    "Context",
    "Approach",
    "Outcome",
    "Evidence",
    "Reuse when",
    "Do not reuse when",
    "Follow-up",
]
EMPTY_GUARDED_FIELDS = {"Problem class", "Approach", "Evidence", "Reuse when", "Do not reuse when"}
EXTERNAL_ACTIONS = {"commit", "push", "deploy", "db", "database", "infra", "infrastructure"}


class EvidenceError(ValueError):
    """Raised when Evidence Records cannot be parsed."""


@dataclass(frozen=True)
class EvidenceRecord:
    record_id: str
    line: int
    fields: dict[str, str]

    @property
    def record_type(self) -> str:
        return self.fields["Type"]

    @property
    def problem_class(self) -> str:
        return self.fields["Problem class"]

    @property
    def approach(self) -> str:
        return self.fields["Approach"]

    @property
    def outcome(self) -> str:
        return self.fields["Outcome"]


@dataclass(frozen=True)
class ParseResult:
    records: list[EvidenceRecord]
    errors: list[str]


def normalize_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def is_field_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or ":" not in stripped:
        return False
    key, _ = stripped.split(":", 1)
    return bool(key.strip())


def split_field(line: str) -> tuple[str, str]:
    key, value = line.strip().split(":", 1)
    return key.strip(), value.strip()


def evidence_block_lines(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "## Evidence Records":
            start_index = index + 1
            break
    if start_index is None:
        return []

    result: list[tuple[int, str]] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        if line.startswith("## ") and line.strip() != "## Evidence Records":
            break
        result.append((index + 1, line))
    return result


def has_evidence_records_block(text: str) -> bool:
    return any(line.strip() == "## Evidence Records" for line in text.splitlines())


def error(record_id: str, line: int, message: str) -> EvidenceError:
    return EvidenceError(f"{record_id} line {line}: {message}")


def parse_record(record_id: str, start_line: int, lines: list[tuple[int, str]]) -> EvidenceRecord:
    fields: dict[str, str] = {}
    last_field: str | None = None

    for line_number, line in lines:
        if not line.strip():
            continue

        if is_field_line(line):
            key, value = split_field(line)
            if key in fields:
                raise error(record_id, line_number, f"repeated field: {key}")
            fields[key] = value
            last_field = key
            continue

        if line[0].isspace():
            if last_field is None:
                raise error(record_id, line_number, "continuation without field")
            value = line.strip()
            if value.startswith("#"):
                raise error(record_id, line_number, "markdown heading inside field body")
            fields[last_field] = f"{fields[last_field]}\n{value}" if fields[last_field] else value
            continue

        raise error(record_id, line_number, "unindented stray text inside record")

    for field in REQUIRED_FIELDS:
        if field not in fields:
            raise error(record_id, start_line, f"missing required field: {field}")

    for field in EMPTY_GUARDED_FIELDS:
        if not fields[field].strip():
            raise error(record_id, start_line, f"empty required field: {field}")

    record_type = fields["Type"]
    if record_type not in ALLOWED_TYPES:
        raise error(record_id, start_line, f"unknown Type: {record_type}")

    outcome = fields["Outcome"]
    if outcome not in ALLOWED_OUTCOMES:
        raise error(record_id, start_line, f"unknown Outcome: {outcome}")

    normalized_fields = {key: value.strip() for key, value in fields.items()}
    return EvidenceRecord(record_id=record_id, line=start_line, fields=normalized_fields)


def parse_notes(text: str) -> ParseResult:
    block = evidence_block_lines(text)
    records: list[EvidenceRecord] = []
    seen_ids: set[str] = set()
    current_id: str | None = None
    current_line = 0
    current_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal current_id, current_line, current_lines
        if current_id is None:
            return
        if current_id in seen_ids:
            raise error(current_id, current_line, "duplicate record id")
        seen_ids.add(current_id)
        records.append(parse_record(current_id, current_line, current_lines))
        current_id = None
        current_line = 0
        current_lines = []

    for line_number, line in block:
        stripped = line.strip()
        if stripped.startswith("### Evidence:"):
            flush()
            record_id = stripped[len("### Evidence:") :].strip()
            if not record_id:
                raise EvidenceError(f"line {line_number}: missing record id")
            current_id = record_id
            current_line = line_number
            current_lines = []
            continue

        if current_id is None:
            continue

        if stripped.startswith("### "):
            raise error(current_id, line_number, "unexpected markdown heading inside record")
        current_lines.append((line_number, line))

    flush()
    return ParseResult(records=records, errors=[])


def record_to_json(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": record.record_id,
        "line": record.line,
        "type": record.record_type,
        "problem_class": record.problem_class,
        "approach": record.approach,
        "outcome": record.outcome,
        "fields": dict(sorted(record.fields.items())),
    }


def has_missing_reuse_boundaries(records: list[EvidenceRecord]) -> bool:
    for record in records:
        for field in ["Reuse when", "Do not reuse when"]:
            if record.fields[field].strip().upper() == "UNKNOWN":
                return True
    return False


def classify_state(counts: dict[str, int], records: list[EvidenceRecord], min_successes: int) -> tuple[str, list[str]]:
    bad_count = counts["failure"] + counts["rejected"] + counts["regression"]
    blockers: list[str] = []
    if has_missing_reuse_boundaries(records):
        blockers.append("missing reuse boundaries")

    if bad_count >= 2:
        return "Anti-pattern", blockers
    if counts["success"] >= min_successes and not bad_count and not blockers:
        return "Local Best Practice", blockers
    if counts["success"] >= 2:
        return "Candidate Practice", blockers
    return "Observed", blockers


def group_auto_gate(state: str, records: list[EvidenceRecord], blockers: list[str]) -> dict[str, Any]:
    gate_blockers = list(blockers)
    frozen = any(
        record.outcome == "regression"
        and record.fields.get("Applied by", "").strip().lower() == "automatic-local-best-practice"
        for record in records
    )
    if frozen:
        gate_blockers.append("architecture approval required")

    if state != "Local Best Practice":
        gate_blockers.append(f"practice state is {state}")

    if any(normalize_bool(record.fields.get("Do not reuse when hit")) for record in records):
        gate_blockers.append("do-not-reuse matched")

    if any(record.fields.get("Context match", "").strip().lower() == "uncertain" for record in records):
        gate_blockers.append("context match uncertain")

    external_actions = {
        record.fields.get("External action", "").strip().lower()
        for record in records
        if record.fields.get("External action")
    }
    blocked_external = sorted(action for action in external_actions if action in EXTERNAL_ACTIONS)
    if blocked_external:
        gate_blockers.append(f"external action blocked: {', '.join(blocked_external)}")

    has_verification = any(
        record.fields.get("Verification", "").strip()
        and record.fields.get("Verification", "").strip().lower() != "none"
        for record in records
    )
    if not has_verification:
        gate_blockers.append("fresh verification required")

    unique_blockers = list(dict.fromkeys(gate_blockers))
    return {
        "allowed": not unique_blockers,
        "blockers": unique_blockers,
        "frozen": frozen,
        "attempt_record_required": not unique_blockers,
    }


def summarize_group(problem_class: str, approach: str, records: list[EvidenceRecord], min_successes: int) -> dict[str, Any]:
    counts = {
        "success": 0,
        "failure": 0,
        "regression": 0,
        "rejected": 0,
        "unknown": 0,
        "architecture_attempts": 0,
        "architecture_failures": 0,
        "orchestration_failures": 0,
    }
    for record in records:
        counts[record.outcome] += 1
        if record.record_type == "Architecture Attempt":
            counts["architecture_attempts"] += 1
        elif record.record_type == "Architecture Failure":
            counts["architecture_failures"] += 1
        elif record.record_type == "Orchestration Failure":
            counts["orchestration_failures"] += 1

    counts["attempts"] = len(records)
    counts["incomplete"] = counts["unknown"]
    state, promotion_blockers = classify_state(counts, records, min_successes)
    return {
        "problem_class": problem_class,
        "approach": approach,
        **counts,
        "state": state,
        "promotion_blockers": promotion_blockers,
        "auto_gate": group_auto_gate(state, records, promotion_blockers),
        "record_ids": [record.record_id for record in records],
    }


def analyze_notes(text: str, min_successes: int = 3) -> dict[str, Any]:
    parsed = parse_notes(text)
    grouped: dict[tuple[str, str], list[EvidenceRecord]] = {}
    for record in parsed.records:
        grouped.setdefault((record.problem_class, record.approach), []).append(record)

    groups = [
        summarize_group(problem_class, approach, records, min_successes)
        for (problem_class, approach), records in sorted(grouped.items())
    ]

    return {
        "records": [record_to_json(record) for record in parsed.records],
        "groups": groups,
        "errors": parsed.errors,
        "summary": {
            "records": len(parsed.records),
            "groups": len(groups),
            "min_successes": min_successes,
        },
    }


def analyze_file(path: Path, min_successes: int = 3, fail_on_invalid: bool = False) -> tuple[dict[str, Any], int]:
    if not path.exists():
        if fail_on_invalid:
            return {"records": [], "groups": [], "errors": [f"notes file not found: {path}"]}, 1
        return {
            "records": [],
            "groups": [],
            "errors": [],
            "summary": {"records": 0, "groups": 0, "min_successes": min_successes},
        }, 0

    text = path.read_text(encoding="utf-8")
    if fail_on_invalid and not has_evidence_records_block(text):
        return {
            "records": [],
            "groups": [],
            "errors": ["missing ## Evidence Records block"],
            "summary": {"records": 0, "groups": 0, "min_successes": min_successes},
        }, 1
    try:
        return analyze_notes(text, min_successes=min_successes), 0
    except EvidenceError as exc:
        return {
            "records": [],
            "groups": [],
            "errors": [str(exc)],
            "summary": {"records": 0, "groups": 0, "min_successes": min_successes},
        }, 1
