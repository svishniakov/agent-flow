#!/usr/bin/env python3
"""Tests for model-eval planning and certification guards without starting Codex."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from model_eval_manifest import CorpusManifest, corpus_fingerprint


def load_cli_module():
    path = Path(__file__).with_name("model-eval.py")
    spec = importlib.util.spec_from_file_location("agent_flow_model_eval_cli", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load model-eval CLI module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_corpus(root: Path) -> CorpusManifest:
    (root / "tasks").mkdir(parents=True)
    (root / "evaluators").mkdir()
    (root / "tasks" / "prompt.md").write_text("Implement behavior.\n", encoding="utf-8")
    evaluator = {
        "schema_version": 1,
        "commands": [
            {
                "id": "behavior",
                "repository_id": "fixture",
                "scope": "lane",
                "argv": ["python3", "check.py"],
            }
        ],
        "positive_checks": ["behavior passes"],
        "negative_checks": ["base fails"],
    }
    (root / "evaluators" / "evaluator.json").write_text(
        json.dumps(evaluator, indent=2) + "\n",
        encoding="utf-8",
    )
    tasks = [
        {
            "id": task_id,
            "prompt": "tasks/prompt.md",
            "evaluator": "evaluators/evaluator.json",
            "repositories": ["fixture"],
            "revisions": {
                "fixture": {
                    "base": "1" * 40,
                    "gold": "2" * 40,
                }
            },
            "lanes": [
                {"id": "worker", "role": "bun-worker"},
                *(
                    [{"id": "worker-two", "role": "bun-worker"}]
                    if task_id == "task_two"
                    else []
                ),
            ],
        }
        for task_id in ("task_one", "task_two")
    ]
    data = {
        "schema_version": 1,
        "corpus_id": "fixture-corpus",
        "repositories": {"fixture": {"path_env": "FIXTURE_REPO"}},
        "tasks": tasks,
    }
    (root / "manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return CorpusManifest(root=root, data=data, repositories={"fixture": root})


def test_certification_fingerprint_and_schedule(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    fingerprint = corpus_fingerprint(corpus)
    certification = root / "certification.json"
    certification.write_text(
        json.dumps(
            {
                "corpus_id": "fixture-corpus",
                "corpus_fingerprint": fingerprint,
                "certification_harness_fingerprint": cli._certification_harness_fingerprint(),
                "provenance": cli._execution_provenance(corpus, corpus.tasks),
                "status": "certified",
                "tasks": [
                    {"task_id": "task_one", "certified": True},
                    {"task_id": "task_two", "certified": True},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    cli._verify_certification(certification, corpus, corpus.tasks)
    cli._verify_certification(certification, corpus, corpus.tasks[:1])
    configurations = cli._configurations(None)
    schedule = cli._schedule(corpus.tasks, configurations, 2, 42)
    if len(schedule) != 8 or schedule != cli._schedule(corpus.tasks, configurations, 2, 42):
        raise AssertionError("paired schedule is incomplete or nondeterministic")
    for index in range(0, len(schedule), 2):
        pair = schedule[index : index + 2]
        if {(item["task_id"], item["repeat"]) for item in pair} != {
            (pair[0]["task_id"], pair[0]["repeat"])
        }:
            raise AssertionError("configuration pair was split")
        if {item["config_id"] for item in pair} != {"luna-medium", "terra-medium"}:
            raise AssertionError("paired schedule lost a configuration")

    (corpus.root / "tasks" / "prompt.md").write_text("Changed behavior.\n", encoding="utf-8")
    try:
        cli._verify_certification(certification, corpus, corpus.tasks)
    except ValueError as exc:
        if "fingerprint" not in str(exc):
            raise AssertionError(f"wrong stale-certification error: {exc}") from exc
    else:
        raise AssertionError("stale certification was accepted")


def test_certification_drift_is_fail_closed(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    output = root / "certification.json"
    args = argparse.Namespace(corpus=corpus.root, task=["task_one"], output=output)
    original_load = cli.load_corpus
    original_snapshot = cli.snapshot_repository_state
    original_assert = cli.assert_repository_state
    original_certify = cli._certify_snapshot
    source_checks: list[Path] = []
    try:
        cli.load_corpus = lambda *_args, **_kwargs: corpus
        cli.snapshot_repository_state = lambda path: {"path": str(path)}
        cli.assert_repository_state = lambda path, _state: source_checks.append(path)

        def mutate_corpus(_corpus, _task, snapshot, _temp_root):
            if snapshot == "base":
                (corpus.root / "tasks" / "prompt.md").write_text(
                    "Mutated during certification.\n",
                    encoding="utf-8",
                )
                return {"status": "fail", "checks": []}
            return {"status": "pass", "checks": []}

        cli._certify_snapshot = mutate_corpus
        try:
            cli.command_certify(args)
        except ValueError as exc:
            if "before task_one gold" not in str(exc) or "corpus changed" not in str(exc):
                raise AssertionError(f"wrong certification drift error: {exc}") from exc
        else:
            raise AssertionError("mid-certification corpus drift was accepted")
        if output.exists():
            raise AssertionError("drifted certification wrote a valid output")
        if source_checks != [corpus.root]:
            raise AssertionError("source invariant was not checked after certification failure")
    finally:
        cli.load_corpus = original_load
        cli.snapshot_repository_state = original_snapshot
        cli.assert_repository_state = original_assert
        cli._certify_snapshot = original_certify


def test_certification_harness_drift_is_fail_closed(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    output = root / "certification.json"
    args = argparse.Namespace(corpus=corpus.root, task=["task_one"], output=output)
    original_load = cli.load_corpus
    original_snapshot = cli.snapshot_repository_state
    original_assert = cli.assert_repository_state
    original_certify = cli._certify_snapshot
    original_harness = cli._certification_harness_fingerprint
    harness = {"fingerprint": "before"}
    source_checks: list[Path] = []
    try:
        cli.load_corpus = lambda *_args, **_kwargs: corpus
        cli.snapshot_repository_state = lambda path: {"path": str(path)}
        cli.assert_repository_state = lambda path, _state: source_checks.append(path)
        cli._certification_harness_fingerprint = lambda: harness["fingerprint"]

        def mutate_harness(_corpus, _task, snapshot, _temp_root):
            if snapshot == "base":
                harness["fingerprint"] = "after"
                return {"status": "fail", "checks": []}
            return {"status": "pass", "checks": []}

        cli._certify_snapshot = mutate_harness
        try:
            cli.command_certify(args)
        except ValueError as exc:
            if "before task_one gold" not in str(exc) or "harness changed" not in str(exc):
                raise AssertionError(f"wrong certification harness drift error: {exc}") from exc
        else:
            raise AssertionError("mid-certification harness drift was accepted")
        if output.exists():
            raise AssertionError("drifted harness wrote a valid certification")
        if source_checks != [corpus.root]:
            raise AssertionError("source invariant was not checked after harness drift")
    finally:
        cli.load_corpus = original_load
        cli.snapshot_repository_state = original_snapshot
        cli.assert_repository_state = original_assert
        cli._certify_snapshot = original_certify
        cli._certification_harness_fingerprint = original_harness


def test_writable_paths_reject_source_and_symlink_targets(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    blocked = [corpus.root / "output.json"]
    link = root / "source-link"
    link.symlink_to(corpus.root, target_is_directory=True)
    blocked.append(link / "nested" / "output.json")
    for path in blocked:
        try:
            cli._assert_writable_path_outside_sources(corpus, path, "test output")
        except ValueError as exc:
            if "inside source repository" not in str(exc):
                raise AssertionError(f"wrong writable path error: {exc}") from exc
        else:
            raise AssertionError(f"source-backed writable path was accepted: {path}")

    hardlink = root / "hardlink-output.json"
    os.link(corpus.root / "tasks" / "prompt.md", hardlink)
    try:
        cli._assert_writable_path_outside_sources(corpus, hardlink, "test output")
    except ValueError as exc:
        if "hard-linked" not in str(exc):
            raise AssertionError(f"wrong hardlink output error: {exc}") from exc
    else:
        raise AssertionError("hard-linked source file was accepted as writable output")


def test_transitive_harness_dependencies_are_bound(root: Path) -> None:
    cli = load_cli_module()
    if "task_facts.py" not in cli.CERTIFICATION_HARNESS_FILES:
        raise AssertionError("certification fingerprint omits task_facts.py")
    if "agent_config.py" not in cli.EXECUTION_HARNESS_FILES:
        raise AssertionError("execution fingerprint omits agent_config.py")
    if "testdata/model-evals/codex-0.144.1-gpt-5.6-model-catalog.json" not in (
        cli.EXECUTION_HARNESS_ASSETS
    ):
        raise AssertionError("execution fingerprint omits the pinned model catalog")
    dependency = root / "dependency.py"
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_text("VALUE = 1\n", encoding="utf-8")
    before = cli._files_fingerprint([("dependency.py", dependency)])
    dependency.write_text("VALUE = 2\n", encoding="utf-8")
    after = cli._files_fingerprint([("dependency.py", dependency)])
    if before == after:
        raise AssertionError("harness dependency mutation did not change its fingerprint")
    versions = cli._toolchain_versions()
    if any(not isinstance(versions.get(tool), str) for tool in cli.TOOLCHAIN_COMMANDS):
        raise AssertionError(f"toolchain provenance is incomplete: {versions}")
    cli._require_codex_cli_version("codex-cli 0.144.1")
    for version in (None, "codex-cli 0.145.0"):
        try:
            cli._require_codex_cli_version(version)
        except ValueError:
            continue
        raise AssertionError(f"incompatible Codex CLI version was accepted: {version}")


def _cell(task_id: str, config_id: str, repeat: int) -> dict:
    return {
        "task_id": task_id,
        "config_id": config_id,
        "repeat": repeat,
        "status": "pass",
        "selection_status": "exact",
        "critical_failures": 0,
        "lane_statuses": ["pass"],
        "integration_required": False,
        "integration_pass": None,
        "pairwise_quality": "tie",
        "model_attempts": 2 if task_id == "task_two" else 1,
        "token_usage": 10,
        "duration_ms": 20,
    }


def test_score_reconstructs_canonical_plan(root: Path) -> None:
    cli = load_cli_module()
    root.mkdir(parents=True)
    corpus = create_corpus(root / "corpus")
    configuration_args = [
        "luna:gpt-5.6-luna:medium",
        "terra:gpt-5.6-terra:medium",
    ]
    configurations = cli._configurations(configuration_args)
    plan = cli._run_plan(corpus, corpus.tasks, configurations, 2, 42, "complete")
    if plan["scheduled_model_calls"] != 12 or plan["max_model_calls"] != 24:
        raise AssertionError("run plan did not bind single- and multi-lane call budgets")
    for item in plan["schedule"]:
        expected_calls = 2 if item["task_id"] == "task_two" else 1
        if (
            item["scheduled_model_calls"] != expected_calls
            or item["max_model_calls"] != expected_calls * 2
        ):
            raise AssertionError("schedule item lost its per-cell model call bounds")
    cells = [
        _cell(task_id, config_id, repeat)
        for task_id in ("task_one", "task_two")
        for repeat in (1, 2)
        for config_id in ("luna", "terra")
    ]
    results = {
        "status": "complete",
        "corpus_id": plan["corpus_id"],
        "corpus_fingerprint": plan["corpus_fingerprint"],
        "execution_harness_fingerprint": plan["execution_harness_fingerprint"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "cells": cells,
    }
    plan_path = root / "plan.json"
    cells_path = root / "cells.json"
    score_path = root / "score.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    cells_path.write_text(json.dumps(results), encoding="utf-8")
    args = argparse.Namespace(
        corpus=corpus.root,
        cells=cells_path,
        plan=plan_path,
        task=None,
        configuration=configuration_args,
        repeats=2,
        seed=42,
        left="luna",
        right="terra",
        output=score_path,
    )
    original_load = cli.load_corpus
    try:
        cli.load_corpus = lambda *_args, **_kwargs: corpus
        if cli.command_score(args) != 0:
            raise AssertionError("valid score inputs failed")
        score = json.loads(score_path.read_text(encoding="utf-8"))
        if (
            score.get("winner") != "tie"
            or score.get("status") != "decision"
            or score.get("scheduled_cells") != 8
        ):
            raise AssertionError(f"unexpected complete score: {score}")
        if score.get("execution_harness_fingerprint") != plan["execution_harness_fingerprint"]:
            raise AssertionError("score lost its execution harness fingerprint")

        report_args = argparse.Namespace(
            corpus=corpus.root,
            score=score_path,
            plan=plan_path,
            cells=cells_path,
            output=None,
        )
        report_stdout = io.StringIO()
        with redirect_stdout(report_stdout):
            cli.command_report(report_args)
        if "Winner: `tie`" not in report_stdout.getvalue():
            raise AssertionError("validated score did not render a decision report")

        tampered_score = dict(score)
        tampered_score["winner"] = "luna"
        score_path.write_text(json.dumps(tampered_score), encoding="utf-8")
        try:
            cli.command_report(report_args)
        except ValueError as exc:
            if "does not match" not in str(exc):
                raise AssertionError(f"wrong tampered report error: {exc}") from exc
        else:
            raise AssertionError("report trusted a manually changed winner")
        score_path.write_text(json.dumps(score), encoding="utf-8")

        foreign_score = dict(score)
        foreign_score["corpus_fingerprint"] = "foreign"
        score_path.write_text(json.dumps(foreign_score), encoding="utf-8")
        try:
            cli.command_report(report_args)
        except ValueError as exc:
            if "does not match" not in str(exc):
                raise AssertionError(f"wrong foreign report error: {exc}") from exc
        else:
            raise AssertionError("report trusted a foreign score")
        score_path.write_text(json.dumps(score), encoding="utf-8")

        one_repeat_plan = cli._run_plan(
            corpus,
            corpus.tasks,
            configurations,
            1,
            42,
            "complete",
        )
        one_repeat_cells = [cell for cell in cells if cell["repeat"] == 1]
        plan_path.write_text(json.dumps(one_repeat_plan), encoding="utf-8")
        cells_path.write_text(
            json.dumps(
                {
                    **results,
                    "plan_fingerprint": one_repeat_plan["plan_fingerprint"],
                    "cells": one_repeat_cells,
                }
            ),
            encoding="utf-8",
        )
        args.repeats = 1
        cli.command_score(args)
        baseline_score = json.loads(score_path.read_text(encoding="utf-8"))
        if (
            baseline_score.get("winner") is not None
            or baseline_score.get("status") != "insufficient-baseline"
        ):
            raise AssertionError(f"single repeat produced a decision: {baseline_score}")

        unstable_cells = [dict(value) for value in cells]
        unstable = next(
            value
            for value in unstable_cells
            if value["task_id"] == "task_one"
            and value["config_id"] == "luna"
            and value["repeat"] == 2
        )
        unstable["status"] = "fail"
        unstable["lane_statuses"] = ["fail"]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        cells_path.write_text(
            json.dumps({**results, "cells": unstable_cells}),
            encoding="utf-8",
        )
        args.repeats = 2
        cli.command_score(args)
        repeat_score = json.loads(score_path.read_text(encoding="utf-8"))
        if repeat_score.get("winner") is not None or repeat_score.get("status") != "needs-repeats":
            raise AssertionError(f"unresolved baseline produced a decision: {repeat_score}")
        if repeat_score.get("adaptive_repeat_tasks") != ["task_one"]:
            raise AssertionError(f"wrong third-repeat request: {repeat_score}")

        third_plan = cli._run_plan(
            corpus,
            corpus.tasks,
            configurations,
            3,
            42,
            "complete",
        )
        third_cells = [
            _cell(task_id, config_id, repeat)
            for task_id in ("task_one", "task_two")
            for repeat in (1, 2, 3)
            for config_id in ("luna", "terra")
        ]
        unstable_third = next(
            value
            for value in third_cells
            if value["task_id"] == "task_one"
            and value["config_id"] == "luna"
            and value["repeat"] == 2
        )
        unstable_third["status"] = "fail"
        unstable_third["lane_statuses"] = ["fail"]
        plan_path.write_text(json.dumps(third_plan), encoding="utf-8")
        cells_path.write_text(
            json.dumps(
                {
                    **results,
                    "plan_fingerprint": third_plan["plan_fingerprint"],
                    "cells": third_cells,
                }
            ),
            encoding="utf-8",
        )
        args.repeats = 3
        cli.command_score(args)
        resolved_score = json.loads(score_path.read_text(encoding="utf-8"))
        if resolved_score.get("status") != "decision" or resolved_score.get(
            "adaptive_repeat_tasks"
        ):
            raise AssertionError(f"paired third repeat did not resolve decision: {resolved_score}")

        partial_tasks = [corpus.tasks[0]]
        partial_plan = cli._run_plan(
            corpus,
            partial_tasks,
            configurations,
            2,
            42,
            "complete",
        )
        partial_cells = [cell for cell in cells if cell["task_id"] == "task_one"]
        plan_path.write_text(json.dumps(partial_plan), encoding="utf-8")
        cells_path.write_text(
            json.dumps(
                {
                    **results,
                    "plan_fingerprint": partial_plan["plan_fingerprint"],
                    "cells": partial_cells,
                }
            ),
            encoding="utf-8",
        )
        args.task = ["task_one"]
        args.repeats = 2
        cli.command_score(args)
        partial_score = json.loads(score_path.read_text(encoding="utf-8"))
        if partial_score.get("winner") is not None or partial_score.get("status") != "partial-coverage":
            raise AssertionError(f"partial corpus produced a decision: {partial_score}")

        args.task = None
        try:
            cli.command_score(args)
        except cli.ScoringError as exc:
            if "canonical run plan" not in str(exc):
                raise AssertionError(f"wrong subset binding error: {exc}") from exc
        else:
            raise AssertionError("score trusted a partial plan without explicit task selection")
    finally:
        cli.load_corpus = original_load


def test_adaptive_and_recovery_plans_are_bound(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    configurations = cli._configurations(
        ["luna:gpt-5.6-luna:medium", "terra:gpt-5.6-terra:medium"]
    )
    base_plan = cli._run_plan(corpus, corpus.tasks, configurations, 2, 42, "complete")
    base_cells = [
        _cell(task_id, config_id, repeat)
        for task_id in ("task_one", "task_two")
        for repeat in (1, 2)
        for config_id in ("luna", "terra")
    ]
    next(
        cell
        for cell in base_cells
        if cell["task_id"] == "task_one"
        and cell["config_id"] == "luna"
        and cell["repeat"] == 2
    ).update({"status": "fail", "lane_statuses": ["fail"]})
    base_results = {
        "status": "complete",
        "corpus_id": base_plan["corpus_id"],
        "corpus_fingerprint": base_plan["corpus_fingerprint"],
        "execution_harness_fingerprint": base_plan["execution_harness_fingerprint"],
        "plan_fingerprint": base_plan["plan_fingerprint"],
        "cells": base_cells,
    }
    base_score = cli._score_result(base_cells, base_plan, "luna", "terra")
    if base_score["adaptive_repeat_tasks"] != ["task_one"]:
        raise AssertionError("fixture did not select one adaptive task")
    adaptive_plan = cli._sparse_run_plan(
        corpus,
        corpus.tasks[:1],
        configurations,
        [("task_one", "luna", 3), ("task_one", "terra", 3)],
        43,
        "complete",
        "adaptive",
        base_plan,
        base_results,
        base_score,
    )
    if adaptive_plan["scheduled_model_calls"] != 2 or adaptive_plan["repeats"] != 3:
        raise AssertionError("adaptive plan did not schedule only paired repeat 3")
    adaptive_cells = [_cell("task_one", config_id, 3) for config_id in ("luna", "terra")]
    adaptive_results = {
        "status": "complete",
        "corpus_id": adaptive_plan["corpus_id"],
        "corpus_fingerprint": adaptive_plan["corpus_fingerprint"],
        "execution_harness_fingerprint": adaptive_plan["execution_harness_fingerprint"],
        "plan_fingerprint": adaptive_plan["plan_fingerprint"],
        "cells": adaptive_cells,
    }
    if len(cli.validate_score_inputs(adaptive_results, adaptive_plan, adaptive_plan)) != 2:
        raise AssertionError("valid sparse adaptive run was rejected")
    adaptive_plan_path = root / "adaptive-plan.json"
    adaptive_cells_path = root / "adaptive-cells.json"
    adaptive_plan_path.write_text(json.dumps(adaptive_plan), encoding="utf-8")
    adaptive_cells_path.write_text(json.dumps(adaptive_results), encoding="utf-8")
    merged, accounting = cli._prepare_score_cells(
        corpus,
        corpus.tasks,
        configurations,
        base_results,
        base_plan,
        base_plan,
        "luna",
        "terra",
        adaptive_plan_path,
        adaptive_cells_path,
        None,
    )
    if len(merged) != 10 or accounting["scheduled_model_calls"] != 14:
        raise AssertionError("adaptive cells and call budgets were not merged canonically")

    failed_parent_cells = json.loads(json.dumps(base_cells))
    failed = failed_parent_cells[0]
    failed.update(
        {
            "status": "infrastructure-error",
            "selection_status": "unverified",
            "lane_statuses": ["infrastructure-error"],
            "model_attempts": 0,
        }
    )
    failed_parent = {**base_results, "cells": failed_parent_cells}
    recovery_plan = cli._sparse_run_plan(
        corpus,
        corpus.tasks[:1],
        configurations,
        [(failed["task_id"], failed["config_id"], failed["repeat"])],
        44,
        "complete",
        "recovery",
        base_plan,
        failed_parent,
    )
    recovered_cell = _cell(failed["task_id"], failed["config_id"], failed["repeat"])
    recovery_results = {
        "status": "complete",
        "corpus_id": recovery_plan["corpus_id"],
        "corpus_fingerprint": recovery_plan["corpus_fingerprint"],
        "execution_harness_fingerprint": recovery_plan["execution_harness_fingerprint"],
        "plan_fingerprint": recovery_plan["plan_fingerprint"],
        "cells": [recovered_cell],
    }
    recovery_root = root / "recovery-run"
    recovery_root.mkdir()
    (recovery_root / "plan.json").write_text(json.dumps(recovery_plan), encoding="utf-8")
    (recovery_root / "cells.json").write_text(json.dumps(recovery_results), encoding="utf-8")
    validated_parent = cli.validate_score_inputs(
        failed_parent,
        base_plan,
        base_plan,
        allow_infrastructure=True,
    )
    replaced, consumed = cli._apply_recovery_runs(
        base_plan,
        failed_parent,
        validated_parent,
        [(recovery_plan, recovery_results)],
    )
    if consumed != [recovery_plan] or any(
        cell["status"] == "infrastructure-error" for cell in replaced
    ):
        raise AssertionError("recovery did not replace exactly the parent infrastructure cell")
    recovered, recovery_accounting = cli._prepare_score_cells(
        corpus,
        corpus.tasks,
        configurations,
        failed_parent,
        base_plan,
        base_plan,
        "luna",
        "terra",
        None,
        None,
        [recovery_root],
    )
    if any(cell["status"] == "infrastructure-error" for cell in recovered):
        raise AssertionError("score merge retained a recovered infrastructure cell")
    if recovery_accounting["observed_model_attempts"] != 12:
        raise AssertionError("recovery accounting discarded original or recovery attempts")
    try:
        cli._apply_recovery_runs(
            base_plan,
            failed_parent,
            validated_parent,
            [(recovery_plan, recovery_results), (recovery_plan, recovery_results)],
        )
    except ValueError as exc:
        if "same cell" not in str(exc):
            raise AssertionError(f"wrong duplicate recovery error: {exc}") from exc
    else:
        raise AssertionError("duplicate recovery replacement was accepted")


def test_predictability_plan_counts_logical_turns(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus")
    response = corpus.root / "tasks" / "architect-response.md"
    response.write_text("decision_id: fixture\n", encoding="utf-8")
    corpus.tasks[0]["predictability"] = {
        "ambiguity": {
            "decision_id": "fixture",
            "architect_response": "tasks/architect-response.md",
        }
    }
    configurations = cli._configurations(
        ["luna:gpt-5.6-luna:medium", "terra:gpt-5.6-terra:medium"]
    )
    plan = cli._run_plan(corpus, corpus.tasks, configurations, 2, 42, "complete")
    if plan["scheduled_model_calls"] != 16 or plan["max_model_calls"] != 32:
        raise AssertionError("ambiguity logical turns were omitted from the model-call budget")
    task_one = [item for item in plan["schedule"] if item["task_id"] == "task_one"]
    if any(item["scheduled_model_calls"] != 2 for item in task_one):
        raise AssertionError("ambiguity schedule item did not reserve two logical turns")


def _run_args(root: Path, configurations: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        corpus=root / "unused-corpus",
        certification=root / "unused-certification.json",
        artifacts=root / "artifacts",
        task=["task_one"],
        configuration=configurations,
        repeats=1,
        seed=42,
        temp_root=root / "temporary",
        execute=True,
    )


def test_execute_requires_chatgpt_subscription(root: Path) -> None:
    cli = load_cli_module()
    if cli._normalize_codex_login_status(
        0,
        "",
        "Logged in using ChatGPT\n",
    ) != "chatgpt":
        raise AssertionError("ChatGPT subscription login was not recognized")
    if cli._normalize_codex_login_status(
        0,
        "",
        "Logged in using an API key - sk-sensitive\n",
    ) != "api-key":
        raise AssertionError("API-key login was not normalized safely")
    if cli._normalize_codex_login_status(1, "", "login failed") != "unavailable":
        raise AssertionError("failed login status was accepted")

    cli._require_chatgpt_subscription("chatgpt")
    for mode in ("api-key", "unavailable"):
        try:
            cli._require_chatgpt_subscription(mode)
        except ValueError as exc:
            if "ChatGPT subscription" not in str(exc) or "sk-sensitive" in str(exc):
                raise AssertionError(f"unsafe subscription error: {exc}") from exc
        else:
            raise AssertionError(f"{mode} login was accepted for execution")

    corpus = create_corpus(root / "corpus")
    original_load = cli.load_corpus
    original_tool_version = cli._tool_version
    original_login_status = cli._codex_login_status
    original_verify = cli._verify_certification
    try:
        cli.load_corpus = lambda _path: corpus
        cli._tool_version = lambda tool, arguments: (
            "codex-cli 0.144.1"
            if tool == "codex"
            else original_tool_version(tool, arguments)
        )
        verify_calls: list[str] = []
        cli._verify_certification = lambda *_args: verify_calls.append("verify")
        for mode in ("api-key", "unavailable"):
            case_root = root / mode
            cli._codex_login_status = lambda mode=mode: mode
            try:
                cli.command_run(
                    _run_args(case_root, ["luna:gpt-5.6-luna:medium"])
                )
            except ValueError as exc:
                if "ChatGPT subscription" not in str(exc):
                    raise AssertionError(f"wrong {mode} preflight error: {exc}") from exc
            else:
                raise AssertionError(f"{mode} execution reached the run")
            if (case_root / "artifacts").exists():
                raise AssertionError(f"{mode} execution created artifacts before rejection")
        if verify_calls:
            raise AssertionError("subscription rejection happened after certification work")
    finally:
        cli.load_corpus = original_load
        cli._tool_version = original_tool_version
        cli._codex_login_status = original_login_status
        cli._verify_certification = original_verify


def test_run_rechecks_fingerprints(root: Path) -> None:
    cli = load_cli_module()
    corpus = create_corpus(root / "corpus-after")
    original_load = cli.load_corpus
    original_verify = cli._verify_certification
    original_runner = cli.run_eval_cell
    original_harness_fingerprint = cli._execution_harness_fingerprint
    original_login_status = cli._codex_login_status
    try:
        cli.load_corpus = lambda _path: corpus
        cli._verify_certification = lambda *_args: {}
        cli._execution_harness_fingerprint = lambda _corpus: "stable-harness"
        cli._codex_login_status = lambda: "chatgpt"
        calls: list[str] = []

        def mutate_after_cell(*args, **kwargs):
            calls.append(kwargs["cell_id"])
            (corpus.root / "tasks" / "prompt.md").write_text(
                "Changed during cell.\n", encoding="utf-8"
            )
            return SimpleNamespace(cell=_cell("task_one", "luna", 1))

        cli.run_eval_cell = mutate_after_cell
        try:
            cli.command_run(
                _run_args(root / "after", ["luna:gpt-5.6-luna:medium"])
            )
        except ValueError as exc:
            if "after suite" not in str(exc) or "corpus changed" not in str(exc):
                raise AssertionError(f"wrong post-suite fingerprint error: {exc}") from exc
        else:
            raise AssertionError("post-suite corpus mutation was accepted")
        if len(calls) != 1:
            raise AssertionError("post-suite check did not run after the only cell")
        partial = json.loads(
            (root / "after" / "artifacts" / "cells.json").read_text(encoding="utf-8")
        )
        if partial.get("status") != "running":
            raise AssertionError("failed snapshot check left scoreable cells")

        corpus = create_corpus(root / "corpus-before")
        cli.load_corpus = lambda _path: corpus
        calls.clear()

        def mutate_before_next(*args, **kwargs):
            calls.append(kwargs["cell_id"])
            if len(calls) == 1:
                (corpus.root / "tasks" / "prompt.md").write_text(
                    "Changed before next cell.\n", encoding="utf-8"
                )
            config_id = args[2].config_id
            return SimpleNamespace(cell=_cell("task_one", config_id, 1))

        cli.run_eval_cell = mutate_before_next
        try:
            cli.command_run(
                _run_args(
                    root / "before",
                    [
                        "luna:gpt-5.6-luna:medium",
                        "terra:gpt-5.6-terra:medium",
                    ],
                )
            )
        except ValueError as exc:
            if "before " not in str(exc) or "corpus changed" not in str(exc):
                raise AssertionError(f"wrong per-cell fingerprint error: {exc}") from exc
        else:
            raise AssertionError("corpus mutation before a later cell was accepted")
        if len(calls) != 1:
            raise AssertionError("second cell started after corpus mutation")

        corpus = create_corpus(root / "corpus-harness")
        cli.load_corpus = lambda _path: corpus
        harness_state = {"value": "before"}
        cli._execution_harness_fingerprint = lambda _corpus: harness_state["value"]

        def mutate_harness(*args, **kwargs):
            harness_state["value"] = "after"
            return SimpleNamespace(cell=_cell("task_one", "luna", 1))

        cli.run_eval_cell = mutate_harness
        try:
            cli.command_run(
                _run_args(root / "harness", ["luna:gpt-5.6-luna:medium"])
            )
        except ValueError as exc:
            if "execution harness changed" not in str(exc):
                raise AssertionError(f"wrong harness fingerprint error: {exc}") from exc
        else:
            raise AssertionError("execution harness mutation was accepted")

        corpus = create_corpus(root / "corpus-complete")
        cli.load_corpus = lambda _path: corpus
        cli._execution_harness_fingerprint = lambda _corpus: "stable-harness"
        cli.run_eval_cell = lambda *args, **kwargs: SimpleNamespace(
            cell=_cell("task_one", "luna", 1)
        )
        complete_root = root / "complete"
        with redirect_stdout(io.StringIO()):
            exit_code = cli.command_run(
                _run_args(complete_root, ["luna:gpt-5.6-luna:medium"])
            )
        if exit_code != 0:
            raise AssertionError("stable fake run did not complete")
        for filename in ("plan.json", "cells.json"):
            document = json.loads(
                (complete_root / "artifacts" / filename).read_text(encoding="utf-8")
            )
            if document.get("status") != "complete":
                raise AssertionError(f"{filename} was not finalized")
        provenance = json.loads(
            (complete_root / "artifacts" / "provenance.json").read_text(encoding="utf-8")
        )
        if provenance.get("codex_login_status") != "chatgpt":
            raise AssertionError("run provenance did not record ChatGPT subscription auth")
        if any(
            not isinstance(provenance.get("toolchain_versions", {}).get(tool), str)
            for tool in cli.TOOLCHAIN_COMMANDS
        ):
            raise AssertionError("run provenance omitted a toolchain version")
        final_plan = json.loads(
            (complete_root / "artifacts" / "plan.json").read_text(encoding="utf-8")
        )
        final_cells = json.loads(
            (complete_root / "artifacts" / "cells.json").read_text(encoding="utf-8")
        )
        if final_plan.get("coverage") != "partial":
            raise AssertionError("selected task subset was not marked as partial coverage")
        if not final_plan.get("plan_fingerprint") or final_cells.get(
            "plan_fingerprint"
        ) != final_plan.get("plan_fingerprint"):
            raise AssertionError("run artifacts were not bound to one plan fingerprint")
    finally:
        cli.load_corpus = original_load
        cli._verify_certification = original_verify
        cli.run_eval_cell = original_runner
        cli._execution_harness_fingerprint = original_harness_fingerprint
        cli._codex_login_status = original_login_status


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-cli-") as raw_root:
        root = Path(raw_root)
        test_certification_fingerprint_and_schedule(root / "certification")
        test_certification_drift_is_fail_closed(root / "certification-drift")
        test_certification_harness_drift_is_fail_closed(root / "certification-harness-drift")
        test_writable_paths_reject_source_and_symlink_targets(root / "writable-paths")
        test_transitive_harness_dependencies_are_bound(root / "harness-dependencies")
        test_score_reconstructs_canonical_plan(root / "score")
        test_adaptive_and_recovery_plans_are_bound(root / "adaptive-recovery")
        test_predictability_plan_counts_logical_turns(root / "predictability-budget")
        test_execute_requires_chatgpt_subscription(root / "subscription-auth")
        test_run_rechecks_fingerprints(root / "run")
    print("PASS model eval CLI tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
