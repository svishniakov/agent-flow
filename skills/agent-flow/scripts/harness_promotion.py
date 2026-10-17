"""Promote Harness Evaluation findings into project-local Evidence Records."""

from __future__ import annotations

import json
import re
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence_records import EvidenceError, analyze_notes, render_records
from journal_io import (JournalError, JournalSnapshot, evidence_block_upsert, evidence_record_fields,
                        evidence_record_row, load_evidence_records, memory_path)


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"
HARNESS_EVALUATION_PATH = "harness-evaluation.json"


@dataclass(frozen=True)
class PromotionResult:
    promoted: int
    skipped: int
    dry_run: bool
    record_ids: list[str]
    preview_groups: list[dict[str, Any]] = field(default_factory=list)


class PromotionError(ValueError):
    """Raised when Harness Evaluation promotion cannot proceed."""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "run"


def compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def keyword_token(value: object) -> str | None:
    token = slugify(compact(value))
    return token or None


def finding_keywords(data: dict[str, Any], finding: dict[str, Any]) -> list[str]:
    raw_values: list[object] = [
        finding.get("problem_class"),
        finding.get("type"),
        finding.get("outcome"),
        *list(data.get("learning_triggers") if isinstance(data.get("learning_triggers"), list) else []),
    ]
    keywords: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        token = keyword_token(value)
        if token is None or token in seen:
            continue
        keywords.append(token)
        seen.add(token)
    return keywords or ["harness"]


def finding_provenance(run_dir: Path, data: dict[str, Any], finding: dict[str, Any]) -> str:
    triggers = join_values(data.get("learning_triggers"))
    evidence = finding_evidence(finding)
    return "; ".join(
        [
            f"run={run_dir}",
            f"finding={compact(finding.get('id'))}",
            f"triggers={triggers}",
            f"evidence={evidence}",
        ]
    )


def load_harness_evaluation(run_dir: Path, *, snapshot=None) -> dict[str, Any]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    path = run_dir / HARNESS_EVALUATION_PATH
    if not snapshot.exists(path):
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} not found: {path}")
    try:
        data = json.loads(snapshot.read_text(path))
    except json.JSONDecodeError as error:
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} invalid JSON: {error}") from error
    if not isinstance(data, dict):
        raise PromotionError(f"{HARNESS_EVALUATION_PATH} must be a JSON object")
    return data


def validation_command_text(run_dir: Path) -> str:
    return f"python3 scripts/validate-run.py --run-dir {run_dir} --mode full"


