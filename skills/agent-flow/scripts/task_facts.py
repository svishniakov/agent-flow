#!/usr/bin/env python3
"""Validate and normalize deterministic Agent Flow task facts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


TASK_FACTS_SCHEMA_VERSION = 1
ROLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ALLOWED_SURFACES = {
    "backend",
    "cli",
    "docs",
    "frontend",
    "runtime",
    "storage",
}
TASK_CLASS_PRECEDENCE = (
    "high-risk",
    "migration",
    "public-contract",
    "cross-repo",
    "local",
)
ALLOWED_TASK_CLASSES = set(TASK_CLASS_PRECEDENCE)
ALLOWED_PRODUCTION_RISKS = {"normal", "high"}
REQUIRED_FIELDS = {
    "role",
    "changes_files",
    "repo_count",
    "surfaces",
    "task_classes",
    "public_contract",
    "migration",
    "external_write",
    "production_risk",
}


class TaskFactsError(ValueError):
    """Raised when Task Facts do not satisfy the deterministic policy contract."""


def _string_list(value: object, field: str, allowed: set[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TaskFactsError(f"{field} must be a non-empty array")
    if not all(isinstance(item, str) and item for item in value):
        raise TaskFactsError(f"{field} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise TaskFactsError(f"{field} must not contain duplicates")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise TaskFactsError(f"{field} contains unknown values: {', '.join(unknown)}")
    return list(value)


def _boolean(data: Mapping[str, Any], field: str) -> bool:
    value = data[field]
    if not isinstance(value, bool):
        raise TaskFactsError(f"{field} must be a boolean")
    return value


def derived_task_classes(data: Mapping[str, Any]) -> set[str]:
    """Return task classes implied by objective Task Facts."""
    classes = {"cross-repo" if data["repo_count"] > 1 else "local"}
    if data["public_contract"]:
        classes.add("public-contract")
    if data["migration"]:
        classes.add("migration")
    if data["production_risk"] == "high":
        classes.add("high-risk")
    return classes


def primary_task_class(task_classes: list[str] | set[str]) -> str:
    """Select the highest-precedence class without model judgment."""
    selected = set(task_classes)
    for task_class in TASK_CLASS_PRECEDENCE:
        if task_class in selected:
            return task_class
    raise TaskFactsError("task_classes does not contain a routable class")


def normalize_task_facts(value: object, known_roles: set[str] | None = None) -> dict[str, Any]:
    """Validate Task Facts and return a canonical, JSON-serializable object."""
    if not isinstance(value, Mapping):
        raise TaskFactsError("Task Facts must be an object")

    data = dict(value)
    missing = sorted(REQUIRED_FIELDS - data.keys())
    if missing:
        raise TaskFactsError(f"Task Facts missing fields: {', '.join(missing)}")
    unknown = sorted(set(data) - REQUIRED_FIELDS)
    if unknown:
        raise TaskFactsError(f"Task Facts contains unknown fields: {', '.join(unknown)}")

    role = data["role"]
    if not isinstance(role, str) or not ROLE_PATTERN.fullmatch(role):
        raise TaskFactsError("role must be a lowercase kebab-case slug")
    if known_roles is not None and role not in known_roles:
        raise TaskFactsError(f"unknown role: {role}")

    changes_files = _boolean(data, "changes_files")
    public_contract = _boolean(data, "public_contract")
    migration = _boolean(data, "migration")
    external_write = _boolean(data, "external_write")

    repo_count = data["repo_count"]
    if isinstance(repo_count, bool) or not isinstance(repo_count, int) or repo_count < 0:
        raise TaskFactsError("repo_count must be a non-negative integer")
    if changes_files and repo_count < 1:
        raise TaskFactsError("changes_files=true requires repo_count >= 1")

    surfaces = _string_list(data["surfaces"], "surfaces", ALLOWED_SURFACES)
    task_classes = _string_list(data["task_classes"], "task_classes", ALLOWED_TASK_CLASSES)

    production_risk = data["production_risk"]
    if production_risk not in ALLOWED_PRODUCTION_RISKS:
        raise TaskFactsError("production_risk must be normal or high")

    normalized = {
        "role": role,
        "changes_files": changes_files,
        "repo_count": repo_count,
        "surfaces": sorted(surfaces),
        "task_classes": [item for item in TASK_CLASS_PRECEDENCE if item in task_classes],
        "public_contract": public_contract,
        "migration": migration,
        "external_write": external_write,
        "production_risk": production_risk,
    }
    required_classes = derived_task_classes(normalized)
    missing_classes = sorted(required_classes - set(task_classes))
    if missing_classes:
        raise TaskFactsError(
            "task_classes missing values implied by facts: " + ", ".join(missing_classes)
        )
    normalized["primary_task_class"] = primary_task_class(task_classes)
    return normalized
