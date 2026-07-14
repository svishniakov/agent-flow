#!/usr/bin/env python3
"""Fixture tests for deterministic model-evaluation scoring."""

from __future__ import annotations

from copy import deepcopy

from model_eval_score import (
    ScoringError,
    adaptive_repeat_tasks,
    cell_passed,
    compare_configurations,
    plan_fingerprint,
    validate_cell,
    validate_score_inputs,
)


def cell(
    task_id: str,
    config_id: str,
    repeat: int,
    *,
    passed: bool = True,
    critical_failures: int = 0,
    pairwise_quality: str = "tie",
    selection_status: str = "exact",
    lanes: list[str] | None = None,
    integration_required: bool = False,
    integration_pass: bool | None = None,
    tokens: int = 100,
    duration_ms: int = 1000,
) -> dict:
    lane_statuses = lanes or (["pass"] if passed else ["fail"])
    return {
        "task_id": task_id,
        "config_id": config_id,
        "repeat": repeat,
        "status": "pass" if passed else "fail",
        "selection_status": selection_status,
        "critical_failures": critical_failures,
        "lane_statuses": lane_statuses,
        "integration_required": integration_required,
        "integration_pass": integration_pass,
        "pairwise_quality": pairwise_quality,
        "model_attempts": len(lane_statuses),
        "token_usage": tokens,
        "duration_ms": duration_ms,
    }


def predictability_cell(
    task_id: str,
    config_id: str,
    repeat: int,
    score: int,
    *,
    hard_fail_reasons: list[str] | None = None,
) -> dict:
    hard = hard_fail_reasons or []
    value = cell(task_id, config_id, repeat, passed=score >= 80 and not hard)
    value["predictability"] = {
        "schema_version": 1,
        "score": score,
        "pass": score >= 80 and not hard,
        "components": {
            "requirements": 40,
            "instruction_fidelity": 35,
            "decision_discipline": 15,
            "claim_fidelity": 10,
        },
        "hard_fail_reasons": hard,
        "soft_deviations": [],
        "evidence": [],
    }
    return value


def test_critical_failure_dominates_pass_count() -> None:
    values = [
        cell("task-a", "luna", 1, critical_failures=1),
        cell("task-a", "luna", 2),
        cell("task-b", "luna", 1),
        cell("task-b", "luna", 2),
        cell("task-a", "terra", 1, passed=False),
        cell("task-a", "terra", 2, passed=False),
        cell("task-b", "terra", 1),
        cell("task-b", "terra", 2),
    ]
    result = compare_configurations(values, "luna", "terra")
    if result["winner"] != "terra":
        raise AssertionError("critical failures must dominate task pass count")


def test_stability_precedes_quality_and_cost() -> None:
    values = [
        cell("task-a", "luna", 1, pairwise_quality="win", tokens=10),
        cell("task-a", "luna", 2, passed=False, pairwise_quality="win", tokens=10),
        cell("task-a", "luna", 3, pairwise_quality="win", tokens=10),
        cell("task-a", "terra", 1, pairwise_quality="loss", tokens=100),
        cell("task-a", "terra", 2, pairwise_quality="loss", tokens=100),
        cell("task-a", "terra", 3, pairwise_quality="loss", tokens=100),
    ]
    result = compare_configurations(values, "luna", "terra")
    if result["winner"] != "terra":
        raise AssertionError("stability must precede pairwise quality and token usage")


def test_cross_repo_and_substitution_rules() -> None:
    cross_repo = validate_cell(
        cell(
            "cross",
            "luna",
            1,
            lanes=["pass", "fail"],
            integration_required=True,
            integration_pass=True,
        )
    )
    if cell_passed(cross_repo):
        raise AssertionError("one passing lane must not pass a cross-repo cell")

    substituted = validate_cell(cell("task", "luna", 1, selection_status="substituted"))
    if cell_passed(substituted):
        raise AssertionError("substituted cells must not score for the target config")


