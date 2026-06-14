#!/usr/bin/env python3
"""Validate traceable runs, Architecture Capability Router, Architecture Artifact Authoring Automation, Claim Evidence Gate, and Harness Evaluation Loop gates."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from architecture_capabilities import (
    ARCHITECTURE_CAPABILITY_REGISTRY_PATH,
    validate_architecture_capabilities_shape,
    validate_architecture_capability_registry,
)


FULL_REQUIRED_FILES = [
    "manifest.md",
    "context.md",
    "route.md",
    "plan.md",
    "definition-of-done.md",
    "decisions.md",
    "artifacts.json",
    "timeline.jsonl",
    "final.md",
]

FULL_REQUIRED_DIRS = ["handoffs", "checks", "artifacts"]
COMPACT_REQUIRED_FILES = ["run.md", "checks.md", "final.md"]
REQUIRED_TIMELINE_KEYS = {
    "timestamp",
    "stage",
    "role",
    "stable_agent_name",
    "stable_agent_slug",
    "status",
    "summary",
    "artifacts",
    "next_step",
}
FINAL_VERDICTS = {"ship", "pass-with-risks", "blocked", "fail"}
POSITIVE_FINAL_VERDICTS = {"ship", "pass-with-risks"}
AGENT_TODO_PLACEHOLDER = "TODO(agent):"
AGENT_EXECUTION_MODES = {"subagent", "role-lane"}
DELEGATION_SUMMARY_PATH = "delegation-summary.json"
DELEGATION_TRACE_SECTION = "Delegation Trace"
CONTINUATION_SUMMARY_PATH = "continuation-summary.json"
CONTINUATION_SUMMARY_SECTION = "Continuation Summary"
CONTINUATION_REVALIDATION_SECTION = "Continuation Revalidation"
CONTINUATION_REVIEW_SECTION = "Continuation Review"
HARNESS_EVALUATION_PATH = "harness-evaluation.json"
HARNESS_EVALUATION_SECTION = "Harness Evaluation"
HARNESS_EVALUATION_REVIEW_SECTION = "Harness Evaluation Review"
CLAIM_EVIDENCE_PATH = "claim-evidence.json"
CLAIM_EVIDENCE_LABEL = "Claim Evidence"
LANE_TYPES = {"architecture", "implementation", "integration", "qa", "review"}
TRACE_BUDGETS = {"release", "standard"}
WORKER_LANE_TYPES = {"implementation", "integration"}
LANE_STATUSES = {
    "planned",
    "spawned",
    "active",
    "pass",
    "pass-with-risks",
    "fail",
    "blocked",
    "timed-out",
    "replaced",
}
SUCCESSFUL_LANE_STATUSES = {"pass", "pass-with-risks"}
UNRESOLVED_LANE_STATUSES = {"planned", "spawned", "active", "timed-out", "fail", "blocked"}
IMPLEMENTATION_STAGES = {"implementation", "fix"}
SUCCESSFUL_CHECK_STAGES = {"verification", "checks"}
SUCCESS_STATUSES = {"pass", "ship", "pass-with-risks"}
CONTINUATION_STAGES = {"blocked-checkpoint", "continuation"}
CONTINUATION_STATUSES = {"resumed-ready", "resumed-blocked", "resumed-fail"}
HARNESS_EVALUATION_STATUSES = {"evaluated", "needs-review", "blocked-learning"}
HARNESS_LEARNING_TRIGGERS = {
    "continuation",
    "risk-mitigation",
    "risk-resolution",
    "blocked-resolution",
    "architecture-drift",
    "architecture-recheck",
    "readiness-recovery",
    "nonpositive-architecture-final",
}
HARNESS_FINDING_TYPES = {
    "recovery-success",
    "architecture-failure",
    "orchestration-failure",
    "qa-gap",
    "readiness-gap",
    "anti-pattern",
    "local-practice-candidate",
}
HARNESS_OUTCOMES = {"success", "failure", "regression", "rejected", "unknown"}
HARNESS_PROPOSAL_TYPES = {"evidence-record"}
HARNESS_PROPOSAL_TARGET = "Evidence Records"
CLAIM_EVIDENCE_STATUSES = {"supported", "gap"}
CLAIM_EVIDENCE_OWNER_TYPES = {"qa", "review"}
COMMIT_HASH_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
COMMIT_DECLARATION_PATTERN = re.compile(
    r"(?i)(^\s*(?:[-*]\s*)?(?:product\s+)?commit\s*:|committed\s+as)"
)
KEBAB_CASE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ARCHITECTURE_CONTRACT_SECTIONS = [
    "Selected Architecture",
    "Rejected Alternatives",
    "Module Boundaries",
    "Data And State Flow",
    "Public Contracts",
    "Worker Ownership",
    "Forbidden Changes",
    "QA Gates",
    "Reviewer Checklist",
    "Stop Conditions",
]
ARCHITECTURE_DESIGN_BRIEF_SECTIONS = [
    "Problem Shape",
    "Selected Matrix Facets",
    "System Boundaries",
    "Data And State Model",
    "Public Interfaces",
    "Execution Plan",
    "Risk Model",
    "Verification Strategy",
    "Open Questions",
    "Decision",
]
ARCHITECTURE_CONTRACT_SECTION_SET = set(ARCHITECTURE_CONTRACT_SECTIONS)
ARCHITECTURE_DESIGN_DECISION_STATUSES = {"approved", "needs-revision", "rejected"}
ARCHITECTURE_DESIGN_DECISION_STATUS_LINES = {
    "Status: approved": "approved",
    "Status: needs-revision": "needs-revision",
    "Status: rejected": "rejected",
}
ARCHITECTURE_DESIGN_DECISION_SECTION = "Decision"
ARCHITECTURE_DESIGN_EXECUTION_PLAN_SECTION = "Execution Plan"
ARCHITECTURE_DESIGN_MATRIX_SECTION = "Selected Matrix Facets"
ARCHITECTURE_COMPLIANCE_STATUSES = {"compliant", "drift"}
ARCHITECTURE_COMPLIANCE_SECTION = "Architecture Compliance"
ENGINEERING_SIMPLICITY_SECTION = "Engineering Simplicity"
ENGINEERING_SIMPLICITY_SCOPE_FIELD = "engineering_simplicity_scope"
ENGINEERING_SIMPLICITY_SCOPE_SECTION = "Engineering Simplicity Scope"
ENGINEERING_SIMPLICITY_STATUSES = {"pass", "fixed", "drift"}
ENGINEERING_SIMPLICITY_REQUIRED_CHECKS = [
    "no-extra-work",
    "stdlib-native-first",
    "existing-helper-first",
    "dependency-justified",
    "abstraction-justified",
    "smallest-working-diff",
    "tests-fit-risk",
]
ENGINEERING_SIMPLICITY_CHECK_SET = set(ENGINEERING_SIMPLICITY_REQUIRED_CHECKS)
ENGINEERING_SIMPLICITY_FIXABLE_PHRASES = [
    "fixable",
    "overengineering",
    "over-engineering",
    "duplicated helper",
    "duplicate helper",
    "unnecessary abstraction",
    "dependency drift",
    "stack drift",
    "wider-than-needed",
    "wider than needed",
]
ARCHITECTURE_INVARIANTS_SECTION = "Architecture Invariants"
ARCHITECTURE_MATRIX_MISMATCHES_SECTION = "Architecture Matrix Mismatches"
CONTRACT_DRIFT_SECTION = "Contract Drift"
RISK_MITIGATIONS_PATH = "risk-mitigations.json"
RISK_MITIGATIONS_SECTION = "Risk Mitigations"
RISK_MITIGATION_REVIEW_SECTION = "Risk Mitigation Review"
RISK_RESOLUTIONS_PATH = "risk-resolutions.json"
RISK_RESOLUTIONS_SECTION = "Risk Resolutions"
RISK_RESOLUTION_VERIFICATION_SECTION = "Risk Resolution Verification"
RISK_RESOLUTION_REVIEW_SECTION = "Risk Resolution Review"
VERIFICATION_READINESS_PATH = "verification-readiness.json"
VERIFICATION_READINESS_SECTION = "Verification Readiness"
VERIFICATION_GATE_RESULTS_SECTION = "Verification Gate Results"
VERIFICATION_READINESS_STATUSES = {"ready", "needs-approval", "paused-blocked", "blocked"}
VERIFICATION_READINESS_ATTEMPT_STATUSES = {"ready", "needs-approval", "blocked"}
VERIFICATION_GATE_READINESS_STATUSES = {"ready", "needs-approval", "blocked"}
VERIFICATION_APPROVAL_STATUSES = {"pending", "approved", "declined"}
VERIFICATION_APPROVAL_EXECUTION_STATUSES = {"succeeded", "failed", "not-run"}
VERIFICATION_RESULT_STATUSES = {"pass", "blocked"}
SENIOR_QA_TEST_DESIGN_REVIEW_SECTION = "Senior QA Test Design Review"
RESOLUTION_ARCHITECT_REVIEW_SECTION = "Resolution Architect Review"
SUPERVISING_ARCHITECT_REVIEW_SECTION = "Supervising Architect Review"
BLOCKED_RECOVERY_PATH_LABEL = "Blocked Recovery Path"
RISK_MITIGATION_STATUS = "identified"
RISK_MITIGATION_NEXT_GATE = "resolution"
RISK_MITIGATION_CATEGORIES = {
    "verification-gap",
    "architecture-drift",
    "incomplete-implementation",
    "test-gap",
    "security-risk",
    "data-risk",
    "ux-risk",
    "dependency-risk",
    "release-risk",
    "unknown",
}
RISK_MITIGATION_REQUIRED_FIELDS = [
    "id",
    "status",
    "detected_by",
    "category",
    "problem",
    "impact",
    "affected_scope",
    "evidence",
    "next_gate",
    "owner_lane",
]
RISK_RESOLUTION_STATUSES = {"fixed", "mitigated", "contained", "unresolved"}
RISK_RESOLUTION_PASS_STATUSES = {"fixed", "mitigated", "contained"}
RISK_RESOLUTION_ATTEMPT_STATUSES = {"fixed", "mitigated", "contained", "blocked", "unresolved"}
RISK_RESOLUTION_TYPES = {
    "code-change",
    "test-added",
    "evidence-added",
    "scope-contained",
    "architecture-recheck",
    "config-change",
    "docs-corrected",
    "not-resolved",
}
RISK_RESOLUTION_REQUIRED_FIELDS = [
    "risk_id",
    "status",
    "resolution_type",
    "owner_lane",
    "resolution",
    "evidence",
    "verification",
    "verified_by",
    "reviewed_by",
]
BLOCKED_RESOLUTION_REASONS = {
    "acceptance-criteria-gap",
    "qa-execution-gap",
    "wrong-qa-conclusion",
    "external-blocker",
    "invalid-resolution-approach",
    "bad-resolution-implementation",
    "architecture-mismatch",
    "insufficient-evidence",
    "test-flake",
    "unknown",
}
ROLLBACK_STATUSES = {"rolled-back", "not-needed", "not-possible"}
BLOCKED_LESSON_STATUSES = {"quarantined", "confirmed"}
SENIOR_QA_REVIEW_STATUSES = {"criteria-expanded", "criteria-adequate", "criteria-blocked"}
SENIOR_QA_RECHECK_RESULTS = {"pass", "blocked", "fail"}
ARCHITECT_REVIEW_DECISIONS = {"revised-approach", "confirmed-approach", "blocked"}
SUPERVISING_ARCHITECT_REVIEW_DECISIONS = {
    "revised-approach",
    "confirmed-final-block",
    "external-blocker",
    "fail",
}
ARCHITECTURE_CONTEXT_AXES = {
    "product_context": "Product Context",
    "application_surface": "Application Surface",
    "architecture_pattern": "Architecture Pattern",
    "stack_runtime": "Stack Runtime",
    "risk_gates": "Risk Gates",
    "verification_gates": "Verification Gates",
}
ARCHITECTURE_MATRIX_PATH = Path(__file__).resolve().parents[1] / "references" / "architecture-matrix.md"
MATRIX_FACET_PATTERN = re.compile(r"^\s*-\s+`([^`]+)`\s*:")
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
CLAIM_EVIDENCE_PATTERN = re.compile(r"^\s*-\s+Claim Evidence:\s+`([^`]+)`\s*$")


def detect_mode(run_dir: Path, requested_mode: str) -> str:
    if requested_mode != "auto":
        return requested_mode

    if (run_dir / "run.md").exists() and not (run_dir / "manifest.md").exists():
        return "compact"
    return "full"


def is_empty_file(path: Path) -> bool:
    return not path.read_text(encoding="utf-8").strip()


def validate_artifacts_index(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return errors

    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError as exc:
        return [f"artifacts.json invalid JSON: {exc}"]

    if isinstance(data, list):
        return errors
    if isinstance(data, dict):
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append("artifacts.json field 'artifacts' must be a JSON array")
        return errors

    errors.append("artifacts.json must be a JSON array or an object with an artifacts array")
    return errors


def load_json(path: Path, display_name: str) -> tuple[object | None, list[str]]:
    if not path.exists():
        return None, []
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null"), []
    except json.JSONDecodeError as exc:
        return None, [f"{display_name} invalid JSON: {exc}"]


def validate_jsonl(path: Path, display_name: str | None = None) -> list[str]:
    errors: list[str] = []
    label = display_name or path.name
    if not path.exists():
        return [f"missing {label}"]
    has_event = False
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        has_event = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{label}:{index}: invalid JSON: {exc}")
            continue

        if not isinstance(event, dict):
            errors.append(f"{label}:{index}: event must be a JSON object")
            continue

        missing = REQUIRED_TIMELINE_KEYS - event.keys()
        if missing:
            errors.append(f"{label}:{index}: missing keys: {', '.join(sorted(missing))}")
        if "artifacts" in event and not isinstance(event["artifacts"], list):
            errors.append(f"{label}:{index}: artifacts must be a JSON array")
    if not has_event:
        errors.append(f"{label}: no events")
    return errors


def load_jsonl_events(path: Path) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    errors: list[str] = []
    if not path.exists():
        return events, [f"missing {path.name}"]

    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index}: invalid JSON: {exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"{path.name}:{index}: event must be a JSON object")
            continue
        events.append(event)
    return events, errors


def parse_event_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def event_key(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_execution_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("_", "-")


def normalize_lane_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("_", "-")


def extract_declared_commit_hashes(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []

    hashes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not COMMIT_DECLARATION_PATTERN.search(line):
            continue
        hashes.extend(match.group(0).lower() for match in COMMIT_HASH_PATTERN.finditer(line))
    return list(dict.fromkeys(hashes))


def event_commit_hashes(event: dict) -> list[str]:
    values: list[str] = []
    commit_hash = event.get("commit_hash")
    if isinstance(commit_hash, str):
        values.append(commit_hash)
    for field in ["summary", "next_step"]:
        value = event.get(field)
        if isinstance(value, str):
            values.extend(match.group(0) for match in COMMIT_HASH_PATTERN.finditer(value))
    return [value.lower() for value in values]


def read_fields(path: Path, name: str) -> list[str]:
    prefix = f"{name}:"
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            values.append(line[len(prefix) :].strip())
    return values


def read_single_field(path: Path, name: str) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    values = read_fields(path, name)
    if len(values) != 1:
        return None
    return values[0]


def hashes_match(left: str, right: str) -> bool:
    return left.startswith(right) or right.startswith(left)


def validate_final_timeline_event(run_dir: Path, require_timeline: bool) -> list[str]:
    timeline_path = run_dir / "timeline.jsonl"
    if not timeline_path.exists():
        return ["missing timeline.jsonl for final timeline event"] if require_timeline else []

    timeline_events, timeline_load_errors = load_jsonl_events(timeline_path)
    if timeline_load_errors:
        return []
    if not timeline_events:
        return ["timeline.jsonl: no events"]

    errors: list[str] = []
    final_events = [event for event in timeline_events if event.get("stage") == "final"]
    if not final_events:
        errors.append("timeline.jsonl: missing final event")
    elif len(final_events) > 1:
        errors.append("timeline.jsonl: multiple final events")

    last_event = timeline_events[-1]
    if last_event.get("stage") != "final":
        errors.append("timeline.jsonl: last event must be stage=final")
    if last_event.get("role") != "orchestrator":
        errors.append("timeline.jsonl: final event must be owned by role=orchestrator")
    return errors


def validate_timeline_sequence(run_dir: Path) -> list[str]:
    timeline_path = run_dir / "timeline.jsonl"
    if not timeline_path.exists():
        return []

    timeline_events, timeline_load_errors = load_jsonl_events(timeline_path)
    if timeline_load_errors or not timeline_events:
        return []

    errors: list[str] = []
    previous_timestamp: datetime | None = None
    for index, event in enumerate(timeline_events, start=1):
        timestamp = parse_event_timestamp(event.get("timestamp"))
        if timestamp is None:
            errors.append(f"timeline.jsonl:{index}: invalid timestamp")
            continue
        if previous_timestamp is not None and timestamp < previous_timestamp:
            errors.append(f"timeline.jsonl:{index}: timestamp must be non-decreasing")
        previous_timestamp = timestamp

    last_implementation_index = max(
        (
            index
            for index, event in enumerate(timeline_events)
            if event.get("role") == "orchestrator" and event.get("stage") in IMPLEMENTATION_STAGES
        ),
        default=None,
    )
    last_successful_check_index = max(
        (
            index
            for index, event in enumerate(timeline_events)
            if event.get("role") == "orchestrator"
            and event.get("stage") in SUCCESSFUL_CHECK_STAGES
            and event.get("status") in SUCCESS_STATUSES
        ),
        default=None,
    )

    if (
        last_implementation_index is not None
        and last_successful_check_index is not None
        and last_successful_check_index < last_implementation_index
    ):
        errors.append(
            "timeline.jsonl: final successful verification/checks event must come after "
            "the last orchestrator implementation/fix event"
        )

    commit_indexes = [
        index
        for index, event in enumerate(timeline_events)
        if event.get("role") == "orchestrator" and event.get("stage") == "commit"
    ]
    final_indexes = [
        index
        for index, event in enumerate(timeline_events)
        if event.get("role") == "orchestrator" and event.get("stage") == "final"
    ]
    if commit_indexes:
        last_commit_index = max(commit_indexes)
        if last_successful_check_index is not None and last_commit_index < last_successful_check_index:
            errors.append(
                "timeline.jsonl: orchestrator commit event must come after the "
                "last successful verification/checks event"
            )
        if final_indexes and last_commit_index > max(final_indexes):
            errors.append("timeline.jsonl: orchestrator commit event must come before final event")

    declared_commit_hashes = extract_declared_commit_hashes(run_dir / "final.md")
    if declared_commit_hashes:
        commit_events = [
            event
            for event in timeline_events
            if event.get("role") == "orchestrator" and event.get("stage") == "commit"
        ]
        if not commit_events:
            errors.append("timeline.jsonl: final.md declares a commit but no orchestrator stage=commit event exists")
        else:
            commit_event_hashes = [
                commit_hash
                for event in commit_events
                for commit_hash in event_commit_hashes(event)
            ]
            for declared_hash in declared_commit_hashes:
                if not any(hashes_match(declared_hash, event_hash) for event_hash in commit_event_hashes):
                    errors.append(
                        f"timeline.jsonl: final.md declares commit {declared_hash} "
                        "but no matching stage=commit event records it"
                    )

    return errors


def validate_agent_traces(run_dir: Path) -> list[str]:
    errors: list[str] = []
    agents_dir = run_dir / "agents"
    if not agents_dir.exists():
        return errors
    if not agents_dir.is_dir():
        return ["agents exists but is not a directory"]

    timeline_events, timeline_load_errors = load_jsonl_events(run_dir / "timeline.jsonl")
    timeline_event_keys = set()
    if not timeline_load_errors:
        timeline_event_keys = {event_key(event) for event in timeline_events}
    else:
        errors.extend(f"agents require timeline.jsonl: {error}" for error in timeline_load_errors)

    for agent_dir in sorted(agents_dir.iterdir()):
        display_dir = agent_dir.relative_to(run_dir).as_posix()
        if not agent_dir.is_dir():
            errors.append(f"{display_dir} is not a directory")
            continue

        trace_path = agent_dir / "trace.jsonl"
        display_name = trace_path.relative_to(run_dir).as_posix()
        errors.extend(validate_jsonl(trace_path, display_name))
        trace_events, trace_load_errors = load_jsonl_events(trace_path)
        if trace_load_errors:
            continue

        execution_modes = {
            normalize_execution_mode(event.get("execution_mode"))
            for event in trace_events
            if event.get("execution_mode") is not None
        }
        invalid_modes = sorted(mode for mode in execution_modes if mode not in AGENT_EXECUTION_MODES)
        if invalid_modes:
            errors.append(f"{display_name}: invalid execution_mode values: {', '.join(invalid_modes)}")

        is_role_lane = execution_modes == {"role-lane"}
        if not is_role_lane:
            spawned_events = [event for event in trace_events if event.get("stage") == "spawned"]
            if not spawned_events:
                errors.append(
                    f"{display_name}: missing spawned event with codex_thread_id; "
                    "use execution_mode=role-lane for non-spawned role lanes"
                )
            elif not any(event.get("codex_thread_id") for event in spawned_events):
                errors.append(f"{display_name}: spawned event missing codex_thread_id")

        if timeline_load_errors:
            continue
        for index, event in enumerate(trace_events, start=1):
            if event_key(event) not in timeline_event_keys:
                errors.append(f"{display_name}:{index}: event missing from timeline.jsonl")
    return errors


def resolve_run_path(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return run_dir / path


def lane_label(lane_id: object, index: int) -> str:
    if isinstance(lane_id, str) and lane_id:
        return lane_id
    return f"lanes[{index}]"


def validate_lane_string_field(lane: dict, field: str, label: str, errors: list[str]) -> str | None:
    value = lane.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"lane-map.json: lane {label} missing string field '{field}'")
        return None
    return value


def validate_lane_path_list(run_dir: Path, paths: object, label: str, field: str, errors: list[str]) -> list[str]:
    if not isinstance(paths, list):
        errors.append(f"lane-map.json: lane {label} field '{field}' must be an array")
        return []

    result: list[str] = []
    for index, path in enumerate(paths):
        if not isinstance(path, str) or not path:
            errors.append(f"lane-map.json: lane {label} {field}[{index}] must be a non-empty string")
            continue
        result.append(path)
        if not resolve_run_path(run_dir, path).exists():
            errors.append(f"lane-map.json: lane {label} {field}[{index}] not found: {path}")
    return result


def lane_artifact_references(lanes: list[object]) -> list[str]:
    references: list[str] = []
    seen: set[str] = set()
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        handoff = lane.get("handoff")
        if isinstance(handoff, str) and handoff and handoff not in seen:
            references.append(handoff)
            seen.add(handoff)
        evidence = lane.get("evidence")
        if isinstance(evidence, list):
            for path in evidence:
                if isinstance(path, str) and path and path not in seen:
                    references.append(path)
                    seen.add(path)
        design_brief = lane.get("architecture_design_brief")
        if isinstance(design_brief, str) and design_brief and design_brief not in seen:
            references.append(design_brief)
            seen.add(design_brief)
    return references


def validate_no_agent_placeholders(run_dir: Path, lanes: list[object]) -> list[str]:
    errors: list[str] = []
    for reference in lane_artifact_references(lanes):
        path = resolve_run_path(run_dir, reference)
        if not path.exists() or not path.is_file():
            continue
        if AGENT_TODO_PLACEHOLDER in path.read_text(encoding="utf-8"):
            errors.append(
                "lane-map.json: positive final Verdict blocked by "
                f"{AGENT_TODO_PLACEHOLDER} in {reference}"
            )
    return errors


def risk_id(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def validate_risk_mitigation_text_field(risk: dict, field: str, label: str) -> list[str]:
    value = risk.get(field)
    if not isinstance(value, str) or not value.strip():
        return [f"risk-mitigations.json {label}.{field} must be a non-empty string"]
    return []


def validate_risk_mitigation_evidence(run_dir: Path, value: object, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return [f"risk-mitigations.json {label}.evidence must be a non-empty array"]
    for index, path in enumerate(value):
        if not isinstance(path, str) or not path:
            errors.append(
                f"risk-mitigations.json {label}.evidence[{index}] must be a non-empty string"
            )
            continue
        if not resolve_run_path(run_dir, path).exists():
            errors.append(
                f"risk-mitigations.json {label}.evidence[{index}] not found: {path}"
            )
    return errors


def validate_risk_mitigations(
    run_dir: Path,
    final_verdict: str | None,
) -> tuple[list[dict], list[str]]:
    path = run_dir / RISK_MITIGATIONS_PATH
    required = final_verdict == "pass-with-risks"
    if not path.exists():
        if required:
            return [], [f"{RISK_MITIGATIONS_PATH} is required for Verdict: pass-with-risks"]
        return [], []

    data, load_errors = load_json(path, RISK_MITIGATIONS_PATH)
    if load_errors:
        return [], load_errors
    if not isinstance(data, dict):
        return [], [f"{RISK_MITIGATIONS_PATH} must be a JSON object"]

    errors: list[str] = []
    if data.get("version") != 1:
        errors.append(f"{RISK_MITIGATIONS_PATH} field 'version' must be 1")

    risks = data.get("risks")
    if not isinstance(risks, list) or not risks:
        errors.append(f"{RISK_MITIGATIONS_PATH} field 'risks' must be a non-empty array")
        return [], errors

    parsed_risks: list[dict] = []
    seen_ids: set[str] = set()
    for index, risk in enumerate(risks):
        label = f"risks[{index}]"
        if not isinstance(risk, dict):
            errors.append(f"{RISK_MITIGATIONS_PATH} {label} must be an object")
            continue

        for field in RISK_MITIGATION_REQUIRED_FIELDS:
            if field not in risk:
                errors.append(f"{RISK_MITIGATIONS_PATH} {label} missing field: {field}")

        current_id = risk_id(risk.get("id"))
        if current_id is None:
            errors.append(f"{RISK_MITIGATIONS_PATH} {label}.id must be a non-empty string")
        elif not KEBAB_CASE_PATTERN.fullmatch(current_id):
            errors.append(f"{RISK_MITIGATIONS_PATH} {label}.id must be kebab-case")
        elif current_id in seen_ids:
            errors.append(f"{RISK_MITIGATIONS_PATH} duplicate risk id: {current_id}")
        else:
            seen_ids.add(current_id)

        if risk.get("status") != RISK_MITIGATION_STATUS:
            errors.append(f"{RISK_MITIGATIONS_PATH} {label}.status must be identified")

        category = risk.get("category")
        if category not in RISK_MITIGATION_CATEGORIES:
            allowed = ", ".join(sorted(RISK_MITIGATION_CATEGORIES))
            errors.append(
                f"{RISK_MITIGATIONS_PATH} {label}.category invalid "
                f"(expected one of: {allowed})"
            )

        for field in ["detected_by", "problem", "impact", "affected_scope", "owner_lane"]:
            errors.extend(validate_risk_mitigation_text_field(risk, field, label))

        errors.extend(validate_risk_mitigation_evidence(run_dir, risk.get("evidence"), label))

        if risk.get("next_gate") != RISK_MITIGATION_NEXT_GATE:
            errors.append(f"{RISK_MITIGATIONS_PATH} {label}.next_gate must be resolution")

        parsed_risks.append(risk)

    return parsed_risks, errors


def validate_risk_resolution_text_field(resolution: dict, field: str, label: str) -> list[str]:
    value = resolution.get(field)
    if not isinstance(value, str) or not value.strip():
        return [f"{RISK_RESOLUTIONS_PATH} {label}.{field} must be a non-empty string"]
    return []


def validate_risk_resolution_evidence(run_dir: Path, value: object, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return [f"{RISK_RESOLUTIONS_PATH} {label}.evidence must be a non-empty array"]
    for index, path in enumerate(value):
        if not isinstance(path, str) or not path:
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.evidence[{index}] must be a non-empty string"
            )
            continue
        if not resolve_run_path(run_dir, path).exists():
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.evidence[{index}] not found: {path}"
            )
    return errors


def validate_risk_resolution_string_list(
    value: object,
    label: str,
    field: str,
    *,
    required_non_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or (required_non_empty and not value):
        return [f"{RISK_RESOLUTIONS_PATH} {label}.{field} must be a non-empty array"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.{field}[{index}] must be a non-empty string"
            )
    return errors


def validate_risk_resolution_rollback(
    run_dir: Path,
    rollback: object,
    label: str,
    final_verdict: str | None,
) -> list[str]:
    if not isinstance(rollback, dict):
        return [f"{RISK_RESOLUTIONS_PATH} {label} missing rollback"]

    errors: list[str] = []
    status = rollback.get("status")
    if status not in ROLLBACK_STATUSES:
        allowed = ", ".join(sorted(ROLLBACK_STATUSES))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.rollback.status invalid "
            f"(expected one of: {allowed})"
        )
    elif status == "not-possible" and final_verdict == "pass-with-risks":
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.rollback.status not-possible "
            "is not allowed for Verdict: pass-with-risks"
        )

    summary = rollback.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{RISK_RESOLUTIONS_PATH} {label}.rollback.summary must be a non-empty string")

    if status == "rolled-back":
        errors.extend(
            validate_risk_resolution_evidence(
                run_dir,
                rollback.get("evidence"),
                f"{label}.rollback",
            )
        )
    elif "evidence" in rollback:
        errors.extend(
            validate_risk_resolution_evidence(
                run_dir,
                rollback.get("evidence"),
                f"{label}.rollback",
            )
        )
    return errors


def validate_blocked_lesson(lesson: object, label: str) -> list[str]:
    if not isinstance(lesson, dict):
        return [f"{RISK_RESOLUTIONS_PATH} {label} missing blocked_lesson"]

    errors: list[str] = []
    status = lesson.get("status")
    if status not in BLOCKED_LESSON_STATUSES:
        allowed = ", ".join(sorted(BLOCKED_LESSON_STATUSES))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_lesson.status invalid "
            f"(expected one of: {allowed})"
        )
    classification = lesson.get("classification")
    if classification not in BLOCKED_RESOLUTION_REASONS:
        allowed = ", ".join(sorted(BLOCKED_RESOLUTION_REASONS))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_lesson.classification invalid "
            f"(expected one of: {allowed})"
        )
    summary = lesson.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{RISK_RESOLUTIONS_PATH} {label}.blocked_lesson.summary must be a non-empty string")
    errors.extend(
        validate_risk_resolution_string_list(
            lesson.get("forbidden_repeat"),
            f"{label}.blocked_lesson",
            "forbidden_repeat",
        )
    )
    return errors


def validate_senior_qa_recovery(
    run_dir: Path,
    value: object,
    label: str,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery missing senior_qa_test_design_review"]

    errors: list[str] = []
    lane = value.get("lane")
    if not isinstance(lane, str) or not lane.strip():
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.senior_qa_test_design_review.lane "
            "must be a non-empty string"
        )
    status = value.get("status")
    if status not in SENIOR_QA_REVIEW_STATUSES:
        allowed = ", ".join(sorted(SENIOR_QA_REVIEW_STATUSES))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.senior_qa_test_design_review.status "
            f"invalid (expected one of: {allowed})"
        )
    for field in [
        "covered_acceptance_criteria",
        "missing_acceptance_criteria",
        "ambiguous_acceptance_criteria",
        "added_acceptance_criteria",
        "external_blockers",
    ]:
        errors.extend(
            validate_risk_resolution_string_list(
                value.get(field),
                f"{label}.blocked_recovery.senior_qa_test_design_review",
                field,
                required_non_empty=False,
            )
        )
    for field in [
        "test_cases",
        "edge_cases",
        "negative_cases",
        "regression_cases",
        "integration_cases",
        "data_state_cases",
        "environment_cases",
    ]:
        errors.extend(
            validate_risk_resolution_string_list(
                value.get(field),
                f"{label}.blocked_recovery.senior_qa_test_design_review",
                field,
            )
        )
    recheck_result = value.get("recheck_result")
    if recheck_result not in SENIOR_QA_RECHECK_RESULTS:
        allowed = ", ".join(sorted(SENIOR_QA_RECHECK_RESULTS))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.senior_qa_test_design_review.recheck_result "
            f"invalid (expected one of: {allowed})"
        )
    errors.extend(
        validate_risk_resolution_evidence(
            run_dir,
            value.get("evidence"),
            f"{label}.blocked_recovery.senior_qa_test_design_review",
        )
    )
    return errors


def validate_architect_recovery(
    run_dir: Path,
    value: object,
    label: str,
    field: str,
    decisions: set[str],
    *,
    require_instruction: bool = False,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery missing {field}"]

    errors: list[str] = []
    lane = value.get("lane")
    if not isinstance(lane, str) or not lane.strip():
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.{field}.lane must be a non-empty string"
        )
    decision = value.get("decision")
    if decision not in decisions:
        allowed = ", ".join(sorted(decisions))
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.{field}.decision invalid "
            f"(expected one of: {allowed})"
        )
    instruction = value.get("instruction")
    if require_instruction and (not isinstance(instruction, str) or not instruction.strip()):
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.{field}.instruction "
            "must be a non-empty string"
        )
    elif decision == "revised-approach" and (not isinstance(instruction, str) or not instruction.strip()):
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.{field}.instruction "
            "must be a non-empty string"
        )
    elif "instruction" in value and (not isinstance(instruction, str) or not instruction.strip()):
        errors.append(
            f"{RISK_RESOLUTIONS_PATH} {label}.blocked_recovery.{field}.instruction "
            "must be a non-empty string"
        )
    errors.extend(
        validate_risk_resolution_string_list(
            value.get("forbidden_repeat"),
            f"{label}.blocked_recovery.{field}",
            "forbidden_repeat",
        )
    )
    errors.extend(
        validate_risk_resolution_evidence(
            run_dir,
            value.get("evidence"),
            f"{label}.blocked_recovery.{field}",
        )
    )
    return errors


def validate_resolution_attempts(
    run_dir: Path,
    resolution: dict,
    label: str,
    final_verdict: str | None,
) -> list[str]:
    attempts = resolution.get("attempts")
    if attempts is None:
        return []
    if not isinstance(attempts, list) or not attempts:
        return [f"{RISK_RESOLUTIONS_PATH} {label}.attempts must be a non-empty array"]

    errors: list[str] = []
    if len(attempts) > 3:
        errors.append(f"{RISK_RESOLUTIONS_PATH} {label}.attempts must not contain more than 3 attempts")

    attempt_numbers: list[int] = []
    blocked_attempt_numbers: set[int] = set()
    for index, attempt in enumerate(attempts):
        attempt_label = f"{label}.attempts[{index}]"
        if not isinstance(attempt, dict):
            errors.append(f"{RISK_RESOLUTIONS_PATH} {attempt_label} must be an object")
            continue

        number = attempt.get("attempt")
        if not isinstance(number, int) or isinstance(number, bool):
            errors.append(f"{RISK_RESOLUTIONS_PATH} {attempt_label}.attempt must be an integer")
        else:
            attempt_numbers.append(number)

        status = attempt.get("status")
        if status not in RISK_RESOLUTION_ATTEMPT_STATUSES:
            allowed = ", ".join(sorted(RISK_RESOLUTION_ATTEMPT_STATUSES))
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {attempt_label}.status invalid "
                f"(expected one of: {allowed})"
            )

        resolution_type = attempt.get("resolution_type")
        if resolution_type not in RISK_RESOLUTION_TYPES:
            allowed = ", ".join(sorted(RISK_RESOLUTION_TYPES))
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {attempt_label}.resolution_type invalid "
                f"(expected one of: {allowed})"
            )

        for field in ["owner_lane", "resolution", "verification", "verified_by", "reviewed_by"]:
            errors.extend(validate_risk_resolution_text_field(attempt, field, attempt_label))

        errors.extend(validate_risk_resolution_evidence(run_dir, attempt.get("evidence"), attempt_label))

        if status == "blocked":
            if isinstance(number, int) and not isinstance(number, bool):
                blocked_attempt_numbers.add(number)
            blocked_reason = attempt.get("blocked_reason")
            if blocked_reason not in BLOCKED_RESOLUTION_REASONS:
                allowed = ", ".join(sorted(BLOCKED_RESOLUTION_REASONS))
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {attempt_label}.blocked_reason invalid "
                    f"(expected one of: {allowed})"
                )
            errors.extend(validate_risk_resolution_rollback(run_dir, attempt.get("rollback"), attempt_label, final_verdict))
            errors.extend(validate_blocked_lesson(attempt.get("blocked_lesson"), attempt_label))

    if attempt_numbers != list(range(1, len(attempt_numbers) + 1)):
        errors.append(f"{RISK_RESOLUTIONS_PATH} {label}.attempts must be contiguous from 1")

    if final_verdict == "pass-with-risks" and attempts:
        last_attempt = attempts[-1]
        if isinstance(last_attempt, dict) and last_attempt.get("status") not in RISK_RESOLUTION_PASS_STATUSES:
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.attempts last attempt must be fixed, mitigated, or contained "
                "for Verdict: pass-with-risks"
            )

    if blocked_attempt_numbers:
        recovery = resolution.get("blocked_recovery")
        if not isinstance(recovery, dict):
            errors.append(f"{RISK_RESOLUTIONS_PATH} {label} missing blocked_recovery")
            recovery = {}
        if 1 in blocked_attempt_numbers:
            errors.extend(
                validate_senior_qa_recovery(
                    run_dir,
                    recovery.get("senior_qa_test_design_review"),
                    label,
                )
            )
            errors.extend(
                validate_architect_recovery(
                    run_dir,
                    recovery.get("architect_review"),
                    label,
                    "architect_review",
                    ARCHITECT_REVIEW_DECISIONS,
                    require_instruction=True,
                )
            )
        if 2 in blocked_attempt_numbers:
            errors.extend(
                validate_architect_recovery(
                    run_dir,
                    recovery.get("supervising_architect_review"),
                    label,
                    "supervising_architect_review",
                    SUPERVISING_ARCHITECT_REVIEW_DECISIONS,
                )
            )
        if 3 in blocked_attempt_numbers and final_verdict not in {"blocked", "fail"}:
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.attempts third blocked attempt requires "
                "Verdict: blocked or fail"
            )

    return errors


def validate_risk_resolutions(
    run_dir: Path,
    final_verdict: str | None,
    mitigation_risks: list[dict],
) -> tuple[list[dict], list[str]]:
    path = run_dir / RISK_RESOLUTIONS_PATH
    required = final_verdict == "pass-with-risks"
    if not path.exists():
        if required:
            return [], [f"{RISK_RESOLUTIONS_PATH} is required for Verdict: pass-with-risks"]
        return [], []

    data, load_errors = load_json(path, RISK_RESOLUTIONS_PATH)
    if load_errors:
        return [], load_errors
    if not isinstance(data, dict):
        return [], [f"{RISK_RESOLUTIONS_PATH} must be a JSON object"]

    errors: list[str] = []
    if data.get("version") != 1:
        errors.append(f"{RISK_RESOLUTIONS_PATH} field 'version' must be 1")

    resolutions = data.get("resolutions")
    if not isinstance(resolutions, list) or not resolutions:
        errors.append(f"{RISK_RESOLUTIONS_PATH} field 'resolutions' must be a non-empty array")
        return [], errors

    mitigation_ids = set(risk_mitigation_ids(mitigation_risks))
    parsed_resolutions: list[dict] = []
    seen_ids: set[str] = set()
    for index, resolution in enumerate(resolutions):
        label = f"resolutions[{index}]"
        if not isinstance(resolution, dict):
            errors.append(f"{RISK_RESOLUTIONS_PATH} {label} must be an object")
            continue

        for field in RISK_RESOLUTION_REQUIRED_FIELDS:
            if field not in resolution:
                errors.append(f"{RISK_RESOLUTIONS_PATH} {label} missing field: {field}")

        current_id = risk_id(resolution.get("risk_id"))
        if current_id is None:
            errors.append(f"{RISK_RESOLUTIONS_PATH} {label}.risk_id must be a non-empty string")
        elif current_id in seen_ids:
            errors.append(f"{RISK_RESOLUTIONS_PATH} duplicate risk_id: {current_id}")
        else:
            seen_ids.add(current_id)
            if mitigation_ids and current_id not in mitigation_ids:
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.risk_id unknown risk: {current_id}"
                )

        status = resolution.get("status")
        if status not in RISK_RESOLUTION_STATUSES:
            allowed = ", ".join(sorted(RISK_RESOLUTION_STATUSES))
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.status invalid "
                f"(expected one of: {allowed})"
            )
        elif final_verdict == "pass-with-risks" and status not in RISK_RESOLUTION_PASS_STATUSES:
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.status {status} is not allowed "
                "for Verdict: pass-with-risks"
            )
        elif status == "unresolved" and final_verdict not in {"blocked", "fail"}:
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.status unresolved is allowed only "
                "for Verdict: blocked or fail"
            )

        resolution_type = resolution.get("resolution_type")
        if resolution_type not in RISK_RESOLUTION_TYPES:
            allowed = ", ".join(sorted(RISK_RESOLUTION_TYPES))
            errors.append(
                f"{RISK_RESOLUTIONS_PATH} {label}.resolution_type invalid "
                f"(expected one of: {allowed})"
            )

        for field in ["owner_lane", "resolution", "verification", "verified_by", "reviewed_by"]:
            errors.extend(validate_risk_resolution_text_field(resolution, field, label))

        errors.extend(validate_risk_resolution_evidence(run_dir, resolution.get("evidence"), label))
        errors.extend(validate_resolution_attempts(run_dir, resolution, label, final_verdict))
        parsed_resolutions.append(resolution)

    if final_verdict == "pass-with-risks":
        for mitigation_id in risk_mitigation_ids(mitigation_risks):
            if mitigation_id not in seen_ids:
                errors.append(f"{RISK_RESOLUTIONS_PATH} missing resolution for risk id: {mitigation_id}")

    return parsed_resolutions, errors


def risk_mitigation_ids(risks: list[dict]) -> list[str]:
    ids: list[str] = []
    for risk in risks:
        current_id = risk_id(risk.get("id"))
        if current_id is not None:
            ids.append(current_id)
    return ids


def risk_resolution_ids(resolutions: list[dict]) -> list[str]:
    ids: list[str] = []
    for resolution in resolutions:
        current_id = risk_id(resolution.get("risk_id"))
        if current_id is not None:
            ids.append(current_id)
    return ids


def validate_final_risk_mitigation_coverage(final_path: Path, risk_ids: list[str]) -> list[str]:
    if not final_path.exists() or not final_path.is_file():
        return []

    missing = missing_markdown_headings(final_path, [RISK_MITIGATIONS_SECTION])
    if missing:
        return [f"final.md missing section: {section}" for section in missing]

    section_text = markdown_section_text(final_path.read_text(encoding="utf-8"), RISK_MITIGATIONS_SECTION)
    return [
        f"final.md Risk Mitigations missing risk id: {risk_id_value}"
        for risk_id_value in risk_ids
        if not contains_facet_id(section_text, risk_id_value)
    ]


def validate_final_risk_resolution_coverage(final_path: Path, risk_ids: list[str]) -> list[str]:
    if not final_path.exists() or not final_path.is_file():
        return []

    missing = missing_markdown_headings(final_path, [RISK_RESOLUTIONS_SECTION])
    if missing:
        return [f"final.md missing section: {section}" for section in missing]

    section_text = markdown_section_text(final_path.read_text(encoding="utf-8"), RISK_RESOLUTIONS_SECTION)
    return [
        f"final.md Risk Resolutions missing risk id: {risk_id_value}"
        for risk_id_value in risk_ids
        if not contains_facet_id(section_text, risk_id_value)
    ]


def validate_risk_mitigation_lane_references(
    risks: list[dict],
    lane_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    for index, risk in enumerate(risks):
        label = f"risks[{index}]"
        for field in ["detected_by", "owner_lane"]:
            value = risk.get(field)
            if isinstance(value, str) and value and value not in lane_ids:
                errors.append(
                    f"{RISK_MITIGATIONS_PATH} {label}.{field} lane not found: {value}"
                )
    return errors


def lane_wave(lane: dict | None) -> int | None:
    if not lane:
        return None
    wave = lane.get("wave")
    if isinstance(wave, int) and not isinstance(wave, bool):
        return wave
    return None


def validate_risk_resolution_lane_references(
    resolutions: list[dict],
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    resolution_items: list[tuple[str, dict]] = []
    for index, resolution in enumerate(resolutions):
        resolution_items.append((f"resolutions[{index}]", resolution))
        attempts = resolution.get("attempts")
        if isinstance(attempts, list):
            for attempt_index, attempt in enumerate(attempts):
                if isinstance(attempt, dict):
                    resolution_items.append((f"resolutions[{index}].attempts[{attempt_index}]", attempt))

    for label, resolution in resolution_items:
        owner_lane_id = resolution.get("owner_lane")
        verified_by_id = resolution.get("verified_by")
        reviewed_by_id = resolution.get("reviewed_by")

        owner_lane = None
        verified_by_lane = None
        reviewed_by_lane = None

        if isinstance(owner_lane_id, str) and owner_lane_id:
            owner_lane = lane_by_id.get(owner_lane_id)
            if owner_lane is None:
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.owner_lane lane not found: {owner_lane_id}"
                )
        if isinstance(verified_by_id, str) and verified_by_id:
            verified_by_lane = lane_by_id.get(verified_by_id)
            if verified_by_lane is None:
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.verified_by lane not found: {verified_by_id}"
                )
            elif (
                verified_by_lane.get("type") != "qa"
                or normalized_status_by_id.get(verified_by_id) not in SUCCESSFUL_LANE_STATUSES
            ):
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.verified_by must reference "
                    f"a successful qa lane: {verified_by_id}"
                )
        if isinstance(reviewed_by_id, str) and reviewed_by_id:
            reviewed_by_lane = lane_by_id.get(reviewed_by_id)
            if reviewed_by_lane is None:
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.reviewed_by lane not found: {reviewed_by_id}"
                )
            elif (
                reviewed_by_lane.get("type") != "review"
                or normalized_status_by_id.get(reviewed_by_id) not in SUCCESSFUL_LANE_STATUSES
            ):
                errors.append(
                    f"{RISK_RESOLUTIONS_PATH} {label}.reviewed_by must reference "
                    f"a successful review lane: {reviewed_by_id}"
                )

        owner_wave = lane_wave(owner_lane)
        verified_wave = lane_wave(verified_by_lane)
        reviewed_wave = lane_wave(reviewed_by_lane)
        if owner_wave is not None and verified_wave is not None and owner_wave > verified_wave:
            errors.append(f"{RISK_RESOLUTIONS_PATH} {label} owner_lane must not run after verified_by")
        if verified_wave is not None and reviewed_wave is not None and verified_wave > reviewed_wave:
            errors.append(f"{RISK_RESOLUTIONS_PATH} {label} verified_by must not run after reviewed_by")

    return errors


def recovery_lane(
    recovery: dict,
    field: str,
) -> str | None:
    value = recovery.get(field)
    if not isinstance(value, dict):
        return None
    lane = value.get("lane")
    return lane if isinstance(lane, str) and lane else None


def validate_recovery_handoff(
    run_dir: Path,
    lane_id: str,
    lane: dict,
    section: str,
    risk_id_value: str,
    *,
    required_text: str | None = None,
) -> list[str]:
    errors: list[str] = []
    handoff = lane.get("handoff")
    if not isinstance(handoff, str) or not handoff:
        errors.append(f"lane-map.json: lane {lane_id} requires handoff for Blocked Resolution Gate")
        return errors
    handoff_path = resolve_run_path(run_dir, handoff)
    for missing in missing_markdown_headings(handoff_path, [section]):
        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {missing}")
    text = markdown_section_text(
        handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else "",
        section,
    )
    if not contains_facet_id(text, risk_id_value):
        errors.append(f"lane-map.json: lane {lane_id} {section} missing risk id: {risk_id_value}")
    if required_text and required_text not in text:
        errors.append(f"lane-map.json: lane {lane_id} {section} missing instruction text")
    return errors


def validate_recovery_lane_shape(
    field: str,
    lane_id: str | None,
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
    *,
    expected_type: str,
    expected_role: str,
    critical: bool | None,
) -> tuple[dict | None, list[str]]:
    if not lane_id:
        return None, [f"{field} lane must be a non-empty string"]
    lane = lane_by_id.get(lane_id)
    if lane is None:
        return None, [f"{field} lane not found: {lane_id}"]
    status = normalized_status_by_id.get(lane_id)
    if (
        lane.get("type") != expected_type
        or lane.get("role") != expected_role
        or status not in SUCCESSFUL_LANE_STATUSES
        or (critical is not None and lane.get("critical") is not critical)
    ):
        if expected_role == "senior-qa-verifier":
            return lane, [
                f"{field} must reference a successful senior-qa-verifier qa lane: {lane_id}"
            ]
        if expected_role == "supervising-architect":
            return lane, [
                f"{field} must reference a successful non-critical supervising-architect architecture lane: {lane_id}"
            ]
        if expected_role == "architect":
            return lane, [
                f"{field} must reference a successful non-critical architect architecture lane: {lane_id}"
            ]
        return lane, [f"{field} references invalid lane: {lane_id}"]
    return lane, []


def validate_blocked_resolution_recovery_lanes(
    run_dir: Path,
    resolutions: list[dict],
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    for index, resolution in enumerate(resolutions):
        risk_id_value = risk_id(resolution.get("risk_id"))
        if not risk_id_value:
            continue
        attempts = resolution.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            continue
        blocked_numbers = {
            attempt.get("attempt")
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("status") == "blocked"
        }
        if not blocked_numbers:
            continue
        recovery = resolution.get("blocked_recovery")
        if not isinstance(recovery, dict):
            continue

        senior_lane: dict | None = None
        architect_lane: dict | None = None
        supervising_lane: dict | None = None

        if 1 in blocked_numbers:
            senior_lane_id = recovery_lane(recovery, "senior_qa_test_design_review")
            senior_lane, lane_errors = validate_recovery_lane_shape(
                "senior_qa_test_design_review",
                senior_lane_id,
                lane_by_id,
                normalized_status_by_id,
                expected_type="qa",
                expected_role="senior-qa-verifier",
                critical=None,
            )
            errors.extend(f"risk-resolutions.json resolutions[{index}].blocked_recovery.{error}" for error in lane_errors)
            if senior_lane_id and senior_lane:
                errors.extend(
                    validate_recovery_handoff(
                        run_dir,
                        senior_lane_id,
                        senior_lane,
                        SENIOR_QA_TEST_DESIGN_REVIEW_SECTION,
                        risk_id_value,
                    )
                )

            architect_review = recovery.get("architect_review") if isinstance(recovery.get("architect_review"), dict) else {}
            architect_lane_id = recovery_lane(recovery, "architect_review")
            architect_lane, lane_errors = validate_recovery_lane_shape(
                "architect_review",
                architect_lane_id,
                lane_by_id,
                normalized_status_by_id,
                expected_type="architecture",
                expected_role="architect",
                critical=False,
            )
            errors.extend(f"risk-resolutions.json resolutions[{index}].blocked_recovery.{error}" for error in lane_errors)
            architect_instruction = architect_review.get("instruction") if isinstance(architect_review, dict) else None
            if architect_lane_id and architect_lane:
                errors.extend(
                    validate_recovery_handoff(
                        run_dir,
                        architect_lane_id,
                        architect_lane,
                        RESOLUTION_ARCHITECT_REVIEW_SECTION,
                        risk_id_value,
                        required_text=architect_instruction if isinstance(architect_instruction, str) else None,
                    )
                )

        if 2 in blocked_numbers:
            supervising_review = (
                recovery.get("supervising_architect_review")
                if isinstance(recovery.get("supervising_architect_review"), dict)
                else {}
            )
            supervising_lane_id = recovery_lane(recovery, "supervising_architect_review")
            supervising_lane, lane_errors = validate_recovery_lane_shape(
                "supervising_architect_review",
                supervising_lane_id,
                lane_by_id,
                normalized_status_by_id,
                expected_type="architecture",
                expected_role="supervising-architect",
                critical=False,
            )
            errors.extend(f"risk-resolutions.json resolutions[{index}].blocked_recovery.{error}" for error in lane_errors)
            supervising_instruction = supervising_review.get("instruction") if isinstance(supervising_review, dict) else None
            if supervising_lane_id and supervising_lane:
                errors.extend(
                    validate_recovery_handoff(
                        run_dir,
                        supervising_lane_id,
                        supervising_lane,
                        SUPERVISING_ARCHITECT_REVIEW_SECTION,
                        risk_id_value,
                        required_text=supervising_instruction if isinstance(supervising_instruction, str) else None,
                    )
                )

        attempt_by_number = {
            attempt.get("attempt"): attempt
            for attempt in attempts
            if isinstance(attempt, dict)
        }
        attempt_1 = attempt_by_number.get(1)
        attempt_2 = attempt_by_number.get(2)
        attempt_3 = attempt_by_number.get(3)

        if isinstance(attempt_1, dict) and attempt_1.get("status") == "blocked":
            blocked_waves = [
                lane_wave(lane_by_id.get(lane_id))
                for lane_id in [attempt_1.get("verified_by"), attempt_1.get("reviewed_by")]
                if isinstance(lane_id, str)
            ]
            latest_blocked_wave = max([wave for wave in blocked_waves if wave is not None], default=None)
            senior_lane_id = recovery_lane(recovery, "senior_qa_test_design_review")
            senior_wave = lane_wave(lane_by_id.get(senior_lane_id)) if senior_lane_id else None
            architect_lane_id = recovery_lane(recovery, "architect_review")
            architect_wave = lane_wave(lane_by_id.get(architect_lane_id)) if architect_lane_id else None
            if latest_blocked_wave is not None and senior_wave is not None and senior_wave <= latest_blocked_wave:
                errors.append(f"risk-resolutions.json resolutions[{index}] senior_qa_test_design_review must run after blocked attempt 1 verification")
            if senior_wave is not None and architect_wave is not None and architect_wave <= senior_wave:
                errors.append(f"risk-resolutions.json resolutions[{index}] architect_review must run after senior_qa_test_design_review")
            if isinstance(attempt_2, dict):
                owner_wave = lane_wave(lane_by_id.get(attempt_2.get("owner_lane")))
                if architect_wave is not None and owner_wave is not None and owner_wave <= architect_wave:
                    errors.append(f"risk-resolutions.json resolutions[{index}] attempt 2 owner_lane must run after architect_review")

        if isinstance(attempt_2, dict) and attempt_2.get("status") == "blocked":
            blocked_waves = [
                lane_wave(lane_by_id.get(lane_id))
                for lane_id in [attempt_2.get("verified_by"), attempt_2.get("reviewed_by")]
                if isinstance(lane_id, str)
            ]
            latest_blocked_wave = max([wave for wave in blocked_waves if wave is not None], default=None)
            supervising_lane_id = recovery_lane(recovery, "supervising_architect_review")
            supervising_wave = lane_wave(lane_by_id.get(supervising_lane_id)) if supervising_lane_id else None
            if latest_blocked_wave is not None and supervising_wave is not None and supervising_wave <= latest_blocked_wave:
                errors.append(f"risk-resolutions.json resolutions[{index}] supervising_architect_review must run after blocked attempt 2 verification")
            if isinstance(attempt_3, dict):
                owner_wave = lane_wave(lane_by_id.get(attempt_3.get("owner_lane")))
                if supervising_wave is not None and owner_wave is not None and owner_wave <= supervising_wave:
                    errors.append(f"risk-resolutions.json resolutions[{index}] attempt 3 owner_lane must run after supervising_architect_review")

    return errors


def load_lane_trace_events(run_dir: Path, role: str, lane_id: str) -> tuple[list[dict], list[str]]:
    trace_path = run_dir / "agents" / role / "trace.jsonl"
    if not trace_path.exists():
        return [], [f"lane-map.json: lane {lane_id} missing trace file: agents/{role}/trace.jsonl"]

    events, load_errors = load_jsonl_events(trace_path)
    if load_errors:
        return [], [f"lane-map.json: lane {lane_id} trace load error: {error}" for error in load_errors]

    return [event for event in events if event.get("lane_id") == lane_id], []


def validate_subagent_lane_trace(
    run_dir: Path,
    lane_id: str,
    role: str,
    *,
    lane_status: str | None = None,
    handoff: str | None = None,
) -> list[str]:
    events, errors = load_lane_trace_events(run_dir, role, lane_id)
    if errors:
        return errors
    if not events:
        return [f"lane-map.json: lane {lane_id} has no trace event with matching lane_id"]

    spawned_events = [event for event in events if event.get("stage") == "spawned"]
    if not spawned_events:
        return [f"lane-map.json: lane {lane_id} missing spawned trace event"]
    if not any(event.get("codex_thread_id") for event in spawned_events):
        return [f"lane-map.json: lane {lane_id} spawned trace event missing codex_thread_id"]

    if lane_status in SUCCESSFUL_LANE_STATUSES:
        terminal_events = [
            event
            for event in events
            if event.get("stage") == "handoff"
            and event.get("status") in SUCCESSFUL_LANE_STATUSES
        ]
        if not terminal_events:
            return [f"lane-map.json: lane {lane_id} missing terminal handoff trace event"]
        if handoff and not any(handoff in event.get("artifacts", []) for event in terminal_events):
            return [
                f"lane-map.json: lane {lane_id} "
                "terminal handoff trace event missing handoff artifact"
            ]
    return []


def require_non_empty_string(
    data: dict,
    field: str,
    label: str,
    errors: list[str],
) -> str | None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")
        return None
    return value


def delegation_summary_ids(records: object, field: str, label: str, errors: list[str]) -> list[str]:
    if not isinstance(records, list):
        errors.append(f"{label}.{field} must be an array")
        return []
    ids: list[str] = []
    for index, record in enumerate(records):
        record_label = f"{label}.{field}[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{record_label} must be an object")
            continue
        lane_id = record.get("lane_id")
        if isinstance(lane_id, str) and lane_id:
            ids.append(lane_id)
    return ids


def validate_delegation_summary_record_paths(
    run_dir: Path,
    record: dict,
    lane: dict,
    record_label: str,
    errors: list[str],
) -> None:
    trace = require_non_empty_string(record, "trace", record_label, errors)
    if trace is not None:
        trace_path = resolve_run_path(run_dir, trace)
        if not trace_path.exists():
            errors.append(f"{record_label}.trace not found: {trace}")
        role = record.get("role")
        if isinstance(role, str) and role:
            expected_trace = f"agents/{role}/trace.jsonl"
            if trace != expected_trace:
                errors.append(f"{record_label}.trace must be {expected_trace}")

    handoff = require_non_empty_string(record, "handoff", record_label, errors)
    lane_handoff = lane.get("handoff")
    if handoff is not None:
        if not resolve_run_path(run_dir, handoff).exists():
            errors.append(f"{record_label}.handoff not found: {handoff}")
        if isinstance(lane_handoff, str) and lane_handoff and handoff != lane_handoff:
            errors.append(f"{record_label}.handoff must match lane handoff: {lane_handoff}")


def validate_delegation_summary(
    run_dir: Path,
    lanes: list[dict],
    lane_by_id: dict[str, dict],
    final_verdict: str | None,
    *,
    required: bool,
) -> tuple[dict | None, list[str]]:
    path = run_dir / DELEGATION_SUMMARY_PATH
    if not path.exists():
        if required:
            return None, [f"{DELEGATION_SUMMARY_PATH} is required for positive lane-map run"]
        return None, []

    data, load_errors = load_json(path, DELEGATION_SUMMARY_PATH)
    errors = list(load_errors)
    if errors:
        return None, errors
    if not isinstance(data, dict):
        return None, [f"{DELEGATION_SUMMARY_PATH} must be a JSON object"]

    if data.get("version") != 1:
        errors.append(f"{DELEGATION_SUMMARY_PATH}: version must be 1")

    subagents_used = data.get("subagents_used")
    role_lanes_used = data.get("role_lanes_used")
    if not isinstance(subagents_used, bool):
        errors.append(f"{DELEGATION_SUMMARY_PATH}: subagents_used must be a boolean")
        subagents_used = False
    if not isinstance(role_lanes_used, bool):
        errors.append(f"{DELEGATION_SUMMARY_PATH}: role_lanes_used must be a boolean")
        role_lanes_used = False

    subagents = data.get("subagents")
    role_lanes = data.get("role_lanes")
    subagent_ids = delegation_summary_ids(subagents, "subagents", DELEGATION_SUMMARY_PATH, errors)
    role_lane_ids = delegation_summary_ids(role_lanes, "role_lanes", DELEGATION_SUMMARY_PATH, errors)

    notes = data.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        errors.append(f"{DELEGATION_SUMMARY_PATH}: notes must be a non-empty string")

    if subagents_used is False and subagent_ids:
        errors.append(f"{DELEGATION_SUMMARY_PATH}: subagents_used=false forbids subagents")
    if subagents_used is True and not subagent_ids:
        errors.append(f"{DELEGATION_SUMMARY_PATH}: subagents_used=true requires subagents")
    if role_lanes_used is False and role_lane_ids:
        errors.append(f"{DELEGATION_SUMMARY_PATH}: role_lanes_used=false forbids role_lanes")
    if role_lanes_used is True and not role_lane_ids:
        errors.append(f"{DELEGATION_SUMMARY_PATH}: role_lanes_used=true requires role_lanes")

    expected_subagent_ids = sorted(
        lane["id"]
        for lane in lanes
        if normalize_execution_mode(lane.get("execution_mode")) == "subagent"
        and normalize_lane_status(lane.get("status")) not in {"planned", "timed-out", "replaced"}
    )
    expected_role_lane_ids = sorted(
        lane["id"]
        for lane in lanes
        if normalize_execution_mode(lane.get("execution_mode")) == "role-lane"
        and normalize_lane_status(lane.get("status")) not in {"planned", "timed-out", "replaced"}
    )
    if sorted(subagent_ids) != expected_subagent_ids:
        errors.append(
            f"{DELEGATION_SUMMARY_PATH}: subagents must cover execution_mode=subagent lanes"
        )
    if sorted(role_lane_ids) != expected_role_lane_ids:
        errors.append(
            f"{DELEGATION_SUMMARY_PATH}: role_lanes must cover execution_mode=role-lane lanes"
        )

    if isinstance(subagents, list):
        for index, record in enumerate(subagents):
            record_label = f"{DELEGATION_SUMMARY_PATH}.subagents[{index}]"
            if not isinstance(record, dict):
                continue
            lane_id = require_non_empty_string(record, "lane_id", record_label, errors)
            role = require_non_empty_string(record, "role", record_label, errors)
            codex_thread_id = require_non_empty_string(record, "codex_thread_id", record_label, errors)
            if lane_id is None:
                continue
            lane = lane_by_id.get(lane_id)
            if lane is None:
                errors.append(f"{DELEGATION_SUMMARY_PATH}: unknown lane id: {lane_id}")
                continue
            if normalize_execution_mode(lane.get("execution_mode")) != "subagent":
                errors.append(f"{DELEGATION_SUMMARY_PATH}: lane {lane_id} is not execution_mode=subagent")
            if role is not None and lane.get("role") != role:
                errors.append(f"{record_label}.role must match lane role: {lane.get('role')}")
            validate_delegation_summary_record_paths(run_dir, record, lane, record_label, errors)
            if role is not None and codex_thread_id is not None:
                events, event_errors = load_lane_trace_events(run_dir, role, lane_id)
                errors.extend(event_errors)
                if events and not any(
                    event.get("codex_thread_id") == codex_thread_id for event in events
                ):
                    errors.append(f"{record_label}.codex_thread_id missing from lane trace events")

    if isinstance(role_lanes, list):
        for index, record in enumerate(role_lanes):
            record_label = f"{DELEGATION_SUMMARY_PATH}.role_lanes[{index}]"
            if not isinstance(record, dict):
                continue
            lane_id = require_non_empty_string(record, "lane_id", record_label, errors)
            role = require_non_empty_string(record, "role", record_label, errors)
            require_non_empty_string(record, "reason", record_label, errors)
            if lane_id is None:
                continue
            lane = lane_by_id.get(lane_id)
            if lane is None:
                errors.append(f"{DELEGATION_SUMMARY_PATH}: unknown lane id: {lane_id}")
                continue
            if normalize_execution_mode(lane.get("execution_mode")) != "role-lane":
                errors.append(f"{DELEGATION_SUMMARY_PATH}: lane {lane_id} is not execution_mode=role-lane")
            if role is not None and lane.get("role") != role:
                errors.append(f"{record_label}.role must match lane role: {lane.get('role')}")

    if final_verdict in POSITIVE_FINAL_VERDICTS:
        errors.extend(validate_final_delegation_trace(run_dir / "final.md", data))
        if not subagent_ids:
            errors.extend(validate_no_unbacked_subagent_claims(run_dir))

    return data, errors


def validate_final_delegation_trace(final_path: Path, summary: dict) -> list[str]:
    errors: list[str] = []
    if not final_path.exists():
        return errors
    text = final_path.read_text(encoding="utf-8")
    if not has_markdown_heading(text, DELEGATION_TRACE_SECTION):
        return [f"final.md missing section: {DELEGATION_TRACE_SECTION}"]
    section = markdown_section_text(text, DELEGATION_TRACE_SECTION)

    expected_subagents = "yes" if summary.get("subagents_used") else "no"
    expected_role_lanes = "yes" if summary.get("role_lanes_used") else "no"
    if f"Subagents Used: {expected_subagents}" not in section:
        errors.append(f"final.md Delegation Trace missing Subagents Used: {expected_subagents}")
    if f"Role Lanes Used: {expected_role_lanes}" not in section:
        errors.append(f"final.md Delegation Trace missing Role Lanes Used: {expected_role_lanes}")

    subagent_records = summary.get("subagents") if isinstance(summary.get("subagents"), list) else []
    role_lane_records = summary.get("role_lanes") if isinstance(summary.get("role_lanes"), list) else []
    subagent_ids = [
        record.get("lane_id")
        for record in subagent_records
        if isinstance(record, dict) and isinstance(record.get("lane_id"), str)
    ]
    role_lane_ids = [
        record.get("lane_id")
        for record in role_lane_records
        if isinstance(record, dict) and isinstance(record.get("lane_id"), str)
    ]
    traces = [
        record.get("trace")
        for record in subagent_records
        if isinstance(record, dict) and isinstance(record.get("trace"), str)
    ]
    if not subagent_ids and "Subagent Lanes: none" not in section:
        errors.append("final.md Delegation Trace missing Subagent Lanes: none")
    for lane_id in subagent_ids:
        if not contains_facet_id(section, lane_id):
            errors.append(f"final.md Delegation Trace missing subagent lane id: {lane_id}")
    if not role_lane_ids and "Role Lanes: none" not in section:
        errors.append("final.md Delegation Trace missing Role Lanes: none")
    for lane_id in role_lane_ids:
        if not contains_facet_id(section, lane_id):
            errors.append(f"final.md Delegation Trace missing role lane id: {lane_id}")
    if not traces and "Subagent Trace Evidence: none" not in section:
        errors.append("final.md Delegation Trace missing Subagent Trace Evidence: none")
    for trace in traces:
        if trace not in section:
            errors.append(f"final.md Delegation Trace missing subagent trace path: {trace}")
    return errors


def validate_no_unbacked_subagent_claims(run_dir: Path) -> list[str]:
    errors: list[str] = []
    allowed_fragments = [
        "subagents used: no",
        "subagent lanes: none",
        "subagent trace evidence: none",
        "no subagents",
        "no spawned subagents",
        "subagents skipped",
        "spawn_agent unavailable",
        "role-lane is not subagent",
        "role lanes are not subagent",
        "role-lane work is not subagent",
        "not subagent execution",
    ]
    banned_phrases = [
        "sidecar",
        "spawned subagent",
        "subagent found",
        "subagent returned",
        "subagent completed",
        "explorer subagent",
        "explorer sidecar",
    ]
    for relative in ["final.md", "route.md", "manifest.md"]:
        path = run_dir / relative
        if not path.exists() or not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            normalized = " ".join(line.lower().split())
            if not normalized:
                continue
            if any(fragment in normalized for fragment in allowed_fragments):
                continue
            if any(phrase in normalized for phrase in banned_phrases):
                errors.append(
                    f"{relative}:{line_number}: claims subagent/sidecar without spawned trace evidence"
                )
                continue
            if "subagent" in normalized and "subagents" not in normalized:
                errors.append(
                    f"{relative}:{line_number}: claims subagent/sidecar without spawned trace evidence"
                )
    return errors


def has_markdown_heading(text: str, heading: str) -> bool:
    pattern = re.compile(rf"(?m)^\s{{0,3}}#{{1,6}}\s+{re.escape(heading)}\s*#*\s*$")
    return bool(pattern.search(text))


def markdown_section_text(text: str, heading: str) -> str:
    lines = text.splitlines()
    section_lines: list[str] = []
    in_section = False
    section_level = 0

    for line in lines:
        match = MARKDOWN_HEADING_PATTERN.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if in_section and level <= section_level:
                break
            if title == heading:
                in_section = True
                section_level = level
                continue
        if in_section:
            section_lines.append(line)

    return "\n".join(section_lines).strip()


def contains_facet_id(text: str, facet: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(facet)}(?![A-Za-z0-9_-])")
    return bool(pattern.search(text))


def successful_lane_event_indexes(timeline_events: list[dict], lane_id: str) -> list[int]:
    return [
        index
        for index, event in enumerate(timeline_events)
        if event.get("lane_id") == lane_id
        and event.get("status") in SUCCESS_STATUSES
        and event.get("stage") != "spawned"
    ]


def first_successful_lane_event_index(
    timeline_events: list[dict],
    lane_id: str,
) -> int | None:
    indexes = successful_lane_event_indexes(timeline_events, lane_id)
    return min(indexes) if indexes else None


def read_timeline_events(run_dir: Path) -> tuple[list[dict], list[str]]:
    timeline_path = run_dir / "timeline.jsonl"
    if not timeline_path.exists():
        return [], ["missing timeline.jsonl for continuation validation"]
    events, load_errors = load_jsonl_events(timeline_path)
    if load_errors:
        return [], load_errors
    return events, []


def continuation_stage_present(timeline_events: list[dict]) -> bool:
    return any(event.get("stage") in CONTINUATION_STAGES for event in timeline_events)


def validate_string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        result.append(item)
    return result


def validate_markdown_section_coverage(
    path: Path,
    section: str,
    required_ids: list[str],
    *,
    missing_section_error: str,
    missing_id_error_prefix: str,
    errors: list[str],
) -> None:
    if not path.exists():
        return
    missing = missing_markdown_headings(path, [section])
    if missing:
        errors.append(missing_section_error)
        return
    section_text = markdown_section_text(path.read_text(encoding="utf-8"), section)
    for required_id in required_ids:
        if not contains_facet_id(section_text, required_id):
            errors.append(f"{missing_id_error_prefix}: {required_id}")


def claim_evidence_ids_from_contract(path: Path) -> tuple[list[str], list[str]]:
    if not path.exists():
        return [], []
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    claim_ids: list[str] = []
    seen: set[str] = set()

    for section in ["QA Gates", "Reviewer Checklist"]:
        if missing_markdown_headings(path, [section]):
            continue
        section_text = markdown_section_text(text, section)
        section_ids: list[str] = []
        for line in section_text.splitlines():
            match = CLAIM_EVIDENCE_PATTERN.match(line)
            if not match:
                continue
            claim_id_value = match.group(1).strip()
            if not KEBAB_CASE_PATTERN.fullmatch(claim_id_value):
                errors.append(
                    f"architecture contract handoff {section} "
                    f"Claim Evidence id must be kebab-case: {claim_id_value}"
                )
                continue
            section_ids.append(claim_id_value)
            if claim_id_value not in seen:
                seen.add(claim_id_value)
                claim_ids.append(claim_id_value)
        if not section_ids:
            errors.append(f"architecture contract handoff {section} missing Claim Evidence")

    return claim_ids, errors


def validate_claim_evidence_records(
    run_dir: Path,
    *,
    required_claim_ids: list[str],
    required: bool,
    final_verdict: str | None,
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
) -> list[str]:
    path = run_dir / CLAIM_EVIDENCE_PATH
    if not path.exists():
        if required:
            return [f"{CLAIM_EVIDENCE_PATH} is required for positive architecture contract run"]
        return []

    data, load_errors = load_json(path, CLAIM_EVIDENCE_PATH)
    if load_errors:
        return load_errors
    if not isinstance(data, dict):
        return [f"{CLAIM_EVIDENCE_PATH} must be a JSON object"]

    errors: list[str] = []
    if data.get("version") != 1:
        errors.append(f"{CLAIM_EVIDENCE_PATH} field 'version' must be 1")

    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append(f"{CLAIM_EVIDENCE_PATH} field 'claims' must be a non-empty array")
        return errors

    seen_claim_ids: set[str] = set()
    for index, claim_record in enumerate(claims):
        label = f"claims[{index}]"
        if not isinstance(claim_record, dict):
            errors.append(f"{CLAIM_EVIDENCE_PATH} {label} must be an object")
            continue

        claim_id_value = validate_string(claim_record.get("id"), f"{CLAIM_EVIDENCE_PATH} {label}.id", errors)
        if claim_id_value:
            if not KEBAB_CASE_PATTERN.fullmatch(claim_id_value):
                errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.id must be kebab-case")
            elif claim_id_value in seen_claim_ids:
                errors.append(f"{CLAIM_EVIDENCE_PATH} duplicate claim id: {claim_id_value}")
            else:
                seen_claim_ids.add(claim_id_value)

        owner_lane_id = validate_string(
            claim_record.get("owner_lane"),
            f"{CLAIM_EVIDENCE_PATH} {label}.owner_lane",
            errors,
        )
        reviewed_by_id = validate_string(
            claim_record.get("reviewed_by"),
            f"{CLAIM_EVIDENCE_PATH} {label}.reviewed_by",
            errors,
        )
        section = validate_string(
            claim_record.get("section"),
            f"{CLAIM_EVIDENCE_PATH} {label}.section",
            errors,
        )

        owner_lane = lane_by_id.get(owner_lane_id) if owner_lane_id else None
        if owner_lane_id and owner_lane is None:
            errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.owner_lane not found: {owner_lane_id}")
        elif owner_lane_id and owner_lane is not None:
            owner_status = normalized_status_by_id.get(owner_lane_id)
            if (
                owner_lane.get("type") not in CLAIM_EVIDENCE_OWNER_TYPES
                or owner_status not in SUCCESSFUL_LANE_STATUSES
            ):
                errors.append(
                    f"{CLAIM_EVIDENCE_PATH} {label}.owner_lane must reference "
                    "successful qa or review lane"
                )

        reviewer_lane = lane_by_id.get(reviewed_by_id) if reviewed_by_id else None
        if reviewed_by_id and reviewer_lane is None:
            errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.reviewed_by not found: {reviewed_by_id}")
        elif reviewed_by_id and reviewer_lane is not None:
            reviewer_status = normalized_status_by_id.get(reviewed_by_id)
            if reviewer_lane.get("type") != "review" or reviewer_status not in SUCCESSFUL_LANE_STATUSES:
                errors.append(
                    f"{CLAIM_EVIDENCE_PATH} {label}.reviewed_by must reference "
                    "successful review lane"
                )

        if owner_lane is not None and section and claim_id_value:
            handoff = owner_lane.get("handoff")
            if not isinstance(handoff, str) or not handoff:
                errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.owner_lane has no handoff")
            else:
                handoff_path = resolve_run_path(run_dir, handoff)
                if not handoff_path.exists():
                    errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.owner handoff not found: {handoff}")
                elif missing_markdown_headings(handoff_path, [section]):
                    errors.append(
                        f"{CLAIM_EVIDENCE_PATH} {label}.section missing from "
                        f"owner handoff: {section}"
                    )
                else:
                    section_text = markdown_section_text(
                        handoff_path.read_text(encoding="utf-8"),
                        section,
                    )
                    if not contains_facet_id(section_text, claim_id_value):
                        errors.append(
                            f"{CLAIM_EVIDENCE_PATH} {label}.owner handoff section "
                            f"missing claim id: {claim_id_value}"
                        )

        status = claim_record.get("status")
        if status not in CLAIM_EVIDENCE_STATUSES:
            allowed = ", ".join(sorted(CLAIM_EVIDENCE_STATUSES))
            errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.status must be one of: {allowed}")
        elif final_verdict in POSITIVE_FINAL_VERDICTS and status != "supported":
            errors.append(
                f"{CLAIM_EVIDENCE_PATH} {label}.status must be supported for "
                "positive final Verdict"
            )

        validate_string(claim_record.get("claim"), f"{CLAIM_EVIDENCE_PATH} {label}.claim", errors)
        validate_string_list(claim_record.get("subjects"), f"{CLAIM_EVIDENCE_PATH} {label}.subjects", errors)

        evidence_records = claim_record.get("evidence")
        if not isinstance(evidence_records, list) or not evidence_records:
            errors.append(f"{CLAIM_EVIDENCE_PATH} {label}.evidence must be a non-empty array")
            continue
        for evidence_index, evidence_record in enumerate(evidence_records):
            evidence_label = f"{label}.evidence[{evidence_index}]"
            if not isinstance(evidence_record, dict):
                errors.append(f"{CLAIM_EVIDENCE_PATH} {evidence_label} must be an object")
                continue
            evidence_path_value = validate_string(
                evidence_record.get("path"),
                f"{CLAIM_EVIDENCE_PATH} {evidence_label}.path",
                errors,
            )
            evidence_text = ""
            if evidence_path_value:
                evidence_path = resolve_run_path(run_dir, evidence_path_value)
                if not evidence_path.exists():
                    errors.append(
                        f"{CLAIM_EVIDENCE_PATH} {evidence_label}.path not found: "
                        f"{evidence_path_value}"
                    )
                elif evidence_path.is_file():
                    evidence_text = evidence_path.read_text(encoding="utf-8")
            markers = validate_string_list(
                evidence_record.get("markers"),
                f"{CLAIM_EVIDENCE_PATH} {evidence_label}.markers",
                errors,
            )
            if evidence_text:
                for marker_index, marker in enumerate(markers):
                    if marker not in evidence_text:
                        errors.append(
                            f"{CLAIM_EVIDENCE_PATH} {evidence_label}.markers[{marker_index}] "
                            f"not found in {evidence_path_value}"
                        )

    for required_claim_id in required_claim_ids:
        if required_claim_id not in seen_claim_ids:
            errors.append(f"{CLAIM_EVIDENCE_PATH} missing required claim id: {required_claim_id}")

    return errors


def validate_continuation_summary(
    run_dir: Path,
    *,
    final_verdict: str | None,
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
    successful_worker_lanes: list[tuple[str, int, str | None]],
    verification_readiness_data: dict | None,
) -> list[str]:
    timeline_events, timeline_errors = read_timeline_events(run_dir)
    if timeline_errors:
        return []

    continuation_present = continuation_stage_present(timeline_events)
    path = run_dir / CONTINUATION_SUMMARY_PATH
    required = final_verdict in POSITIVE_FINAL_VERDICTS and continuation_present
    if not path.exists():
        if required:
            return [f"{CONTINUATION_SUMMARY_PATH} is required for positive continuation run"]
        return []

    data, errors = load_json(path, CONTINUATION_SUMMARY_PATH)
    if errors:
        return errors
    if not isinstance(data, dict):
        return [f"{CONTINUATION_SUMMARY_PATH} must be a JSON object"]

    if data.get("version") != 1:
        errors.append(f"{CONTINUATION_SUMMARY_PATH} field 'version' must be 1")
    status = data.get("status")
    if status not in CONTINUATION_STATUSES:
        errors.append(f"{CONTINUATION_SUMMARY_PATH} status invalid")
    elif status == "resumed-ready" and final_verdict not in POSITIVE_FINAL_VERDICTS:
        errors.append(f"{CONTINUATION_SUMMARY_PATH} status resumed-ready requires positive final Verdict")
    elif status == "resumed-blocked" and final_verdict != "blocked":
        errors.append(f"{CONTINUATION_SUMMARY_PATH} status resumed-blocked requires final Verdict: blocked")
    elif status == "resumed-fail" and final_verdict != "fail":
        errors.append(f"{CONTINUATION_SUMMARY_PATH} status resumed-fail requires final Verdict: fail")
    if final_verdict in POSITIVE_FINAL_VERDICTS and status != "resumed-ready":
        errors.append(f"{CONTINUATION_SUMMARY_PATH} status resumed-ready required for positive final Verdict")

    checkpoint = data.get("previous_checkpoint")
    checkpoint_lane_id: str | None = None
    checkpoint_index: int | None = None
    if not isinstance(checkpoint, dict):
        errors.append(f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint must be an object")
    else:
        checkpoint_lane_id = validate_string(
            checkpoint.get("lane_id"),
            f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.lane_id",
            errors,
        )
        verdict = checkpoint.get("verdict")
        if verdict != "blocked":
            errors.append(f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.verdict must be blocked")
        snapshot = validate_string(
            checkpoint.get("snapshot"),
            f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.snapshot",
            errors,
        )
        if snapshot:
            snapshot_path = resolve_run_path(run_dir, snapshot)
            if not snapshot_path.exists():
                errors.append(
                    f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.snapshot not found: {snapshot}"
                )
            elif read_single_field(snapshot_path, "Verdict") != "blocked":
                errors.append(
                    f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.snapshot must record Verdict: blocked"
                )
        if checkpoint_lane_id:
            checkpoint_indexes = [
                index
                for index, event in enumerate(timeline_events)
                if event.get("lane_id") == checkpoint_lane_id
                and event.get("stage") == "blocked-checkpoint"
                and event.get("status") == "blocked"
            ]
            if checkpoint_indexes:
                checkpoint_index = min(checkpoint_indexes)
            else:
                errors.append(
                    f"{CONTINUATION_SUMMARY_PATH} previous_checkpoint.lane_id "
                    f"missing blocked-checkpoint timeline event: {checkpoint_lane_id}"
                )

    resolved_blockers = data.get("resolved_blockers")
    blocker_ids: list[str] = []
    if not isinstance(resolved_blockers, list) or not resolved_blockers:
        if final_verdict in POSITIVE_FINAL_VERDICTS:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} resolved_blockers must be a non-empty array")
        resolved_blockers = []
    for index, blocker in enumerate(resolved_blockers):
        label = f"{CONTINUATION_SUMMARY_PATH} resolved_blockers[{index}]"
        if not isinstance(blocker, dict):
            errors.append(f"{label} must be an object")
            continue
        blocker_id = validate_string(blocker.get("id"), f"{label}.id", errors)
        if blocker_id:
            if not KEBAB_CASE_PATTERN.fullmatch(blocker_id):
                errors.append(f"{label}.id must be kebab-case")
            blocker_ids.append(blocker_id)
        validate_string(blocker.get("resolution"), f"{label}.resolution", errors)
        validate_existing_path_list(run_dir, blocker.get("evidence"), f"{label}.evidence", errors)
    if len(set(blocker_ids)) != len(blocker_ids):
        errors.append(f"{CONTINUATION_SUMMARY_PATH} duplicate resolved blocker id")

    readiness_lane = validate_string(
        data.get("readiness_lane"),
        f"{CONTINUATION_SUMMARY_PATH} readiness_lane",
        errors,
    )
    readiness_index: int | None = None
    if readiness_lane:
        if readiness_lane not in lane_by_id:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} readiness_lane not found: {readiness_lane}")
        ready_attempt_lanes = {
            attempt.get("lane")
            for attempt in (verification_readiness_data or {}).get("attempts", [])
            if isinstance(attempt, dict) and attempt.get("status") == "ready"
        }
        if readiness_lane not in ready_attempt_lanes:
            errors.append(
                f"{CONTINUATION_SUMMARY_PATH} readiness_lane must reference a ready Verification Readiness Gate lane"
            )
        readiness_index = first_successful_lane_event_index(timeline_events, readiness_lane)
        if readiness_index is None:
            errors.append(f"lane-map.json: continuation timeline for lane {readiness_lane} missing event")
        elif checkpoint_index is not None and readiness_index <= checkpoint_index:
            errors.append(
                f"{CONTINUATION_SUMMARY_PATH} readiness_lane must run after previous checkpoint"
            )

    historical_worker_lanes = validate_string_list(
        data.get("historical_worker_lanes"),
        f"{CONTINUATION_SUMMARY_PATH} historical_worker_lanes",
        errors,
    )
    new_worker_lanes = validate_string_list(
        data.get("new_worker_lanes"),
        f"{CONTINUATION_SUMMARY_PATH} new_worker_lanes",
        errors,
    )
    revalidated_lanes = validate_string_list(
        data.get("revalidated_lanes"),
        f"{CONTINUATION_SUMMARY_PATH} revalidated_lanes",
        errors,
    )
    for field, values in [
        ("historical_worker_lanes", historical_worker_lanes),
        ("new_worker_lanes", new_worker_lanes),
        ("revalidated_lanes", revalidated_lanes),
    ]:
        for lane_id in values:
            lane = lane_by_id.get(lane_id)
            if lane is None:
                errors.append(f"{CONTINUATION_SUMMARY_PATH} {field} unknown lane id: {lane_id}")
            elif lane.get("type") not in WORKER_LANE_TYPES:
                errors.append(f"{CONTINUATION_SUMMARY_PATH} {field} must reference worker lanes: {lane_id}")

    worker_ids = [lane_id for lane_id, _wave, _handoff in successful_worker_lanes]
    worker_event_indexes: dict[str, list[int]] = {
        lane_id: successful_lane_event_indexes(timeline_events, lane_id)
        for lane_id in worker_ids
    }
    for lane_id, indexes in worker_event_indexes.items():
        if not indexes:
            errors.append(f"lane-map.json: continuation timeline for lane {lane_id} missing event")
            continue
        for event_index in indexes:
            if readiness_index is None:
                continue
            if checkpoint_index is not None and checkpoint_index < event_index < readiness_index:
                errors.append(
                    f"lane-map.json: continuation worker lane {lane_id} must run after "
                    "ready Verification Readiness Gate"
                )
            elif event_index < readiness_index:
                if lane_id not in historical_worker_lanes:
                    errors.append(f"{CONTINUATION_SUMMARY_PATH} missing historical worker lane: {lane_id}")
                if lane_id not in revalidated_lanes:
                    errors.append(
                        f"{CONTINUATION_SUMMARY_PATH} missing revalidated historical worker lane: {lane_id}"
                    )
            elif event_index > readiness_index and lane_id not in new_worker_lanes:
                errors.append(f"{CONTINUATION_SUMMARY_PATH} missing new worker lane: {lane_id}")

    qa_recheck_lane = validate_string(
        data.get("qa_recheck_lane"),
        f"{CONTINUATION_SUMMARY_PATH} qa_recheck_lane",
        errors,
    )
    reviewer_recheck_lane = validate_string(
        data.get("reviewer_recheck_lane"),
        f"{CONTINUATION_SUMMARY_PATH} reviewer_recheck_lane",
        errors,
    )
    required_section_ids = [*blocker_ids, *historical_worker_lanes, *new_worker_lanes]
    for lane_id, expected_type, section, label in [
        (qa_recheck_lane, "qa", CONTINUATION_REVALIDATION_SECTION, "qa"),
        (reviewer_recheck_lane, "review", CONTINUATION_REVIEW_SECTION, "reviewer"),
    ]:
        if not lane_id:
            continue
        lane = lane_by_id.get(lane_id)
        if lane is None:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} {label}_recheck_lane not found: {lane_id}")
            continue
        if lane.get("type") != expected_type:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} {label}_recheck_lane must be type={expected_type}: {lane_id}")
        if normalized_status_by_id.get(lane_id) not in SUCCESSFUL_LANE_STATUSES:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} {label}_recheck_lane must pass: {lane_id}")
        event_index = first_successful_lane_event_index(timeline_events, lane_id)
        if event_index is None:
            errors.append(f"lane-map.json: continuation timeline for lane {lane_id} missing event")
        elif readiness_index is not None and event_index <= readiness_index:
            errors.append(f"{CONTINUATION_SUMMARY_PATH} {label}_recheck_lane must run after readiness lane")
        handoff = lane.get("handoff")
        if not isinstance(handoff, str) or not handoff:
            errors.append(f"lane-map.json: lane {lane_id} requires handoff for Continuation Gate")
            continue
        handoff_path = resolve_run_path(run_dir, handoff)
        validate_markdown_section_coverage(
            handoff_path,
            section,
            required_section_ids,
            missing_section_error=f"lane-map.json: lane {lane_id} handoff missing section: {section}",
            missing_id_error_prefix=f"lane-map.json: lane {lane_id} {section} missing continuation id",
            errors=errors,
        )

    if final_verdict in POSITIVE_FINAL_VERDICTS:
        final_path = run_dir / "final.md"
        validate_markdown_section_coverage(
            final_path,
            CONTINUATION_SUMMARY_SECTION,
            required_section_ids,
            missing_section_error=f"final.md missing section: {CONTINUATION_SUMMARY_SECTION}",
            missing_id_error_prefix="final.md Continuation Summary missing continuation id",
            errors=errors,
        )

    validate_string(data.get("notes"), f"{CONTINUATION_SUMMARY_PATH} notes", errors)
    return errors


def has_blocked_resolution_attempts(resolution_records: list[dict]) -> bool:
    for resolution in resolution_records:
        attempts = resolution.get("attempts")
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if isinstance(attempt, dict) and attempt.get("status") == "blocked":
                return True
    return False


def detect_harness_learning_triggers(
    run_dir: Path,
    *,
    final_verdict: str | None,
    architecture_contract_required: bool,
    resolution_records: list[dict],
    drifting_worker_lanes: list[tuple[str, int | None, str | None]],
    verification_readiness_data: dict | None,
) -> set[str]:
    triggers: set[str] = set()
    timeline_events, timeline_errors = read_timeline_events(run_dir)
    if not timeline_errors and continuation_stage_present(timeline_events):
        triggers.add("continuation")
    if (run_dir / CONTINUATION_SUMMARY_PATH).exists():
        triggers.add("continuation")
    if (run_dir / RISK_MITIGATIONS_PATH).exists():
        triggers.add("risk-mitigation")
    if (run_dir / RISK_RESOLUTIONS_PATH).exists():
        triggers.add("risk-resolution")
    if has_blocked_resolution_attempts(resolution_records):
        triggers.add("blocked-resolution")
    if drifting_worker_lanes:
        triggers.add("architecture-drift")
        if any(recheck_lane for _lane_id, _wave, recheck_lane in drifting_worker_lanes):
            triggers.add("architecture-recheck")
    if isinstance(verification_readiness_data, dict):
        status = verification_readiness_data.get("status")
        attempts = verification_readiness_data.get("attempts")
        approval_requests = verification_readiness_data.get("approval_requests")
        approval_executions = verification_readiness_data.get("approval_executions")
        if (
            status in {"needs-approval", "paused-blocked", "blocked"}
            or (isinstance(attempts, list) and len(attempts) > 1)
            or (isinstance(approval_requests, list) and bool(approval_requests))
            or (isinstance(approval_executions, list) and bool(approval_executions))
        ):
            triggers.add("readiness-recovery")
    if architecture_contract_required and final_verdict in {"blocked", "fail"}:
        triggers.add("nonpositive-architecture-final")
    return triggers


def validate_harness_id(value: object, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a non-empty string")
        return None
    if not KEBAB_CASE_PATTERN.fullmatch(value):
        errors.append(f"{label} must be kebab-case")
    return value


def validate_harness_finding(
    run_dir: Path,
    finding: object,
    index: int,
    selected_context_facets: set[str],
    selected_capabilities: set[str],
    known_capabilities: set[str],
    seen_ids: set[str],
    errors: list[str],
) -> str | None:
    label = f"{HARNESS_EVALUATION_PATH} findings[{index}]"
    if not isinstance(finding, dict):
        errors.append(f"{label} must be an object")
        return None

    for field in [
        "id",
        "type",
        "problem_class",
        "architecture_context",
        "architecture_capabilities",
        "approach",
        "outcome",
        "evidence",
        "lesson",
        "reuse_when",
        "do_not_reuse_when",
    ]:
        if field not in finding:
            errors.append(f"{label} missing field: {field}")

    finding_id = validate_harness_id(finding.get("id"), f"{label}.id", errors)
    if finding_id:
        if finding_id in seen_ids:
            errors.append(f"{HARNESS_EVALUATION_PATH} duplicate finding id: {finding_id}")
        seen_ids.add(finding_id)

    if finding.get("type") not in HARNESS_FINDING_TYPES:
        errors.append(f"{label}.type invalid")
    if finding.get("outcome") not in HARNESS_OUTCOMES:
        errors.append(f"{label}.outcome invalid")

    for field in ["problem_class", "approach", "lesson", "reuse_when", "do_not_reuse_when"]:
        validate_string(finding.get(field), f"{label}.{field}", errors)

    context_values = validate_string_list(
        finding.get("architecture_context"),
        f"{label}.architecture_context",
        errors,
    )
    for context_index, facet in enumerate(context_values):
        if facet not in selected_context_facets:
            errors.append(f"{label}.architecture_context[{context_index}] unselected facet: {facet}")

    capability_values = validate_string_list(
        finding.get("architecture_capabilities"),
        f"{label}.architecture_capabilities",
        errors,
    )
    for capability_index, capability in enumerate(capability_values):
        if known_capabilities and capability not in known_capabilities:
            errors.append(
                f"{label}.architecture_capabilities[{capability_index}] "
                f"unknown capability: {capability}"
            )
            continue
        if capability not in selected_capabilities:
            errors.append(
                f"{label}.architecture_capabilities[{capability_index}] "
                f"unselected capability: {capability}"
            )

    validate_existing_path_list(run_dir, finding.get("evidence"), f"{label}.evidence", errors)
    return finding_id


def validate_harness_proposal(
    run_dir: Path,
    proposal: object,
    index: int,
    seen_ids: set[str],
    errors: list[str],
) -> str | None:
    label = f"{HARNESS_EVALUATION_PATH} proposals[{index}]"
    if not isinstance(proposal, dict):
        errors.append(f"{label} must be an object")
        return None

    for field in [
        "id",
        "type",
        "status",
        "target",
        "rationale",
        "evidence",
        "requires_human_approval",
    ]:
        if field not in proposal:
            errors.append(f"{label} missing field: {field}")

    proposal_id = validate_harness_id(proposal.get("id"), f"{label}.id", errors)
    if proposal_id:
        if proposal_id in seen_ids:
            errors.append(f"{HARNESS_EVALUATION_PATH} duplicate proposal id: {proposal_id}")
        seen_ids.add(proposal_id)

    if proposal.get("type") not in HARNESS_PROPOSAL_TYPES:
        errors.append(f"{label}.type must be evidence-record")
    if proposal.get("status") != "proposed":
        errors.append(f"{label}.status must be proposed")
    if proposal.get("target") != HARNESS_PROPOSAL_TARGET:
        errors.append(f"{label}.target must be Evidence Records")
    else:
        validate_string(proposal.get("target"), f"{label}.target", errors)
    if proposal.get("requires_human_approval") is not False:
        errors.append(f"{label}.requires_human_approval must be false")
    validate_string(proposal.get("rationale"), f"{label}.rationale", errors)
    validate_existing_path_list(run_dir, proposal.get("evidence"), f"{label}.evidence", errors)
    return proposal_id


def validate_harness_evaluation(
    run_dir: Path,
    *,
    final_verdict: str | None,
    learning_triggers: set[str],
    architecture_context_facets: list[str],
    architecture_capabilities: list[str],
    known_architecture_capability_ids: set[str],
    successful_reviewer_lanes: list[tuple[str, int, str | None]],
    require_reviewer_review: bool,
) -> list[str]:
    path = run_dir / HARNESS_EVALUATION_PATH
    if not path.exists():
        if learning_triggers:
            return [f"{HARNESS_EVALUATION_PATH} is required for triggered learning run"]
        return []

    data, errors = load_json(path, HARNESS_EVALUATION_PATH)
    if errors:
        return errors
    if not isinstance(data, dict):
        return [f"{HARNESS_EVALUATION_PATH} must be a JSON object"]

    if data.get("version") != 1:
        errors.append(f"{HARNESS_EVALUATION_PATH} field 'version' must be 1")

    status = data.get("status")
    if status not in HARNESS_EVALUATION_STATUSES:
        errors.append(f"{HARNESS_EVALUATION_PATH} status invalid")
    elif status == "blocked-learning" and final_verdict not in {"blocked", "fail"}:
        errors.append(f"{HARNESS_EVALUATION_PATH} status blocked-learning requires final Verdict: blocked or fail")

    declared_triggers = validate_string_list(
        data.get("learning_triggers"),
        f"{HARNESS_EVALUATION_PATH} learning_triggers",
        errors,
    )
    declared_trigger_set = set(declared_triggers)
    for trigger in declared_triggers:
        if trigger not in HARNESS_LEARNING_TRIGGERS:
            errors.append(f"{HARNESS_EVALUATION_PATH} unknown learning trigger: {trigger}")
        elif trigger not in learning_triggers:
            errors.append(f"{HARNESS_EVALUATION_PATH} learning trigger not present in run: {trigger}")
    for trigger in sorted(learning_triggers - declared_trigger_set):
        errors.append(f"{HARNESS_EVALUATION_PATH} missing learning trigger: {trigger}")

    validate_existing_path_list(
        run_dir,
        data.get("source_artifacts"),
        f"{HARNESS_EVALUATION_PATH} source_artifacts",
        errors,
    )

    if status == "blocked-learning":
        validate_string(data.get("blocked_reason"), f"{HARNESS_EVALUATION_PATH} blocked_reason", errors)
        validate_existing_path_list(
            run_dir,
            data.get("blocked_evidence"),
            f"{HARNESS_EVALUATION_PATH} blocked_evidence",
            errors,
        )

    findings = data.get("findings")
    proposals = data.get("proposals")
    if status in {"evaluated", "needs-review"}:
        if not isinstance(findings, list) or not findings:
            errors.append(f"{HARNESS_EVALUATION_PATH} findings must be a non-empty array")
            findings = []
        if not isinstance(proposals, list) or not proposals:
            errors.append(f"{HARNESS_EVALUATION_PATH} proposals must be a non-empty array")
            proposals = []
    else:
        if findings is None:
            findings = []
        if proposals is None:
            proposals = []
        if not isinstance(findings, list):
            errors.append(f"{HARNESS_EVALUATION_PATH} findings must be an array")
            findings = []
        if not isinstance(proposals, list):
            errors.append(f"{HARNESS_EVALUATION_PATH} proposals must be an array")
            proposals = []

    finding_ids: list[str] = []
    proposal_ids: list[str] = []
    seen_finding_ids: set[str] = set()
    seen_proposal_ids: set[str] = set()
    selected_context_set = set(architecture_context_facets)
    selected_capability_set = set(architecture_capabilities)
    known_capability_set = known_architecture_capability_ids | selected_capability_set

    for index, finding in enumerate(findings):
        finding_id = validate_harness_finding(
            run_dir,
            finding,
            index,
            selected_context_set,
            selected_capability_set,
            known_capability_set,
            seen_finding_ids,
            errors,
        )
        if finding_id:
            finding_ids.append(finding_id)

    for index, proposal in enumerate(proposals):
        proposal_id = validate_harness_proposal(
            run_dir,
            proposal,
            index,
            seen_proposal_ids,
            errors,
        )
        if proposal_id:
            proposal_ids.append(proposal_id)

    required_ids = [*finding_ids, *proposal_ids]
    if required_ids:
        validate_markdown_section_coverage(
            run_dir / "final.md",
            HARNESS_EVALUATION_SECTION,
            required_ids,
            missing_section_error=f"final.md missing section: {HARNESS_EVALUATION_SECTION}",
            missing_id_error_prefix="final.md Harness Evaluation missing harness id",
            errors=errors,
        )

    if (
        require_reviewer_review
        and final_verdict in POSITIVE_FINAL_VERDICTS
        and learning_triggers
    ):
        if not successful_reviewer_lanes:
            errors.append("lane-map.json: positive Harness Evaluation requires successful reviewer lane")
        for lane_id, _wave, handoff in successful_reviewer_lanes:
            if not isinstance(handoff, str) or not handoff:
                errors.append(f"lane-map.json: lane {lane_id} requires handoff for Harness Evaluation Review")
                continue
            validate_markdown_section_coverage(
                resolve_run_path(run_dir, handoff),
                HARNESS_EVALUATION_REVIEW_SECTION,
                required_ids,
                missing_section_error=(
                    f"lane-map.json: lane {lane_id} handoff missing section: "
                    f"{HARNESS_EVALUATION_REVIEW_SECTION}"
                ),
                missing_id_error_prefix=(
                    f"lane-map.json: lane {lane_id} "
                    f"{HARNESS_EVALUATION_REVIEW_SECTION} missing harness id"
                ),
                errors=errors,
            )

    return errors


def load_architecture_matrix_facets() -> tuple[dict[str, set[str]], list[str]]:
    facets = {axis: set() for axis in ARCHITECTURE_CONTEXT_AXES}
    if not ARCHITECTURE_MATRIX_PATH.exists():
        return facets, [f"Architecture Matrix not found: {ARCHITECTURE_MATRIX_PATH}"]

    heading_to_axis = {
        heading: axis for axis, heading in ARCHITECTURE_CONTEXT_AXES.items()
    }
    current_axis: str | None = None
    for line in ARCHITECTURE_MATRIX_PATH.read_text(encoding="utf-8").splitlines():
        heading_match = MARKDOWN_HEADING_PATTERN.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            current_axis = heading_to_axis.get(heading) if level == 3 else None
            continue
        if current_axis is None:
            continue
        facet_match = MATRIX_FACET_PATTERN.match(line)
        if facet_match:
            facets[current_axis].add(facet_match.group(1))

    errors = []
    for axis, heading in ARCHITECTURE_CONTEXT_AXES.items():
        if not facets[axis]:
            errors.append(f"Architecture Matrix section '{heading}' has no facet ids")
    return facets, errors


def validate_architecture_context_shape(
    data: dict,
    allowed_facets: dict[str, set[str]],
    *,
    required: bool,
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    errors: list[str] = []
    selected_by_axis: dict[str, list[str]] = {axis: [] for axis in ARCHITECTURE_CONTEXT_AXES}
    raw_context = data.get("architecture_context")
    if raw_context is None:
        if required:
            errors.append("lane-map.json field 'architecture_context' is required")
        return selected_by_axis, [], errors
    if not isinstance(raw_context, dict):
        return selected_by_axis, [], ["lane-map.json field 'architecture_context' must be an object"]

    expected_axes = set(ARCHITECTURE_CONTEXT_AXES)
    for axis in sorted(set(raw_context) - expected_axes):
        errors.append(f"lane-map.json architecture_context unknown axis: {axis}")

    selected_facets: list[str] = []
    for axis in ARCHITECTURE_CONTEXT_AXES:
        if axis not in raw_context:
            errors.append(f"lane-map.json architecture_context missing axis: {axis}")
            continue
        value = raw_context[axis]
        if not isinstance(value, list):
            errors.append(f"lane-map.json architecture_context.{axis} must be an array")
            continue
        for index, facet in enumerate(value):
            if not isinstance(facet, str) or not facet.strip():
                errors.append(
                    f"lane-map.json architecture_context.{axis}[{index}] "
                    "must be a non-empty string"
                )
                continue
            if facet not in allowed_facets.get(axis, set()):
                errors.append(
                    f"lane-map.json architecture_context.{axis}[{index}] "
                    f"unknown Architecture Matrix facet: {facet}"
                )
                continue
            selected_by_axis[axis].append(facet)
            selected_facets.append(facet)

    if required and not selected_facets:
        errors.append("lane-map.json architecture_context must select at least one facet")
    return selected_by_axis, selected_facets, errors


def selected_verification_gate_pairs(
    architecture_context_by_axis: dict[str, list[str]],
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for axis in ["risk_gates", "verification_gates"]:
        pairs.extend((axis, facet) for facet in architecture_context_by_axis.get(axis, []))
    return pairs


def validate_string(value: object, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return None
    return value


def validate_existing_path_list(
    run_dir: Path,
    value: object,
    label: str,
    errors: list[str],
) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty array")
        return []
    paths: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        paths.append(item)
        if not resolve_run_path(run_dir, item).exists():
            errors.append(f"{label}[{index}] not found: {item}")
    return paths


def validate_verification_gate_records(
    run_dir: Path,
    gates: object,
    label: str,
    selected_pairs: list[tuple[str, str]],
    *,
    status_field: str,
    allowed_statuses: set[str],
) -> tuple[set[tuple[str, str]], list[str]]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    selected_set = set(selected_pairs)
    if not isinstance(gates, list):
        return seen, [f"{label}.gates must be a non-empty array"]
    if not gates:
        if selected_pairs:
            return seen, [f"{label}.gates must be a non-empty array"]
        return seen, []

    for index, gate in enumerate(gates):
        gate_label = f"{label}.gates[{index}]"
        if not isinstance(gate, dict):
            errors.append(f"{gate_label} must be an object")
            continue
        axis = gate.get("axis")
        facet = gate.get("facet")
        if axis not in {"risk_gates", "verification_gates"}:
            errors.append(f"{gate_label}.axis must be risk_gates or verification_gates")
            continue
        if not isinstance(facet, str) or not facet:
            errors.append(f"{gate_label}.facet must be a non-empty string")
            continue
        pair = (axis, facet)
        if pair in seen:
            errors.append(f"{label} duplicate gate facet: {facet}")
        seen.add(pair)
        if pair not in selected_set:
            errors.append(f"{label} unknown selected gate facet: {facet}")
        status = gate.get(status_field)
        if status not in allowed_statuses:
            allowed = ", ".join(sorted(allowed_statuses))
            errors.append(f"{gate_label}.{status_field} invalid (expected one of: {allowed})")
        validate_string(gate.get("notes"), f"{gate_label}.notes", errors)
        if status_field == "readiness":
            validate_string(gate.get("check"), f"{gate_label}.check", errors)
        validate_existing_path_list(run_dir, gate.get("evidence"), f"{gate_label}.evidence", errors)

    for axis, facet in selected_pairs:
        if (axis, facet) not in seen:
            errors.append(f"{label} missing selected gate facet: {facet}")
    return seen, errors


def validate_verification_readiness(
    run_dir: Path,
    lane_map: dict,
    *,
    required: bool,
    architecture_context_by_axis: dict[str, list[str]],
    lane_by_id: dict[str, dict],
    normalized_status_by_id: dict[str, str],
    final_verdict: str | None,
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    selected_pairs = selected_verification_gate_pairs(architecture_context_by_axis)
    raw_config = lane_map.get("verification_readiness")
    if raw_config is None:
        if required:
            errors.append("lane-map.json field 'verification_readiness' is required")
        return None, errors
    if not isinstance(raw_config, dict):
        return None, ["lane-map.json field 'verification_readiness' must be an object"]

    artifact = raw_config.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        errors.append("lane-map.json verification_readiness.artifact must be a non-empty string")
        return None, errors
    artifact_path = resolve_run_path(run_dir, artifact)
    if not artifact_path.exists():
        errors.append(f"{artifact} not found")
        return None, errors

    lane_ids = raw_config.get("lanes")
    if not isinstance(lane_ids, list) or not lane_ids:
        errors.append("lane-map.json verification_readiness.lanes must be a non-empty array")
        lane_ids = []
    readiness_lane_ids: list[str] = []
    for index, lane_id in enumerate(lane_ids):
        if not isinstance(lane_id, str) or not lane_id:
            errors.append(f"lane-map.json verification_readiness.lanes[{index}] must be a non-empty string")
            continue
        readiness_lane_ids.append(lane_id)
        lane = lane_by_id.get(lane_id)
        if lane is None:
            errors.append(f"lane-map.json verification_readiness lane not found: {lane_id}")
            continue
        if lane.get("type") != "qa":
            errors.append(f"lane-map.json verification_readiness lane must be type=qa: {lane_id}")
        if lane.get("role") != "qa-verifier":
            errors.append(f"lane-map.json verification_readiness lane must use role=qa-verifier: {lane_id}")
        if lane.get("critical") is not True:
            errors.append(f"lane-map.json verification_readiness lane must be critical: {lane_id}")
        if normalized_status_by_id.get(lane_id) in SUCCESSFUL_LANE_STATUSES:
            handoff = lane.get("handoff")
            if isinstance(handoff, str) and handoff:
                handoff_path = resolve_run_path(run_dir, handoff)
                for section in missing_markdown_headings(
                    handoff_path,
                    [VERIFICATION_GATE_RESULTS_SECTION],
                ):
                    errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")

    data, json_errors = load_json(artifact_path, artifact)
    errors.extend(json_errors)
    if not isinstance(data, dict):
        errors.append(f"{artifact} must be a JSON object")
        return None, errors
    if data.get("version") != 1:
        errors.append(f"{artifact} version must be 1")
    status = data.get("status")
    if status not in VERIFICATION_READINESS_STATUSES:
        errors.append(f"{artifact} status invalid")

    attempts = data.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        errors.append(f"{artifact} attempts must be a non-empty array")
        attempts = []
    approval_requests = data.get("approval_requests")
    if not isinstance(approval_requests, list):
        errors.append(f"{artifact} approval_requests must be an array")
        approval_requests = []
    approval_executions = data.get("approval_executions")
    if not isinstance(approval_executions, list):
        errors.append(f"{artifact} approval_executions must be an array")
        approval_executions = []

    request_by_id: dict[str, dict] = {}
    for index, request in enumerate(approval_requests):
        label = f"{artifact} approval_requests[{index}]"
        if not isinstance(request, dict):
            errors.append(f"{label} must be an object")
            continue
        request_id = validate_string(request.get("id"), f"{label}.id", errors)
        if request_id:
            if not KEBAB_CASE_PATTERN.fullmatch(request_id):
                errors.append(f"{label}.id must be kebab-case")
            if request_id in request_by_id:
                errors.append(f"{artifact} duplicate approval request id: {request_id}")
            request_by_id[request_id] = request
        request_status = request.get("status")
        if request_status not in VERIFICATION_APPROVAL_STATUSES:
            errors.append(f"{label}.status invalid")
        validate_string(request.get("reason"), f"{label}.reason", errors)
        validate_string(request.get("manual_instruction"), f"{label}.manual_instruction", errors)
        if request.get("resume_phrase") != "Готово":
            errors.append(f"{label}.resume_phrase must be Готово")
        affected_gates = request.get("affected_gates")
        if not isinstance(affected_gates, list) or not affected_gates:
            errors.append(f"{label}.affected_gates must be a non-empty array")
        elif selected_pairs:
            selected_facets = {facet for _axis, facet in selected_pairs}
            for gate_index, facet in enumerate(affected_gates):
                if not isinstance(facet, str) or not facet:
                    errors.append(f"{label}.affected_gates[{gate_index}] must be a non-empty string")
                elif facet not in selected_facets:
                    errors.append(f"{label}.affected_gates[{gate_index}] unknown selected gate facet: {facet}")
        commands = request.get("commands")
        if not isinstance(commands, list) or not commands:
            errors.append(f"{label}.commands must be a non-empty array")
        else:
            for command_index, command in enumerate(commands):
                command_label = f"{label}.commands[{command_index}]"
                if not isinstance(command, dict):
                    errors.append(f"{command_label} must be an object")
                    continue
                validate_string(command.get("cwd"), f"{command_label}.cwd", errors)
                validate_string(command.get("command"), f"{command_label}.command", errors)
                validate_string(command.get("source"), f"{command_label}.source", errors)
                if command.get("requires_user_approval") is not True:
                    errors.append(f"{command_label}.requires_user_approval must be true")

    successful_execution_request_ids: set[str] = set()
    for index, execution in enumerate(approval_executions):
        label = f"{artifact} approval_executions[{index}]"
        if not isinstance(execution, dict):
            errors.append(f"{label} must be an object")
            continue
        request_id = validate_string(execution.get("request_id"), f"{label}.request_id", errors)
        if request_id and request_id not in request_by_id:
            errors.append(f"{label}.request_id unknown approval request: {request_id}")
        execution_status = execution.get("status")
        if execution_status not in VERIFICATION_APPROVAL_EXECUTION_STATUSES:
            errors.append(f"{label}.status invalid")
        evidence = validate_existing_path_list(run_dir, execution.get("evidence"), f"{label}.evidence", errors)
        if request_id and execution_status == "succeeded" and evidence:
            successful_execution_request_ids.add(request_id)

    latest_attempt_status: str | None = None
    ready_lane_ids: list[str] = []
    blocked_ids: set[str] = set()
    for index, attempt in enumerate(attempts):
        label = f"{artifact} attempts[{index}]"
        if not isinstance(attempt, dict):
            errors.append(f"{label} must be an object")
            continue
        validate_string(attempt.get("id"), f"{label}.id", errors)
        lane_id = validate_string(attempt.get("lane"), f"{label}.lane", errors)
        if lane_id and lane_id not in readiness_lane_ids:
            errors.append(f"{label}.lane must be listed in lane-map verification_readiness.lanes: {lane_id}")
        attempt_status = attempt.get("status")
        if attempt_status not in VERIFICATION_READINESS_ATTEMPT_STATUSES:
            errors.append(f"{label}.status invalid")
        else:
            latest_attempt_status = attempt_status
            if attempt_status == "ready" and lane_id:
                ready_lane_ids.append(lane_id)
        _seen, gate_errors = validate_verification_gate_records(
            run_dir,
            attempt.get("gates"),
            label,
            selected_pairs,
            status_field="readiness",
            allowed_statuses=VERIFICATION_GATE_READINESS_STATUSES,
        )
        errors.extend(gate_errors)
        blockers = attempt.get("blockers")
        if not isinstance(blockers, list):
            errors.append(f"{label}.blockers must be an array")
            blockers = []
        else:
            for blocker in blockers:
                if isinstance(blocker, str) and blocker:
                    blocked_ids.add(blocker)
        request_ids = attempt.get("approval_requests")
        if not isinstance(request_ids, list):
            errors.append(f"{label}.approval_requests must be an array")
            request_ids = []
        for request_id in request_ids:
            if not isinstance(request_id, str) or not request_id:
                errors.append(f"{label}.approval_requests must contain non-empty strings")
            elif request_id not in request_by_id:
                errors.append(f"{label}.approval_requests unknown approval request: {request_id}")
        if attempt_status == "ready":
            if blockers:
                errors.append(f"{label}.blockers must be empty when status=ready")
            if request_ids:
                errors.append(f"{label}.approval_requests must be empty when status=ready")
            for gate in attempt.get("gates", []) if isinstance(attempt.get("gates"), list) else []:
                if isinstance(gate, dict) and gate.get("readiness") != "ready":
                    errors.append(f"{label}.gates must all be ready when status=ready")
                    break
        elif attempt_status == "needs-approval":
            if not blockers:
                errors.append(f"{label}.blockers must be non-empty when status=needs-approval")
            if not request_ids:
                errors.append(f"{label}.approval_requests must be non-empty when status=needs-approval")
        elif attempt_status == "blocked" and not blockers:
            errors.append(f"{label}.blockers must be non-empty when status=blocked")

    for request_id, request in request_by_id.items():
        if request.get("status") == "approved" and request_id not in successful_execution_request_ids:
            errors.append(f"{artifact} approval request {request_id} approved without execution evidence")

    if status == "ready":
        if latest_attempt_status != "ready" or not ready_lane_ids:
            errors.append(f"{artifact} status ready requires a latest ready attempt")
        if any(request.get("status") == "pending" for request in request_by_id.values()):
            errors.append(f"{artifact} status ready cannot have pending approval requests")
    elif status == "needs-approval":
        if not any(request.get("status") == "pending" for request in request_by_id.values()):
            errors.append(f"{artifact} status needs-approval requires a pending approval request")
    elif status == "paused-blocked":
        if final_verdict != "blocked":
            errors.append(f"{artifact} status paused-blocked requires final Verdict: blocked")
        if not any(request.get("status") == "declined" for request in request_by_id.values()):
            errors.append(f"{artifact} status paused-blocked requires a declined approval request")
        final_path = run_dir / "final.md"
        if final_path.exists():
            missing = missing_markdown_headings(final_path, [VERIFICATION_READINESS_SECTION])
            for section in missing:
                errors.append(f"final.md missing section: {section}")
            final_text = markdown_section_text(final_path.read_text(encoding="utf-8"), VERIFICATION_READINESS_SECTION)
            if "Готово" not in final_text:
                errors.append("final.md Verification Readiness missing resume phrase: Готово")
            for blocker_id in blocked_ids:
                if blocker_id and not contains_facet_id(final_text, blocker_id):
                    errors.append(f"final.md Verification Readiness missing blocker id: {blocker_id}")
    elif status == "blocked":
        if final_verdict not in {"blocked", "fail"}:
            errors.append(f"{artifact} status blocked requires final Verdict: blocked or fail")

    return data, errors


def validate_qa_verification_results_shape(
    run_dir: Path,
    lane: dict,
    label: str,
    *,
    selected_pairs: list[tuple[str, str]],
) -> tuple[dict | None, list[str]]:
    results = lane.get("verification_results")
    if not isinstance(results, dict):
        return None, [f"lane-map.json: lane {label} missing verification_results"]
    errors: list[str] = []
    status = results.get("status")
    if status not in VERIFICATION_RESULT_STATUSES:
        errors.append(f"lane-map.json: lane {label} verification_results.status invalid")
    _seen, gate_errors = validate_verification_gate_records(
        run_dir,
        results.get("gates"),
        f"lane-map.json: lane {label} verification_results",
        selected_pairs,
        status_field="status",
        allowed_statuses=VERIFICATION_RESULT_STATUSES,
    )
    errors.extend(gate_errors)
    if status == "pass":
        for gate in results.get("gates", []) if isinstance(results.get("gates"), list) else []:
            if isinstance(gate, dict) and gate.get("status") != "pass":
                errors.append(f"lane-map.json: lane {label} verification_results pass requires all gates pass")
                break
    elif status == "blocked":
        if not any(
            isinstance(gate, dict) and gate.get("status") == "blocked"
            for gate in (results.get("gates", []) if isinstance(results.get("gates"), list) else [])
        ):
            errors.append(f"lane-map.json: lane {label} verification_results blocked requires a blocked gate")
    return results, errors


def validate_selected_architecture_facets(path: Path, selected_facets: list[str]) -> list[str]:
    if not path.exists() or not selected_facets:
        return []
    selected_architecture = markdown_section_text(
        path.read_text(encoding="utf-8"),
        "Selected Architecture",
    )
    return [
        f"Selected Architecture missing Architecture Matrix facet: {facet}"
        for facet in selected_facets
        if not contains_facet_id(selected_architecture, facet)
    ]


def validate_selected_architecture_capabilities(
    path: Path,
    selected_capabilities: list[str],
) -> list[str]:
    if not path.exists() or not selected_capabilities:
        return []
    selected_architecture = markdown_section_text(
        path.read_text(encoding="utf-8"),
        "Selected Architecture",
    )
    return [
        f"Selected Architecture missing architecture capability: {capability}"
        for capability in selected_capabilities
        if not contains_facet_id(selected_architecture, capability)
    ]


def missing_facets_in_markdown_sections(
    path: Path,
    headings: list[str],
    facets: list[str],
) -> list[str]:
    if not path.exists() or not facets:
        return []
    text = path.read_text(encoding="utf-8")
    section_text = "\n".join(markdown_section_text(text, heading) for heading in headings)
    return [facet for facet in facets if not contains_facet_id(section_text, facet)]


def missing_capabilities_in_markdown_sections(
    path: Path,
    headings: list[str],
    capabilities: list[str],
) -> list[str]:
    if not path.exists() or not capabilities:
        return []
    text = path.read_text(encoding="utf-8")
    section_text = "\n".join(markdown_section_text(text, heading) for heading in headings)
    return [
        capability
        for capability in capabilities
        if not contains_facet_id(section_text, capability)
    ]


def parse_architecture_design_decision(path: Path) -> tuple[str | None, list[str]]:
    text = path.read_text(encoding="utf-8")
    decision_text = markdown_section_text(text, ARCHITECTURE_DESIGN_DECISION_SECTION)
    status_lines = [
        line.strip()
        for line in decision_text.splitlines()
        if line.strip().startswith("Status:")
    ]
    if len(status_lines) != 1:
        return None, ["Architecture Design Brief Decision must contain exactly one Status line"]

    status_line = status_lines[0]
    status = ARCHITECTURE_DESIGN_DECISION_STATUS_LINES.get(status_line)
    if status is None:
        raw_status = status_line.removeprefix("Status:").strip() or status_line
        return None, [f"invalid Architecture Design Brief Decision status: {raw_status}"]
    return status, []


def validate_architecture_design_brief(
    path: Path,
    selected_facets: list[str],
    selected_capabilities: list[str],
) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    missing_sections = missing_markdown_headings(path, ARCHITECTURE_DESIGN_BRIEF_SECTIONS)
    errors.extend(
        f"architecture design brief missing section: {section}"
        for section in missing_sections
    )

    if ARCHITECTURE_DESIGN_MATRIX_SECTION not in missing_sections:
        errors.extend(
            f"Selected Matrix Facets missing Architecture Matrix facet: {facet}"
            for facet in missing_facets_in_markdown_sections(
                path,
                [ARCHITECTURE_DESIGN_MATRIX_SECTION],
                selected_facets,
            )
        )

    if ARCHITECTURE_DESIGN_EXECUTION_PLAN_SECTION not in missing_sections:
        errors.extend(
            f"Execution Plan missing architecture capability: {capability}"
            for capability in missing_capabilities_in_markdown_sections(
                path,
                [ARCHITECTURE_DESIGN_EXECUTION_PLAN_SECTION],
                selected_capabilities,
            )
        )

    decision_status: str | None = None
    if ARCHITECTURE_DESIGN_DECISION_SECTION not in missing_sections:
        decision_status, decision_errors = parse_architecture_design_decision(path)
        errors.extend(decision_errors)
    return decision_status, errors


def validate_architecture_contract_handoff(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        f"architecture contract handoff missing section: {section}"
        for section in missing_markdown_headings(path, ARCHITECTURE_CONTRACT_SECTIONS)
    ]


def missing_markdown_headings(path: Path, headings: list[str]) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [heading for heading in headings if not has_markdown_heading(text, heading)]


def requires_architecture_capability_citation(text: str) -> bool:
    normalized = text.lower()
    retained_complexity_phrases = [
        "retained dependency",
        "retained dependencies",
        "kept dependency",
        "kept dependencies",
        "dependency retained",
        "dependencies retained",
        "justified dependency",
        "dependency justified",
        "retained abstraction",
        "retained abstractions",
        "kept abstraction",
        "kept abstractions",
        "abstraction retained",
        "abstractions retained",
        "justified abstraction",
        "abstraction justified",
    ]
    return any(phrase in normalized for phrase in retained_complexity_phrases)


def contains_fixable_simplicity_problem(text: str) -> bool:
    normalized = text.lower()
    return any(phrase in normalized for phrase in ENGINEERING_SIMPLICITY_FIXABLE_PHRASES)


def validate_non_empty_string_array(
    value: object,
    *,
    label: str,
    field: str,
    required: bool,
) -> list[str]:
    if not isinstance(value, list):
        return [f"lane-map.json: lane {label} {field} must be an array"]
    if required and not value:
        return [f"lane-map.json: lane {label} {field} must be a non-empty array"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"lane-map.json: lane {label} {field}[{index}] "
                "must be a non-empty string"
            )
    return errors


def validate_surface_id_array(
    value: object,
    *,
    prefix: str,
    field: str,
    required: bool,
) -> tuple[list[str], list[str]]:
    label = f"{prefix}.{field}"
    if not isinstance(value, list):
        return [], [f"lane-map.json: {label} must be an array"]
    if required and not value:
        return [], [f"lane-map.json: {label} must be a non-empty array"]

    surfaces: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"lane-map.json: {label}[{index}] must be a non-empty string"
            )
            continue
        if not KEBAB_CASE_PATTERN.fullmatch(item):
            errors.append(f"lane-map.json: {label}[{index}] must be kebab-case")
            continue
        if item in seen:
            errors.append(f"lane-map.json: {label} duplicate surface: {item}")
            continue
        seen.add(item)
        surfaces.append(item)
    return surfaces, errors


def validate_scope_evidence_paths(
    run_dir: Path,
    value: object,
    *,
    prefix: str,
    required: bool,
) -> tuple[list[str], str, list[str]]:
    label = f"{prefix}.evidence"
    if not isinstance(value, list):
        return [], "", [f"lane-map.json: {label} must be an array"]
    if required and not value:
        return [], "", [f"lane-map.json: {label} must be a non-empty array"]

    paths: list[str] = []
    text_parts: list[str] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(
                f"lane-map.json: {label}[{index}] must be a non-empty string"
            )
            continue
        paths.append(item)
        path = resolve_run_path(run_dir, item)
        if not path.exists():
            errors.append(f"lane-map.json: {label}[{index}] not found: {item}")
            continue
        text_parts.append(path.read_text(encoding="utf-8"))
    return paths, "\n".join(text_parts), errors


def validate_engineering_simplicity_scope(
    run_dir: Path,
    data: dict,
    *,
    required: bool,
    allow_pending: bool,
) -> tuple[list[str], list[str], set[str], list[str]]:
    raw_scope = data.get(ENGINEERING_SIMPLICITY_SCOPE_FIELD)
    if raw_scope is None:
        if required and not allow_pending:
            return [], [], set(), [
                "lane-map.json: positive architecture-gated worker run "
                f"requires {ENGINEERING_SIMPLICITY_SCOPE_FIELD}"
            ]
        return [], [], set(), []
    if not isinstance(raw_scope, dict):
        return [], [], set(), [
            f"lane-map.json: {ENGINEERING_SIMPLICITY_SCOPE_FIELD} must be an object"
        ]

    strict_required = required and not allow_pending
    primary_surfaces, primary_errors = validate_surface_id_array(
        raw_scope.get("primary_surfaces"),
        prefix=ENGINEERING_SIMPLICITY_SCOPE_FIELD,
        field="primary_surfaces",
        required=strict_required,
    )
    secondary_surfaces, secondary_errors = validate_surface_id_array(
        raw_scope.get("secondary_surfaces", []),
        prefix=ENGINEERING_SIMPLICITY_SCOPE_FIELD,
        field="secondary_surfaces",
        required=False,
    )

    errors = [*primary_errors, *secondary_errors]
    overlap = sorted(set(primary_surfaces) & set(secondary_surfaces))
    for surface in overlap:
        errors.append(
            "lane-map.json: engineering_simplicity_scope surface cannot be both "
            f"primary and secondary: {surface}"
        )

    evidence_value = raw_scope.get("evidence", [])
    _paths, _text, evidence_errors = validate_scope_evidence_paths(
        run_dir,
        evidence_value,
        prefix=ENGINEERING_SIMPLICITY_SCOPE_FIELD,
        required=strict_required,
    )
    errors.extend(evidence_errors)

    notes = raw_scope.get("notes")
    if strict_required and (not isinstance(notes, str) or not notes.strip()):
        errors.append(
            "lane-map.json: engineering_simplicity_scope.notes "
            "must be a non-empty string"
        )

    declared_surfaces = set(primary_surfaces) | set(secondary_surfaces)
    return primary_surfaces, secondary_surfaces, declared_surfaces, errors


def validate_engineering_simplicity_scope_coverage(
    run_dir: Path,
    *,
    simplicity: dict,
    label: str,
    declared_primary_surfaces: set[str],
    declared_secondary_surfaces: set[str],
) -> tuple[list[str], list[str], list[str]]:
    prefix = (
        f"lane {label} "
        "architecture_compliance.engineering_simplicity.scope_coverage"
    )
    raw_coverage = simplicity.get("scope_coverage")
    if not isinstance(raw_coverage, dict):
        return [], [], [
            f"lane-map.json: lane {label} missing "
            "architecture_compliance.engineering_simplicity.scope_coverage"
        ]

    primary_surfaces, primary_errors = validate_surface_id_array(
        raw_coverage.get("primary_surfaces", []),
        prefix=prefix,
        field="primary_surfaces",
        required=False,
    )
    secondary_surfaces, secondary_errors = validate_surface_id_array(
        raw_coverage.get("secondary_surfaces", []),
        prefix=prefix,
        field="secondary_surfaces",
        required=False,
    )
    errors = [*primary_errors, *secondary_errors]

    covered_surfaces = [*primary_surfaces, *secondary_surfaces]
    if not covered_surfaces:
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.engineering_simplicity.scope_coverage "
            "must cover at least one primary or secondary surface"
        )

    for surface in primary_surfaces:
        if surface not in declared_primary_surfaces:
            errors.append(
                f"lane-map.json: lane {label} scope_coverage "
                f"covers undeclared surface: {surface}"
            )
    for surface in secondary_surfaces:
        if surface not in declared_secondary_surfaces:
            errors.append(
                f"lane-map.json: lane {label} scope_coverage "
                f"covers undeclared surface: {surface}"
            )

    _paths, evidence_text, evidence_errors = validate_scope_evidence_paths(
        run_dir,
        raw_coverage.get("evidence"),
        prefix=prefix,
        required=True,
    )
    errors.extend(evidence_errors)
    if evidence_text:
        for surface in covered_surfaces:
            if not contains_facet_id(evidence_text, surface):
                errors.append(
                    f"lane-map.json: lane {label} "
                    f"scope_coverage evidence missing surface: {surface}"
                )

    notes = raw_coverage.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.engineering_simplicity.scope_coverage.notes "
            "must be a non-empty string"
        )

    return primary_surfaces, secondary_surfaces, errors


def validate_engineering_simplicity_shape(
    compliance: dict,
    label: str,
    *,
    selected_capabilities: list[str],
) -> list[str]:
    errors: list[str] = []
    simplicity = compliance.get("engineering_simplicity")
    if not isinstance(simplicity, dict):
        return [
            f"lane-map.json: lane {label} "
            "missing architecture_compliance.engineering_simplicity"
        ]

    status = simplicity.get("status")
    if status not in ENGINEERING_SIMPLICITY_STATUSES:
        allowed = ", ".join(sorted(ENGINEERING_SIMPLICITY_STATUSES))
        errors.append(
            f"lane-map.json: lane {label} invalid "
            f"architecture_compliance.engineering_simplicity.status '{status}' "
            f"(expected one of: {allowed})"
        )

    checks = simplicity.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.engineering_simplicity.checks "
            "must be a non-empty array"
        )
        present_checks: set[str] = set()
    else:
        present_checks = set()
        for index, check in enumerate(checks):
            if not isinstance(check, str) or not check.strip():
                errors.append(
                    f"lane-map.json: lane {label} "
                    "architecture_compliance.engineering_simplicity.checks"
                    f"[{index}] must be a non-empty string"
                )
                continue
            if check not in ENGINEERING_SIMPLICITY_CHECK_SET:
                errors.append(
                    f"lane-map.json: lane {label} "
                    "architecture_compliance.engineering_simplicity.checks"
                    f"[{index}] unknown check: {check}"
                )
                continue
            present_checks.add(check)
        for check in ENGINEERING_SIMPLICITY_REQUIRED_CHECKS:
            if check not in present_checks:
                errors.append(
                    f"lane-map.json: lane {label} "
                    "architecture_compliance.engineering_simplicity.checks "
                    f"missing required check: {check}"
                )

    findings = simplicity.get("findings")
    actions = simplicity.get("actions")
    errors.extend(
        validate_non_empty_string_array(
            findings,
            label=label,
            field="architecture_compliance.engineering_simplicity.findings",
            required=False,
        )
    )
    errors.extend(
        validate_non_empty_string_array(
            actions,
            label=label,
            field="architecture_compliance.engineering_simplicity.actions",
            required=False,
        )
    )

    notes = simplicity.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.engineering_simplicity.notes "
            "must be a non-empty string"
        )

    if status == "fixed":
        if not isinstance(findings, list) or not findings:
            errors.append(
                f"lane-map.json: lane {label} "
                "fixed engineering_simplicity requires non-empty findings"
            )
        if not isinstance(actions, list) or not actions:
            errors.append(
                f"lane-map.json: lane {label} "
                "fixed engineering_simplicity requires non-empty actions"
            )

    remediation_text_parts: list[str] = []
    if isinstance(findings, list):
        remediation_text_parts.extend(
            finding for finding in findings if isinstance(finding, str)
        )
    if isinstance(actions, list):
        remediation_text_parts.extend(
            action for action in actions if isinstance(action, str)
        )
    if isinstance(notes, str):
        remediation_text_parts.append(notes)
    if status == "pass" and any(
        contains_fixable_simplicity_problem(part) for part in remediation_text_parts
    ):
        errors.append(
            f"lane-map.json: lane {label} "
            "engineering_simplicity status=pass cannot report fixable remediation findings"
        )

    parent_status = compliance.get("status")
    recheck_lane = compliance.get("recheck_lane")
    if status == "drift":
        if parent_status != "drift":
            errors.append(
                f"lane-map.json: lane {label} "
                "engineering_simplicity drift requires "
                "architecture_compliance.status=drift"
            )
        if not isinstance(recheck_lane, str) or not recheck_lane:
            errors.append(
                f"lane-map.json: lane {label} "
                "engineering_simplicity drift requires recheck_lane"
            )
    elif parent_status != "drift" and status not in {"pass", "fixed"}:
        errors.append(
            f"lane-map.json: lane {label} successful worker lane accepts only "
            "engineering_simplicity pass or fixed unless architecture_compliance.status=drift"
        )

    citation_parts = [notes if isinstance(notes, str) else ""]
    if isinstance(findings, list):
        citation_parts.extend(finding for finding in findings if isinstance(finding, str))
    if isinstance(actions, list):
        citation_parts.extend(action for action in actions if isinstance(action, str))
    citation_text = "\n".join(citation_parts)
    if requires_architecture_capability_citation(citation_text):
        if not any(
            contains_facet_id(citation_text, capability)
            for capability in selected_capabilities
        ):
            errors.append(
                f"lane-map.json: lane {label} retained dependency or abstraction "
                "must cite a selected architecture capability"
            )

    return errors


def validate_architecture_compliance_shape(
    lane: dict,
    label: str,
    *,
    selected_matrix_facets: set[str],
    known_matrix_facets: set[str],
    selected_capabilities: list[str],
) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    compliance = lane.get("architecture_compliance")
    if not isinstance(compliance, dict):
        return None, [f"lane-map.json: lane {label} missing architecture_compliance"]

    status = compliance.get("status")
    if status not in ARCHITECTURE_COMPLIANCE_STATUSES:
        allowed = ", ".join(sorted(ARCHITECTURE_COMPLIANCE_STATUSES))
        errors.append(
            f"lane-map.json: lane {label} invalid architecture_compliance.status "
            f"'{status}' (expected one of: {allowed})"
        )

    contract_sections = compliance.get("contract_sections")
    if not isinstance(contract_sections, list) or not contract_sections:
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.contract_sections must be a non-empty array"
        )
    else:
        for index, section in enumerate(contract_sections):
            if not isinstance(section, str) or section not in ARCHITECTURE_CONTRACT_SECTION_SET:
                errors.append(
                    f"lane-map.json: lane {label} "
                    f"architecture_compliance.contract_sections[{index}] "
                    f"unknown architecture contract section: {section}"
                )

    matrix_facets = compliance.get("matrix_facets")
    if not isinstance(matrix_facets, list) or not matrix_facets:
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.matrix_facets must be a non-empty array"
        )
    else:
        for index, facet in enumerate(matrix_facets):
            if not isinstance(facet, str) or not facet.strip():
                errors.append(
                    f"lane-map.json: lane {label} "
                    f"architecture_compliance.matrix_facets[{index}] "
                    "must be a non-empty string"
                )
                continue
            if facet not in known_matrix_facets:
                errors.append(
                    f"lane-map.json: lane {label} "
                    f"architecture_compliance.matrix_facets[{index}] "
                    f"unknown architecture_context facet: {facet}"
                )
                continue
            if facet not in selected_matrix_facets:
                errors.append(
                    f"lane-map.json: lane {label} "
                    f"architecture_compliance.matrix_facets[{index}] "
                    f"unselected architecture_context facet: {facet}"
                )

    notes = compliance.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.notes must be a non-empty string"
        )

    recheck_lane = compliance.get("recheck_lane")
    if recheck_lane is not None and (not isinstance(recheck_lane, str) or not recheck_lane):
        errors.append(
            f"lane-map.json: lane {label} "
            "architecture_compliance.recheck_lane must be null or a non-empty string"
        )
    if status == "compliant" and isinstance(recheck_lane, str) and recheck_lane:
        errors.append(
            f"lane-map.json: lane {label} compliant architecture_compliance "
            "must not set recheck_lane"
        )

    errors.extend(
        validate_engineering_simplicity_shape(
            compliance,
            label,
            selected_capabilities=selected_capabilities,
        )
    )

    return compliance, errors


def validate_lane_map(
    run_dir: Path,
    mitigation_risks: list[dict] | None = None,
    resolution_records: list[dict] | None = None,
    *,
    allow_pending: bool = False,
) -> list[str]:
    lane_map_path = run_dir / "lane-map.json"
    data, errors = load_json(lane_map_path, "lane-map.json")
    if errors or data is None:
        return errors

    if not isinstance(data, dict):
        return ["lane-map.json must be a JSON object"]

    schema_version = data.get("schema_version")
    if schema_version not in {1, 2}:
        errors.append("lane-map.json field 'schema_version' must be 1 or 2")

    architecture_contract_required = data.get("architecture_contract_required", False)
    if not isinstance(architecture_contract_required, bool):
        errors.append("lane-map.json field 'architecture_contract_required' must be a boolean")
        architecture_contract_required = False

    architecture_contract_independent = data.get("architecture_contract_independent", False)
    if not isinstance(architecture_contract_independent, bool):
        errors.append("lane-map.json field 'architecture_contract_independent' must be a boolean")
        architecture_contract_independent = False

    budget: str | None = None
    if schema_version == 2:
        raw_budget = data.get("budget")
        if raw_budget is None:
            errors.append("lane-map.json field 'budget' is required for schema v2")
        elif raw_budget not in TRACE_BUDGETS:
            allowed = ", ".join(sorted(TRACE_BUDGETS))
            errors.append(f"lane-map.json field 'budget' must be one of: {allowed}")
        else:
            budget = raw_budget

    architecture_context_by_axis: dict[str, list[str]] = {
        axis: [] for axis in ARCHITECTURE_CONTEXT_AXES
    }
    architecture_context_facets: list[str] = []
    architecture_capabilities: list[str] = []
    known_matrix_facets: set[str] = set()
    known_architecture_capability_ids: set[str] = set()
    if architecture_contract_required or "architecture_context" in data:
        matrix_facets, matrix_errors = load_architecture_matrix_facets()
        errors.extend(matrix_errors)
        known_matrix_facets = {
            facet for facets in matrix_facets.values() for facet in facets
        }
        (
            architecture_context_by_axis,
            architecture_context_facets,
            context_errors,
        ) = validate_architecture_context_shape(
            data,
            matrix_facets,
            required=architecture_contract_required,
        )
        errors.extend(context_errors)

    if architecture_contract_required or "architecture_capabilities" in data:
        capabilities_by_id, capability_registry_errors = validate_architecture_capability_registry(
            ARCHITECTURE_CAPABILITY_REGISTRY_PATH,
            validate_skills=False,
            require_full_matrix_coverage=False,
        )
        known_architecture_capability_ids = set(capabilities_by_id)
        errors.extend(capability_registry_errors)
        architecture_capabilities, capability_errors = validate_architecture_capabilities_shape(
            data,
            capabilities_by_id,
            architecture_context_facets,
            required=architecture_contract_required,
        )
        errors.extend(capability_errors)

    lanes = data.get("lanes")
    if not isinstance(lanes, list):
        errors.append("lane-map.json field 'lanes' must be an array")
        return errors

    final_verdict = read_single_field(run_dir / "final.md", "Verdict")
    raw_worker_lane_count = sum(
        1
        for lane in lanes
        if isinstance(lane, dict) and lane.get("type") in WORKER_LANE_TYPES
    )
    simplicity_scope_required = (
        schema_version == 2
        and architecture_contract_required
        and raw_worker_lane_count > 0
        and final_verdict in POSITIVE_FINAL_VERDICTS
    )
    (
        simplicity_primary_surfaces,
        simplicity_secondary_surfaces,
        simplicity_declared_surfaces,
        simplicity_scope_errors,
    ) = validate_engineering_simplicity_scope(
        run_dir,
        data,
        required=simplicity_scope_required,
        allow_pending=allow_pending,
    )
    errors.extend(simplicity_scope_errors)
    simplicity_declared_primary_surfaces = set(simplicity_primary_surfaces)
    simplicity_declared_secondary_surfaces = set(simplicity_secondary_surfaces)
    covered_primary_surfaces: set[str] = set()

    verification_readiness_lane_ids: set[str] = set()
    raw_verification_readiness = data.get("verification_readiness")
    if isinstance(raw_verification_readiness, dict):
        raw_readiness_lanes = raw_verification_readiness.get("lanes")
        if isinstance(raw_readiness_lanes, list):
            verification_readiness_lane_ids = {
                lane_id
                for lane_id in raw_readiness_lanes
                if isinstance(lane_id, str) and lane_id
            }

    lane_ids: set[str] = set()
    lane_by_id: dict[str, dict] = {}
    normalized_status_by_id: dict[str, str] = {}
    architecture_lane_ids: list[str] = []
    critical_architecture_lane_ids: list[str] = []
    successful_architecture_lane_ids: list[str] = []
    successful_architecture_waves: list[int] = []
    approved_architecture_design_waves: list[int] = []
    not_approved_architecture_design_lane_ids: list[str] = []
    successful_reviewer_lane_ids: list[str] = []
    successful_reviewer_lanes: list[tuple[str, int, str | None]] = []
    successful_qa_lanes: list[tuple[str, int, str | None]] = []
    successful_worker_lanes: list[tuple[str, int, str | None]] = []
    drifting_worker_lanes: list[tuple[str, int | None, str | None]] = []
    simplicity_drifting_worker_lanes: list[tuple[str, int | None, str | None]] = []
    fixed_simplicity_worker_lane_ids: list[str] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane_id = lane.get("id")
        if not isinstance(lane_id, str) or not lane_id:
            continue
        if lane.get("type") not in WORKER_LANE_TYPES:
            continue
        if normalize_lane_status(lane.get("status")) not in SUCCESSFUL_LANE_STATUSES:
            continue
        compliance = lane.get("architecture_compliance")
        if not isinstance(compliance, dict):
            continue
        simplicity = compliance.get("engineering_simplicity")
        if isinstance(simplicity, dict) and simplicity.get("status") == "fixed":
            if lane_id not in fixed_simplicity_worker_lane_ids:
                fixed_simplicity_worker_lane_ids.append(lane_id)
    worker_lane_count = 0
    mitigation_risks = mitigation_risks or []
    mitigation_risk_ids = risk_mitigation_ids(mitigation_risks)
    resolution_records = resolution_records or []
    resolution_risk_ids = risk_resolution_ids(resolution_records)
    claim_evidence_candidate = (
        schema_version == 2
        and architecture_contract_required
        and final_verdict in POSITIVE_FINAL_VERDICTS
        and not allow_pending
    )
    required_claim_ids: list[str] = []
    claim_evidence_contract_errors: list[str] = []

    for index, lane in enumerate(lanes):
        if not isinstance(lane, dict):
            errors.append(f"lane-map.json: lanes[{index}] must be an object")
            continue

        lane_id = validate_lane_string_field(lane, "id", f"lanes[{index}]", errors)
        label = lane_label(lane_id, index)
        if not lane_id:
            continue
        if lane_id in lane_ids:
            errors.append(f"lane-map.json: duplicate lane id: {lane_id}")
            continue
        lane_ids.add(lane_id)
        lane_by_id[lane_id] = lane

        role = validate_lane_string_field(lane, "role", label, errors)

        lane_type = lane.get("type")
        if lane_type not in LANE_TYPES:
            allowed = ", ".join(sorted(LANE_TYPES))
            errors.append(f"lane-map.json: lane {label} invalid type '{lane_type}' (expected one of: {allowed})")
        elif lane_type in WORKER_LANE_TYPES:
            worker_lane_count += 1

        wave = lane.get("wave")
        if not isinstance(wave, int) or isinstance(wave, bool):
            errors.append(f"lane-map.json: lane {label} field 'wave' must be an integer")

        critical = lane.get("critical")
        if not isinstance(critical, bool):
            errors.append(f"lane-map.json: lane {label} field 'critical' must be a boolean")

        execution_mode = normalize_execution_mode(lane.get("execution_mode"))
        if execution_mode not in AGENT_EXECUTION_MODES:
            allowed = ", ".join(sorted(AGENT_EXECUTION_MODES))
            errors.append(
                f"lane-map.json: lane {label} invalid execution_mode '{lane.get('execution_mode')}' "
                f"(expected one of: {allowed})"
            )

        status = normalize_lane_status(lane.get("status"))
        if status not in LANE_STATUSES:
            allowed = ", ".join(sorted(LANE_STATUSES))
            errors.append(
                f"lane-map.json: lane {label} invalid status '{lane.get('status')}' "
                f"(expected one of: {allowed})"
            )
        else:
            normalized_status_by_id[lane_id] = status

        evidence = lane.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"lane-map.json: lane {label} field 'evidence' must be an array")

        handoff = lane.get("handoff")
        if handoff is not None and (not isinstance(handoff, str) or not handoff):
            errors.append(f"lane-map.json: lane {label} field 'handoff' must be null or a non-empty string")

        replacement = lane.get("replacement")
        if replacement is not None and (not isinstance(replacement, str) or not replacement):
            errors.append(f"lane-map.json: lane {label} field 'replacement' must be null or a non-empty string")

        if critical is True and status in SUCCESSFUL_LANE_STATUSES:
            if not isinstance(handoff, str) or not handoff:
                errors.append(f"lane-map.json: lane {label} successful critical lane requires handoff")
            elif not resolve_run_path(run_dir, handoff).exists():
                errors.append(f"lane-map.json: lane {label} handoff not found: {handoff}")
            evidence_paths = validate_lane_path_list(run_dir, evidence, label, "evidence", errors)
            if not evidence_paths:
                errors.append(f"lane-map.json: lane {label} successful critical lane requires evidence")

        if (
            execution_mode == "subagent"
            and role
            and status not in {"planned", "timed-out", "replaced"}
        ):
            errors.extend(
                validate_subagent_lane_trace(
                    run_dir,
                    lane_id,
                    role,
                    lane_status=status,
                    handoff=handoff if isinstance(handoff, str) else None,
                )
            )

        if lane_type == "architecture":
            architecture_lane_ids.append(lane_id)
            if critical is True:
                critical_architecture_lane_ids.append(lane_id)
            if status in SUCCESSFUL_LANE_STATUSES:
                successful_architecture_lane_ids.append(lane_id)
                if isinstance(wave, int) and not isinstance(wave, bool):
                    successful_architecture_waves.append(wave)
                if architecture_contract_required and critical is True:
                    if "architecture_design_brief" not in lane:
                        errors.append(
                            f"lane-map.json: lane {label} missing architecture_design_brief"
                        )
                    else:
                        design_brief = lane.get("architecture_design_brief")
                        if not isinstance(design_brief, str) or not design_brief:
                            errors.append(
                                f"lane-map.json: lane {label} "
                                "architecture_design_brief must be a non-empty string"
                            )
                        else:
                            design_brief_path = resolve_run_path(run_dir, design_brief)
                            if not design_brief_path.exists():
                                errors.append(
                                    f"lane-map.json: lane {label} "
                                    f"architecture_design_brief not found: {design_brief}"
                                )
                            else:
                                decision_status, design_errors = validate_architecture_design_brief(
                                    design_brief_path,
                                    architecture_context_facets,
                                    architecture_capabilities,
                                )
                                for design_error in design_errors:
                                    errors.append(
                                        f"lane-map.json: lane {label} {design_error}"
                                    )
                                if not design_errors:
                                    if decision_status == "approved":
                                        if isinstance(wave, int) and not isinstance(wave, bool):
                                            approved_architecture_design_waves.append(wave)
                                    elif decision_status in ARCHITECTURE_DESIGN_DECISION_STATUSES:
                                        not_approved_architecture_design_lane_ids.append(lane_id)
                if critical is True and isinstance(handoff, str) and handoff:
                    contract_path = resolve_run_path(run_dir, handoff)
                    for contract_error in validate_architecture_contract_handoff(contract_path):
                        errors.append(f"lane-map.json: lane {label} {contract_error}")
                    if architecture_contract_required:
                        if claim_evidence_candidate:
                            contract_claim_ids, contract_claim_errors = claim_evidence_ids_from_contract(
                                contract_path
                            )
                            required_claim_ids.extend(contract_claim_ids)
                            for contract_claim_error in contract_claim_errors:
                                claim_evidence_contract_errors.append(
                                    f"lane-map.json: lane {label} {contract_claim_error}"
                                )
                        for facet_error in validate_selected_architecture_facets(
                            contract_path,
                            architecture_context_facets,
                        ):
                            errors.append(f"lane-map.json: lane {label} {facet_error}")
                        for capability_error in validate_selected_architecture_capabilities(
                            contract_path,
                            architecture_capabilities,
                        ):
                            errors.append(f"lane-map.json: lane {label} {capability_error}")
        elif lane_type == "review" and status in SUCCESSFUL_LANE_STATUSES:
            successful_reviewer_lane_ids.append(lane_id)
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_reviewer_lanes.append((lane_id, wave, handoff))
        elif (
            lane_type == "qa"
            and status in SUCCESSFUL_LANE_STATUSES
            and lane_id not in verification_readiness_lane_ids
        ):
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_qa_lanes.append((lane_id, wave, handoff))
        elif lane_type in WORKER_LANE_TYPES and status in SUCCESSFUL_LANE_STATUSES:
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_worker_lanes.append((lane_id, wave, handoff))
                worker_wave: int | None = wave
            else:
                worker_wave = None

            if architecture_contract_required:
                compliance, compliance_errors = validate_architecture_compliance_shape(
                    lane,
                    label,
                    selected_matrix_facets=set(architecture_context_facets),
                    known_matrix_facets=known_matrix_facets,
                    selected_capabilities=architecture_capabilities,
                )
                errors.extend(compliance_errors)
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_COMPLIANCE_SECTION, ENGINEERING_SIMPLICITY_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {label} handoff missing section: {section}")
                    if compliance and isinstance(compliance.get("matrix_facets"), list):
                        worker_matrix_facets = [
                            facet
                            for facet in compliance.get("matrix_facets", [])
                            if isinstance(facet, str) and facet
                        ]
                        for facet in missing_facets_in_markdown_sections(
                            handoff_path,
                            [ARCHITECTURE_COMPLIANCE_SECTION],
                            worker_matrix_facets,
                        ):
                            errors.append(
                                f"lane-map.json: lane {label} "
                                "Architecture Compliance missing "
                                f"Architecture Matrix facet: {facet}"
                            )
                    for check in missing_facets_in_markdown_sections(
                        handoff_path,
                        [ENGINEERING_SIMPLICITY_SECTION],
                        ENGINEERING_SIMPLICITY_REQUIRED_CHECKS,
                    ):
                        errors.append(
                            f"lane-map.json: lane {label} "
                            "Engineering Simplicity missing check: "
                            f"{check}"
                        )
                    if compliance:
                        simplicity = compliance.get("engineering_simplicity")
                        if (
                            simplicity_scope_required
                            and isinstance(simplicity, dict)
                            and simplicity_primary_surfaces
                        ):
                            (
                                worker_primary_surfaces,
                                worker_secondary_surfaces,
                                coverage_errors,
                            ) = validate_engineering_simplicity_scope_coverage(
                                run_dir,
                                simplicity=simplicity,
                                label=label,
                                declared_primary_surfaces=simplicity_declared_primary_surfaces,
                                declared_secondary_surfaces=simplicity_declared_secondary_surfaces,
                            )
                            errors.extend(coverage_errors)
                            covered_primary_surfaces.update(worker_primary_surfaces)
                            covered_surfaces = [
                                *worker_primary_surfaces,
                                *worker_secondary_surfaces,
                            ]
                            for surface in missing_facets_in_markdown_sections(
                                handoff_path,
                                [ENGINEERING_SIMPLICITY_SECTION],
                                covered_surfaces,
                            ):
                                errors.append(
                                    f"lane-map.json: lane {label} "
                                    "Engineering Simplicity missing scope surface: "
                                    f"{surface}"
                                )
                        if isinstance(simplicity, dict) and simplicity.get("status") == "fixed":
                            handoff_text = (
                                handoff_path.read_text(encoding="utf-8")
                                if handoff_path.exists()
                                else ""
                            )
                            simplicity_text = markdown_section_text(
                                handoff_text,
                                ENGINEERING_SIMPLICITY_SECTION,
                            )
                            actions = simplicity.get("actions")
                            if isinstance(actions, list):
                                for action in actions:
                                    if (
                                        isinstance(action, str)
                                        and action.strip()
                                        and action not in simplicity_text
                                    ):
                                        errors.append(
                                            f"lane-map.json: lane {label} "
                                            "Engineering Simplicity missing action: "
                                            f"{action}"
                                        )
                else:
                    errors.append(
                        f"lane-map.json: lane {label} "
                        "successful worker lane requires handoff for architecture compliance"
                    )
                if compliance and compliance.get("status") == "drift":
                    recheck_lane = compliance.get("recheck_lane")
                    drifting_worker_lanes.append(
                        (
                            lane_id,
                            worker_wave,
                            recheck_lane if isinstance(recheck_lane, str) and recheck_lane else None,
                        )
                    )
                    simplicity = compliance.get("engineering_simplicity")
                    if isinstance(simplicity, dict) and simplicity.get("status") == "drift":
                        simplicity_drifting_worker_lanes.append(
                            (
                                lane_id,
                                worker_wave,
                                recheck_lane
                                if isinstance(recheck_lane, str) and recheck_lane
                                else None,
                            )
                        )

    if simplicity_scope_required and not allow_pending:
        for surface in simplicity_primary_surfaces:
            if surface not in covered_primary_surfaces:
                errors.append(
                    "lane-map.json: engineering_simplicity_scope primary surface "
                    f"not covered by worker: {surface}"
                )

    for lane_id, _worker_wave, recheck_lane_id in simplicity_drifting_worker_lanes:
        if not recheck_lane_id:
            errors.append(
                f"lane-map.json: lane {lane_id} "
                "engineering_simplicity drift requires recheck_lane"
            )
            continue
        if recheck_lane_id not in lane_by_id:
            errors.append(
                f"lane-map.json: lane {lane_id} "
                f"engineering_simplicity recheck_lane not found: {recheck_lane_id}"
            )

    for lane_id, lane in lane_by_id.items():
        if lane.get("critical") is not True:
            continue
        status = normalized_status_by_id.get(lane_id)
        if status != "timed-out":
            continue

        replacement = lane.get("replacement")
        if not isinstance(replacement, str) or not replacement:
            errors.append(f"lane-map.json: lane {lane_id} timed-out lane requires replacement")
            continue
        if replacement not in lane_by_id:
            errors.append(f"lane-map.json: lane {lane_id} replacement target not found: {replacement}")
            continue
        replacement_status = normalized_status_by_id.get(replacement)
        if replacement_status not in SUCCESSFUL_LANE_STATUSES:
            errors.append(
                f"lane-map.json: lane {lane_id} replacement {replacement} "
                f"must be pass or pass-with-risks"
            )

    required_claim_ids = list(dict.fromkeys(required_claim_ids))
    claim_evidence_required = (
        claim_evidence_candidate
        and bool(successful_qa_lanes)
        and bool(successful_reviewer_lanes)
    )
    if claim_evidence_required:
        errors.extend(claim_evidence_contract_errors)
    else:
        required_claim_ids = []
    if not allow_pending:
        errors.extend(
            validate_claim_evidence_records(
                run_dir,
                required_claim_ids=required_claim_ids,
                required=claim_evidence_required,
                final_verdict=final_verdict,
                lane_by_id=lane_by_id,
                normalized_status_by_id=normalized_status_by_id,
            )
        )

    delegation_summary_required = (
        schema_version == 2 and final_verdict in POSITIVE_FINAL_VERDICTS
    )
    _delegation_summary, delegation_summary_errors = validate_delegation_summary(
        run_dir,
        [lane for lane in lanes if isinstance(lane, dict)],
        lane_by_id,
        final_verdict,
        required=delegation_summary_required,
    )
    errors.extend(delegation_summary_errors)

    if schema_version == 2:
        if budget == "standard" and worker_lane_count >= 2 and architecture_contract_required is not True:
            errors.append("lane-map.json: standard budget with 2 worker lanes requires architecture_contract_required=true")
        if budget == "release" and architecture_contract_required is not True:
            errors.append("lane-map.json: release budget requires architecture_contract_required=true")

    verification_readiness_data: dict | None = None
    verification_readiness_required = (
        schema_version == 2
        and architecture_contract_required
        and worker_lane_count > 0
    )
    verification_readiness_data, readiness_errors = validate_verification_readiness(
        run_dir,
        data,
        required=verification_readiness_required,
        architecture_context_by_axis=architecture_context_by_axis,
        lane_by_id=lane_by_id,
        normalized_status_by_id=normalized_status_by_id,
        final_verdict=final_verdict,
    )
    errors.extend(readiness_errors)

    ready_readiness_waves: list[int] = []
    if isinstance(verification_readiness_data, dict):
        ready_attempt_lanes = [
            attempt.get("lane")
            for attempt in verification_readiness_data.get("attempts", [])
            if isinstance(attempt, dict) and attempt.get("status") == "ready"
        ]
        for readiness_lane_id in ready_attempt_lanes:
            if not isinstance(readiness_lane_id, str):
                continue
            readiness_lane = lane_by_id.get(readiness_lane_id)
            if not readiness_lane:
                continue
            readiness_wave = readiness_lane.get("wave")
            if isinstance(readiness_wave, int) and not isinstance(readiness_wave, bool):
                ready_readiness_waves.append(readiness_wave)

    errors.extend(
        validate_continuation_summary(
            run_dir,
            final_verdict=final_verdict,
            lane_by_id=lane_by_id,
            normalized_status_by_id=normalized_status_by_id,
            successful_worker_lanes=successful_worker_lanes,
            verification_readiness_data=verification_readiness_data,
        )
    )
    if not allow_pending:
        learning_triggers = detect_harness_learning_triggers(
            run_dir,
            final_verdict=final_verdict,
            architecture_contract_required=architecture_contract_required,
            resolution_records=resolution_records,
            drifting_worker_lanes=drifting_worker_lanes,
            verification_readiness_data=verification_readiness_data,
        )
        errors.extend(
            validate_harness_evaluation(
                run_dir,
                final_verdict=final_verdict,
                learning_triggers=learning_triggers,
                architecture_context_facets=architecture_context_facets,
                architecture_capabilities=architecture_capabilities,
                known_architecture_capability_ids=known_architecture_capability_ids,
                successful_reviewer_lanes=successful_reviewer_lanes,
                require_reviewer_review=True,
            )
        )

    if architecture_contract_required:
        if final_verdict in POSITIVE_FINAL_VERDICTS:
            errors.extend(validate_no_agent_placeholders(run_dir, lanes))

        if not architecture_lane_ids:
            errors.append(
                "lane-map.json: architecture_contract_required requires a critical architecture lane"
            )
        elif not critical_architecture_lane_ids:
            errors.append("lane-map.json: architecture lane must be critical")

        if final_verdict == "ship":
            blocked_architecture_lane_ids = [
                lane_id
                for lane_id in critical_architecture_lane_ids
                if normalized_status_by_id.get(lane_id) in {"fail", "blocked", "timed-out"}
            ]
            if blocked_architecture_lane_ids or not successful_architecture_lane_ids:
                errors.append("lane-map.json: architecture lane must pass before ship")

        if (
            final_verdict in POSITIVE_FINAL_VERDICTS
            and (
                not approved_architecture_design_waves
                or not_approved_architecture_design_lane_ids
            )
        ):
            errors.append(
                "lane-map.json: positive final Verdict requires approved "
                "Architecture Design Brief"
            )

        if architecture_contract_independent:
            for lane_id in successful_architecture_lane_ids:
                lane = lane_by_id[lane_id]
                if normalize_execution_mode(lane.get("execution_mode")) != "subagent":
                    errors.append(
                        "lane-map.json: independent architecture contract requires subagent execution"
                    )

        if successful_reviewer_lane_ids and not successful_architecture_lane_ids:
            errors.append("lane-map.json: reviewer lane requires successful architecture contract")

        if successful_qa_lanes:
            if not successful_architecture_waves:
                errors.append("lane-map.json: qa lane must run after architecture lane")
            else:
                architecture_wave = max(successful_architecture_waves)
                for lane_id, wave, _handoff in successful_qa_lanes:
                    if wave <= architecture_wave:
                        errors.append("lane-map.json: qa lane must run after architecture lane")
                        break
            if successful_worker_lanes:
                worker_wave = max(wave for _lane_id, wave, _handoff in successful_worker_lanes)
                for lane_id, wave, _handoff in successful_qa_lanes:
                    if wave <= worker_wave:
                        errors.append("lane-map.json: qa lane must run after worker lanes")
                        break

        if successful_worker_lanes:
            readiness_status = (
                verification_readiness_data.get("status")
                if isinstance(verification_readiness_data, dict)
                else None
            )
            if (
                (verification_readiness_required or verification_readiness_data is not None)
                and readiness_status != "ready"
            ):
                errors.append("lane-map.json: worker lanes require ready Verification Readiness Gate")
            if ready_readiness_waves:
                latest_ready_readiness_wave = max(ready_readiness_waves)
                for lane_id, wave, _handoff in successful_worker_lanes:
                    if wave <= latest_ready_readiness_wave:
                        errors.append(
                            f"lane-map.json: worker lane {lane_id} must run after "
                            "ready Verification Readiness Gate"
                        )
                        break
            elif verification_readiness_required:
                errors.append("lane-map.json: worker lanes require ready Verification Readiness Gate")

            for lane_id, wave, _handoff in successful_worker_lanes:
                if not any(design_wave < wave for design_wave in approved_architecture_design_waves):
                    errors.append(
                        f"lane-map.json: lane {lane_id} worker lane must run after "
                        "approved Architecture Design Brief"
                    )
            qa_matrix_facets = [
                *architecture_context_by_axis.get("risk_gates", []),
                *architecture_context_by_axis.get("verification_gates", []),
            ]
            for lane_id, _wave, handoff in successful_qa_lanes:
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_INVARIANTS_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
                    if simplicity_scope_required:
                        for section in missing_markdown_headings(
                            handoff_path,
                            [ENGINEERING_SIMPLICITY_SCOPE_SECTION],
                        ):
                            errors.append(
                                f"lane-map.json: lane {lane_id} handoff missing section: {section}"
                            )
                        for surface in missing_facets_in_markdown_sections(
                            handoff_path,
                            [ENGINEERING_SIMPLICITY_SCOPE_SECTION],
                            simplicity_primary_surfaces,
                        ):
                            errors.append(
                                f"lane-map.json: lane {lane_id} "
                                "Engineering Simplicity Scope missing primary surface: "
                                f"{surface}"
                            )
                    for facet in missing_facets_in_markdown_sections(
                        handoff_path,
                        [ARCHITECTURE_INVARIANTS_SECTION],
                        qa_matrix_facets,
                    ):
                        errors.append(
                            f"lane-map.json: lane {lane_id} "
                            "Architecture Invariants missing "
                            f"Architecture Matrix facet: {facet}"
                        )

            selected_gate_pairs = selected_verification_gate_pairs(architecture_context_by_axis)
            for lane_id, lane in lane_by_id.items():
                if lane.get("type") != "qa" or lane_id in verification_readiness_lane_ids:
                    continue
                lane_status = normalized_status_by_id.get(lane_id)
                should_validate_results = (
                    "verification_results" in lane
                    or lane_status in SUCCESSFUL_LANE_STATUSES
                )
                if not should_validate_results:
                    continue
                results, result_errors = validate_qa_verification_results_shape(
                    run_dir,
                    lane,
                    lane_id,
                    selected_pairs=selected_gate_pairs,
                )
                errors.extend(result_errors)
                handoff = lane.get("handoff")
                if not isinstance(handoff, str) or not handoff:
                    errors.append(
                        f"lane-map.json: lane {lane_id} requires handoff for "
                        "Verification Gate Results"
                    )
                else:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [VERIFICATION_GATE_RESULTS_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
                if lane_status in SUCCESSFUL_LANE_STATUSES and (
                    not results or results.get("status") != "pass"
                ):
                    errors.append(
                        f"lane-map.json: lane {lane_id} pass requires "
                        "verification_results.status=pass"
                    )

            for lane_id, _wave, handoff in successful_reviewer_lanes:
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
                    for facet in missing_facets_in_markdown_sections(
                        handoff_path,
                        [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
                        architecture_context_facets,
                    ):
                        errors.append(
                            f"lane-map.json: lane {lane_id} reviewer handoff "
                            f"missing Architecture Matrix facet: {facet}"
                        )
                    for capability in missing_capabilities_in_markdown_sections(
                        handoff_path,
                        [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
                        architecture_capabilities,
                    ):
                        errors.append(
                            f"lane-map.json: lane {lane_id} reviewer handoff "
                            f"missing architecture capability: {capability}"
                        )
                    if handoff_path.exists():
                        handoff_text = handoff_path.read_text(encoding="utf-8")
                        contract_drift_text = markdown_section_text(
                            handoff_text,
                            CONTRACT_DRIFT_SECTION,
                        )
                        if not contains_facet_id(contract_drift_text, ENGINEERING_SIMPLICITY_SECTION):
                            errors.append(
                                f"lane-map.json: lane {lane_id} Contract Drift "
                                "missing Engineering Simplicity"
                            )
                        for worker_lane_id in fixed_simplicity_worker_lane_ids:
                            if not contains_facet_id(contract_drift_text, worker_lane_id):
                                errors.append(
                                    f"lane-map.json: lane {lane_id} Contract Drift "
                                    "missing fixed Engineering Simplicity lane: "
                                    f"{worker_lane_id}"
                                )
                        if simplicity_scope_required:
                            for surface in simplicity_primary_surfaces:
                                if not contains_facet_id(contract_drift_text, surface):
                                    errors.append(
                                        f"lane-map.json: lane {lane_id} Contract Drift "
                                        "missing Engineering Simplicity primary surface: "
                                        f"{surface}"
                                    )
                            if not contains_facet_id(
                                contract_drift_text,
                                "peripheral-only closure",
                            ):
                                errors.append(
                                    f"lane-map.json: lane {lane_id} Contract Drift "
                                    "must reject peripheral-only closure"
                                )

        if successful_reviewer_lanes:
            if successful_architecture_waves:
                architecture_wave = max(successful_architecture_waves)
                for lane_id, wave, _handoff in successful_reviewer_lanes:
                    if wave <= architecture_wave:
                        errors.append("lane-map.json: reviewer lane must run after architecture lane")
                        break
            if successful_worker_lanes:
                worker_wave = max(wave for _lane_id, wave, _handoff in successful_worker_lanes)
                for lane_id, wave, _handoff in successful_reviewer_lanes:
                    if wave <= worker_wave:
                        errors.append("lane-map.json: reviewer lane must run after worker lanes")
                        break
            if successful_qa_lanes:
                qa_wave = max(wave for _lane_id, wave, _handoff in successful_qa_lanes)
                for lane_id, wave, _handoff in successful_reviewer_lanes:
                    if wave <= qa_wave:
                        errors.append("lane-map.json: reviewer lane must run after qa lane")
                        break

        if final_verdict in POSITIVE_FINAL_VERDICTS:
            readiness_status = (
                verification_readiness_data.get("status")
                if isinstance(verification_readiness_data, dict)
                else None
            )
            if (
                (verification_readiness_required or verification_readiness_data is not None)
                and readiness_status != "ready"
            ):
                errors.append(
                    "lane-map.json: positive final Verdict requires ready "
                    "Verification Readiness Gate"
                )
            if simplicity_scope_required:
                final_path = run_dir / "final.md"
                final_text = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
                if not has_markdown_heading(final_text, ENGINEERING_SIMPLICITY_SECTION):
                    errors.append(
                        "final.md missing section: Engineering Simplicity"
                    )
                else:
                    simplicity_final_text = markdown_section_text(
                        final_text,
                        ENGINEERING_SIMPLICITY_SECTION,
                    )
                    for surface in simplicity_primary_surfaces:
                        if not contains_facet_id(simplicity_final_text, surface):
                            errors.append(
                                "final.md Engineering Simplicity missing "
                                f"primary surface: {surface}"
                            )

        if final_verdict == "ship" and successful_worker_lanes:
            if not successful_qa_lanes:
                errors.append("lane-map.json: final Verdict ship requires successful qa lane")
            if not successful_reviewer_lanes:
                errors.append("lane-map.json: final Verdict ship requires successful reviewer lane")

        if final_verdict in {"ship", "pass-with-risks"} and successful_worker_lanes:
            for lane_id, worker_wave, recheck_lane_id in drifting_worker_lanes:
                if not recheck_lane_id:
                    errors.append(f"lane-map.json: lane {lane_id} architecture drift requires recheck_lane")
                    continue

                recheck_lane = lane_by_id.get(recheck_lane_id)
                if recheck_lane is None:
                    errors.append(f"lane-map.json: lane {lane_id} recheck_lane not found: {recheck_lane_id}")
                    continue
                if recheck_lane.get("type") != "architecture":
                    errors.append(
                        f"lane-map.json: lane {lane_id} "
                        "recheck_lane must reference an architecture lane"
                    )
                    continue
                if recheck_lane.get("critical") is not True:
                    errors.append(f"lane-map.json: lane {lane_id} recheck_lane must be critical")
                recheck_status = normalized_status_by_id.get(recheck_lane_id)
                if recheck_status not in SUCCESSFUL_LANE_STATUSES:
                    errors.append(f"lane-map.json: lane {lane_id} recheck_lane must pass")
                recheck_wave = recheck_lane.get("wave")
                if (
                    worker_wave is not None
                    and isinstance(recheck_wave, int)
                    and not isinstance(recheck_wave, bool)
                    and recheck_wave <= worker_wave
                ):
                    errors.append(
                        f"lane-map.json: lane {lane_id} "
                        "recheck_lane must run after drifting worker lane"
                    )

    if resolution_records:
        errors.extend(
            validate_risk_resolution_lane_references(
                resolution_records,
                lane_by_id,
                normalized_status_by_id,
            )
        )
        errors.extend(
            validate_blocked_resolution_recovery_lanes(
                run_dir,
                resolution_records,
                lane_by_id,
                normalized_status_by_id,
            )
        )

    if final_verdict == "pass-with-risks":
        errors.extend(validate_risk_mitigation_lane_references(mitigation_risks, lane_ids))

        if not successful_reviewer_lanes:
            errors.append(
                "lane-map.json: Verdict pass-with-risks requires successful "
                "reviewer lane for Mitigation Gate"
            )
        for lane_id, _wave, handoff in successful_reviewer_lanes:
            if not isinstance(handoff, str) or not handoff:
                errors.append(
                    f"lane-map.json: lane {lane_id} successful reviewer lane "
                    "requires handoff for Mitigation Gate"
                )
                continue
            handoff_path = resolve_run_path(run_dir, handoff)
            for section in missing_markdown_headings(
                handoff_path,
                [RISK_MITIGATION_REVIEW_SECTION],
            ):
                errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
            review_text = markdown_section_text(
                handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else "",
                RISK_MITIGATION_REVIEW_SECTION,
            )
            for risk_id_value in mitigation_risk_ids:
                if not contains_facet_id(review_text, risk_id_value):
                    errors.append(
                        f"lane-map.json: lane {lane_id} reviewer handoff "
                        "Risk Mitigation Review missing risk id: "
                        f"{risk_id_value}"
                    )

        if not successful_qa_lanes:
            errors.append(
                "lane-map.json: Verdict pass-with-risks requires successful "
                "qa lane for Resolution Gate"
            )
        if not successful_reviewer_lanes:
            errors.append(
                "lane-map.json: Verdict pass-with-risks requires successful "
                "reviewer lane for Resolution Gate"
            )

        resolution_qa_ids = {
            value
            for resolution in resolution_records
            for value in [resolution.get("verified_by")]
            if isinstance(value, str) and value
        }
        resolution_reviewer_ids = {
            value
            for resolution in resolution_records
            for value in [resolution.get("reviewed_by")]
            if isinstance(value, str) and value
        }

        for lane_id in sorted(resolution_qa_ids):
            lane = lane_by_id.get(lane_id)
            handoff = lane.get("handoff") if isinstance(lane, dict) else None
            if not isinstance(handoff, str) or not handoff:
                errors.append(
                    f"lane-map.json: lane {lane_id} successful qa lane "
                    "requires handoff for Resolution Gate"
                )
                continue
            handoff_path = resolve_run_path(run_dir, handoff)
            for section in missing_markdown_headings(
                handoff_path,
                [RISK_RESOLUTION_VERIFICATION_SECTION],
            ):
                errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
            verification_text = markdown_section_text(
                handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else "",
                RISK_RESOLUTION_VERIFICATION_SECTION,
            )
            for risk_id_value in resolution_risk_ids:
                if not contains_facet_id(verification_text, risk_id_value):
                    errors.append(
                        f"lane-map.json: lane {lane_id} QA handoff "
                        "Risk Resolution Verification missing risk id: "
                        f"{risk_id_value}"
                    )

        for lane_id in sorted(resolution_reviewer_ids):
            lane = lane_by_id.get(lane_id)
            handoff = lane.get("handoff") if isinstance(lane, dict) else None
            if not isinstance(handoff, str) or not handoff:
                errors.append(
                    f"lane-map.json: lane {lane_id} successful reviewer lane "
                    "requires handoff for Resolution Gate"
                )
                continue
            handoff_path = resolve_run_path(run_dir, handoff)
            for section in missing_markdown_headings(
                handoff_path,
                [RISK_RESOLUTION_REVIEW_SECTION],
            ):
                errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")
            review_text = markdown_section_text(
                handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else "",
                RISK_RESOLUTION_REVIEW_SECTION,
            )
            for risk_id_value in resolution_risk_ids:
                if not contains_facet_id(review_text, risk_id_value):
                    errors.append(
                        f"lane-map.json: lane {lane_id} reviewer handoff "
                        "Risk Resolution Review missing risk id: "
                        f"{risk_id_value}"
                    )

    if final_verdict == "ship":
        for lane_id, lane in lane_by_id.items():
            if lane.get("critical") is not True:
                continue
            status = normalized_status_by_id.get(lane_id)
            if status in SUCCESSFUL_LANE_STATUSES:
                continue
            if status in {"timed-out", "replaced"}:
                replacement = lane.get("replacement")
                replacement_status = normalized_status_by_id.get(replacement) if isinstance(replacement, str) else None
                if replacement_status in SUCCESSFUL_LANE_STATUSES:
                    continue
            blocked_status = status or lane.get("status")
            errors.append(
                f"lane-map.json: final Verdict ship is blocked by critical lane "
                f"{lane_id} status {blocked_status}"
            )

    return errors


def validate_verdict(path: Path) -> list[str]:
    errors: list[str] = []
    verdicts = read_fields(path, "Verdict")
    if not verdicts:
        errors.append(f"{path.name}: missing Verdict field")
    elif len(verdicts) > 1:
        errors.append(f"{path.name}: multiple Verdict fields")
    elif verdicts[0] not in FINAL_VERDICTS:
        verdict = verdicts[0]
        allowed = ", ".join(sorted(FINAL_VERDICTS))
        errors.append(f"{path.name}: invalid Verdict '{verdict}' (expected one of: {allowed})")
    return errors


def validate_compact_run(run_dir: Path, allow_no_check: bool, allow_pending: bool) -> list[str]:
    errors: list[str] = []

    for name in COMPACT_REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")
            continue
        if not path.is_file():
            errors.append(f"{name} exists but is not a file")
            continue
        if is_empty_file(path) and not (name == "checks.md" and allow_no_check):
            errors.append(f"{name}: empty file")

    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.exists() and not artifacts_dir.is_dir():
        errors.append("artifacts exists but is not a directory")

    final_path = run_dir / "final.md"
    final_verdict = read_single_field(final_path, "Verdict") if not allow_pending else None
    if not allow_pending and final_path.exists() and final_path.is_file():
        errors.extend(validate_verdict(final_path))
        mitigation_risks, mitigation_errors = validate_risk_mitigations(run_dir, final_verdict)
        errors.extend(mitigation_errors)
        resolution_records, resolution_errors = validate_risk_resolutions(
            run_dir,
            final_verdict,
            mitigation_risks,
        )
        errors.extend(resolution_errors)
        if final_verdict == "pass-with-risks":
            errors.extend(
                validate_final_risk_mitigation_coverage(
                    final_path,
                    risk_mitigation_ids(mitigation_risks),
                )
            )
            errors.extend(
                validate_final_risk_resolution_coverage(
                    final_path,
                    risk_resolution_ids(resolution_records),
                )
            )
        elif final_verdict in {"blocked", "fail"} and resolution_records:
            errors.extend(
                validate_final_risk_resolution_coverage(
                    final_path,
                    risk_resolution_ids(resolution_records),
                )
            )
    else:
        mitigation_risks = []
        resolution_records = []

    has_timeline = (run_dir / "timeline.jsonl").exists()
    has_agents = (run_dir / "agents").exists()
    if has_timeline or has_agents:
        errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))
        errors.extend(validate_timeline_sequence(run_dir))
        errors.extend(validate_agent_traces(run_dir))
        if not allow_pending:
            errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))

    errors.extend(
        validate_lane_map(
            run_dir,
            mitigation_risks,
            resolution_records,
            allow_pending=allow_pending,
        )
    )

    return errors


def validate_full_run(
    run_dir: Path,
    require_handoff: bool,
    allow_no_check: bool,
    allow_pending: bool,
) -> list[str]:
    errors: list[str] = []

    for name in FULL_REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")

    for name in FULL_REQUIRED_DIRS:
        path = run_dir / name
        if not path.is_dir():
            errors.append(f"missing dir: {name}")

    errors.extend(validate_artifacts_index(run_dir / "artifacts.json"))

    errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))
    errors.extend(validate_timeline_sequence(run_dir))
    errors.extend(validate_agent_traces(run_dir))
    if not allow_pending:
        errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))
        final_verdict = read_single_field(run_dir / "final.md", "Verdict")
        mitigation_risks, mitigation_errors = validate_risk_mitigations(run_dir, final_verdict)
        errors.extend(mitigation_errors)
        resolution_records, resolution_errors = validate_risk_resolutions(
            run_dir,
            final_verdict,
            mitigation_risks,
        )
        errors.extend(resolution_errors)
        if final_verdict == "pass-with-risks":
            errors.extend(
                validate_final_risk_mitigation_coverage(
                    run_dir / "final.md",
                    risk_mitigation_ids(mitigation_risks),
                )
            )
            errors.extend(
                validate_final_risk_resolution_coverage(
                    run_dir / "final.md",
                    risk_resolution_ids(resolution_records),
                )
            )
        elif final_verdict in {"blocked", "fail"} and resolution_records:
            errors.extend(
                validate_final_risk_resolution_coverage(
                    run_dir / "final.md",
                    risk_resolution_ids(resolution_records),
                )
            )
    else:
        mitigation_risks = []
        resolution_records = []

    errors.extend(
        validate_lane_map(
            run_dir,
            mitigation_risks,
            resolution_records,
            allow_pending=allow_pending,
        )
    )
    if not (run_dir / "lane-map.json").exists() and not allow_pending:
        learning_triggers = detect_harness_learning_triggers(
            run_dir,
            final_verdict=read_single_field(run_dir / "final.md", "Verdict"),
            architecture_contract_required=False,
            resolution_records=resolution_records,
            drifting_worker_lanes=[],
            verification_readiness_data=None,
        )
        errors.extend(
            validate_harness_evaluation(
                run_dir,
                final_verdict=read_single_field(run_dir / "final.md", "Verdict"),
                learning_triggers=learning_triggers,
                architecture_context_facets=[],
                architecture_capabilities=[],
                known_architecture_capability_ids=set(),
                successful_reviewer_lanes=[],
                require_reviewer_review=False,
            )
        )

    if require_handoff and not list((run_dir / "handoffs").glob("*.md")):
        errors.append("no handoff markdown files")

    if not allow_no_check and not list((run_dir / "checks").glob("*.md")):
        errors.append("no check markdown files")

    if not allow_pending:
        for name in ["manifest.md", "final.md"]:
            path = run_dir / name
            if not path.exists():
                continue
            errors.extend(validate_verdict(path))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--mode", choices=["auto", "compact", "full"], default="auto")
    parser.add_argument("--require-handoff", action="store_true")
    parser.add_argument("--allow-no-check", action="store_true")
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()

    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    mode = detect_mode(run_dir, args.mode)
    if mode == "compact":
        errors = validate_compact_run(run_dir, args.allow_no_check, args.allow_pending)
    else:
        errors = validate_full_run(
            run_dir,
            args.require_handoff,
            args.allow_no_check,
            args.allow_pending,
        )

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1

    print(f"PASS {run_dir} ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
