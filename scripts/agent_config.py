"""Shared helpers for Agent Flow role model configuration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


ALLOWED_MODELS = {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"}
ALLOWED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
ALLOWED_SERVICE_TIERS = {"priority"}
ALLOWED_ESCALATION_TRIGGERS = {
    "accessibility-risk",
    "ambiguous-product",
    "app-intents",
    "architecture-risk",
    "blocked-replan",
    "brand-critical",
    "broad-cleanup",
    "broad-scope",
    "complex-ux",
    "complex-visual",
    "complex-visual-qa",
    "concurrency",
    "cross-platform",
    "cross-system",
    "current-facts",
    "data-loss",
    "data-processing",
    "design-system",
    "evaluation-risk",
    "external-facts",
    "failing-tests",
    "flaky-tests",
    "graph-rag",
    "high-stakes",
    "kafka",
    "market-strategy",
    "migration",
    "multi-viewport",
    "niche-ui",
    "package-migration",
    "payments-auth",
    "production-rag",
    "prd-spec",
    "public-contract",
    "public-docs",
    "regression-risk",
    "release",
    "retrieval-quality",
    "security",
    "simulator-debug",
    "sparse-sources",
    "ui-copy",
    "visual-risk",
}
DEPRECATED_FRONTMATTER_KEYS = {"model_policy", "speed", "fallback_model"}
ROLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class AgentConfigError(ValueError):
    """Raised when role frontmatter cannot be parsed."""


def default_agents_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "agents"


def resolve_role_path(agents_dir: Path, role: str) -> Path:
    if not ROLE_PATTERN.fullmatch(role):
        raise AgentConfigError(f"invalid role slug: {role}")
    return agents_dir / f"{role}.md"


def read_frontmatter(path: Path) -> dict[str, str]:
    if not path.exists():
        raise AgentConfigError(f"missing role file: {path}")
    if not path.is_file():
        raise AgentConfigError(f"role path is not a file: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise AgentConfigError(f"{path}: missing opening frontmatter marker")

    try:
        end_index = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise AgentConfigError(f"{path}: missing closing frontmatter marker") from exc

    metadata: dict[str, str] = {}
    for number, line in enumerate(lines[1:end_index], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise AgentConfigError(f"{path}:{number}: invalid frontmatter line")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            raise AgentConfigError(f"{path}:{number}: empty frontmatter key")
        if key in metadata:
            raise AgentConfigError(f"{path}:{number}: duplicate frontmatter key: {key}")
        metadata[key] = strip_yaml_quotes(value)

    return metadata


def strip_yaml_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_inline_list(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    for char in value:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escape = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            current.append(char)
            continue
        if char == "," and quote is None:
            item = strip_yaml_quotes("".join(current).strip())
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)

    item = strip_yaml_quotes("".join(current).strip())
    if item:
        items.append(item)
    return items


def role_config(metadata: dict[str, str], role: str, triggers: list[str] | None = None) -> dict[str, Any]:
    requested_triggers = triggers or []
    escalation_triggers = split_inline_list(metadata.get("escalation_triggers"))
    matched_triggers = [trigger for trigger in requested_triggers if trigger in escalation_triggers]
    has_escalation = bool(
        metadata.get("escalation_model")
        or metadata.get("escalation_reasoning_effort")
        or metadata.get("escalation_service_tier")
    )
    escalated = bool(matched_triggers and has_escalation)

    default = {
        "model": metadata.get("model"),
        "reasoning_effort": metadata.get("reasoning_effort"),
        "service_tier": metadata.get("service_tier"),
    }
    escalation = {
        "model": metadata.get("escalation_model") or metadata.get("model"),
        "reasoning_effort": metadata.get("escalation_reasoning_effort") or metadata.get("reasoning_effort"),
        "service_tier": metadata.get("escalation_service_tier") or metadata.get("service_tier"),
        "triggers": escalation_triggers,
    }
    selected = escalation if escalated else default

    return {
        "role": role,
        "model": selected["model"],
        "reasoning_effort": selected["reasoning_effort"],
        "service_tier": selected["service_tier"],
        "escalated": escalated,
        "matched_triggers": matched_triggers,
        "default": default,
        "escalation": escalation,
    }


def validate_model_field(metadata: dict[str, str], key: str, errors: list[str], required: bool = False) -> None:
    value = metadata.get(key)
    if not value:
        if required:
            errors.append(f"missing required frontmatter key: {key}")
        return
    if value not in ALLOWED_MODELS:
        errors.append(f"invalid {key}: {value}")


def validate_reasoning_field(metadata: dict[str, str], key: str, errors: list[str], required: bool = False) -> None:
    value = metadata.get(key)
    if not value:
        if required:
            errors.append(f"missing required frontmatter key: {key}")
        return
    if value not in ALLOWED_REASONING_EFFORTS:
        errors.append(f"invalid {key}: {value}")


def validate_service_tier_field(metadata: dict[str, str], key: str, errors: list[str]) -> None:
    value = metadata.get(key)
    if value and value not in ALLOWED_SERVICE_TIERS:
        errors.append(f"invalid {key}: {value}")


def validate_role_metadata(path: Path, metadata: dict[str, str]) -> list[str]:
    errors: list[str] = []

    deprecated_keys = sorted(DEPRECATED_FRONTMATTER_KEYS & metadata.keys())
    if deprecated_keys:
        errors.append(f"deprecated frontmatter keys: {', '.join(deprecated_keys)}")

    validate_model_field(metadata, "model", errors, required=True)
    validate_model_field(metadata, "escalation_model", errors)
    validate_reasoning_field(metadata, "reasoning_effort", errors, required=True)
    validate_reasoning_field(metadata, "escalation_reasoning_effort", errors)
    validate_service_tier_field(metadata, "service_tier", errors)
    validate_service_tier_field(metadata, "escalation_service_tier", errors)

    escalation_triggers = split_inline_list(metadata.get("escalation_triggers"))
    invalid_triggers = sorted(set(escalation_triggers) - ALLOWED_ESCALATION_TRIGGERS)
    if invalid_triggers:
        errors.append(f"invalid escalation_triggers: {', '.join(invalid_triggers)}")
    has_escalation_config = any(
        metadata.get(key) for key in ["escalation_model", "escalation_reasoning_effort", "escalation_service_tier"]
    )
    if escalation_triggers and not has_escalation_config:
        errors.append("escalation_triggers require escalation_model, escalation_reasoning_effort, or escalation_service_tier")
    if has_escalation_config and not escalation_triggers:
        errors.append("escalation config requires escalation_triggers")

    name = metadata.get("name")
    if name and name != path.stem:
        errors.append(f"name does not match file stem: {name} != {path.stem}")

    return errors
