#!/usr/bin/env python3
"""Validate Agent Flow traceable run completeness before final handoff."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


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
AGENT_EXECUTION_MODES = {"subagent", "role-lane"}
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
COMMIT_HASH_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
COMMIT_DECLARATION_PATTERN = re.compile(
    r"(?i)(^\s*(?:[-*]\s*)?(?:product\s+)?commit\s*:|committed\s+as)"
)
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
ARCHITECTURE_CONTRACT_SECTION_SET = set(ARCHITECTURE_CONTRACT_SECTIONS)
ARCHITECTURE_COMPLIANCE_STATUSES = {"compliant", "drift"}
ARCHITECTURE_COMPLIANCE_SECTION = "Architecture Compliance"
ARCHITECTURE_INVARIANTS_SECTION = "Architecture Invariants"
ARCHITECTURE_MATRIX_MISMATCHES_SECTION = "Architecture Matrix Mismatches"
CONTRACT_DRIFT_SECTION = "Contract Drift"
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


def load_lane_trace_events(run_dir: Path, role: str, lane_id: str) -> tuple[list[dict], list[str]]:
    trace_path = run_dir / "agents" / role / "trace.jsonl"
    if not trace_path.exists():
        return [], [f"lane-map.json: lane {lane_id} missing trace file: agents/{role}/trace.jsonl"]

    events, load_errors = load_jsonl_events(trace_path)
    if load_errors:
        return [], [f"lane-map.json: lane {lane_id} trace load error: {error}" for error in load_errors]

    return [event for event in events if event.get("lane_id") == lane_id], []


def validate_subagent_lane_trace(run_dir: Path, lane_id: str, role: str) -> list[str]:
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
    return []


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
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    raw_context = data.get("architecture_context")
    if raw_context is None:
        if required:
            errors.append("lane-map.json field 'architecture_context' is required")
        return [], errors
    if not isinstance(raw_context, dict):
        return [], ["lane-map.json field 'architecture_context' must be an object"]

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
            selected_facets.append(facet)

    if required and not selected_facets:
        errors.append("lane-map.json architecture_context must select at least one facet")
    return selected_facets, errors


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


def validate_architecture_compliance_shape(
    lane: dict,
    label: str,
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

    return compliance, errors


def validate_lane_map(run_dir: Path) -> list[str]:
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

    architecture_context_facets: list[str] = []
    if architecture_contract_required or "architecture_context" in data:
        matrix_facets, matrix_errors = load_architecture_matrix_facets()
        errors.extend(matrix_errors)
        architecture_context_facets, context_errors = validate_architecture_context_shape(
            data,
            matrix_facets,
            required=architecture_contract_required,
        )
        errors.extend(context_errors)

    lanes = data.get("lanes")
    if not isinstance(lanes, list):
        errors.append("lane-map.json field 'lanes' must be an array")
        return errors

    lane_ids: set[str] = set()
    lane_by_id: dict[str, dict] = {}
    normalized_status_by_id: dict[str, str] = {}
    architecture_lane_ids: list[str] = []
    critical_architecture_lane_ids: list[str] = []
    successful_architecture_lane_ids: list[str] = []
    successful_architecture_waves: list[int] = []
    successful_reviewer_lane_ids: list[str] = []
    successful_reviewer_lanes: list[tuple[str, int, str | None]] = []
    successful_qa_lanes: list[tuple[str, int, str | None]] = []
    successful_worker_lanes: list[tuple[str, int, str | None]] = []
    drifting_worker_lanes: list[tuple[str, int | None, str | None]] = []
    worker_lane_count = 0

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
            errors.extend(validate_subagent_lane_trace(run_dir, lane_id, role))

        if lane_type == "architecture":
            architecture_lane_ids.append(lane_id)
            if critical is True:
                critical_architecture_lane_ids.append(lane_id)
            if status in SUCCESSFUL_LANE_STATUSES:
                successful_architecture_lane_ids.append(lane_id)
                if isinstance(wave, int) and not isinstance(wave, bool):
                    successful_architecture_waves.append(wave)
                if critical is True and isinstance(handoff, str) and handoff:
                    contract_path = resolve_run_path(run_dir, handoff)
                    for contract_error in validate_architecture_contract_handoff(contract_path):
                        errors.append(f"lane-map.json: lane {label} {contract_error}")
                    if architecture_contract_required:
                        for facet_error in validate_selected_architecture_facets(
                            contract_path,
                            architecture_context_facets,
                        ):
                            errors.append(f"lane-map.json: lane {label} {facet_error}")
        elif lane_type == "review" and status in SUCCESSFUL_LANE_STATUSES:
            successful_reviewer_lane_ids.append(lane_id)
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_reviewer_lanes.append((lane_id, wave, handoff))
        elif lane_type == "qa" and status in SUCCESSFUL_LANE_STATUSES:
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_qa_lanes.append((lane_id, wave, handoff))
        elif lane_type in WORKER_LANE_TYPES and status in SUCCESSFUL_LANE_STATUSES:
            if isinstance(wave, int) and not isinstance(wave, bool):
                successful_worker_lanes.append((lane_id, wave, handoff))
                worker_wave: int | None = wave
            else:
                worker_wave = None

            if architecture_contract_required:
                compliance, compliance_errors = validate_architecture_compliance_shape(lane, label)
                errors.extend(compliance_errors)
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_COMPLIANCE_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {label} handoff missing section: {section}")
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

    final_verdict = read_single_field(run_dir / "final.md", "Verdict")

    if schema_version == 2:
        if budget == "standard" and worker_lane_count >= 2 and architecture_contract_required is not True:
            errors.append("lane-map.json: standard budget with 2 worker lanes requires architecture_contract_required=true")
        if budget == "release" and architecture_contract_required is not True:
            errors.append("lane-map.json: release budget requires architecture_contract_required=true")

    if architecture_contract_required:
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
            for lane_id, _wave, handoff in successful_qa_lanes:
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_INVARIANTS_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")

            for lane_id, _wave, handoff in successful_reviewer_lanes:
                if isinstance(handoff, str) and handoff:
                    handoff_path = resolve_run_path(run_dir, handoff)
                    for section in missing_markdown_headings(
                        handoff_path,
                        [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
                    ):
                        errors.append(f"lane-map.json: lane {lane_id} handoff missing section: {section}")

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

        if final_verdict == "ship" and successful_worker_lanes:
            if not successful_qa_lanes:
                errors.append("lane-map.json: final Verdict ship requires successful qa lane")
            if not successful_reviewer_lanes:
                errors.append("lane-map.json: final Verdict ship requires successful reviewer lane")
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
    if not allow_pending and final_path.exists() and final_path.is_file():
        errors.extend(validate_verdict(final_path))

    has_timeline = (run_dir / "timeline.jsonl").exists()
    has_agents = (run_dir / "agents").exists()
    if has_timeline or has_agents:
        errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))
        errors.extend(validate_timeline_sequence(run_dir))
        errors.extend(validate_agent_traces(run_dir))
        if not allow_pending:
            errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))

    errors.extend(validate_lane_map(run_dir))

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
    errors.extend(validate_lane_map(run_dir))
    if not allow_pending:
        errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))

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
