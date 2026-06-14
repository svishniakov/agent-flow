"""Promote Harness Evaluation findings into project-local Evidence Records."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence_records import EvidenceError, parse_notes


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"
HARNESS_EVALUATION_PATH = "harness-evaluation.json"
EVIDENCE_RECORDS_HEADING = "## Evidence Records"


@dataclass(frozen=True)
class PromotionRecord:
    record_id: str
    text: str


@dataclass(frozen=True)
class PromotionResult:
    promoted: int
    skipped: int
    dry_run: bool
    record_ids: list[str]


class PromotionError(ValueError):
    """Raised when Harness Evaluation promotion cannot proceed."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "run"


def compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def load_harness_evaluation(run_dir: Path) -> dict[str, Any]:
    path = run_dir / HARNESS_EVALUATION_PATH
    if not path.exists():
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} must be a JSON object")
    return data


def validation_command_text(run_dir: Path) -> str:
    return f"python3 scripts/validate-run.py --run-dir {run_dir} --mode full"


def validate_run(run_dir: Path) -> tuple[str, str]:
    command = [
        sys.executable,
        str(VALIDATE_RUN),
        "--run-dir",
        str(run_dir),
        "--mode",
        "full",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    command_text = validation_command_text(run_dir)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise PromotionError(f"validate-run failed: {command_text}\n{output}")
    return command_text, result.stdout + result.stderr


def selected_proposal_rationale(data: dict[str, Any]) -> str:
    proposals = data.get("proposals")
    if not isinstance(proposals, list):
        return ""
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        if proposal.get("type") == "evidence-record" and proposal.get("target") == "Evidence Records":
            return compact(proposal.get("rationale"))
    return ""


def finding_record_type(finding: dict[str, Any]) -> str:
    finding_type = finding.get("type")
    outcome = finding.get("outcome")
    if finding_type == "architecture-failure":
        return "Architecture Failure"
    if finding_type == "orchestration-failure":
        return "Orchestration Failure"
    if outcome == "success":
        return "Success"
    if outcome == "failure":
        return "Failure"
    if outcome == "regression":
        return "Regression"
    return "Attempt"


def join_values(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "none"
    return ", ".join(compact(value) for value in values if compact(value)) or "none"


def finding_evidence(finding: dict[str, Any]) -> str:
    values = ["harness-evaluation.json"]
    raw_evidence = finding.get("evidence")
    if isinstance(raw_evidence, list):
        values.extend(compact(value) for value in raw_evidence if compact(value))
    return "; ".join(dict.fromkeys(values))


def build_context(run_dir: Path, data: dict[str, Any], finding: dict[str, Any]) -> str:
    return "; ".join(
        [
            f"run: {run_dir}",
            f"learning_triggers: {join_values(data.get('learning_triggers'))}",
            f"architecture_context: {join_values(finding.get('architecture_context'))}",
            f"architecture_capabilities: {join_values(finding.get('architecture_capabilities'))}",
        ]
    )


def build_record(run_dir: Path, data: dict[str, Any], finding: dict[str, Any], command_text: str) -> PromotionRecord:
    finding_id = compact(finding.get("id"))
    record_id = f"harness-{slugify(run_dir.name)}-{finding_id}"
    rationale = selected_proposal_rationale(data) or "none"
    fields = [
        ("Type", finding_record_type(finding)),
        ("Problem class", compact(finding.get("problem_class"))),
        ("Context", build_context(run_dir, data, finding)),
        ("Approach", compact(finding.get("approach"))),
        ("Outcome", compact(finding.get("outcome"))),
        ("Evidence", finding_evidence(finding)),
        ("Reuse when", compact(finding.get("reuse_when"))),
        ("Do not reuse when", compact(finding.get("do_not_reuse_when"))),
        ("Follow-up", rationale),
        ("Verification", command_text),
    ]
    body = "\n".join(f"{key}: {value}" for key, value in fields)
    return PromotionRecord(record_id=record_id, text=f"### Evidence: {record_id}\n\n{body}\n")


def promotable_records(run_dir: Path, data: dict[str, Any], command_text: str) -> list[PromotionRecord]:
    if not selected_proposal_rationale(data):
        return []
    findings = data.get("findings")
    if not isinstance(findings, list):
        return []
    records: list[PromotionRecord] = []
    for finding in findings:
        if isinstance(finding, dict):
            records.append(build_record(run_dir, data, finding, command_text))
    return records


def existing_record_ids(notes_path: Path) -> set[str]:
    if not notes_path.exists():
        return set()
    try:
        parsed = parse_notes(notes_path.read_text(encoding="utf-8"))
    except EvidenceError as error:
        raise PromotionError(f"invalid Evidence Records in {notes_path}: {error}") from error
    return {record.record_id for record in parsed.records}


def insert_records(text: str, records: list[PromotionRecord]) -> str:
    record_text = "\n".join(record.text.rstrip() for record in records).rstrip() + "\n"
    if not text.strip():
        return f"# Implementation Notes\n\n{EVIDENCE_RECORDS_HEADING}\n\n{record_text}"

    lines = text.splitlines()
    heading_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == EVIDENCE_RECORDS_HEADING:
            heading_index = index
            break

    if heading_index is None:
        base = text.rstrip()
        return f"{base}\n\n{EVIDENCE_RECORDS_HEADING}\n\n{record_text}"

    insert_at = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## ") and lines[index].strip() != EVIDENCE_RECORDS_HEADING:
            insert_at = index
            break

    before = "\n".join(lines[:insert_at]).rstrip()
    after = "\n".join(lines[insert_at:]).lstrip()
    if after:
        return f"{before}\n\n{record_text}\n{after}\n"
    return f"{before}\n\n{record_text}"


def promote_harness_evaluation(run_dir: Path, notes_path: Path, *, dry_run: bool = False) -> PromotionResult:
    resolved_run_dir = run_dir.expanduser().resolve()
    resolved_notes = notes_path.expanduser()
    command_text, _validation_output = validate_run(resolved_run_dir)
    data = load_harness_evaluation(resolved_run_dir)
    records = promotable_records(resolved_run_dir, data, command_text)
    if not records:
        return PromotionResult(promoted=0, skipped=0, dry_run=dry_run, record_ids=[])

    existing_ids = existing_record_ids(resolved_notes)
    new_records = [record for record in records if record.record_id not in existing_ids]
    skipped = len(records) - len(new_records)
    if dry_run or not new_records:
        return PromotionResult(
            promoted=len(new_records),
            skipped=skipped,
            dry_run=dry_run,
            record_ids=[record.record_id for record in new_records],
        )

    existing_text = resolved_notes.read_text(encoding="utf-8") if resolved_notes.exists() else ""
    updated = insert_records(existing_text, new_records)
    resolved_notes.parent.mkdir(parents=True, exist_ok=True)
    resolved_notes.write_text(updated, encoding="utf-8")
    return PromotionResult(
        promoted=len(new_records),
        skipped=skipped,
        dry_run=dry_run,
        record_ids=[record.record_id for record in new_records],
    )