def test_adaptive_repeat_selection() -> None:
    values = [
        cell("unstable", "luna", 1),
        cell("unstable", "luna", 2, passed=False),
        cell("unstable", "terra", 1),
        cell("unstable", "terra", 2),
        cell("quality", "luna", 1, pairwise_quality="win"),
        cell("quality", "luna", 2, pairwise_quality="loss"),
        cell("quality", "terra", 1, pairwise_quality="loss"),
        cell("quality", "terra", 2, pairwise_quality="win"),
        cell("stable", "luna", 1),
        cell("stable", "luna", 2),
        cell("stable", "terra", 1),
        cell("stable", "terra", 2),
    ]
    repeats = adaptive_repeat_tasks(values, ("luna", "terra"))
    if repeats != ["quality", "unstable"]:
        raise AssertionError(f"unexpected adaptive repeats: {repeats}")

    values.extend(
        [
            cell("quality", "luna", 3, pairwise_quality="win"),
            cell("quality", "terra", 3, pairwise_quality="loss"),
            cell("unstable", "luna", 3),
            cell("unstable", "terra", 3),
        ]
    )
    if adaptive_repeat_tasks(values, ("luna", "terra")):
        raise AssertionError("a complete paired third repeat must resolve the request")


def test_predictability_ordering_and_class_conflict() -> None:
    median_values = [
        predictability_cell("task-a", "luna", 1, 90),
        predictability_cell("task-a", "luna", 2, 100),
        predictability_cell("task-a", "terra", 1, 94),
        predictability_cell("task-a", "terra", 2, 94),
    ]
    median_result = compare_configurations(median_values, "luna", "terra")
    if median_result["winner"] != "luna":
        raise AssertionError("predictability median did not precede MAD")
    if median_result["left"]["median_absolute_deviation"] != 5:
        raise AssertionError("predictability MAD is incorrect")

    class_values = [
        predictability_cell("exact", "luna", 1, 100),
        predictability_cell("exact", "luna", 2, 100),
        predictability_cell("exact", "terra", 1, 90),
        predictability_cell("exact", "terra", 2, 90),
        predictability_cell("contract", "luna", 1, 80),
        predictability_cell("contract", "luna", 2, 80),
        predictability_cell("contract", "terra", 1, 100),
        predictability_cell("contract", "terra", 2, 100),
    ]
    class_result = compare_configurations(
        class_values,
        "luna",
        "terra",
        {"exact": "exact-spec", "contract": "contract"},
    )
    if class_result["status"] != "class-dependent" or class_result["winner"] is not None:
        raise AssertionError("conflicting class winners were collapsed into one winner")


def test_predictability_adaptive_repeat_selection() -> None:
    values = [
        predictability_cell("score-range", "luna", 1, 95),
        predictability_cell("score-range", "luna", 2, 84),
        predictability_cell("score-range", "terra", 1, 90),
        predictability_cell("score-range", "terra", 2, 90),
        predictability_cell("hard-change", "luna", 1, 39, hard_fail_reasons=["scope"]),
        predictability_cell("hard-change", "luna", 2, 39, hard_fail_reasons=["claim"]),
        predictability_cell("hard-change", "terra", 1, 100),
        predictability_cell("hard-change", "terra", 2, 100),
    ]
    repeats = adaptive_repeat_tasks(values, ("luna", "terra"))
    if repeats != ["hard-change", "score-range"]:
        raise AssertionError(f"unexpected predictability adaptive repeats: {repeats}")


