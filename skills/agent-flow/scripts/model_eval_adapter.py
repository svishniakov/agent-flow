#!/usr/bin/env python3
"""Exact Codex CLI adapter for Agent Flow model-evaluation lanes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from agent_config import ALLOWED_MODELS, ALLOWED_REASONING_EFFORTS
from model_eval_process import run_process_group
from model_eval_sandbox import (
    permission_profile_args,
    prepare_auth_only_codex_home,
    prepare_scratch,
    sanitized_process_environment,
    shell_policy_args,
)


CONTEXT_WARNING_MARKERS = (
    "skills context budget",
    "skill descriptions removed",
    "skills omitted",
)
TRANSIENT_PATTERNS = (
    re.compile(r"\b429\b"),
    re.compile(r"\b5\d\d\b"),
    re.compile(r"rate[ -]?limit", re.IGNORECASE),
    re.compile(r"service unavailable", re.IGNORECASE),
    re.compile(r"temporarily unavailable", re.IGNORECASE),
)
MODEL_UNAVAILABLE_PATTERNS = (
    re.compile(r"model[^\n]*(?:not found|unavailable|unsupported)", re.IGNORECASE),
    re.compile(r"unknown model", re.IGNORECASE),
)
MODEL_REROUTE_MESSAGE = re.compile(
    r"^model rerouted: ([A-Za-z0-9][A-Za-z0-9._:/-]*) -> "
    r"([A-Za-z0-9][A-Za-z0-9._:/-]*) \(([A-Za-z0-9_]+)\)$"
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(OPENAI_API_KEY|CODEX_API_KEY|API_KEY|ACCESS_TOKEN)=([^\s]+)"),
    re.compile(r'(?i)(["\']?(?:api_key|access_token|password|authorization)["\']?\s*[:=]\s*["\']?)([^"\'\s,}]+)'),
)
ALLOWED_AGENT_STATUS = {"pass", "fail", "blocked"}
ALLOWED_PREDICTABILITY_STATUS = {*ALLOWED_AGENT_STATUS, "decision_request"}
ALLOWED_CHECK_STATUS = {"pass", "fail", "not-run"}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
CODEX_CLI_VERSION = "0.144.1"
CODEX_MODEL_PROVIDER = "openai"
MAX_LANE_ATTEMPTS = 2
MAX_ROLLOUT_BYTES = 32 * 1024 * 1024
PINNED_MODEL_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "testdata"
    / "model-evals"
    / "codex-0.144.1-gpt-5.6-model-catalog.json"
)
PINNED_MODEL_CATALOG_SHA256 = "0633a84ff035a4484890f65145b38d93a72acddccf1d837b6c623cd42d4073d3"
CLIENT_MODEL_CATALOG = "model-catalog.json"
EVAL_ISOLATION_CONFIG_ARGS = (
    "-c",
    "features.multi_agent=false",
    "-c",
    (
        "features.multi_agent_v2={enabled=false,max_concurrent_threads_per_session=1,"
        'root_agent_usage_hint_text="",subagent_usage_hint_text=""}'
    ),
    "-c",
    "features.enable_fanout=false",
    "-c",
    "agents.max_threads=1",
)
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
PORTABLE_OPENAI_SCHEMA_KEYWORDS = {
    "$defs",
    "$ref",
    "$schema",
    "additionalProperties",
    "anyOf",
    "const",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maximum",
    "maxItems",
    "minimum",
    "minItems",
    "multipleOf",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
}


class AdapterError(RuntimeError):
    """Raised when an adapter input or trusted runtime contract is invalid."""


class SelectionEvidenceError(AdapterError):
    """Raised when Codex runtime selection evidence is missing or inconsistent."""

    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


@dataclass(frozen=True)
class AdapterConfig:
    lane_id: str
    role: str
    model: str
    reasoning_effort: str
    primary_workspace: Path
    additional_workspaces: tuple[Path, ...]
    output_schema: Path
    output_path: Path
    scratch_path: Path
    client_codex_home: Path
    auth_source: Path
    timeout_seconds: int
    selected_skills: tuple[str, ...] = ()
    active_gates: tuple[str, ...] = ()
    predictability_output: bool = False


@dataclass(frozen=True)
class AdapterRun:
    lane_result: dict[str, Any]
    stdout_jsonl: str
    stderr: str


@dataclass(frozen=True)
class ModelRerouteEvidence:
    from_model: str
    to_model: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "from_model": self.from_model,
            "to_model": self.to_model,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RuntimeSelectionEvidence:
    thread_id: str
    turn_id: str
    cli_version: str
    model_provider: str
    configured_model: str
    configured_reasoning: str
    selected_model: str
    selected_reasoning: str
    reroutes: tuple[ModelRerouteEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "codex-rollout",
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "cli_version": self.cli_version,
            "model_provider": self.model_provider,
            "configured_model": self.configured_model,
            "configured_reasoning": self.configured_reasoning,
            "selected_model": self.selected_model,
            "selected_reasoning": self.selected_reasoning,
            "reroutes": [reroute.to_dict() for reroute in self.reroutes],
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]
ContextProbe = Callable[[AdapterConfig, dict[str, str]], None]


def redact_text(value: str) -> str:
    """Remove common credentials without dumping the process environment."""
    redacted = value
    redacted = SECRET_PATTERNS[0].sub(r"\1[REDACTED]", redacted)
    redacted = SECRET_PATTERNS[1].sub("[REDACTED]", redacted)
    redacted = SECRET_PATTERNS[2].sub(r"\1=[REDACTED]", redacted)
    redacted = SECRET_PATTERNS[3].sub(r"\1[REDACTED]", redacted)
    return redacted


def _redact_json_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {"api_key", "access_token", "password", "authorization"} or normalized_key.endswith(
                ("_key", "_token", "_secret")
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_json_value(item)
        return redacted
    return value


def redact_jsonl(value: str) -> str:
    """Redact JSONL structurally so retained stdout remains parseable JSON."""
    redacted_lines: list[str] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {
                "type": "invalid_trace_line",
                "line_number": line_number,
                "message": "CLI stdout line was not valid JSON and was not retained.",
            }
        redacted_lines.append(
            json.dumps(_redact_json_value(record), ensure_ascii=False, separators=(",", ":"))
        )
    return "" if not redacted_lines else "\n".join(redacted_lines) + "\n"


def _validate_schema_node(value: object, location: str) -> None:
    if not isinstance(value, dict):
        raise AdapterError(f"output schema node {location} must be an object")
    unsupported = sorted(set(value) - PORTABLE_OPENAI_SCHEMA_KEYWORDS)
    if unsupported:
        raise AdapterError(
            f"output schema node {location} uses unsupported keywords: {', '.join(unsupported)}"
        )

    properties = value.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise AdapterError(f"output schema properties at {location} must be an object")
        required = value.get("required")
        if not isinstance(required, list) or set(required) != set(properties):
            raise AdapterError(f"output schema object at {location} must require every property")
        if value.get("additionalProperties") is not False:
            raise AdapterError(f"output schema object at {location} must forbid additional properties")
        for name, child in properties.items():
            _validate_schema_node(child, f"{location}.properties.{name}")

    definitions = value.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, dict):
            raise AdapterError(f"output schema definitions at {location} must be an object")
        for name, child in definitions.items():
            _validate_schema_node(child, f"{location}.$defs.{name}")

    items = value.get("items")
    if items is not None:
        _validate_schema_node(items, f"{location}.items")

    alternatives = value.get("anyOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise AdapterError(f"output schema anyOf at {location} must be a non-empty array")
        for index, child in enumerate(alternatives):
            _validate_schema_node(child, f"{location}.anyOf[{index}]")


def validate_output_schema(path: Path) -> None:
    """Reject schemas outside the portable Structured Outputs subset before a request."""
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError("output schema is not valid JSON") from exc
    _validate_schema_node(schema, "$")
    if schema.get("type") != "object" or "anyOf" in schema:
        raise AdapterError("output schema root must be an object without anyOf")


def _portable_changed_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AdapterError("changed_paths must contain portable relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise AdapterError("changed_paths must contain portable relative paths")
    return value


def _unique_non_empty_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise AdapterError(f"{label} must be an array")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise AdapterError(f"{label} item {index} must be non-empty")
        normalized.append(item.strip())
    if len(normalized) != len(set(normalized)):
        raise AdapterError(f"{label} must be unique")
    return normalized


def validate_agent_output(value: object, *, predictability: bool = False) -> dict[str, Any]:
    """Validate the untrusted model-authored part of a Lane Result."""
    if not isinstance(value, dict):
        raise AdapterError("agent output must be an object")
    required = {"status", "summary", "changed_paths", "checks", "handoff_path"}
    if predictability:
        required.update(
            {
                "decision_id",
                "missing_decision",
                "affected_requirements",
                "decision_evidence",
                "requirement_results",
            }
        )
    missing = sorted(required - value.keys())
    unknown = sorted(set(value) - required)
    if missing:
        raise AdapterError(f"agent output missing fields: {', '.join(missing)}")
    if unknown:
        raise AdapterError(f"agent output contains unknown fields: {', '.join(unknown)}")
    status = value["status"]
    allowed_statuses = ALLOWED_PREDICTABILITY_STATUS if predictability else ALLOWED_AGENT_STATUS
    if status not in allowed_statuses:
        raise AdapterError("agent output status is invalid")
    summary = value["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise AdapterError("agent output summary must be non-empty")
    changed_paths = value["changed_paths"]
    if not isinstance(changed_paths, list):
        raise AdapterError("agent output changed_paths must be an array")
    normalized_paths = [_portable_changed_path(path) for path in changed_paths]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise AdapterError("agent output changed_paths must be unique")
    checks = value["checks"]
    if not isinstance(checks, list):
        raise AdapterError("agent output checks must be an array")
    normalized_checks: list[dict[str, str]] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != {"command", "status", "evidence"}:
            raise AdapterError(f"agent output check {index} has invalid fields")
        command = check["command"]
        check_status = check["status"]
        evidence = check["evidence"]
        if not isinstance(command, str) or not command:
            raise AdapterError(f"agent output check {index} command must be non-empty")
        if check_status not in ALLOWED_CHECK_STATUS:
            raise AdapterError(f"agent output check {index} status is invalid")
        if not isinstance(evidence, str):
            raise AdapterError(f"agent output check {index} evidence must be a string")
        normalized_checks.append({"command": command, "status": check_status, "evidence": evidence})
    handoff_path = value["handoff_path"]
    if handoff_path is not None:
        handoff_path = _portable_changed_path(handoff_path)
    normalized = {
        "status": status,
        "summary": summary.strip(),
        "changed_paths": normalized_paths,
        "checks": normalized_checks,
        "handoff_path": handoff_path,
    }
    if not predictability:
        return normalized

    decision_id = value["decision_id"]
    missing_decision = value["missing_decision"]
    affected_requirements = _unique_non_empty_strings(
        value["affected_requirements"], "agent output affected_requirements"
    )
    decision_evidence = _unique_non_empty_strings(
        value["decision_evidence"], "agent output decision_evidence"
    )
    raw_requirement_results = value["requirement_results"]
    if not isinstance(raw_requirement_results, list):
        raise AdapterError("agent output requirement_results must be an array")
    requirement_results: list[dict[str, str]] = []
    requirement_ids: set[str] = set()
    for index, result in enumerate(raw_requirement_results):
        if not isinstance(result, dict) or set(result) != {"id", "status", "evidence"}:
            raise AdapterError(f"agent output requirement result {index} has invalid fields")
        requirement_id = result["id"]
        result_status = result["status"]
        evidence = result["evidence"]
        if not isinstance(requirement_id, str) or not ID_PATTERN.fullmatch(requirement_id):
            raise AdapterError(f"agent output requirement result {index} id is invalid")
        if requirement_id in requirement_ids:
            raise AdapterError("agent output requirement result ids must be unique")
        if result_status not in ALLOWED_CHECK_STATUS:
            raise AdapterError(f"agent output requirement result {index} status is invalid")
        if not isinstance(evidence, str):
            raise AdapterError(f"agent output requirement result {index} evidence must be a string")
        requirement_ids.add(requirement_id)
        requirement_results.append(
            {"id": requirement_id, "status": result_status, "evidence": evidence}
        )

    if status == "decision_request":
        if not isinstance(decision_id, str) or not ID_PATTERN.fullmatch(decision_id):
            raise AdapterError("decision_request decision_id is invalid")
        if not isinstance(missing_decision, str) or not missing_decision.strip():
            raise AdapterError("decision_request missing_decision must be non-empty")
        if not affected_requirements or not decision_evidence:
            raise AdapterError("decision_request requirements and evidence must be non-empty")
        if normalized_paths or normalized_checks or requirement_results or handoff_path is not None:
            raise AdapterError("decision_request cannot contain implementation results")
    else:
        if decision_id is not None or missing_decision is not None:
            raise AdapterError("terminal output cannot contain a decision request")
        if affected_requirements or decision_evidence:
            raise AdapterError("terminal output decision arrays must be empty")
        if not requirement_results:
            raise AdapterError("terminal output requirement_results must be non-empty")

    normalized.update(
        {
            "decision_id": decision_id,
            "missing_decision": missing_decision.strip() if isinstance(missing_decision, str) else None,
            "affected_requirements": affected_requirements,
            "decision_evidence": decision_evidence,
            "requirement_results": requirement_results,
        }
    )
    return normalized


def _validate_config(config: AdapterConfig) -> None:
    if not ID_PATTERN.fullmatch(config.lane_id):
        raise AdapterError("lane_id must be a lowercase id")
    if not ID_PATTERN.fullmatch(config.role):
        raise AdapterError("role must be a lowercase id")
    if config.model not in ALLOWED_MODELS:
        raise AdapterError(f"unsupported model: {config.model}")
    if config.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
        raise AdapterError(f"unsupported reasoning effort: {config.reasoning_effort}")
    if not config.primary_workspace.is_dir():
        raise AdapterError("primary workspace does not exist")
    if any(not path.is_dir() for path in config.additional_workspaces):
        raise AdapterError("additional workspace does not exist")
    if not config.output_schema.is_file():
        raise AdapterError("output schema does not exist")
    validate_output_schema(config.output_schema)
    prepare_scratch(config.scratch_path)
    if config.auth_source.is_symlink() or not config.auth_source.is_file():
        raise AdapterError("Codex auth source does not exist or is not a regular file")
    client_home = config.client_codex_home.expanduser().resolve()
    protected_roots = (
        config.primary_workspace,
        *config.additional_workspaces,
        config.scratch_path,
    )
    for root in protected_roots:
        try:
            client_home.relative_to(root.expanduser().resolve())
        except ValueError:
            continue
        raise AdapterError("ephemeral CODEX_HOME must be outside model-readable roots")
    if config.timeout_seconds < 1:
        raise AdapterError("timeout_seconds must be positive")
    if config.selected_skills:
        raise AdapterError("model evaluation adapter does not allow optional skills")
    if PINNED_MODEL_CATALOG.is_symlink() or not PINNED_MODEL_CATALOG.is_file():
        raise AdapterError("pinned Codex model catalog is missing or invalid")
    try:
        catalog_bytes = PINNED_MODEL_CATALOG.read_bytes()
        catalog = json.loads(catalog_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError("pinned Codex model catalog is missing or invalid") from exc
    if hashlib.sha256(catalog_bytes).hexdigest() != PINNED_MODEL_CATALOG_SHA256:
        raise AdapterError("pinned Codex model catalog digest does not match the adapter")
    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise AdapterError("pinned Codex model catalog has no models")
    entries = {
        entry.get("slug"): entry
        for entry in models
        if isinstance(entry, dict) and isinstance(entry.get("slug"), str)
    }
    if set(entries) != {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        raise AdapterError("pinned Codex model catalog has unexpected model entries")
    if config.model not in entries:
        raise AdapterError(f"model evaluation catalog does not contain {config.model}")
    if any(entry.get("multi_agent_version") is not None for entry in entries.values()):
        raise AdapterError("pinned Codex model catalog enables multi-agent execution")


def _client_model_catalog(config: AdapterConfig) -> Path:
    return (config.client_codex_home / CLIENT_MODEL_CATALOG).expanduser().resolve()


def build_codex_command(config: AdapterConfig) -> list[str]:
    """Build argv without a shell or prompt interpolation."""
    _validate_config(config)
    command = [
        "codex",
        "--model",
        config.model,
        "-c",
        f'model_reasoning_effort="{config.reasoning_effort}"',
        "-c",
        f"model_catalog_json={json.dumps(str(_client_model_catalog(config)))}",
        *EVAL_ISOLATION_CONFIG_ARGS,
        *permission_profile_args(
            config.scratch_path,
            workspace_roots=(config.primary_workspace, *config.additional_workspaces),
        ),
        *shell_policy_args(config.scratch_path),
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--strict-config",
        "--json",
        "--output-schema",
        str(config.output_schema.resolve()),
        "--output-last-message",
        str(config.output_path.resolve()),
        "--cd",
        str(config.primary_workspace.resolve()),
    ]
    for workspace in config.additional_workspaces:
        command.extend(["--add-dir", str(workspace.resolve())])
    command.append("-")
    return command


def build_codex_resume_command(config: AdapterConfig, thread_id: str) -> list[str]:
    """Build an exact same-thread Codex continuation command."""
    _validate_config(config)
    try:
        if str(uuid.UUID(thread_id)) != thread_id.lower():
            raise ValueError
    except ValueError as exc:
        raise AdapterError("resume thread id must be a canonical UUID") from exc
    return [
        "codex",
        "--model",
        config.model,
        "-c",
        f'model_reasoning_effort="{config.reasoning_effort}"',
        "-c",
        f"model_catalog_json={json.dumps(str(_client_model_catalog(config)))}",
        *EVAL_ISOLATION_CONFIG_ARGS,
        *permission_profile_args(
            config.scratch_path,
            workspace_roots=(config.primary_workspace, *config.additional_workspaces),
        ),
        *shell_policy_args(config.scratch_path),
        "--ask-for-approval",
        "never",
        "exec",
        "resume",
        "--ignore-user-config",
        "--strict-config",
        "--json",
        "--output-schema",
        str(config.output_schema.resolve()),
        "--output-last-message",
        str(config.output_path.resolve()),
        thread_id,
        "-",
    ]


def _jsonl_events(value: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"CLI stdout line {line_number} is not JSON") from exc
        if not isinstance(event, dict):
            raise AdapterError(f"CLI stdout line {line_number} is not an object")
        events.append(event)
    return events


def _trace_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if not completed:
        raise AdapterError("CLI trace has no turn.completed event")
    if len(completed) != 1:
        raise AdapterError("CLI trace must contain exactly one turn.completed event")
    raw_usage = completed[0].get("usage")
    if not isinstance(raw_usage, dict):
        raise AdapterError("CLI turn.completed usage is missing")
    usage: dict[str, int] = {}
    for key in USAGE_FIELDS:
        value = raw_usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AdapterError(f"CLI turn.completed usage.{key} is invalid")
        usage[key] = value
    if usage["input_tokens"] == 0:
        raise AdapterError("CLI turn.completed usage has no accounted input tokens")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise AdapterError("CLI turn.completed cached input exceeds total input")
    if usage["reasoning_output_tokens"] > usage["output_tokens"]:
        raise AdapterError("CLI turn.completed reasoning output exceeds total output")
    return usage


def _trace_model_reroutes(events: list[dict[str, Any]]) -> tuple[ModelRerouteEvidence, ...]:
    """Read the version-pinned reroute message emitted by Codex exec JSONL."""
    reroutes: list[ModelRerouteEvidence] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "error":
            continue
        message = item.get("message")
        if not isinstance(message, str) or not message.startswith("model rerouted:"):
            continue
        match = MODEL_REROUTE_MESSAGE.fullmatch(message)
        if match is None:
            raise SelectionEvidenceError("invalid-trace", "Codex model reroute trace is malformed")
        reroutes.append(
            ModelRerouteEvidence(
                from_model=match.group(1),
                to_model=match.group(2),
                reason=match.group(3),
            )
        )
    return tuple(reroutes)


def _merge_usage(total: dict[str, int], attempt: dict[str, int]) -> None:
    for key, value in attempt.items():
        total[key] = total.get(key, 0) + value


def _thread_id_from_events(events: list[dict[str, Any]], *, required: bool) -> str | None:
    thread_ids = {
        event.get("thread_id")
        for event in events
        if event.get("type") == "thread.started"
        and isinstance(event.get("thread_id"), str)
        and event["thread_id"]
    }
    if len(thread_ids) > 1:
        raise SelectionEvidenceError("invalid-trace", "CLI trace contains multiple thread ids")
    if not thread_ids:
        if required:
            raise SelectionEvidenceError("model-unverified", "CLI trace has no thread id")
        return None
    thread_id = next(iter(thread_ids))
    try:
        if str(uuid.UUID(thread_id)) != thread_id.lower():
            raise ValueError
    except ValueError as exc:
        raise SelectionEvidenceError("invalid-trace", "CLI thread id is not a canonical UUID") from exc
    return thread_id


def _path_has_symlink(path: Path, root: Path) -> bool:
    current = path
    while current != root:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            return True
        current = parent
    return root.is_symlink()


def find_rollout_path(
    codex_home: Path,
    thread_id: str,
    *,
    max_bytes: int = MAX_ROLLOUT_BYTES,
) -> Path:
    """Find the sole rollout owned by one isolated Codex exec thread."""
    if max_bytes < 1:
        raise AdapterError("max rollout size must be positive")
    sessions = codex_home.expanduser().resolve() / "sessions"
    if not sessions.is_dir() or sessions.is_symlink():
        raise SelectionEvidenceError("model-unverified", "Codex rollout directory is missing")
    pattern = f"*/*/*/rollout-*-{thread_id}.jsonl"
    raw_candidates = list(sessions.glob(pattern))
    if not raw_candidates:
        raise SelectionEvidenceError("model-unverified", "Codex rollout is missing")
    if len(raw_candidates) != 1:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout is ambiguous")

    candidate = raw_candidates[0]
    if _path_has_symlink(candidate, sessions):
        raise SelectionEvidenceError("invalid-trace", "Codex rollout path contains a symlink")
    try:
        metadata = os.lstat(candidate)
    except OSError as exc:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout cannot be inspected") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SelectionEvidenceError("invalid-trace", "Codex rollout is not a regular file")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(sessions)
    except ValueError as exc:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout escaped its isolated home") from exc
    if metadata.st_size < 1 or metadata.st_size > max_bytes:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout size is outside the allowed bound")
    return resolved


def assert_only_expected_rollouts(codex_home: Path, thread_ids: set[str]) -> None:
    """Reject child or otherwise unaccounted Codex sessions in one eval lane."""
    sessions = codex_home.expanduser().resolve() / "sessions"
    if not sessions.exists():
        if thread_ids:
            raise SelectionEvidenceError("model-unverified", "Codex rollout directory is missing")
        return
    if not sessions.is_dir() or sessions.is_symlink():
        raise SelectionEvidenceError("invalid-trace", "Codex rollout directory is invalid")
    candidates = list(sessions.glob("*/*/*/rollout-*.jsonl"))
    if len(candidates) != len(thread_ids):
        raise SelectionEvidenceError(
            "invalid-trace",
            "Codex home contains an unexpected rollout; child runs are not allowed",
        )
    for thread_id in thread_ids:
        find_rollout_path(codex_home, thread_id)


def _non_empty_string(value: object, label: str, error_kind: str = "invalid-trace") -> str:
    if not isinstance(value, str) or not value:
        raise SelectionEvidenceError(error_kind, f"Codex rollout {label} is missing")
    return value


def parse_rollout_selection(
    lines: Iterable[str],
    *,
    expected_thread_id: str,
    expected_cli_version: str = CODEX_CLI_VERSION,
    expected_provider: str = CODEX_MODEL_PROVIDER,
    expected_turn_count: int = 1,
) -> RuntimeSelectionEvidence:
    """Reduce a rollout to trusted model-selection fields without retaining its content."""
    session_meta: tuple[str, str, str, str, str] | None = None
    turn_contexts: list[tuple[str, str, str]] = []
    if expected_turn_count < 1:
        raise SelectionEvidenceError("invalid-trace", "expected turn count must be positive")

    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionEvidenceError(
                "invalid-trace", f"Codex rollout line {line_number} is not JSON"
            ) from exc
        if not isinstance(record, dict):
            raise SelectionEvidenceError(
                "invalid-trace", f"Codex rollout line {line_number} is not an object"
            )
        record_type = record.get("type")
        if record_type not in {"session_meta", "turn_context"}:
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise SelectionEvidenceError(
                "invalid-trace", f"Codex rollout {record_type} payload is invalid"
            )

        if record_type == "session_meta":
            current_meta = (
                _non_empty_string(payload.get("id"), "session_meta.id"),
                _non_empty_string(payload.get("session_id"), "session_meta.session_id"),
                _non_empty_string(payload.get("cli_version"), "session_meta.cli_version"),
                _non_empty_string(payload.get("model_provider"), "session_meta.model_provider"),
                _non_empty_string(payload.get("source"), "session_meta.source"),
            )
            if session_meta is not None:
                raise SelectionEvidenceError("invalid-trace", "Codex rollout has duplicate session metadata")
            session_meta = current_meta
            continue

        if record_type == "turn_context":
            current_context = (
                _non_empty_string(payload.get("turn_id"), "turn_context.turn_id"),
                _non_empty_string(payload.get("model"), "turn_context.model", "model-unverified"),
                _non_empty_string(
                    payload.get("effort"), "turn_context.effort", "reasoning-unverified"
                ),
            )
            if not turn_contexts or current_context != turn_contexts[-1]:
                turn_contexts.append(current_context)
            continue

    if session_meta is None:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout has no session metadata")
    thread_id, session_id, cli_version, provider, source = session_meta
    if thread_id != expected_thread_id or session_id != expected_thread_id:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout thread id does not match stdout")
    if cli_version != expected_cli_version:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout CLI version is unexpected")
    if provider != expected_provider:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout model provider is unexpected")
    if source != "exec":
        raise SelectionEvidenceError("invalid-trace", "Codex rollout source is not codex exec")
    if not turn_contexts:
        raise SelectionEvidenceError("model-unverified", "Codex rollout has no turn selection context")
    if len(turn_contexts) != expected_turn_count:
        raise SelectionEvidenceError(
            "invalid-trace", "Codex rollout has an unexpected number of turn contexts"
        )

    turn_id, configured_model, configured_reasoning = turn_contexts[-1]
    return RuntimeSelectionEvidence(
        thread_id=thread_id,
        turn_id=turn_id,
        cli_version=cli_version,
        model_provider=provider,
        configured_model=configured_model,
        configured_reasoning=configured_reasoning,
        selected_model=configured_model,
        selected_reasoning=configured_reasoning,
        reroutes=(),
    )


def _apply_reroutes(
    evidence: RuntimeSelectionEvidence,
    reroutes: tuple[ModelRerouteEvidence, ...],
) -> RuntimeSelectionEvidence:
    selected_model = evidence.configured_model
    for reroute in reroutes:
        if reroute.from_model != selected_model:
            raise SelectionEvidenceError("invalid-trace", "Codex model reroute chain is inconsistent")
        selected_model = reroute.to_model
    return RuntimeSelectionEvidence(
        thread_id=evidence.thread_id,
        turn_id=evidence.turn_id,
        cli_version=evidence.cli_version,
        model_provider=evidence.model_provider,
        configured_model=evidence.configured_model,
        configured_reasoning=evidence.configured_reasoning,
        selected_model=selected_model,
        selected_reasoning=evidence.selected_reasoning,
        reroutes=reroutes,
    )


def read_rollout_selection(
    codex_home: Path,
    thread_id: str,
    *,
    expected_turn_count: int = 1,
) -> RuntimeSelectionEvidence:
    path = find_rollout_path(codex_home, thread_id)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return parse_rollout_selection(
                handle,
                expected_thread_id=thread_id,
                expected_turn_count=expected_turn_count,
            )
    except UnicodeDecodeError as exc:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout is not UTF-8") from exc
    except OSError as exc:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout cannot be read") from exc


def _isolated_process_environment(config: AdapterConfig) -> dict[str, str]:
    client_home = prepare_auth_only_codex_home(
        config.client_codex_home,
        config.auth_source,
    )
    temporary = client_home / "tmp"
    temporary.mkdir(exist_ok=True)
    model_catalog = _client_model_catalog(config)
    if model_catalog.exists() or model_catalog.is_symlink():
        raise AdapterError("disposable Codex home already contains a model catalog")
    shutil.copyfile(PINNED_MODEL_CATALOG, model_catalog)
    model_catalog.chmod(0o600)
    environment = sanitized_process_environment()
    environment.update(
        {
            "CODEX_HOME": str(client_home),
            "HOME": str(client_home),
            "TMPDIR": str(temporary),
        }
    )
    return environment


def _continuation_process_environment(config: AdapterConfig) -> dict[str, str]:
    client_home = config.client_codex_home.expanduser().resolve()
    auth = client_home / "auth.json"
    catalog = _client_model_catalog(config)
    if client_home.is_symlink() or not client_home.is_dir():
        raise AdapterError("disposable Codex home is unavailable for continuation")
    if auth.is_symlink() or not auth.is_file():
        raise AdapterError("disposable Codex authentication is unavailable for continuation")
    if catalog.is_symlink() or not catalog.is_file():
        raise AdapterError("disposable model catalog is unavailable for continuation")
    if hashlib.sha256(catalog.read_bytes()).hexdigest() != PINNED_MODEL_CATALOG_SHA256:
        raise AdapterError("disposable model catalog changed before continuation")
    temporary = client_home / "tmp"
    temporary.mkdir(exist_ok=True)
    environment = sanitized_process_environment()
    environment.update(
        {
            "CODEX_HOME": str(client_home),
            "HOME": str(client_home),
            "TMPDIR": str(temporary),
        }
    )
    return environment


def _rollout_turn_count(codex_home: Path, thread_id: str) -> int:
    path = find_rollout_path(codex_home, thread_id)
    contexts: list[tuple[object, object, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise SelectionEvidenceError("invalid-trace", "Codex rollout cannot be read") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionEvidenceError(
                "invalid-trace", f"Codex rollout line {line_number} is not JSON"
            ) from exc
        if not isinstance(record, dict) or record.get("type") != "turn_context":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise SelectionEvidenceError("invalid-trace", "Codex rollout turn context is invalid")
        context = (payload.get("turn_id"), payload.get("model"), payload.get("effort"))
        if not contexts or context != contexts[-1]:
            contexts.append(context)
    if not contexts:
        raise SelectionEvidenceError("model-unverified", "Codex rollout has no turn context")
    return len(contexts)


def verify_prompt_context(
    config: AdapterConfig,
    environment: dict[str, str],
    runner: Runner = run_process_group,
) -> None:
    """Reject user-global instructions and non-system skills before any model call."""
    command = [
        "codex",
        "--model",
        config.model,
        "-c",
        f'model_reasoning_effort="{config.reasoning_effort}"',
        "-c",
        f"model_catalog_json={json.dumps(str(_client_model_catalog(config)))}",
        *EVAL_ISOLATION_CONFIG_ARGS,
        "-C",
        str(config.primary_workspace.resolve()),
        "debug",
        "prompt-input",
        "Agent Flow context isolation probe.",
    ]
    process = runner(
        command,
        env=environment,
        text=True,
        capture_output=True,
        timeout=min(config.timeout_seconds, 30),
        check=False,
    )
    if process.returncode:
        raise AdapterError("Codex prompt-context probe failed")
    try:
        prompt_input = json.loads(process.stdout or "")
    except json.JSONDecodeError as exc:
        raise AdapterError("Codex prompt-context probe returned invalid JSON") from exc
    if not isinstance(prompt_input, list):
        raise AdapterError("Codex prompt-context probe returned an invalid payload")
    serialized = json.dumps(prompt_input, ensure_ascii=False)
    forbidden_markers = (
        "Глобальные правила Codex",
        "Dynamic Rule Loading",
    )
    if any(marker in serialized for marker in forbidden_markers):
        raise AdapterError("user-global Codex context leaked into the evaluation prompt")
    if "spawn_agent" in serialized:
        raise AdapterError("Codex multi-agent context leaked into the evaluation prompt")
    client_skills = (config.client_codex_home / "skills" / ".system").resolve()
    skill_paths = re.findall(r"\(file: ([^)]+/SKILL\.md)\)", serialized)
    for raw_path in skill_paths:
        try:
            Path(raw_path).resolve().relative_to(client_skills)
        except ValueError as exc:
            raise AdapterError("non-system skill leaked into the evaluation prompt") from exc


def _contains_context_warning(stdout: str, stderr: str) -> bool:
    combined = (stdout + "\n" + stderr).lower()
    return any(marker in combined for marker in CONTEXT_WARNING_MARKERS)


def classify_failure(stdout: str, stderr: str, timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    combined = stdout + "\n" + stderr
    if _contains_context_warning(stdout, stderr):
        return "invalid-context"
    if any(pattern.search(combined) for pattern in MODEL_UNAVAILABLE_PATTERNS):
        return "model-unavailable"
    if any(pattern.search(combined) for pattern in TRANSIENT_PATTERNS):
        return "transient"
    return "cli-error"


def _failure_result(
    config: AdapterConfig,
    error_kind: str,
    attempts: int,
    duration_ms: int,
    status: str = "infrastructure-error",
    selection_status: str = "unverified",
    *,
    usage: dict[str, int] | None = None,
    selection_evidence: tuple[RuntimeSelectionEvidence, ...] = (),
    selected_model: str | None = None,
    selected_reasoning: str | None = None,
    agent_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "lane_id": config.lane_id,
        "role": config.role,
        "adapter": "cli",
        "requested_model": config.model,
        "requested_reasoning": config.reasoning_effort,
        "selected_model": selected_model,
        "selected_reasoning": selected_reasoning,
        "selection_status": selection_status,
        "fallback_reason": None,
        "verification_level": "normal",
        "selection_evidence": [evidence.to_dict() for evidence in selection_evidence],
        "selected_skills": [],
        "active_gates": list(config.active_gates),
        "status": status,
        "error_kind": error_kind,
        "summary": "Codex CLI lane did not produce a valid agent output.",
        "changed_paths": [],
        "checks": [],
        "usage": dict(usage or {}),
        "duration_ms": duration_ms,
        "attempts": attempts,
        "handoff_path": None,
    }
    if agent_output is not None:
        result.update(
            {
                "summary": agent_output["summary"],
                "changed_paths": agent_output["changed_paths"],
                "checks": agent_output["checks"],
                "handoff_path": agent_output["handoff_path"],
            }
        )
    if config.predictability_output:
        result.update(
            {
                "decision_id": None,
                "missing_decision": None,
                "affected_requirements": [],
                "decision_evidence": [],
                "requirement_results": [],
            }
        )
        if agent_output is not None:
            result.update(
                {
                    "decision_id": agent_output["decision_id"],
                    "missing_decision": agent_output["missing_decision"],
                    "affected_requirements": agent_output["affected_requirements"],
                    "decision_evidence": agent_output["decision_evidence"],
                    "requirement_results": agent_output["requirement_results"],
                }
            )
    return result


def _selection_state(
    config: AdapterConfig,
    evidence: tuple[RuntimeSelectionEvidence, ...],
) -> tuple[str, str, str, str | None]:
    if not evidence:
        raise SelectionEvidenceError("model-unverified", "Codex selection evidence is missing")
    for attempt in evidence:
        if attempt.configured_model != config.model:
            raise SelectionEvidenceError(
                "model-unverified", "Codex configured a different model than requested"
            )
        if attempt.configured_reasoning != config.reasoning_effort:
            raise SelectionEvidenceError(
                "reasoning-unverified", "Codex configured a different reasoning effort"
            )
    last = evidence[-1]
    substituted = any(attempt.reroutes for attempt in evidence)
    return (
        "substituted" if substituted else "exact",
        last.selected_model,
        last.selected_reasoning,
        "model-rerouted" if substituted else None,
    )


def _run_cli_turn(
    config: AdapterConfig,
    prompt: str,
    runner: Runner = run_process_group,
    clock: Callable[[], float] = time.monotonic,
    context_probe: ContextProbe = verify_prompt_context,
    *,
    resume_thread_id: str | None = None,
) -> AdapterRun:
    """Run one exact model lane with at most one fully accounted retry."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise AdapterError("prompt must be non-empty")
    active_thread_id = resume_thread_id
    expected_turn_count = 0
    if active_thread_id is None:
        process_environment = _isolated_process_environment(config)
        try:
            context_probe(config, process_environment)
        except (AdapterError, OSError, subprocess.TimeoutExpired) as exc:
            result = _failure_result(config, "invalid-context", 0, 0)
            result["summary"] = f"Prompt context isolation failed: {exc}"
            return AdapterRun(result, "", "")
    else:
        build_codex_resume_command(config, active_thread_id)
        process_environment = _continuation_process_environment(config)
        expected_turn_count = _rollout_turn_count(config.client_codex_home, active_thread_id)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    start = clock()
    transient_retries = 0
    format_retries = 0
    attempts = 0
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    total_usage: dict[str, int] = {}
    selection_attempts: list[RuntimeSelectionEvidence] = []
    rollout_thread_ids: set[str] = set()
    current_prompt = prompt

    def retained_stdout() -> str:
        return "".join(stdout_parts)

    def retained_stderr(extra: str | None = None) -> str:
        parts = [part for part in stderr_parts if part]
        if extra:
            parts.append(redact_text(extra))
        return "\n".join(parts)

    def evidence_tuple() -> tuple[RuntimeSelectionEvidence, ...]:
        return tuple(selection_attempts)

    while attempts < MAX_LANE_ATTEMPTS:
        attempts += 1
        command = (
            build_codex_resume_command(config, active_thread_id)
            if active_thread_id is not None
            else build_codex_command(config)
        )
        config.output_path.unlink(missing_ok=True)
        timed_out = False
        try:
            process = runner(
                command,
                input=current_prompt,
                text=True,
                capture_output=True,
                timeout=config.timeout_seconds,
                check=False,
                env=process_environment,
            )
            stdout = process.stdout or ""
            stderr = process.stderr or ""
            returncode = process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            returncode = -1
            timed_out = True

        stdout_parts.append(redact_jsonl(stdout))
        stderr_parts.append(redact_text(stderr))
        duration_ms = max(0, round((clock() - start) * 1000))

        try:
            events = _jsonl_events(stdout)
        except AdapterError as exc:
            return AdapterRun(
                _failure_result(
                    config,
                    "invalid-trace",
                    attempts,
                    duration_ms,
                    usage=total_usage,
                    selection_evidence=evidence_tuple(),
                ),
                retained_stdout(),
                retained_stderr(str(exc)),
            )
        usage_error: AdapterError | None = None
        try:
            attempt_usage = _trace_usage(events)
        except AdapterError as exc:
            attempt_usage = {}
            usage_error = exc
        _merge_usage(total_usage, attempt_usage)

        thread_id: str | None = None
        current_selection: RuntimeSelectionEvidence | None = None
        selection_error: SelectionEvidenceError | None = None
        try:
            thread_id = _thread_id_from_events(events, required=not bool(returncode))
            if thread_id is not None:
                if active_thread_id is not None and thread_id != active_thread_id:
                    raise SelectionEvidenceError(
                        "invalid-trace", "Codex continuation returned a different thread id"
                    )
                turn_count = expected_turn_count + 1 if active_thread_id is not None else 1
                current_selection = _apply_reroutes(
                    read_rollout_selection(
                        config.client_codex_home,
                        thread_id,
                        expected_turn_count=turn_count,
                    ),
                    _trace_model_reroutes(events),
                )
                selection_attempts.append(current_selection)
                rollout_thread_ids.add(thread_id)
                expected_turn_count = turn_count
        except SelectionEvidenceError as exc:
            selection_error = exc

        if selection_error is None:
            try:
                assert_only_expected_rollouts(config.client_codex_home, rollout_thread_ids)
            except SelectionEvidenceError as exc:
                selection_error = exc

        if _contains_context_warning(stdout, stderr):
            return AdapterRun(
                _failure_result(
                    config,
                    "invalid-context",
                    attempts,
                    duration_ms,
                    usage=total_usage,
                    selection_evidence=evidence_tuple(),
                ),
                retained_stdout(),
                retained_stderr(),
            )

        if returncode:
            if usage_error is not None:
                return AdapterRun(
                    _failure_result(
                        config,
                        "invalid-trace",
                        attempts,
                        duration_ms,
                        usage=total_usage,
                        selection_evidence=evidence_tuple(),
                    ),
                    retained_stdout(),
                    retained_stderr(str(usage_error)),
                )
            error_kind = classify_failure(stdout, stderr, timed_out)
            if (
                error_kind in {"timeout", "transient"}
                and transient_retries < 1
                and attempts < MAX_LANE_ATTEMPTS
            ):
                if current_selection is None:
                    evidence_error = selection_error or SelectionEvidenceError(
                        "model-unverified",
                        "retryable CLI attempt has no model-selection evidence",
                    )
                    return AdapterRun(
                        _failure_result(
                            config,
                            evidence_error.error_kind,
                            attempts,
                            duration_ms,
                            usage=total_usage,
                            selection_evidence=evidence_tuple(),
                        ),
                        retained_stdout(),
                        retained_stderr(str(evidence_error)),
                    )
                transient_retries += 1
                if config.predictability_output:
                    active_thread_id = current_selection.thread_id
                continue
            if current_selection is None:
                evidence_error = selection_error or SelectionEvidenceError(
                    "model-unverified" if error_kind == "timeout" else error_kind,
                    "terminal CLI attempt has no model-selection evidence",
                )
                return AdapterRun(
                    _failure_result(
                        config,
                        evidence_error.error_kind,
                        attempts,
                        duration_ms,
                        usage=total_usage,
                        selection_evidence=evidence_tuple(),
                    ),
                    retained_stdout(),
                    retained_stderr(str(evidence_error)),
                )
            result_status = "fail" if error_kind == "timeout" else "infrastructure-error"
            if error_kind == "timeout":
                error_kind = "model-timeout"
            selection_status = "unverified"
            selected_model: str | None = None
            selected_reasoning: str | None = None
            fallback_reason: str | None = None
            if selection_attempts:
                try:
                    (
                        selection_status,
                        selected_model,
                        selected_reasoning,
                        fallback_reason,
                    ) = _selection_state(config, evidence_tuple())
                except SelectionEvidenceError:
                    pass
            result = _failure_result(
                config,
                error_kind,
                attempts,
                duration_ms,
                result_status,
                selection_status,
                usage=total_usage,
                selection_evidence=evidence_tuple(),
                selected_model=selected_model,
                selected_reasoning=selected_reasoning,
            )
            result["fallback_reason"] = fallback_reason
            return AdapterRun(
                result,
                retained_stdout(),
                retained_stderr(str(selection_error) if selection_error else None),
            )

        agent_output: dict[str, Any] | None = None
        output_error: Exception | None = None
        try:
            raw_output = json.loads(config.output_path.read_text(encoding="utf-8"))
            agent_output = validate_agent_output(
                raw_output,
                predictability=config.predictability_output,
            )
        except (AdapterError, FileNotFoundError, json.JSONDecodeError) as exc:
            output_error = exc

        if selection_error is not None:
            last_evidence = selection_attempts[-1] if selection_attempts else None
            return AdapterRun(
                _failure_result(
                    config,
                    selection_error.error_kind,
                    attempts,
                    duration_ms,
                    usage=total_usage,
                    selection_evidence=evidence_tuple(),
                    selected_model=(last_evidence.selected_model if last_evidence else None),
                    selected_reasoning=(
                        last_evidence.selected_reasoning if last_evidence else None
                    ),
                    agent_output=agent_output,
                ),
                retained_stdout(),
                retained_stderr(str(selection_error)),
            )

        if usage_error is not None:
            last_evidence = selection_attempts[-1] if selection_attempts else None
            return AdapterRun(
                _failure_result(
                    config,
                    "invalid-trace",
                    attempts,
                    duration_ms,
                    usage=total_usage,
                    selection_evidence=evidence_tuple(),
                    selected_model=(last_evidence.selected_model if last_evidence else None),
                    selected_reasoning=(
                        last_evidence.selected_reasoning if last_evidence else None
                    ),
                    agent_output=agent_output,
                ),
                retained_stdout(),
                retained_stderr(str(usage_error)),
            )

        try:
            selection_status, selected_model, selected_reasoning, fallback_reason = _selection_state(
                config, evidence_tuple()
            )
        except SelectionEvidenceError as exc:
            last_evidence = selection_attempts[-1] if selection_attempts else None
            return AdapterRun(
                _failure_result(
                    config,
                    exc.error_kind,
                    attempts,
                    duration_ms,
                    usage=total_usage,
                    selection_evidence=evidence_tuple(),
                    selected_model=(last_evidence.selected_model if last_evidence else None),
                    selected_reasoning=(
                        last_evidence.selected_reasoning if last_evidence else None
                    ),
                    agent_output=agent_output,
                ),
                retained_stdout(),
                retained_stderr(str(exc)),
            )

        if output_error is not None:
            if format_retries < 1 and attempts < MAX_LANE_ATTEMPTS:
                format_retries += 1
                current_prompt = (
                    prompt
                    + "\n\nThe previous response did not satisfy the required JSON schema. "
                    + "Return one complete JSON object that matches the schema exactly."
                )
                if config.predictability_output:
                    active_thread_id = current_selection.thread_id
                continue
            result = _failure_result(
                config,
                "invalid-output",
                attempts,
                duration_ms,
                "fail",
                selection_status,
                usage=total_usage,
                selection_evidence=evidence_tuple(),
                selected_model=selected_model,
                selected_reasoning=selected_reasoning,
            )
            result["fallback_reason"] = fallback_reason
            result["summary"] = f"Invalid agent output: {output_error}"
            return AdapterRun(result, retained_stdout(), retained_stderr())

        if agent_output is None:
            raise AdapterError("validated agent output unexpectedly missing")

        lane_result = {
            "lane_id": config.lane_id,
            "role": config.role,
            "adapter": "cli",
            "requested_model": config.model,
            "requested_reasoning": config.reasoning_effort,
            "selected_model": selected_model,
            "selected_reasoning": selected_reasoning,
            "selection_status": selection_status,
            "fallback_reason": fallback_reason,
            "verification_level": "normal",
            "selection_evidence": [
                evidence.to_dict() for evidence in evidence_tuple()
            ],
            "selected_skills": [],
            "active_gates": list(config.active_gates),
            "status": agent_output["status"],
            "error_kind": None,
            "summary": agent_output["summary"],
            "changed_paths": agent_output["changed_paths"],
            "checks": agent_output["checks"],
            "usage": total_usage,
            "duration_ms": duration_ms,
            "attempts": attempts,
            "handoff_path": agent_output["handoff_path"],
        }
        if config.predictability_output:
            lane_result.update(
                {
                    "decision_id": agent_output["decision_id"],
                    "missing_decision": agent_output["missing_decision"],
                    "affected_requirements": agent_output["affected_requirements"],
                    "decision_evidence": agent_output["decision_evidence"],
                    "requirement_results": agent_output["requirement_results"],
                }
            )
        return AdapterRun(lane_result, retained_stdout(), retained_stderr())

    duration_ms = max(0, round((clock() - start) * 1000))
    return AdapterRun(
        _failure_result(
            config,
            "retry-exhausted",
            attempts,
            duration_ms,
            usage=total_usage,
            selection_evidence=evidence_tuple(),
        ),
        retained_stdout(),
        retained_stderr(),
    )


