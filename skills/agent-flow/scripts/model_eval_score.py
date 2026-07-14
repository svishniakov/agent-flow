#!/usr/bin/env python3
"""Deterministic scoring for paired Agent Flow model-evaluation cells."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from typing import Any


SCORABLE_SELECTION_STATUSES = {"exact"}
ALLOWED_CELL_STATUSES = {"pass", "fail", "blocked", "infrastructure-error"}
ALLOWED_LANE_STATUSES = {"pass", "fail", "blocked", "infrastructure-error"}
ALLOWED_PAIRWISE_QUALITY = {"win", "loss", "tie", "not-run"}
RESULT_ENVELOPE_FIELDS = {
    "status",
    "corpus_id",
    "corpus_fingerprint",
    "execution_harness_fingerprint",
    "plan_fingerprint",
    "cells",
}
PLAN_BOUND_FIELDS = (
    "corpus_id",
    "corpus_fingerprint",
    "execution_harness_fingerprint",
    "coverage",
    "task_ids",
    "configurations",
    "seed",
    "repeats",
    "scheduled_model_calls",
    "max_model_calls",
    "schedule",
)
PLAN_FIELDS = {"status", "plan_fingerprint", *PLAN_BOUND_FIELDS}
PLAN_LINK_FIELDS = {
    "run_kind",
    "parent_plan_fingerprint",
    "parent_cells_digest",
    "base_score_digest",
}
SCHEDULE_FIELDS = {
    "task_id",
    "config_id",
    "model",
    "reasoning_effort",
    "repeat",
    "cell_id",
    "scheduled_model_calls",
    "max_model_calls",
}
CONFIGURATION_FIELDS = {"config_id", "model", "reasoning_effort"}


class ScoringError(ValueError):
    """Raised when cell evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class ConfigurationScore:
    config_id: str
    critical_failures: int
    passed_tasks: int
    unstable_tasks: int
    pairwise_wins: int
    token_usage: int
    duration_ms: int
    excluded_cells: int

    @property
    def ordering_key(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.critical_failures,
            -self.passed_tasks,
            self.unstable_tasks,
            -self.pairwise_wins,
            self.token_usage,
            self.duration_ms,
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "config_id": self.config_id,
            "critical_failures": self.critical_failures,
            "passed_tasks": self.passed_tasks,
            "unstable_tasks": self.unstable_tasks,
            "pairwise_wins": self.pairwise_wins,
            "token_usage": self.token_usage,
            "duration_ms": self.duration_ms,
            "excluded_cells": self.excluded_cells,
        }


def _non_negative_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ScoringError(f"{label} must be a non-negative integer")
    return value