def test_infrastructure_gap_cannot_reduce_compared_cost() -> None:
    luna_infrastructure = cell("task-a", "luna", 2, tokens=0, duration_ms=0)
    luna_infrastructure["status"] = "infrastructure-error"
    luna_infrastructure["lane_statuses"] = ["infrastructure-error"]
    values = [
        cell("task-a", "luna", 1),
        luna_infrastructure,
        cell("task-a", "terra", 1),
        cell("task-a", "terra", 2),
    ]
    result = compare_configurations(values, "luna", "terra")
    if result["winner"] != "tie":
        raise AssertionError("an infrastructure gap must not win through lower token usage")
    if result["paired_cells"] != 1 or result["unpaired_cells"] != {"luna": 0, "terra": 1}:
        raise AssertionError(f"paired coverage was not reported correctly: {result}")


def _score_documents() -> tuple[dict, dict, dict]:
    cells = [
        cell("task-a", "luna", 1),
        cell("task-a", "terra", 1),
        cell("task-a", "luna", 2),
        cell("task-a", "terra", 2),
    ]
    plan = {
        "status": "complete",
        "corpus_id": "corpus",
        "corpus_fingerprint": "corpus-fingerprint",
        "execution_harness_fingerprint": "harness-fingerprint",
        "coverage": "full",
        "task_ids": ["task-a"],
        "configurations": [
            {"config_id": "luna", "model": "model-luna", "reasoning_effort": "medium"},
            {"config_id": "terra", "model": "model-terra", "reasoning_effort": "medium"},
        ],
        "seed": 42,
        "repeats": 2,
        "scheduled_model_calls": 4,
        "max_model_calls": 8,
        "schedule": [
            {
                "task_id": item["task_id"],
                "config_id": item["config_id"],
                "model": f"model-{item['config_id']}",
                "reasoning_effort": "medium",
                "repeat": item["repeat"],
                "cell_id": f"{item['task_id']}-{item['config_id']}-r{item['repeat']}",
                "scheduled_model_calls": 1,
                "max_model_calls": 2,
            }
            for item in cells
        ],
    }
    plan["plan_fingerprint"] = plan_fingerprint(plan)
    results = {
        "status": "complete",
        "corpus_id": plan["corpus_id"],
        "corpus_fingerprint": plan["corpus_fingerprint"],
        "execution_harness_fingerprint": plan["execution_harness_fingerprint"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "cells": cells,
    }
    return results, plan, deepcopy(plan)


def _expect_score_error(
    results: object,
    plan: object,
    expected_plan: object,
    message: str,
) -> None:
    try:
        validate_score_inputs(results, plan, expected_plan)
    except ScoringError as exc:
        if message not in str(exc):
            raise AssertionError(f"unexpected score guard error: {exc}") from exc
    else:
        raise AssertionError(f"score guard accepted invalid inputs: {message}")


def test_complete_envelope_and_schedule_are_required() -> None:
    results, plan, expected_plan = _score_documents()
    if len(validate_score_inputs(results, plan, expected_plan)) != 4:
        raise AssertionError("complete score inputs were rejected")

    _expect_score_error(results["cells"], plan, expected_plan, "full result envelope")

    stale = deepcopy(results)
    stale["corpus_fingerprint"] = "changed"
    _expect_score_error(stale, plan, expected_plan, "corpus_fingerprint")

    stale_harness = deepcopy(results)
    stale_harness["execution_harness_fingerprint"] = "changed"
    _expect_score_error(stale_harness, plan, expected_plan, "execution_harness_fingerprint")

    running = deepcopy(results)
    running["status"] = "running"
    _expect_score_error(running, plan, expected_plan, "not complete")

    running_plan = deepcopy(plan)
    running_plan["status"] = "running"
    _expect_score_error(results, running_plan, expected_plan, "run plan is not complete")

    missing = deepcopy(results)
    missing["cells"].pop()
    _expect_score_error(missing, plan, expected_plan, "missing scheduled cells")

    extra = deepcopy(results)
    extra["cells"].append(cell("task-extra", "luna", 1))
    _expect_score_error(extra, plan, expected_plan, "unscheduled cells")

    duplicate = deepcopy(results)
    duplicate["cells"].append(deepcopy(duplicate["cells"][0]))
    _expect_score_error(duplicate, plan, expected_plan, "duplicate cells")

    infrastructure = deepcopy(results)
    infrastructure["cells"][0]["status"] = "infrastructure-error"
    infrastructure["cells"][0]["lane_statuses"] = ["infrastructure-error"]
    _expect_score_error(infrastructure, plan, expected_plan, "infrastructure cells")

    unverified = deepcopy(results)
    unverified["cells"][0]["selection_status"] = "unverified"
    _expect_score_error(unverified, plan, expected_plan, "selection is unverified")

    equivalent = deepcopy(results)
    equivalent["cells"][0]["selection_status"] = "equivalent"
    _expect_score_error(equivalent, plan, expected_plan, "selection is unverified")

    under_counted = deepcopy(results)
    under_counted["cells"][0]["model_attempts"] = 0
    _expect_score_error(under_counted, plan, expected_plan, "scheduled bounds")

    over_budget = deepcopy(results)
    over_budget["cells"][0]["model_attempts"] = 6
    _expect_score_error(over_budget, plan, expected_plan, "scheduled bounds")

    redistributed = deepcopy(results)
    for value, attempts in zip(redistributed["cells"], (2, 2, 0, 0)):
        value["model_attempts"] = attempts
    _expect_score_error(redistributed, plan, expected_plan, "scheduled bounds")


def test_canonical_plan_integrity_is_required() -> None:
    results, plan, expected_plan = _score_documents()

    truncated_plan = deepcopy(plan)
    truncated_plan["task_ids"] = ["task-a"]
    truncated_plan["schedule"] = truncated_plan["schedule"][:2]
    truncated_plan["repeats"] = 1
    truncated_plan["scheduled_model_calls"] = 2
    truncated_plan["max_model_calls"] = 4
    truncated_plan["plan_fingerprint"] = plan_fingerprint(truncated_plan)
    truncated_results = deepcopy(results)
    truncated_results["cells"] = truncated_results["cells"][:2]
    truncated_results["plan_fingerprint"] = truncated_plan["plan_fingerprint"]
    _expect_score_error(
        truncated_results,
        truncated_plan,
        expected_plan,
        "does not match canonical run plan",
    )

    for field, replacement, error in (
        ("model", "foreign-model", "does not match configuration"),
        ("reasoning_effort", "low", "does not match configuration"),
        ("cell_id", "foreign-cell-id", "canonical run plan"),
    ):
        tampered = deepcopy(plan)
        tampered["schedule"][0][field] = replacement
        tampered["plan_fingerprint"] = plan_fingerprint(tampered)
        tampered_results = deepcopy(results)
        tampered_results["plan_fingerprint"] = tampered["plan_fingerprint"]
        _expect_score_error(
            tampered_results,
            tampered,
            expected_plan,
            error,
        )

    removed_pair = deepcopy(plan)
    removed_pair["schedule"] = removed_pair["schedule"][2:]
    removed_pair["plan_fingerprint"] = plan_fingerprint(removed_pair)
    _expect_score_error(results, removed_pair, expected_plan, "schedule is missing cells")

    bad_fingerprint = deepcopy(plan)
    bad_fingerprint["plan_fingerprint"] = "0" * 64
    _expect_score_error(
        results,
        bad_fingerprint,
        expected_plan,
        "plan_fingerprint does not match",
    )


def main() -> int:
    test_critical_failure_dominates_pass_count()
    test_stability_precedes_quality_and_cost()
    test_cross_repo_and_substitution_rules()
    test_adaptive_repeat_selection()
    test_predictability_ordering_and_class_conflict()
    test_predictability_adaptive_repeat_selection()
    test_infrastructure_gap_cannot_reduce_compared_cost()
    test_complete_envelope_and_schedule_are_required()
    test_canonical_plan_integrity_is_required()
    print("PASS model eval score fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
