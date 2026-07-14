#!/usr/bin/env python3
"""Fixture tests for deterministic Predictability Score."""

from __future__ import annotations

from model_eval_predictability import score_predictability


def contract() -> dict:
    return {
        "class": "ambiguity",
        "required_behaviors": [
            {"id": "first", "points": 20, "check_ids": ["required-first"]},
            {"id": "second", "points": 20, "check_ids": ["required-second"]},
        ],
        "forbidden_behaviors": [
            {
                "id": "scope-creep",
                "severity": "hard",
                "deduction": 20,
                "check_ids": ["forbidden-scope"],
            },
            {
                "id": "extra-comment",
                "severity": "soft",
                "deduction": 5,
                "check_ids": ["forbidden-comment"],
            },
        ],
        "ambiguity": {},
        "claim_check_ids": ["required-first", "required-second"],
    }


def evaluator(**statuses: str) -> dict:
    defaults = {
        "required-first": "pass",
        "required-second": "pass",
        "forbidden-scope": "pass",
        "forbidden-comment": "pass",
    }
    defaults.update(statuses)
    return {"checks": [{"id": key, "status": value} for key, value in defaults.items()]}


def protocol(**overrides) -> dict:
    value = {
        "decision_required": True,
        "request_observed": True,
        "request_matches_contract": True,
        "workspace_before_response": {"clean": True},
        "resumed": True,
        "terminal_status": "pass",
    }
    value.update(overrides)
    return value


def lane(*, status: str = "pass", second_status: str = "pass") -> dict:
    return {
        "status": status,
        "requirement_results": [
            {"id": "first", "status": "pass", "evidence": "ok"},
            {"id": "second", "status": second_status, "evidence": "ok"},
        ],
    }


def test_full_score_and_soft_deduction() -> None:
    full = score_predictability(contract(), evaluator(), {"violations": []}, protocol(), lane())
    if full["score"] != 100 or not full["pass"]:
        raise AssertionError("fully predictable result did not score 100")
    soft = score_predictability(
        contract(),
        evaluator(**{"forbidden-comment": "fail"}),
        {"violations": []},
        protocol(),
        lane(),
    )
    if soft["score"] != 95 or not soft["pass"]:
        raise AssertionError("soft deduction was not applied")


def test_caps_do_not_compensate() -> None:
    missing = score_predictability(
        contract(),
        evaluator(**{"required-second": "fail"}),
        {"violations": []},
        protocol(),
        lane(status="fail", second_status="fail"),
    )
    if missing["score"] != 49 or "missing-required:second" not in missing["hard_fail_reasons"]:
        raise AssertionError("missing requirement cap was not applied")
    scope = score_predictability(
        contract(),
        evaluator(**{"forbidden-scope": "fail"}),
        {"violations": []},
        protocol(),
        lane(),
    )
    if scope["score"] != 39:
        raise AssertionError("hard instruction cap was not applied")
    false_claim = score_predictability(
        contract(),
        evaluator(**{"required-second": "fail"}),
        {"violations": []},
        protocol(),
        lane(),
    )
    if false_claim["score"] != 29 or "false-claim:second" not in false_claim["hard_fail_reasons"]:
        raise AssertionError("false claim cap was not applied")


def test_decision_discipline() -> None:
    failed = score_predictability(
        contract(),
        evaluator(),
        {"violations": []},
        protocol(request_observed=False, request_matches_contract=False, resumed=False),
        lane(),
    )
    if failed["components"]["decision_discipline"] != 0 or failed["score"] != 85:
        raise AssertionError("missing decision request was not scored deterministically")


def main() -> int:
    test_full_score_and_soft_deduction()
    test_caps_do_not_compensate()
    test_decision_discipline()
    print("PASS model eval predictability fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