def validate_cell(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScoringError("cell must be an object")
    required = {
        "task_id",
        "config_id",
        "repeat",
        "status",
        "selection_status",
        "critical_failures",
        "lane_statuses",
        "integration_required",
        "integration_pass",
        "pairwise_quality",
        "model_attempts",
        "token_usage",
        "duration_ms",
    }
    missing = sorted(required - value.keys())
    unknown = sorted(set(value) - required - {"predictability"})
    if missing:
        raise ScoringError(f"cell missing fields: {', '.join(missing)}")
    if unknown:
        raise ScoringError(f"cell contains unknown fields: {', '.join(unknown)}")
    for field in ("task_id", "config_id"):
        if not isinstance(value[field], str) or not value[field]:
            raise ScoringError(f"cell {field} must be non-empty")
    repeat = value["repeat"]
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ScoringError("cell repeat must be a positive integer")
    if value["status"] not in ALLOWED_CELL_STATUSES:
        raise ScoringError("cell status is invalid")
    if not isinstance(value["selection_status"], str) or not value["selection_status"]:
        raise ScoringError("cell selection_status must be non-empty")
    lane_statuses = value["lane_statuses"]
    if not isinstance(lane_statuses, list) or not lane_statuses:
        raise ScoringError("cell lane_statuses must be a non-empty array")
    if any(status not in ALLOWED_LANE_STATUSES for status in lane_statuses):
        raise ScoringError("cell lane_statuses contains an invalid status")
    if not isinstance(value["integration_required"], bool):
        raise ScoringError("cell integration_required must be a boolean")
    integration_pass = value["integration_pass"]
    if integration_pass is not None and not isinstance(integration_pass, bool):
        raise ScoringError("cell integration_pass must be a boolean or null")
    if value["integration_required"] and integration_pass is None:
        raise ScoringError("integration_required=true requires integration_pass")
    if value["pairwise_quality"] not in ALLOWED_PAIRWISE_QUALITY:
        raise ScoringError("cell pairwise_quality is invalid")
    normalized = dict(value)
    normalized["critical_failures"] = _non_negative_integer(
        value["critical_failures"], "cell critical_failures"
    )
    normalized["model_attempts"] = _non_negative_integer(
        value["model_attempts"], "cell model_attempts"
    )
    normalized["token_usage"] = _non_negative_integer(value["token_usage"], "cell token_usage")
    normalized["duration_ms"] = _non_negative_integer(value["duration_ms"], "cell duration_ms")
    if "predictability" in value:
        normalized["predictability"] = _validate_predictability_block(value["predictability"])
    return normalized


def _validate_predictability_block(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScoringError("cell predictability must be an object")
    required = {
        "schema_version",
        "score",
        "pass",
        "components",
        "hard_fail_reasons",
        "soft_deviations",
        "evidence",
    }
    if set(value) != required:
        raise ScoringError("cell predictability fields are invalid")
    if value["schema_version"] != 1:
        raise ScoringError("cell predictability schema_version is invalid")
    score = value["score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ScoringError("cell predictability score is invalid")
    if not isinstance(value["pass"], bool):
        raise ScoringError("cell predictability pass is invalid")
    components = value["components"]
    component_fields = {
        "requirements",
        "instruction_fidelity",
        "decision_discipline",
        "claim_fidelity",
    }
    if not isinstance(components, dict) or set(components) != component_fields:
        raise ScoringError("cell predictability components are invalid")
    maximums = {
        "requirements": 40,
        "instruction_fidelity": 35,
        "decision_discipline": 15,
        "claim_fidelity": 10,
    }
    for field, maximum in maximums.items():
        component = components[field]
        if (
            isinstance(component, bool)
            or not isinstance(component, int)
            or not 0 <= component <= maximum
        ):
            raise ScoringError(f"cell predictability component {field} is invalid")
    for field in ("hard_fail_reasons", "soft_deviations"):
        items = value[field]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item for item in items)
            or len(items) != len(set(items))
        ):
            raise ScoringError(f"cell predictability {field} is invalid")
    if not isinstance(value["evidence"], list) or any(
        not isinstance(item, dict) for item in value["evidence"]
    ):
        raise ScoringError("cell predictability evidence is invalid")
    expected_pass = score >= 80 and not value["hard_fail_reasons"]
    if value["pass"] != expected_pass:
        raise ScoringError("cell predictability pass does not match score and hard failures")
    return dict(value)


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScoringError(f"{label} must be a non-empty string")
    return value


def _cell_key(value: dict[str, Any]) -> tuple[str, str, int]:
    return value["task_id"], value["config_id"], value["repeat"]


def _string_array(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ScoringError(f"{label} must be a non-empty array")
    if not all(isinstance(item, str) and item for item in value):
        raise ScoringError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ScoringError(f"{label} must not contain duplicates")
    return list(value)


def _schedule_key(
    value: object,
    index: int,
) -> tuple[str, str, str, str, int, str, int, int]:
    if not isinstance(value, dict):
        raise ScoringError(f"plan schedule item {index} must be an object")
    missing = sorted(SCHEDULE_FIELDS - value.keys())
    unknown = sorted(set(value) - SCHEDULE_FIELDS)
    if missing:
        raise ScoringError(
            f"plan schedule item {index} missing fields: {', '.join(missing)}"
        )
    if unknown:
        raise ScoringError(
            f"plan schedule item {index} contains unknown fields: {', '.join(unknown)}"
        )
    task_id = _non_empty_string(value["task_id"], f"plan schedule item {index} task_id")
    config_id = _non_empty_string(
        value["config_id"], f"plan schedule item {index} config_id"
    )
    model = _non_empty_string(value["model"], f"plan schedule item {index} model")
    reasoning_effort = _non_empty_string(
        value["reasoning_effort"], f"plan schedule item {index} reasoning_effort"
    )
    repeat = value["repeat"]
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ScoringError(f"plan schedule item {index} repeat must be a positive integer")
    cell_id = _non_empty_string(value["cell_id"], f"plan schedule item {index} cell_id")
    scheduled_model_calls = _non_negative_integer(
        value["scheduled_model_calls"],
        f"plan schedule item {index} scheduled_model_calls",
    )
    max_model_calls = _non_negative_integer(
        value["max_model_calls"],
        f"plan schedule item {index} max_model_calls",
    )
    if scheduled_model_calls < 1:
        raise ScoringError(
            f"plan schedule item {index} scheduled_model_calls must be positive"
        )
    if max_model_calls < scheduled_model_calls:
        raise ScoringError(
            f"plan schedule item {index} max_model_calls must cover scheduled_model_calls"
        )
    return (
        task_id,
        config_id,
        model,
        reasoning_effort,
        repeat,
        cell_id,
        scheduled_model_calls,
        max_model_calls,
    )


def _format_cell_keys(keys: set[tuple[str, str, int]]) -> str:
    return ", ".join(
        f"{task_id}/{config_id}/r{repeat}"
        for task_id, config_id, repeat in sorted(keys)
    )


def plan_fingerprint(plan: object) -> str:
    """Bind every immutable field that defines a run decision envelope."""
    if not isinstance(plan, dict):
        raise ScoringError("run plan must be an object")
    missing = sorted(set(PLAN_BOUND_FIELDS) - plan.keys())
    if missing:
        raise ScoringError(f"run plan missing fingerprinted fields: {', '.join(missing)}")
    payload = {field: plan[field] for field in PLAN_BOUND_FIELDS}
    payload.update(
        {field: plan[field] for field in sorted(PLAN_LINK_FIELDS) if field in plan}
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_configuration(value: object, index: int) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise ScoringError(f"plan configuration {index} must be an object")
    missing = sorted(CONFIGURATION_FIELDS - value.keys())
    unknown = sorted(set(value) - CONFIGURATION_FIELDS)
    if missing:
        raise ScoringError(f"plan configuration {index} missing fields: {', '.join(missing)}")
    if unknown:
        raise ScoringError(
            f"plan configuration {index} contains unknown fields: {', '.join(unknown)}"
        )
    return (
        _non_empty_string(value["config_id"], f"plan configuration {index} config_id"),
        _non_empty_string(value["model"], f"plan configuration {index} model"),
        _non_empty_string(
            value["reasoning_effort"], f"plan configuration {index} reasoning_effort"
        ),
    )


def _validate_plan(plan: object, label: str) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ScoringError(f"{label} must be an object")
    missing = sorted(PLAN_FIELDS - plan.keys())
    unknown = sorted(set(plan) - PLAN_FIELDS - PLAN_LINK_FIELDS)
    if missing:
        raise ScoringError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ScoringError(f"{label} contains unknown fields: {', '.join(unknown)}")
    if plan["status"] != "complete":
        raise ScoringError(f"{label} is not complete")
    for field in ("corpus_id", "corpus_fingerprint", "execution_harness_fingerprint"):
        _non_empty_string(plan[field], f"{label} {field}")
    run_kind = plan.get("run_kind", "baseline")
    if run_kind not in {"baseline", "adaptive", "recovery"}:
        raise ScoringError(f"{label} run_kind is invalid")
    if run_kind == "baseline" and any(field in plan for field in PLAN_LINK_FIELDS):
        raise ScoringError(f"{label} baseline plan cannot contain linkage fields")
    required_links = {"parent_plan_fingerprint", "parent_cells_digest"}
    if run_kind == "adaptive":
        required_links.add("base_score_digest")
    if run_kind != "baseline":
        missing_links = sorted(required_links - plan.keys())
        if missing_links:
            raise ScoringError(f"{label} linkage fields are missing: {', '.join(missing_links)}")
        for field in required_links:
            _non_empty_string(plan[field], f"{label} {field}")
    if plan["coverage"] not in {"full", "partial"}:
        raise ScoringError(f"{label} coverage must be full or partial")
    task_ids = _string_array(plan["task_ids"], f"{label} task_ids")
    configurations = plan["configurations"]
    if not isinstance(configurations, list) or not configurations:
        raise ScoringError(f"{label} configurations must be a non-empty array")
    configuration_values = [
        _validate_configuration(value, index) for index, value in enumerate(configurations)
    ]
    config_ids = [value[0] for value in configuration_values]
    if len(config_ids) != len(set(config_ids)):
        raise ScoringError(f"{label} configurations must use unique config_id values")
    seed = plan["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScoringError(f"{label} seed must be an integer")
    repeats = plan["repeats"]
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise ScoringError(f"{label} repeats must be a positive integer")
    scheduled_model_calls = _non_negative_integer(
        plan["scheduled_model_calls"], f"{label} scheduled_model_calls"
    )
    max_model_calls = _non_negative_integer(
        plan["max_model_calls"], f"{label} max_model_calls"
    )
    if max_model_calls < scheduled_model_calls:
        raise ScoringError(f"{label} max_model_calls must cover scheduled_model_calls")
    schedule = plan["schedule"]
    if not isinstance(schedule, list) or not schedule:
        raise ScoringError(f"{label} schedule must be a non-empty array")

    configuration_map = {
        config_id: (model, reasoning)
        for config_id, model, reasoning in configuration_values
    }
    full_keys: set[tuple[str, str, str, str, int, str, int, int]] = set()
    partial_keys: set[tuple[str, str, int]] = set()
    cell_ids: set[str] = set()
    for index, item in enumerate(schedule):
        key = _schedule_key(item, index)
        (
            task_id,
            config_id,
            model,
            reasoning,
            repeat,
            cell_id,
            _scheduled_calls,
            _max_calls,
        ) = key
        if key in full_keys:
            raise ScoringError(f"{label} contains a duplicate schedule item")
        full_keys.add(key)
        partial_key = (task_id, config_id, repeat)
        if partial_key in partial_keys:
            raise ScoringError(
                f"{label} contains duplicate cell: {_format_cell_keys({partial_key})}"
            )
        partial_keys.add(partial_key)
        if cell_id in cell_ids:
            raise ScoringError(f"{label} contains duplicate cell_id: {cell_id}")
        cell_ids.add(cell_id)
        if task_id not in task_ids:
            raise ScoringError(f"{label} schedule contains unselected task: {task_id}")
        if config_id not in configuration_map:
            raise ScoringError(f"{label} schedule contains unknown configuration: {config_id}")
        if configuration_map[config_id] != (model, reasoning):
            raise ScoringError(
                f"{label} schedule model/reasoning does not match configuration: {config_id}"
            )
        if repeat > repeats:
            raise ScoringError(f"{label} schedule repeat exceeds repeats")
    if run_kind == "adaptive":
        if repeats != 3:
            raise ScoringError(f"{label} adaptive plan must use repeat 3")
        expected_partial_keys = {
            (task_id, config_id, 3)
            for task_id in task_ids
            for config_id in config_ids
        }
    elif run_kind == "recovery":
        expected_partial_keys = partial_keys
        if set(task_ids) != {task_id for task_id, _config_id, _repeat in partial_keys}:
            raise ScoringError(f"{label} recovery task_ids do not match its schedule")
    else:
        expected_partial_keys = {
            (task_id, config_id, repeat)
            for task_id in task_ids
            for repeat in range(1, repeats + 1)
            for config_id in config_ids
        }
    missing_schedule = expected_partial_keys - partial_keys
    extra_schedule = partial_keys - expected_partial_keys
    if missing_schedule:
        raise ScoringError(
            f"{label} schedule is missing cells: {_format_cell_keys(missing_schedule)}"
        )
    if extra_schedule:
        raise ScoringError(
            f"{label} schedule contains foreign cells: {_format_cell_keys(extra_schedule)}"
        )
    schedule_scheduled_calls = sum(item["scheduled_model_calls"] for item in schedule)
    schedule_max_calls = sum(item["max_model_calls"] for item in schedule)
    if schedule_scheduled_calls != scheduled_model_calls:
        raise ScoringError(f"{label} scheduled_model_calls does not match its schedule")
    if schedule_max_calls != max_model_calls:
        raise ScoringError(f"{label} max_model_calls does not match its schedule")
    fingerprint = _non_empty_string(plan["plan_fingerprint"], f"{label} plan_fingerprint")
    if fingerprint != plan_fingerprint(plan):
        raise ScoringError(f"{label} plan_fingerprint does not match its canonical fields")
    return dict(plan)


def validate_score_inputs(
    results: object,
    plan: object,
    expected_plan: object,
    *,
    allow_infrastructure: bool = False,
) -> list[dict[str, Any]]:
    """Validate a fingerprint-bound, complete result set before declaring a winner."""
    if not isinstance(results, dict):
        raise ScoringError("cell results must be a full result envelope object")
    missing_envelope = sorted(RESULT_ENVELOPE_FIELDS - results.keys())
    unknown_envelope = sorted(set(results) - RESULT_ENVELOPE_FIELDS)
    if missing_envelope:
        raise ScoringError(
            f"cell results envelope missing fields: {', '.join(missing_envelope)}"
        )
    if unknown_envelope:
        raise ScoringError(
            f"cell results envelope contains unknown fields: {', '.join(unknown_envelope)}"
        )
    if results["status"] != "complete":
        raise ScoringError("cell results envelope is not complete")
    normalized_plan = _validate_plan(plan, "run plan")
    normalized_expected = _validate_plan(expected_plan, "canonical run plan")
    for field in PLAN_BOUND_FIELDS:
        if normalized_plan[field] != normalized_expected[field]:
            raise ScoringError(f"run plan {field} does not match canonical run plan")
    if normalized_plan["plan_fingerprint"] != normalized_expected["plan_fingerprint"]:
        raise ScoringError("run plan fingerprint does not match canonical run plan")

    for field in (
        "corpus_id",
        "corpus_fingerprint",
        "execution_harness_fingerprint",
        "plan_fingerprint",
    ):
        result_value = _non_empty_string(results[field], f"cell results {field}")
        plan_value = _non_empty_string(normalized_plan[field], f"run plan {field}")
        if result_value != plan_value:
            raise ScoringError(f"cell results {field} does not match run plan")

    schedule = normalized_plan["schedule"]
    expected_keys: set[tuple[str, str, int]] = set()
    attempt_bounds: dict[tuple[str, str, int], tuple[int, int]] = {}
    for index, item in enumerate(schedule):
        (
            task_id,
            config_id,
            _model,
            _reasoning,
            repeat,
            _cell_id,
            scheduled_calls,
            max_calls,
        ) = _schedule_key(item, index)
        key = task_id, config_id, repeat
        if key in expected_keys:
            raise ScoringError(f"run plan contains duplicate cell: {_format_cell_keys({key})}")
        expected_keys.add(key)
        attempt_bounds[key] = (scheduled_calls, max_calls)

    raw_cells = results["cells"]
    if not isinstance(raw_cells, list):
        raise ScoringError("cell results cells must be an array")
    cells = [validate_cell(value) for value in raw_cells]
    actual_keys: set[tuple[str, str, int]] = set()
    duplicate_keys: set[tuple[str, str, int]] = set()
    for cell in cells:
        key = _cell_key(cell)
        if key in actual_keys:
            duplicate_keys.add(key)
        actual_keys.add(key)
    if duplicate_keys:
        raise ScoringError(
            f"cell results contain duplicate cells: {_format_cell_keys(duplicate_keys)}"
        )

    missing_cells = expected_keys - actual_keys
    extra_cells = actual_keys - expected_keys
    if missing_cells:
        raise ScoringError(
            f"cell results are missing scheduled cells: {_format_cell_keys(missing_cells)}"
        )
    if extra_cells:
        raise ScoringError(
            f"cell results contain unscheduled cells: {_format_cell_keys(extra_cells)}"
        )
    for cell in cells:
        _scheduled, maximum = attempt_bounds[_cell_key(cell)]
        minimum_allowed = (
            0
            if allow_infrastructure and cell["status"] == "infrastructure-error"
            else 1
        )
        if not minimum_allowed <= cell["model_attempts"] <= maximum:
            raise ScoringError(
                "cell model_attempts is outside its scheduled bounds: "
                + _format_cell_keys({_cell_key(cell)})
            )
    actual_model_attempts = sum(cell["model_attempts"] for cell in cells)
    if actual_model_attempts > normalized_plan["max_model_calls"]:
        raise ScoringError("cell results exceed the model call budget")
    infrastructure_cells = {
        _cell_key(cell) for cell in cells if cell["status"] == "infrastructure-error"
    }
    if infrastructure_cells and not allow_infrastructure:
        raise ScoringError(
            "winner is unavailable while infrastructure cells exist: "
            + _format_cell_keys(infrastructure_cells)
        )
    unverified_cells = {
        _cell_key(cell)
        for cell in cells
        if cell["selection_status"] not in SCORABLE_SELECTION_STATUSES
        and not (allow_infrastructure and cell["status"] == "infrastructure-error")
    }
    if unverified_cells:
        raise ScoringError(
            "winner is unavailable while model selection is unverified: "
            + _format_cell_keys(unverified_cells)
        )
    return cells


def is_scorable(cell: dict[str, Any]) -> bool:
    return (
        cell["selection_status"] in SCORABLE_SELECTION_STATUSES
        and cell["status"] != "infrastructure-error"
    )


def cell_passed(cell: dict[str, Any]) -> bool:
    """Require the cell, every lane, and integration evidence to pass."""
    if not is_scorable(cell):
        return False
    if cell["status"] != "pass" or cell["critical_failures"]:
        return False
    if any(status != "pass" for status in cell["lane_statuses"]):
        return False
    if cell["integration_required"] and cell["integration_pass"] is not True:
        return False
    return True


def _group_tasks(cells: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    tasks: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        tasks.setdefault(cell["task_id"], []).append(cell)
    for task_cells in tasks.values():
        task_cells.sort(key=lambda item: item["repeat"])
    return tasks


def aggregate_configuration(
    values: list[object],
    config_id: str,
    included_keys: set[tuple[str, int]] | None = None,
) -> ConfigurationScore:
    cells = [validate_cell(value) for value in values]
    matching = [cell for cell in cells if cell["config_id"] == config_id]
    if not matching:
        raise ScoringError(f"no cells for configuration: {config_id}")
    scorable = [
        cell
        for cell in matching
        if is_scorable(cell)
        and (included_keys is None or (cell["task_id"], cell["repeat"]) in included_keys)
    ]
    tasks = _group_tasks(scorable)
    passed_tasks = 0
    unstable_tasks = 0
    for task_cells in tasks.values():
        outcomes = [cell_passed(cell) for cell in task_cells]
        if len(set(outcomes)) > 1:
            unstable_tasks += 1
        if outcomes and sum(outcomes) > len(outcomes) / 2:
            passed_tasks += 1
    return ConfigurationScore(
        config_id=config_id,
        critical_failures=sum(cell["critical_failures"] for cell in scorable),
        passed_tasks=passed_tasks,
        unstable_tasks=unstable_tasks,
        pairwise_wins=sum(
            1
            for cell in scorable
            if cell_passed(cell) and cell["pairwise_quality"] == "win"
        ),
        token_usage=sum(cell["token_usage"] for cell in scorable),
        duration_ms=sum(cell["duration_ms"] for cell in scorable),
        excluded_cells=len(matching) - len(scorable),
    )


def compare_configurations(
    values: list[object],
    left_config: str,
    right_config: str,
    task_classes: dict[str, str] | None = None,
) -> dict[str, Any]:
    cells = [validate_cell(value) for value in values]
    by_config: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for config_id in (left_config, right_config):
        config_cells: dict[tuple[str, int], dict[str, Any]] = {}
        for cell in cells:
            if cell["config_id"] != config_id or not is_scorable(cell):
                continue
            key = (cell["task_id"], cell["repeat"])
            if key in config_cells:
                raise ScoringError(
                    f"duplicate cell for {config_id}: {cell['task_id']} repeat {cell['repeat']}"
                )
            config_cells[key] = cell
        by_config[config_id] = config_cells
    paired_keys = set(by_config[left_config]) & set(by_config[right_config])
    if not paired_keys:
        raise ScoringError("configurations have no paired scorable cells")
    paired_cells = [
        by_config[config_id][key]
        for config_id in (left_config, right_config)
        for key in sorted(paired_keys)
    ]
    predictability_presence = {"predictability" in cell for cell in paired_cells}
    if len(predictability_presence) > 1:
        raise ScoringError("paired cells mix legacy and predictability evidence")
    if predictability_presence == {True}:
        return _compare_predictability_configurations(
            by_config,
            paired_keys,
            left_config,
            right_config,
            task_classes,
        )
    left = aggregate_configuration(cells, left_config, paired_keys)
    right = aggregate_configuration(cells, right_config, paired_keys)
    if left.ordering_key < right.ordering_key:
        winner = left_config
    elif right.ordering_key < left.ordering_key:
        winner = right_config
    else:
        winner = "tie"
    return {
        "winner": winner,
        "left": left.to_dict(),
        "right": right.to_dict(),
        "paired_cells": len(paired_keys),
        "unpaired_cells": {
            left_config: len(set(by_config[left_config]) - paired_keys),
            right_config: len(set(by_config[right_config]) - paired_keys),
        },
        "ordering": [
            "critical_failures",
            "passed_tasks",
            "unstable_tasks",
            "pairwise_wins",
            "token_usage",
            "duration_ms",
        ],
    }


def _predictability_configuration_score(
    cells: list[dict[str, Any]],
) -> dict[str, int | float]:
    task_groups = _group_tasks(cells)
    passed_tasks = sum(
        1
        for task_cells in task_groups.values()
        if sum(cell["predictability"]["pass"] for cell in task_cells) > len(task_cells) / 2
    )
    scores = [cell["predictability"]["score"] for cell in cells]
    median_score = statistics.median(scores)
    mad = statistics.median(abs(score - median_score) for score in scores)
    return {
        "hard_fail_cells": sum(
            bool(cell["predictability"]["hard_fail_reasons"]) for cell in cells
        ),
        "passed_tasks": passed_tasks,
        "median_score": median_score,
        "median_absolute_deviation": mad,
    }


def _predictability_ordering(score: dict[str, int | float]) -> tuple[int, int, float, float]:
    return (
        int(score["hard_fail_cells"]),
        -int(score["passed_tasks"]),
        -float(score["median_score"]),
        float(score["median_absolute_deviation"]),
    )


def _predictability_winner(
    left: dict[str, int | float],
    right: dict[str, int | float],
    left_config: str,
    right_config: str,
) -> str:
    left_key = _predictability_ordering(left)
    right_key = _predictability_ordering(right)
    if left_key < right_key:
        return left_config
    if right_key < left_key:
        return right_config
    return "tie"


def _compare_predictability_configurations(
    by_config: dict[str, dict[tuple[str, int], dict[str, Any]]],
    paired_keys: set[tuple[str, int]],
    left_config: str,
    right_config: str,
    task_classes: dict[str, str] | None,
) -> dict[str, Any]:
    paired = {
        config_id: [by_config[config_id][key] for key in sorted(paired_keys)]
        for config_id in (left_config, right_config)
    }
    left = _predictability_configuration_score(paired[left_config])
    right = _predictability_configuration_score(paired[right_config])
    winner: str | None = _predictability_winner(left, right, left_config, right_config)
    class_decisions: dict[str, dict[str, Any]] = {}
    if task_classes is not None:
        missing_classes = sorted({task_id for task_id, _repeat in paired_keys} - task_classes.keys())
        if missing_classes:
            raise ScoringError(
                "predictability task classes are missing: " + ", ".join(missing_classes)
            )
        for task_class in sorted({task_classes[task_id] for task_id, _repeat in paired_keys}):
            class_keys = {
                key for key in paired_keys if task_classes[key[0]] == task_class
            }
            class_left = _predictability_configuration_score(
                [by_config[left_config][key] for key in sorted(class_keys)]
            )
            class_right = _predictability_configuration_score(
                [by_config[right_config][key] for key in sorted(class_keys)]
            )
            class_decisions[task_class] = {
                "winner": _predictability_winner(
                    class_left,
                    class_right,
                    left_config,
                    right_config,
                ),
                "left": class_left,
                "right": class_right,
            }
        non_ties = {
            decision["winner"]
            for decision in class_decisions.values()
            if decision["winner"] != "tie"
        }
        if len(non_ties) > 1:
            winner = None
            status = "class-dependent"
        else:
            status = "complete"
    else:
        status = "complete"
    return {
        "status": status,
        "winner": winner,
        "left": {"config_id": left_config, **left},
        "right": {"config_id": right_config, **right},
        "paired_cells": len(paired_keys),
        "unpaired_cells": {
            left_config: len(set(by_config[left_config]) - paired_keys),
            right_config: len(set(by_config[right_config]) - paired_keys),
        },
        "ordering": [
            "hard_fail_cells",
            "passed_tasks",
            "median_score",
            "median_absolute_deviation",
        ],
        "class_decisions": class_decisions,
    }


def adaptive_repeat_tasks(
    values: list[object],
    config_ids: tuple[str, str],
) -> list[str]:
    """Return baseline disagreements that still lack a complete paired third repeat."""
    cells = [validate_cell(value) for value in values]
    selected = [
        cell
        for cell in cells
        if cell["config_id"] in config_ids and cell["repeat"] <= 3 and is_scorable(cell)
    ]
    tasks = sorted({cell["task_id"] for cell in selected})
    repeats: list[str] = []
    for task_id in tasks:
        needs_repeat = False
        for config_id in config_ids:
            config_cells = sorted(
                (
                    cell
                    for cell in selected
                    if cell["task_id"] == task_id and cell["config_id"] == config_id
                ),
                key=lambda item: item["repeat"],
            )
            if len(config_cells) < 2:
                continue
            baseline = config_cells[:2]
            if all("predictability" in cell for cell in baseline):
                predictability_passes = {
                    cell["predictability"]["pass"] for cell in baseline
                }
                scores = [cell["predictability"]["score"] for cell in baseline]
                hard_failures = {
                    tuple(cell["predictability"]["hard_fail_reasons"])
                    for cell in baseline
                }
                if (
                    len(predictability_passes) > 1
                    or max(scores) - min(scores) >= 10
                    or len(hard_failures) > 1
                ):
                    needs_repeat = True
            else:
                outcomes = {cell_passed(cell) for cell in baseline}
                quality = {cell["pairwise_quality"] for cell in baseline}
                if len(outcomes) > 1 or len(quality) > 1:
                    needs_repeat = True
        third_repeat_complete = all(
            any(
                cell["task_id"] == task_id
                and cell["config_id"] == config_id
                and cell["repeat"] == 3
                for cell in selected
            )
            for config_id in config_ids
        )
        if needs_repeat and not third_repeat_complete:
            repeats.append(task_id)
    return repeats