def validate_run(run_dir: Path, *, snapshot=None) -> tuple[str, str]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    spec = importlib.util.spec_from_file_location("agent_flow_promotion_validator", VALIDATE_RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    errors = module.validate_run(run_dir, mode="full", snapshot=snapshot)
    command_text = validation_command_text(run_dir)
    if errors:
        output = "\n".join(errors)
        raise PromotionError(f"validate-run failed: {command_text}\n{output}")
    return command_text, f"PASS revision {snapshot.revision}"


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


def finding_evidence_list(finding: dict[str, Any]) -> list[str]:
    return [part.strip() for part in finding_evidence(finding).split(";") if part.strip()]


def build_context(run_dir: Path, data: dict[str, Any], finding: dict[str, Any]) -> str:
    return "; ".join(
        [
            f"run: {run_dir}",
            f"learning_triggers: {join_values(data.get('learning_triggers'))}",
            f"architecture_context: {join_values(finding.get('architecture_context'))}",
            f"architecture_capabilities: {join_values(finding.get('architecture_capabilities'))}",
        ]
    )


def build_record(run_dir: Path, data: dict[str, Any], finding: dict[str, Any], command_text: str) -> dict[str, Any]:
    """Map one validated finding onto an evidence-record writer request."""
    finding_id = compact(finding.get("id"))
    record_id = f"harness-{slugify(run_dir.name)}-{finding_id}"
    return {
        "summary": compact(finding.get("approach")),
        "status": "pass",
        "id": record_id,
        "record_type": finding_record_type(finding),
        "problem_class": compact(finding.get("problem_class")),
        "context": build_context(run_dir, data, finding),
        "outcome": compact(finding.get("outcome")),
        "evidence": finding_evidence_list(finding),
        "section": "harness",
        "keywords": finding_keywords(data, finding),
        "provenance": finding_provenance(run_dir, data, finding),
        "verification": command_text,
        "reuse_when": compact(finding.get("reuse_when")),
        "do_not_reuse_when": compact(finding.get("do_not_reuse_when")),
        "follow_up": selected_proposal_rationale(data) or "none",
    }


def promotable_records(run_dir: Path, data: dict[str, Any], command_text: str) -> list[dict[str, Any]]:
    if not selected_proposal_rationale(data):
        return []
    findings = data.get("findings")
    if not isinstance(findings, list):
        return []
    records: list[dict[str, Any]] = []
    for finding in findings:
        if isinstance(finding, dict):
            records.append(build_record(run_dir, data, finding, command_text))
    return records


def projected_notes_text(snapshot, requests: list[dict[str, Any]]) -> str:
    """Notes text as it would look after writing the given requests; nothing is written."""
    data = load_evidence_records(snapshot)
    rows = list(data["records"])
    existing = {row["id"] for row in rows}
    for request in requests:
        if request["id"] not in existing:
            rows.append(evidence_record_row(request))
    body = render_records([(row["id"], evidence_record_fields(row)) for row in rows])
    notes = memory_path(snapshot, "evidence-record")
    old = notes.read_bytes() if notes.exists() else b""
    begin = ("<!-- agent-flow:" + snapshot.run_uuid + ":begin -->\n").encode()
    end = ("<!-- agent-flow:" + snapshot.run_uuid + ":end -->\n").encode()
    return evidence_block_upsert(old, begin, end, body).decode()


def promote_harness_evaluation(run_dir: Path, *, dry_run: bool = False) -> PromotionResult:
    resolved_run_dir = run_dir.expanduser().resolve()
    snapshot = JournalSnapshot.open(resolved_run_dir)
    command_text, _validation_output = validate_run(resolved_run_dir, snapshot=snapshot)
    data = load_harness_evaluation(resolved_run_dir, snapshot=snapshot)
    records = promotable_records(resolved_run_dir, data, command_text)
    if not records:
        return PromotionResult(promoted=0, skipped=0, dry_run=dry_run, record_ids=[])

    existing_ids = {row["id"] for row in load_evidence_records(snapshot)["records"]}
    new_records = [record for record in records if record["id"] not in existing_ids]
    skipped = len(records) - len(new_records)
    if not new_records:
        return PromotionResult(promoted=0, skipped=skipped, dry_run=dry_run, record_ids=[], preview_groups=[])

    try:
        preview = analyze_notes(projected_notes_text(snapshot, new_records))
    except (EvidenceError, JournalError) as error:
        raise PromotionError(f"projected Evidence Records invalid: {error}") from error
    if preview.get("errors"):
        raise PromotionError(f"projected Evidence Records invalid: {preview['errors']}")
    record_ids = [record["id"] for record in new_records]
    preview_groups = preview_groups_for_records(preview, record_ids)
    if dry_run:
        return PromotionResult(promoted=len(new_records), skipped=skipped, dry_run=True,
                               record_ids=record_ids, preview_groups=preview_groups)

    from journal_io import write_entry
    for record in new_records:
        try:
            write_entry(resolved_run_dir, "evidence-record", record)
        except JournalError as error:
            raise PromotionError(str(error)) from error
    return PromotionResult(promoted=len(new_records), skipped=skipped, dry_run=False,
                           record_ids=record_ids, preview_groups=preview_groups)


def preview_groups_for_records(report: dict[str, Any], record_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(record_ids)
    groups: list[dict[str, Any]] = []
    for group in report.get("groups", []):
        if wanted.intersection(group.get("record_ids", [])):
            groups.append(group)
    return groups
