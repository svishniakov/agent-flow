#!/usr/bin/env python3
"""Fixture tests for deterministic Task Facts validation."""

from __future__ import annotations

from task_facts import TaskFactsError, normalize_task_facts, primary_task_class


def valid_facts() -> dict:
    return {
        "role": "bun-worker",
        "changes_files": True,
        "repo_count": 1,
        "surfaces": ["storage", "backend"],
        "task_classes": ["local", "public-contract"],
        "public_contract": True,
        "migration": False,
        "external_write": False,
        "production_risk": "normal",
    }


def expect_error(name: str, value: object, needle: str) -> None:
    try:
        normalize_task_facts(value, {"bun-worker", "frontend-worker"})
    except TaskFactsError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"{name}: expected TaskFactsError")
    if needle not in message:
        raise AssertionError(f"{name}: expected {needle!r} in {message!r}")


def test_normalizes_valid_facts() -> None:
    normalized = normalize_task_facts(valid_facts(), {"bun-worker"})
    if normalized["surfaces"] != ["backend", "storage"]:
        raise AssertionError("surfaces were not normalized")
    if normalized["primary_task_class"] != "public-contract":
        raise AssertionError("public-contract precedence was not applied")


def test_high_risk_precedence() -> None:
    facts = valid_facts()
    facts["production_risk"] = "high"
    facts["migration"] = True
    facts["task_classes"] = ["local", "public-contract", "migration", "high-risk"]
    normalized = normalize_task_facts(facts, {"bun-worker"})
    if normalized["primary_task_class"] != "high-risk":
        raise AssertionError("high-risk did not win precedence")
    if primary_task_class({"local", "migration"}) != "migration":
        raise AssertionError("migration precedence failed")


def test_rejects_inconsistent_facts() -> None:
    missing_cross_repo = valid_facts()
    missing_cross_repo["repo_count"] = 2
    expect_error("cross repo class", missing_cross_repo, "cross-repo")

    duplicate_surface = valid_facts()
    duplicate_surface["surfaces"] = ["backend", "backend"]
    expect_error("duplicate surface", duplicate_surface, "duplicates")

    unknown_role = valid_facts()
    unknown_role["role"] = "ghost-worker"
    expect_error("unknown role", unknown_role, "unknown role")

    no_repository = valid_facts()
    no_repository["repo_count"] = 0
    no_repository["task_classes"] = ["local", "public-contract"]
    expect_error("file change without repo", no_repository, "repo_count >= 1")

    extra_field = valid_facts()
    extra_field["model"] = "gpt-5.6-luna"
    expect_error("policy output in facts", extra_field, "unknown fields")


def main() -> int:
    test_normalizes_valid_facts()
    test_high_risk_precedence()
    test_rejects_inconsistent_facts()
    print("PASS task facts fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