def _attach_turn_metadata(
    config: AdapterConfig,
    run: AdapterRun,
    logical_turn: int,
) -> AdapterRun:
    if not config.predictability_output:
        return run
    lane_result = dict(run.lane_result)
    selection_evidence = lane_result.get("selection_evidence", [])
    thread_ids = [
        evidence.get("thread_id")
        for evidence in selection_evidence
        if isinstance(evidence, dict) and isinstance(evidence.get("thread_id"), str)
    ]
    thread_id = thread_ids[-1] if thread_ids else None
    turn_count = 0
    if thread_id is not None:
        try:
            turn_count = _rollout_turn_count(config.client_codex_home, thread_id)
        except SelectionEvidenceError:
            turn_count = 0
    lane_result["thread_id"] = thread_id
    lane_result["turn_count"] = turn_count
    lane_result["turns"] = [
        {
            "order": logical_turn,
            "thread_id": thread_id,
            "usage": dict(lane_result.get("usage", {})),
            "duration_ms": lane_result.get("duration_ms", 0),
            "attempts": lane_result.get("attempts", 0),
            "selection_evidence": selection_evidence,
        }
    ]
    return AdapterRun(lane_result, run.stdout_jsonl, run.stderr)


def run_cli_lane(
    config: AdapterConfig,
    prompt: str,
    runner: Runner = run_process_group,
    clock: Callable[[], float] = time.monotonic,
    context_probe: ContextProbe = verify_prompt_context,
) -> AdapterRun:
    """Run an initial Codex turn while preserving the legacy v1 adapter contract."""
    return _attach_turn_metadata(
        config,
        _run_cli_turn(config, prompt, runner, clock, context_probe),
        1,
    )


def resume_cli_lane(
    config: AdapterConfig,
    prompt: str,
    thread_id: str,
    runner: Runner = run_process_group,
    clock: Callable[[], float] = time.monotonic,
    *,
    logical_turn: int = 2,
) -> AdapterRun:
    """Resume a predictability lane in the exact Codex thread."""
    if not config.predictability_output:
        raise AdapterError("same-thread continuation requires predictability output")
    if logical_turn < 2:
        raise AdapterError("continuation logical turn must be at least two")
    return _attach_turn_metadata(
        config,
        _run_cli_turn(
            config,
            prompt,
            runner,
            clock,
            resume_thread_id=thread_id,
        ),
        logical_turn,
    )
