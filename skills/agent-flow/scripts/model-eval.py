#!/usr/bin/env python3
"""Validate Agent Flow model-eval corpora and score completed cells."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_config import ALLOWED_MODELS, ALLOWED_REASONING_EFFORTS
from model_eval_adapter import CODEX_CLI_VERSION, MAX_LANE_ATTEMPTS
from model_eval_evaluator import EvaluatorError, run_evaluator
from model_eval_manifest import ManifestError, corpus_fingerprint, load_corpus, read_git_blob
from model_eval_predictability import score_predictability
from model_eval_runner import (
    EvalConfiguration,
    RunnerError,
    cell_identifier,
    run_eval_cell,
)
from model_eval_sandbox import SandboxConfigError
from model_eval_score import (
    ScoringError,
    adaptive_repeat_tasks,
    compare_configurations,
    plan_fingerprint,
    validate_score_inputs,
)
from model_eval_workspace import (
    WorkspaceError,
    apply_gold_patch,
    assert_repository_state,
    cleanup_synthetic_workspace,
    create_synthetic_workspace,
    initialize_temp_root,
    snapshot_repository_state,
)


CONFIG_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_CONFIGURATIONS = (
    EvalConfiguration("luna-medium", "gpt-5.6-luna", "medium"),
    EvalConfiguration("terra-medium", "gpt-5.6-terra", "medium"),
)
CERTIFICATION_HARNESS_FILES = (
    "model-eval.py",
    "model_eval_evaluator.py",
    "model_eval_manifest.py",
    "model_eval_predictability.py",
    "model_eval_process.py",
    "model_eval_sandbox.py",
    "model_eval_workspace.py",
    "task_facts.py",
)
EXECUTION_HARNESS_FILES = CERTIFICATION_HARNESS_FILES + (
    "agent_config.py",
    "model_eval_adapter.py",
    "model_eval_runner.py",
    "model_eval_score.py",
)
EXECUTION_HARNESS_ASSETS = (
    "testdata/model-evals/agent-output.schema.json",
    "testdata/model-evals/agent-output-predictability.schema.json",
    "testdata/model-evals/codex-0.144.1-gpt-5.6-model-catalog.json",
)
TOOLCHAIN_COMMANDS = {
    "bun": ("--version",),
    "codex": ("--version",),
    "git": ("--version",),
    "go": ("version",),
    "node": ("--version",),
    "pnpm": ("--version",),
    "python3": ("--version",),
}
LOCKFILE_CANDIDATES = (
    "bun.lock",
    "bun.lockb",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)
CODEX_CHATGPT_LOGIN = "Logged in using ChatGPT"
CODEX_API_KEY_LOGIN_PREFIX = "Logged in using an API key"


def _toolchain_path() -> str:
    directories: list[str] = []
    for tool in TOOLCHAIN_COMMANDS:
        executable = shutil.which(tool)
        if executable is None:
            continue
        for directory in (
            Path(executable).expanduser().absolute().parent,
            Path(executable).resolve().parent,
        ):
            value = str(directory)
            if value not in directories:
                directories.append(value)
    for value in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if value not in directories:
            directories.append(value)
    return os.pathsep.join(directories)


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label}: {path}: {exc}") from exc


def _files_fingerprint(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for label, path in sorted(files):
        payload = path.read_bytes()
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _tool_version(tool: str, arguments: tuple[str, ...]) -> str | None:
    executable = shutil.which(tool)
    if executable is None:
        return None
    environment = {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": _toolchain_path(),
    }
    try:
        result = subprocess.run(
            [executable, *arguments],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0][:500] if output else None


def _toolchain_versions() -> dict[str, str | None]:
    return {
        tool: _tool_version(tool, arguments)
        for tool, arguments in sorted(TOOLCHAIN_COMMANDS.items())
    }


def _require_codex_cli_version(actual: str | None) -> None:
    expected = f"codex-cli {CODEX_CLI_VERSION}"
    if actual != expected:
        raise ValueError(f"model eval requires {expected}, found {actual or 'unavailable'}")


def _normalize_codex_login_status(
    returncode: int,
    stdout: str,
    stderr: str,
) -> str:
    if returncode:
        return "unavailable"
    lines = [line.strip() for line in (stdout + "\n" + stderr).splitlines() if line.strip()]
    if CODEX_CHATGPT_LOGIN in lines:
        return "chatgpt"
    if any(line.startswith(CODEX_API_KEY_LOGIN_PREFIX) for line in lines):
        return "api-key"
    return "unavailable"


def _codex_login_status() -> str:
    executable = shutil.which("codex")
    if executable is None:
        return "unavailable"
    environment = {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": _toolchain_path(),
    }
    if "CODEX_HOME" in os.environ:
        environment["CODEX_HOME"] = os.environ["CODEX_HOME"]
    try:
        result = subprocess.run(
            [executable, "login", "status"],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return _normalize_codex_login_status(
        result.returncode,
        result.stdout or "",
        result.stderr or "",
    )


def _require_chatgpt_subscription(status: str) -> None:
    if status != "chatgpt":
        raise ValueError(
            "model eval execution requires Codex login through a ChatGPT subscription; "
            f"found {status}"
        )


def _fingerprint_with_toolchain(fingerprint: str) -> str:
    digest = hashlib.sha256()
    digest.update(fingerprint.encode("ascii"))
    digest.update(b"\0")
    digest.update(
        json.dumps(
            _toolchain_versions(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _certification_harness_fingerprint() -> str:
    scripts = Path(__file__).resolve().parent
    return _fingerprint_with_toolchain(
        _files_fingerprint([(name, scripts / name) for name in CERTIFICATION_HARNESS_FILES])
    )


def _execution_harness_fingerprint(corpus) -> str:
    scripts = Path(__file__).resolve().parent
    skill_root = scripts.parent
    files = [(name, scripts / name) for name in EXECUTION_HARNESS_FILES]
    files.extend((asset, skill_root / asset) for asset in EXECUTION_HARNESS_ASSETS)
    roles = sorted({lane["role"] for task in corpus.tasks for lane in task["lanes"]})
    files.extend((f"agents/{role}.md", skill_root / "agents" / f"{role}.md") for role in roles)
    return _fingerprint_with_toolchain(_files_fingerprint(files))


def _lockfile_digests(corpus, tasks: list[dict]) -> dict[str, dict[str, str]]:
    snapshots: dict[tuple[str, str], dict[str, str]] = {}
    for task in tasks:
        for repository_id in task["repositories"]:
            revision_contract = task["revisions"][repository_id]
            for revision in (
                revision_contract["base"],
                *([revision_contract["gold"]] if "gold" in revision_contract else []),
            ):
                key = (repository_id, revision)
                if key in snapshots:
                    continue
                files: dict[str, str] = {}
                for path in LOCKFILE_CANDIDATES:
                    blob = read_git_blob(corpus.repositories[repository_id], revision, path)
                    if blob.returncode == 0:
                        files[path] = hashlib.sha256(blob.stdout).hexdigest()
                snapshots[key] = files
    return {
        f"{repository_id}:{revision}": files
        for (repository_id, revision), files in sorted(snapshots.items())
    }


def _execution_provenance(corpus, tasks: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "toolchain_versions": _toolchain_versions(),
        "codex_login_status": _codex_login_status(),
        "lockfile_digests": _lockfile_digests(corpus, tasks),
    }


def _source_repository_roots(corpus) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for repository_id, config in corpus.data["repositories"].items():
        resolved = corpus.repositories.get(repository_id)
        if resolved is None:
            raw_path = os.environ.get(config["path_env"])
            if not raw_path:
                raise ValueError(
                    f"repository {repository_id} requires environment variable "
                    f"{config['path_env']} to validate writable paths"
                )
            resolved = Path(raw_path).expanduser().resolve()
        roots[repository_id] = resolved.resolve()
    return roots


def _assert_writable_path_outside_sources(corpus, path: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError(f"{label} path must not be a symlink")
    resolved = expanded.resolve()
    for repository_id, repository in _source_repository_roots(corpus).items():
        try:
            resolved.relative_to(repository)
        except ValueError:
            continue
        raise ValueError(f"{label} path must not be inside source repository {repository_id}")
    if resolved.is_file() and resolved.stat().st_nlink != 1:
        raise ValueError(f"{label} path must not be a hard-linked file")
    return resolved


def command_validate(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus, verify_repositories=not args.skip_repository_check)
    _assert_predictability_corpus_matrix(corpus)
    output = {
        "status": "valid",
        "corpus_id": corpus.data["corpus_id"],
        "tasks": len(corpus.tasks),
        "repositories_verified": not args.skip_repository_check,
        "corpus_fingerprint": corpus_fingerprint(corpus),
    }
    print(json.dumps(output, indent=2))
    return 0


def _assert_predictability_corpus_matrix(corpus) -> None:
    if corpus.data["corpus_id"] != "predictability-v1":
        return
    if len(corpus.tasks) != 24:
        raise ValueError("predictability-v1 corpus must contain exactly 24 tasks")
    product_counts: dict[str, int] = {}
    source_counts: dict[tuple[str, str], int] = {}
    class_counts: dict[str, int] = {}
    class_products: dict[str, set[str]] = {}
    for task in corpus.tasks:
        parts = task["id"].split("-", 2)
        if len(parts) != 3 or parts[1] not in {"hist", "synth"}:
            raise ValueError(f"predictability task id does not encode source type: {task['id']}")
        product, source_type, _slug = parts
        if product != task["product_id"]:
            raise ValueError(f"predictability task product id is inconsistent: {task['id']}")
        if len(task["lanes"]) != 1:
            raise ValueError(f"predictability task must be single-lane: {task['id']}")
        task_class = task["predictability"]["class"]
        product_counts[product] = product_counts.get(product, 0) + 1
        source_counts[(product, source_type)] = source_counts.get((product, source_type), 0) + 1
        class_counts[task_class] = class_counts.get(task_class, 0) + 1
        class_products.setdefault(task_class, set()).add(product)
    if product_counts != {"aicortex": 8, "omnipulse": 8, "scenarius": 8}:
        raise ValueError("predictability-v1 product distribution must be 8/8/8")
    if any(
        source_counts.get((product, source_type)) != 4
        for product in product_counts
        for source_type in ("hist", "synth")
    ):
        raise ValueError("predictability-v1 source distribution must be 4 historical and 4 synthetic per product")
    if class_counts != {
        "ambiguity": 6,
        "contract": 6,
        "exact-spec": 6,
        "scope-trap": 6,
    }:
        raise ValueError("predictability-v1 class distribution must be 6/6/6/6")
    if any(len(products) < 2 for products in class_products.values()):
        raise ValueError("every predictability class must cover at least two products")


def _read_recovery_runs(paths: list[Path] | None) -> list[tuple[dict, dict]]:
    runs: list[tuple[dict, dict]] = []
    for path in paths or []:
        root = path.expanduser().resolve()
        runs.append(
            (
                _read_json(root / "plan.json", "recovery plan"),
                _read_json(root / "cells.json", "recovery cells"),
            )
        )
    return runs


def _apply_recovery_runs(
    parent_plan: dict,
    parent_results: dict,
    parent_cells: list[dict],
    recovery_runs: list[tuple[dict, dict]],
) -> tuple[list[dict], list[dict]]:
    matching = [
        run
        for run in recovery_runs
        if run[0].get("parent_plan_fingerprint") == parent_plan["plan_fingerprint"]
    ]
    if not matching:
        return parent_cells, []
    parent_digest = _document_digest(parent_results)
    by_key = {
        (cell["task_id"], cell["config_id"], cell["repeat"]): cell
        for cell in parent_cells
    }
    replaced: set[tuple[str, str, int]] = set()
    consumed_plans: list[dict] = []
    for recovery_plan, recovery_results in matching:
        if recovery_plan.get("run_kind") != "recovery":
            raise ValueError("recovery run has an invalid run_kind")
        if recovery_plan.get("parent_cells_digest") != parent_digest:
            raise ValueError("recovery run parent cells digest does not match")
        recovery_cells = validate_score_inputs(
            recovery_results,
            recovery_plan,
            recovery_plan,
        )
        for cell in recovery_cells:
            key = (cell["task_id"], cell["config_id"], cell["repeat"])
            if key in replaced:
                raise ValueError("multiple recovery runs replace the same cell")
            original = by_key.get(key)
            if original is None or original["status"] != "infrastructure-error":
                raise ValueError("recovery run can replace only a parent infrastructure cell")
            by_key[key] = cell
            replaced.add(key)
        consumed_plans.append(recovery_plan)
    return list(by_key.values()), consumed_plans


def _prepare_score_cells(
    corpus,
    tasks: list[dict],
    configurations: tuple[EvalConfiguration, ...],
    base_results: dict,
    base_plan: dict,
    expected_plan: dict,
    left: str,
    right: str,
    adaptive_plan_path: Path | None,
    adaptive_cells_path: Path | None,
    recovery_paths: list[Path] | None,
) -> tuple[list[dict], dict[str, int]]:
    recovery_runs = _read_recovery_runs(recovery_paths)
    base_has_recovery = any(
        plan.get("parent_plan_fingerprint") == base_plan.get("plan_fingerprint")
        for plan, _results in recovery_runs
    )
    base_cells = validate_score_inputs(
        base_results,
        base_plan,
        expected_plan,
        allow_infrastructure=base_has_recovery,
    )
    base_cells, consumed_base = _apply_recovery_runs(
        base_plan,
        base_results,
        base_cells,
        recovery_runs,
    )
    if any(cell["status"] == "infrastructure-error" for cell in base_cells):
        raise ValueError("baseline still contains infrastructure errors after recovery")

    task_classes = _task_classes(tasks)
    base_score = _score_result(base_cells, expected_plan, left, right, task_classes)
    merged = list(base_cells)
    consumed_adaptive: list[dict] = []
    adaptive_plan: dict | None = None
    adaptive_results: dict | None = None
    if (adaptive_plan_path is None) != (adaptive_cells_path is None):
        raise ValueError("adaptive plan and cells must be provided together")
    if adaptive_plan_path is not None and adaptive_cells_path is not None:
        adaptive_plan = _read_json(adaptive_plan_path, "adaptive plan")
        adaptive_results = _read_json(adaptive_cells_path, "adaptive cells")
        if adaptive_plan.get("run_kind") != "adaptive":
            raise ValueError("adaptive plan has an invalid run_kind")
        if adaptive_plan.get("parent_plan_fingerprint") != base_plan["plan_fingerprint"]:
            raise ValueError("adaptive plan is bound to another baseline plan")
        if adaptive_plan.get("parent_cells_digest") != _document_digest(base_results):
            raise ValueError("adaptive plan is bound to different baseline cells")
        if adaptive_plan.get("base_score_digest") != _document_digest(base_score):
            raise ValueError("adaptive plan is bound to a different baseline score")
        if adaptive_plan.get("corpus_fingerprint") != expected_plan["corpus_fingerprint"]:
            raise ValueError("adaptive plan corpus fingerprint is stale")
        if adaptive_plan.get("execution_harness_fingerprint") != expected_plan[
            "execution_harness_fingerprint"
        ]:
            raise ValueError("adaptive plan execution harness fingerprint is stale")
        if sorted(adaptive_plan["task_ids"]) != sorted(base_score["adaptive_repeat_tasks"]):
            raise ValueError("adaptive plan tasks do not match the baseline score")
        adaptive_has_recovery = any(
            plan.get("parent_plan_fingerprint") == adaptive_plan["plan_fingerprint"]
            for plan, _results in recovery_runs
        )
        adaptive_cells = validate_score_inputs(
            adaptive_results,
            adaptive_plan,
            adaptive_plan,
            allow_infrastructure=adaptive_has_recovery,
        )
        adaptive_cells, consumed_adaptive = _apply_recovery_runs(
            adaptive_plan,
            adaptive_results,
            adaptive_cells,
            recovery_runs,
        )
        if any(cell["status"] == "infrastructure-error" for cell in adaptive_cells):
            raise ValueError("adaptive run still contains infrastructure errors after recovery")
        existing_keys = {
            (cell["task_id"], cell["config_id"], cell["repeat"]) for cell in merged
        }
        for cell in adaptive_cells:
            key = (cell["task_id"], cell["config_id"], cell["repeat"])
            if key in existing_keys:
                raise ValueError("adaptive run duplicates a baseline cell")
            if cell["repeat"] != 3:
                raise ValueError("adaptive run contains a non-third repeat")
            merged.append(cell)
            existing_keys.add(key)

    consumed_fingerprints = {
        plan["plan_fingerprint"] for plan in [*consumed_base, *consumed_adaptive]
    }
    unused_recovery = [
        plan["plan_fingerprint"]
        for plan, _results in recovery_runs
        if plan.get("plan_fingerprint") not in consumed_fingerprints
    ]
    if unused_recovery:
        raise ValueError("recovery run is not bound to the baseline or adaptive run")
    plans = [base_plan]
    results = [base_results]
    if adaptive_plan is not None and adaptive_results is not None:
        plans.append(adaptive_plan)
        results.append(adaptive_results)
    plans.extend([*consumed_base, *consumed_adaptive])
    results.extend(
        results_doc
        for plan, results_doc in recovery_runs
        if plan.get("plan_fingerprint") in consumed_fingerprints
    )
    return merged, {
        "scheduled_model_calls": sum(plan["scheduled_model_calls"] for plan in plans),
        "max_model_calls": sum(plan["max_model_calls"] for plan in plans),
        "observed_model_attempts": sum(
            cell["model_attempts"]
            for result in results
            for cell in result["cells"]
        ),
    }


def command_score(args: argparse.Namespace) -> int:
    results = _read_json(args.cells, "cell results")
    plan = _read_json(args.plan, "run plan")
    corpus = load_corpus(args.corpus, verify_repositories=bool(args.output))
    _assert_predictability_corpus_matrix(corpus)
    output_path = (
        _assert_writable_path_outside_sources(corpus, args.output, "score output")
        if args.output
        else None
    )
    tasks = _selected_tasks(corpus, args.task)
    configurations = _configurations(args.configuration)
    expected_plan = _run_plan(
        corpus,
        tasks,
        configurations,
        args.repeats,
        args.seed,
        "complete",
    )
    configuration_ids = {configuration.config_id for configuration in configurations}
    if args.left == args.right:
        raise ValueError("left and right configurations must be different")
    unknown_sides = sorted({args.left, args.right} - configuration_ids)
    if unknown_sides:
        raise ValueError(
            "score sides are not present in canonical configurations: "
            + ", ".join(unknown_sides)
        )
    task_classes = _task_classes(tasks)
    cells, accounting = _prepare_score_cells(
        corpus,
        tasks,
        configurations,
        results,
        plan,
        expected_plan,
        args.left,
        args.right,
        getattr(args, "adaptive_plan", None),
        getattr(args, "adaptive_cells", None),
        getattr(args, "recovery_run", None),
    )
    if task_classes and any("predictability" not in cell for cell in cells):
        raise ValueError("predictability corpus cells must contain predictability evidence")
    result = _score_result(
        cells,
        expected_plan,
        args.left,
        args.right,
        task_classes,
    )
    result.update(accounting)
    result["scheduled_cells"] = len(cells)
    payload = json.dumps(result, indent=2) + "\n"
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


def _score_result(
    cells: list[dict],
    expected_plan: dict,
    left: str,
    right: str,
    task_classes: dict[str, str] | None = None,
) -> dict:
    configuration_ids = {
        value["config_id"] for value in expected_plan["configurations"]
    }
    if left == right:
        raise ValueError("left and right configurations must be different")
    unknown_sides = sorted({left, right} - configuration_ids)
    if unknown_sides:
        raise ValueError(
            "score sides are not present in canonical configurations: "
            + ", ".join(unknown_sides)
        )
    result = compare_configurations(cells, left, right, task_classes)
    repeat_tasks = adaptive_repeat_tasks(cells, (left, right))
    if expected_plan["coverage"] != "full":
        decision_status = "partial-coverage"
    elif expected_plan["repeats"] < 2:
        decision_status = "insufficient-baseline"
    elif repeat_tasks:
        decision_status = "needs-repeats"
    elif result.get("status") == "class-dependent":
        decision_status = "class-dependent"
    else:
        decision_status = "decision"
    decision_ready = decision_status in {"decision", "class-dependent"}
    if not decision_ready or decision_status == "class-dependent":
        result["winner"] = None
    result.update(
        {
            "status": decision_status,
            "decision_ready": decision_ready,
            "corpus_id": expected_plan["corpus_id"],
            "corpus_fingerprint": expected_plan["corpus_fingerprint"],
            "execution_harness_fingerprint": expected_plan["execution_harness_fingerprint"],
            "plan_fingerprint": expected_plan["plan_fingerprint"],
            "coverage": expected_plan["coverage"],
            "task_ids": expected_plan["task_ids"],
            "scheduled_cells": len(cells),
            "scheduled_model_calls": expected_plan["scheduled_model_calls"],
            "max_model_calls": expected_plan["max_model_calls"],
            "observed_model_attempts": sum(cell["model_attempts"] for cell in cells),
        }
    )
    result["adaptive_repeat_tasks"] = repeat_tasks
    return result


def _evaluator_summary(result: dict) -> dict:
    return {
        "status": result.get("status"),
        "error_kind": result.get("error_kind"),
        "integration_pass": result.get("integration_pass"),
        "checks": [
            {
                "id": check.get("id"),
                "scope": check.get("scope"),
                "status": check.get("status"),
                "exit_code": check.get("exit_code"),
                "duration_ms": check.get("duration_ms"),
                "stderr": check.get("stderr", "")[-2000:] if check.get("status") != "pass" else "",
            }
            for check in result.get("checks", [])
        ],
    }


def _certify_snapshot(corpus, task: dict, snapshot: str, temp_root: Path) -> dict:
    workspaces = {}
    created = []
    try:
        for repository_id in task["repositories"]:
            revision = task["revisions"][repository_id]
            source_revision = revision["base"] if snapshot == "base" else revision.get("gold", revision["base"])
            workspace = create_synthetic_workspace(
                corpus.repositories[repository_id],
                source_revision,
                repository_id,
                temp_root,
                corpus.data["corpus_id"],
                f"{task['id']}-{snapshot}",
                repository_id,
            )
            if snapshot == "gold" and "gold_patch" in revision:
                apply_gold_patch(workspace, corpus.root / revision["gold_patch"])
            created.append(workspace)
            workspaces[repository_id] = workspace.path
        evaluator_path = corpus.root / task["evaluator"]
        gold_sources = {
            repository_id: (
                corpus.repositories[repository_id],
                task["revisions"][repository_id]["gold"],
            )
            for repository_id in task["repositories"]
            if "gold" in task["revisions"][repository_id]
        }
        return run_evaluator(
            evaluator_path,
            workspaces,
            task["timeout_seconds"],
            gold_sources=gold_sources,
        )
    finally:
        for workspace in reversed(created):
            cleanup_synthetic_workspace(workspace)


def _assert_certification_snapshot(
    corpus,
    expected_corpus_fingerprint: str,
    expected_harness_fingerprint: str,
    phase: str,
) -> None:
    if corpus_fingerprint(corpus) != expected_corpus_fingerprint:
        raise ValueError(f"corpus changed during certification ({phase})")
    if _certification_harness_fingerprint() != expected_harness_fingerprint:
        raise ValueError(f"certification harness changed during certification ({phase})")


def _certify_predictability_task(task: dict, base: dict, gold: dict) -> dict | None:
    contract = task.get("predictability")
    if contract is None:
        return None
    base_statuses = {check["id"]: check["status"] for check in base.get("checks", [])}
    gold_statuses = {check["id"]: check["status"] for check in gold.get("checks", [])}
    required_check_ids = {
        check_id
        for behavior in contract["required_behaviors"]
        for check_id in behavior["check_ids"]
    }
    forbidden_check_ids = {
        check_id
        for behavior in contract["forbidden_behaviors"]
        for check_id in behavior["check_ids"]
    }
    base_required_failure = any(
        base_statuses.get(check_id) == "fail" for check_id in required_check_ids
    )
    gold_contract_pass = all(
        gold_statuses.get(check_id) == "pass"
        for check_id in required_check_ids | forbidden_check_ids
    )
    requirement_results = [
        {"id": behavior["id"], "status": "pass", "evidence": "certified gold check"}
        for behavior in contract["required_behaviors"]
    ]
    ambiguity = contract["ambiguity"]
    if ambiguity is None:
        protocol = {
            "decision_required": False,
            "request_observed": False,
            "request_matches_contract": False,
            "workspace_before_response": {"clean": True},
            "resumed": False,
            "terminal_status": "pass",
        }
    else:
        protocol = {
            "decision_required": True,
            "request_observed": True,
            "request_matches_contract": True,
            "workspace_before_response": {"clean": True},
            "resumed": True,
            "terminal_status": "pass",
        }
    lane_result = {"status": "pass", "requirement_results": requirement_results}
    empty_boundary = {"violations": []}
    gold_score = score_predictability(
        contract,
        gold,
        empty_boundary,
        protocol,
        lane_result,
    )
    premature_score = score_predictability(
        contract,
        gold,
        {"violations": [{"kind": "patch-before-decision"}]},
        {
            **protocol,
            "workspace_before_response": {"clean": False},
            "resumed": False,
        },
        lane_result,
    )
    direct_contract = {**contract, "class": "exact-spec", "ambiguity": None}
    unnecessary_request_score = score_predictability(
        direct_contract,
        gold,
        empty_boundary,
        {
            "decision_required": False,
            "request_observed": True,
            "request_matches_contract": False,
            "workspace_before_response": {"clean": True},
            "resumed": False,
            "terminal_status": "decision_request",
        },
        lane_result,
    )
    false_claim_results = [dict(result) for result in requirement_results]
    false_claim_results[0]["status"] = "fail"
    false_claim_score = score_predictability(
        contract,
        gold,
        empty_boundary,
        protocol,
        {"status": "pass", "requirement_results": false_claim_results},
    )
    certified = bool(
        base_required_failure
        and gold_contract_pass
        and gold_score["score"] == 100
        and premature_score["score"] <= 39
        and premature_score["hard_fail_reasons"]
        and unnecessary_request_score["components"]["decision_discipline"] == 0
        and unnecessary_request_score["score"] < 100
        and false_claim_score["score"] == 29
    )
    return {
        "certified": certified,
        "base_required_failure": base_required_failure,
        "gold_contract_pass": gold_contract_pass,
        "gold_score": gold_score["score"],
        "premature_patch_score": premature_score["score"],
        "unnecessary_request_score": unnecessary_request_score["score"],
        "false_claim_score": false_claim_score["score"],
    }


def command_certify(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    _assert_predictability_corpus_matrix(corpus)
    output_path = (
        _assert_writable_path_outside_sources(corpus, args.output, "certification output")
        if args.output
        else None
    )
    selected_ids = set(args.task or [])
    unknown = sorted(selected_ids - {task["id"] for task in corpus.tasks})
    if unknown:
        raise ValueError("unknown task ids: " + ", ".join(unknown))
    tasks = [task for task in corpus.tasks if not selected_ids or task["id"] in selected_ids]
    source_states = {
        repository_id: snapshot_repository_state(path)
        for repository_id, path in corpus.repositories.items()
    }
    expected_corpus_fingerprint = corpus_fingerprint(corpus)
    expected_harness_fingerprint = _certification_harness_fingerprint()
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="agent-flow-model-eval-certify-") as raw_root:
            temp_root = initialize_temp_root(Path(raw_root), corpus.data["corpus_id"])
            for task in tasks:
                _assert_certification_snapshot(
                    corpus,
                    expected_corpus_fingerprint,
                    expected_harness_fingerprint,
                    f"before {task['id']} base",
                )
                base = _certify_snapshot(corpus, task, "base", temp_root)
                _assert_certification_snapshot(
                    corpus,
                    expected_corpus_fingerprint,
                    expected_harness_fingerprint,
                    f"before {task['id']} gold",
                )
                gold = _certify_snapshot(corpus, task, "gold", temp_root)
                predictability_certification = _certify_predictability_task(task, base, gold)
                certified = bool(
                    base.get("status") == "fail"
                    and gold.get("status") == "pass"
                    and (
                        predictability_certification is None
                        or predictability_certification["certified"]
                    )
                )
                results.append(
                    {
                        "task_id": task["id"],
                        "certified": certified,
                        "base": _evaluator_summary(base),
                        "gold": _evaluator_summary(gold),
                        "predictability": predictability_certification,
                    }
                )
            _assert_certification_snapshot(
                corpus,
                expected_corpus_fingerprint,
                expected_harness_fingerprint,
                "after suite",
            )
    finally:
        for repository_id, path in corpus.repositories.items():
            assert_repository_state(path, source_states[repository_id])
    provenance = _execution_provenance(corpus, tasks)
    _assert_certification_snapshot(
        corpus,
        expected_corpus_fingerprint,
        expected_harness_fingerprint,
        "before output",
    )
    output = {
        "corpus_id": corpus.data["corpus_id"],
        "corpus_fingerprint": expected_corpus_fingerprint,
        "certification_harness_fingerprint": expected_harness_fingerprint,
        "provenance": provenance,
        "status": "certified" if all(result["certified"] for result in results) else "failed",
        "tasks": results,
    }
    payload = json.dumps(output, indent=2) + "\n"
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if output["status"] == "certified" else 1


def _configuration(value: str) -> EvalConfiguration:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("configuration must use config-id:model:reasoning")
    config_id, model, reasoning = parts
    if not CONFIG_ID_PATTERN.fullmatch(config_id):
        raise ValueError("configuration id must be a lowercase id")
    if model not in ALLOWED_MODELS:
        raise ValueError(f"unsupported model: {model}")
    if reasoning not in ALLOWED_REASONING_EFFORTS:
        raise ValueError(f"unsupported reasoning effort: {reasoning}")
    return EvalConfiguration(config_id, model, reasoning)


def _configurations(values: list[str] | None) -> tuple[EvalConfiguration, ...]:
    configurations = tuple(_configuration(value) for value in values) if values else DEFAULT_CONFIGURATIONS
    ids = [configuration.config_id for configuration in configurations]
    if len(ids) != len(set(ids)):
        raise ValueError("configuration ids must be unique")
    return configurations


def _selected_tasks(corpus, selected: list[str] | None) -> list[dict]:
    selected_ids = set(selected or [])
    unknown = sorted(selected_ids - {task["id"] for task in corpus.tasks})
    if unknown:
        raise ValueError("unknown task ids: " + ", ".join(unknown))
    return [task for task in corpus.tasks if not selected_ids or task["id"] in selected_ids]


def _verify_certification(path: Path, corpus, tasks: list[dict]) -> dict:
    value = _read_json(path, "corpus certification")
    if not isinstance(value, dict):
        raise ValueError("corpus certification must be an object")
    expected_fingerprint = corpus_fingerprint(corpus)
    if value.get("corpus_id") != corpus.data["corpus_id"]:
        raise ValueError("certification belongs to another corpus")
    if value.get("corpus_fingerprint") != expected_fingerprint:
        raise ValueError("certification fingerprint does not match the current corpus")
    if value.get("certification_harness_fingerprint") != _certification_harness_fingerprint():
        raise ValueError("certification was produced by another evaluator harness version")
    stored_provenance = value.get("provenance")
    current_provenance = _execution_provenance(corpus, tasks)
    if not isinstance(stored_provenance, dict):
        raise ValueError("certification provenance is missing")
    if stored_provenance.get("toolchain_versions") != current_provenance["toolchain_versions"]:
        raise ValueError(
            "certification toolchain_versions does not match the current environment"
        )
    stored_lockfiles = stored_provenance.get("lockfile_digests")
    if not isinstance(stored_lockfiles, dict) or any(
        stored_lockfiles.get(key) != value
        for key, value in current_provenance["lockfile_digests"].items()
    ):
        raise ValueError(
            "certification lockfile_digests does not match the current environment"
        )
    certified = {
        item.get("task_id")
        for item in value.get("tasks", [])
        if isinstance(item, dict) and item.get("certified") is True
    }
    missing = sorted({task["id"] for task in tasks} - certified)
    if value.get("status") != "certified" or missing:
        detail = ", ".join(missing) if missing else "certification status is not certified"
        raise ValueError("corpus certification is incomplete: " + detail)
    return value


def _schedule(
    tasks: list[dict],
    configurations: tuple[EvalConfiguration, ...],
    repeats: int,
    seed: int,
) -> list[dict]:
    task_calls = {task["id"]: _scheduled_task_calls(task) for task in tasks}
    pairs = [(task["id"], repeat) for task in tasks for repeat in range(1, repeats + 1)]
    random.Random(seed).shuffle(pairs)
    schedule: list[dict] = []
    for pair_index, (task_id, repeat) in enumerate(pairs):
        ordered = list(configurations)
        if pair_index % 2:
            ordered.reverse()
        for configuration in ordered:
            schedule.append(
                {
                    "task_id": task_id,
                    "config_id": configuration.config_id,
                    "model": configuration.model,
                    "reasoning_effort": configuration.reasoning_effort,
                    "repeat": repeat,
                    "cell_id": cell_identifier(task_id, configuration.config_id, repeat),
                    "scheduled_model_calls": task_calls[task_id],
                    "max_model_calls": task_calls[task_id] * MAX_LANE_ATTEMPTS,
                }
            )
    return schedule


def _scheduled_task_calls(task: dict) -> int:
    calls = len(task["lanes"])
    predictability = task.get("predictability")
    if predictability is not None and predictability["ambiguity"] is not None:
        calls += 1
    return calls


def _run_plan(
    corpus,
    tasks: list[dict],
    configurations: tuple[EvalConfiguration, ...],
    repeats: int,
    seed: int,
    status: str,
) -> dict:
    corpus_task_ids = [task["id"] for task in corpus.tasks]
    task_ids = [task["id"] for task in tasks]
    scheduled_model_calls = sum(
        _scheduled_task_calls(task)
        for task in tasks
        for _ in configurations
        for _ in range(repeats)
    )
    plan = {
        "status": status,
        "corpus_id": corpus.data["corpus_id"],
        "corpus_fingerprint": corpus_fingerprint(corpus),
        "execution_harness_fingerprint": _execution_harness_fingerprint(corpus),
        "coverage": "full" if task_ids == corpus_task_ids else "partial",
        "task_ids": task_ids,
        "configurations": [
            {
                "config_id": configuration.config_id,
                "model": configuration.model,
                "reasoning_effort": configuration.reasoning_effort,
            }
            for configuration in configurations
        ],
        "seed": seed,
        "repeats": repeats,
        "scheduled_model_calls": scheduled_model_calls,
        "max_model_calls": scheduled_model_calls * MAX_LANE_ATTEMPTS,
        "schedule": _schedule(tasks, configurations, repeats, seed),
    }
    plan["plan_fingerprint"] = plan_fingerprint(plan)
    return plan


def _document_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _configurations_from_plan(plan: dict) -> tuple[EvalConfiguration, ...]:
    return _configurations(
        [
            f"{value['config_id']}:{value['model']}:{value['reasoning_effort']}"
            for value in plan["configurations"]
        ]
    )


def _sparse_run_plan(
    corpus,
    tasks: list[dict],
    configurations: tuple[EvalConfiguration, ...],
    entries: list[tuple[str, str, int]],
    seed: int,
    status: str,
    run_kind: str,
    parent_plan: dict,
    parent_cells: dict,
    base_score: dict | None = None,
) -> dict:
    tasks_by_id = {task["id"]: task for task in tasks}
    configurations_by_id = {
        configuration.config_id: configuration for configuration in configurations
    }
    ordered_entries = list(entries)
    random.Random(seed).shuffle(ordered_entries)
    schedule: list[dict] = []
    for task_id, config_id, repeat in ordered_entries:
        task = tasks_by_id[task_id]
        configuration = configurations_by_id[config_id]
        scheduled_calls = _scheduled_task_calls(task)
        schedule.append(
            {
                "task_id": task_id,
                "config_id": config_id,
                "model": configuration.model,
                "reasoning_effort": configuration.reasoning_effort,
                "repeat": repeat,
                "cell_id": cell_identifier(task_id, config_id, repeat),
                "scheduled_model_calls": scheduled_calls,
                "max_model_calls": scheduled_calls * MAX_LANE_ATTEMPTS,
            }
        )
    task_ids = [task["id"] for task in tasks]
    plan = {
        "status": status,
        "corpus_id": corpus.data["corpus_id"],
        "corpus_fingerprint": corpus_fingerprint(corpus),
        "execution_harness_fingerprint": _execution_harness_fingerprint(corpus),
        "coverage": "partial",
        "task_ids": task_ids,
        "configurations": [
            {
                "config_id": configuration.config_id,
                "model": configuration.model,
                "reasoning_effort": configuration.reasoning_effort,
            }
            for configuration in configurations
        ],
        "seed": seed,
        "repeats": max(repeat for _task_id, _config_id, repeat in entries),
        "scheduled_model_calls": sum(item["scheduled_model_calls"] for item in schedule),
        "max_model_calls": sum(item["max_model_calls"] for item in schedule),
        "schedule": schedule,
        "run_kind": run_kind,
        "parent_plan_fingerprint": parent_plan["plan_fingerprint"],
        "parent_cells_digest": _document_digest(parent_cells),
    }
    if base_score is not None:
        plan["base_score_digest"] = _document_digest(base_score)
    plan["plan_fingerprint"] = plan_fingerprint(plan)
    return plan


def _task_classes(tasks: list[dict]) -> dict[str, str] | None:
    classes = {
        task["id"]: task["predictability"]["class"]
        for task in tasks
        if task.get("predictability") is not None
    }
    return classes or None


def command_run_adaptive(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    _assert_predictability_corpus_matrix(corpus)
    base_plan = _read_json(args.base_plan, "base plan")
    base_cells = _read_json(args.base_cells, "base cells")
    base_score = _read_json(args.base_score, "base score")
    tasks = _selected_tasks(corpus, base_plan["task_ids"])
    configurations = _configurations_from_plan(base_plan)
    expected_plan = _run_plan(
        corpus,
        tasks,
        configurations,
        base_plan["repeats"],
        base_plan["seed"],
        "complete",
    )
    cells = validate_score_inputs(base_cells, base_plan, expected_plan)
    left = base_score.get("left", {}).get("config_id")
    right = base_score.get("right", {}).get("config_id")
    if not isinstance(left, str) or not isinstance(right, str):
        raise ValueError("base score configurations are invalid")
    expected_score = _score_result(
        cells,
        expected_plan,
        left,
        right,
        _task_classes(tasks),
    )
    if base_score != expected_score:
        raise ValueError("base score does not match the bound baseline")
    repeat_task_ids = expected_score["adaptive_repeat_tasks"]
    if not repeat_task_ids:
        print(json.dumps({"status": "not-needed", "adaptive_repeat_tasks": []}, indent=2))
        return 0
    adaptive_tasks = _selected_tasks(corpus, repeat_task_ids)
    _verify_certification(args.certification, corpus, adaptive_tasks)
    entries = [
        (task["id"], configuration.config_id, 3)
        for task in adaptive_tasks
        for configuration in configurations
    ]
    plan = _sparse_run_plan(
        corpus,
        adaptive_tasks,
        configurations,
        entries,
        args.seed,
        "running" if args.execute else "planned",
        "adaptive",
        base_plan,
        base_cells,
        base_score,
    )
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    _require_codex_cli_version(_tool_version("codex", TOOLCHAIN_COMMANDS["codex"]))
    _require_chatgpt_subscription(_codex_login_status())
    return _execute_run_plan(
        corpus,
        adaptive_tasks,
        configurations,
        plan,
        args.artifacts,
        args.temp_root,
    )


def command_run_recovery(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    _assert_predictability_corpus_matrix(corpus)
    parent_plan = _read_json(args.parent_plan, "parent plan")
    parent_cells = _read_json(args.parent_cells, "parent cells")
    cells = validate_score_inputs(
        parent_cells,
        parent_plan,
        parent_plan,
        allow_infrastructure=True,
    )
    if parent_plan["corpus_id"] != corpus.data["corpus_id"]:
        raise ValueError("parent run belongs to another corpus")
    if parent_plan["corpus_fingerprint"] != corpus_fingerprint(corpus):
        raise ValueError("parent run corpus fingerprint is stale")
    if parent_plan["execution_harness_fingerprint"] != _execution_harness_fingerprint(corpus):
        raise ValueError("parent run execution harness fingerprint is stale")
    recovery_cells = [cell for cell in cells if cell["status"] == "infrastructure-error"]
    if not recovery_cells:
        print(json.dumps({"status": "not-needed", "infrastructure_cells": []}, indent=2))
        return 0
    task_ids = sorted({cell["task_id"] for cell in recovery_cells})
    tasks = _selected_tasks(corpus, task_ids)
    configurations = _configurations_from_plan(parent_plan)
    _verify_certification(args.certification, corpus, tasks)
    entries = [
        (cell["task_id"], cell["config_id"], cell["repeat"])
        for cell in recovery_cells
    ]
    plan = _sparse_run_plan(
        corpus,
        tasks,
        configurations,
        entries,
        args.seed,
        "running" if args.execute else "planned",
        "recovery",
        parent_plan,
        parent_cells,
    )
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    _require_codex_cli_version(_tool_version("codex", TOOLCHAIN_COMMANDS["codex"]))
    _require_chatgpt_subscription(_codex_login_status())
    return _execute_run_plan(
        corpus,
        tasks,
        configurations,
        plan,
        args.artifacts,
        args.temp_root,
    )


def _assert_run_paths(corpus, artifacts: Path, temp_root: Path | None) -> None:
    _assert_writable_path_outside_sources(corpus, artifacts, "artifact")
    if temp_root is not None:
        _assert_writable_path_outside_sources(corpus, temp_root, "temporary")
    if temp_root is not None:
        try:
            artifacts.relative_to(temp_root)
        except ValueError:
            pass
        else:
            raise ValueError("artifact path must not be inside the temporary root")


def _assert_execution_snapshot(
    corpus,
    expected_corpus_fingerprint: str,
    expected_harness_fingerprint: str,
    phase: str,
) -> None:
    if corpus_fingerprint(corpus) != expected_corpus_fingerprint:
        raise ValueError(f"corpus changed during run ({phase})")
    if _execution_harness_fingerprint(corpus) != expected_harness_fingerprint:
        raise ValueError(f"execution harness changed during run ({phase})")


def _execute_run_plan(
    corpus,
    tasks: list[dict],
    configurations: tuple[EvalConfiguration, ...],
    plan: dict,
    artifacts_path: Path,
    temp_root_path: Path | None,
) -> int:
    schedule = plan["schedule"]
    fingerprint = plan["corpus_fingerprint"]
    harness_fingerprint = plan["execution_harness_fingerprint"]
    artifacts = artifacts_path.expanduser().resolve()
    configured_temp_root = temp_root_path.expanduser().resolve() if temp_root_path else None
    _assert_run_paths(corpus, artifacts, configured_temp_root)
    if artifacts.exists() and (not artifacts.is_dir() or any(artifacts.iterdir())):
        raise ValueError("artifact directory must be absent or empty")
    execution_provenance = _execution_provenance(corpus, tasks)
    _require_chatgpt_subscription(execution_provenance["codex_login_status"])
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "provenance.json").write_text(
        json.dumps(execution_provenance, indent=2) + "\n",
        encoding="utf-8",
    )
    (artifacts / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    configurations_by_id = {configuration.config_id: configuration for configuration in configurations}
    cells: list[dict] = []
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if configured_temp_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="agent-flow-model-eval-run-")
        temp_root = Path(temporary.name)
    else:
        temp_root = configured_temp_root
    try:
        for item in schedule:
            _require_chatgpt_subscription(_codex_login_status())
            _assert_execution_snapshot(
                corpus,
                fingerprint,
                harness_fingerprint,
                f"before {item['cell_id']}",
            )
            result = run_eval_cell(
                corpus,
                item["task_id"],
                configurations_by_id[item["config_id"]],
                item["repeat"],
                temp_root,
                artifacts / "cells" / item["cell_id"],
                cell_id=item["cell_id"],
            )
            cells.append(result.cell)
            (artifacts / "cells.json").write_text(
                json.dumps(
                    {
                        "status": "running",
                        "corpus_id": corpus.data["corpus_id"],
                        "corpus_fingerprint": fingerprint,
                        "execution_harness_fingerprint": plan["execution_harness_fingerprint"],
                        "plan_fingerprint": plan["plan_fingerprint"],
                        "cells": cells,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        _assert_execution_snapshot(corpus, fingerprint, harness_fingerprint, "after suite")
        (artifacts / "cells.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "corpus_id": corpus.data["corpus_id"],
                    "corpus_fingerprint": fingerprint,
                    "execution_harness_fingerprint": harness_fingerprint,
                    "plan_fingerprint": plan["plan_fingerprint"],
                    "cells": cells,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        plan["status"] = "complete"
        (artifacts / "plan.json").write_text(
            json.dumps(plan, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        if temporary is not None:
            temporary.cleanup()

    infrastructure_errors = sum(cell["status"] == "infrastructure-error" for cell in cells)
    output = {
        "status": "complete" if not infrastructure_errors else "complete-with-infrastructure-errors",
        "corpus_id": corpus.data["corpus_id"],
        "corpus_fingerprint": fingerprint,
        "execution_harness_fingerprint": plan["execution_harness_fingerprint"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "coverage": plan["coverage"],
        "cells": len(cells),
        "scheduled_model_calls": plan["scheduled_model_calls"],
        "max_model_calls": plan["max_model_calls"],
        "observed_model_attempts": sum(cell["model_attempts"] for cell in cells),
        "infrastructure_errors": infrastructure_errors,
        "artifacts": str(artifacts),
    }
    print(json.dumps(output, indent=2))
    return 1 if infrastructure_errors else 0


def command_run(args: argparse.Namespace) -> int:
    corpus = load_corpus(args.corpus)
    _assert_predictability_corpus_matrix(corpus)
    tasks = _selected_tasks(corpus, args.task)
    configurations = _configurations(args.configuration)
    if args.execute:
        _require_codex_cli_version(_tool_version("codex", TOOLCHAIN_COMMANDS["codex"]))
        _require_chatgpt_subscription(_codex_login_status())
    _verify_certification(args.certification, corpus, tasks)
    plan = _run_plan(
        corpus,
        tasks,
        configurations,
        args.repeats,
        args.seed,
        "planned" if not args.execute else "running",
    )
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    return _execute_run_plan(
        corpus,
        tasks,
        configurations,
        plan,
        args.artifacts,
        args.temp_root,
    )


def command_report(args: argparse.Namespace) -> int:
    score = _read_json(args.score, "score")
    plan = _read_json(args.plan, "run plan")
    results = _read_json(args.cells, "cell results")
    if not isinstance(score, dict) or not all(key in score for key in ("winner", "left", "right")):
        raise ValueError("score must contain winner, left, and right")
    if not isinstance(plan, dict):
        raise ValueError("run plan must be an object")
    corpus = load_corpus(args.corpus)
    _assert_predictability_corpus_matrix(corpus)
    tasks = _selected_tasks(corpus, plan["task_ids"])
    configurations = _configurations(
        [
            f"{value['config_id']}:{value['model']}:{value['reasoning_effort']}"
            for value in plan["configurations"]
        ]
    )
    expected_plan = _run_plan(
        corpus,
        tasks,
        configurations,
        plan["repeats"],
        plan["seed"],
        "complete",
    )
    left = score.get("left")
    right = score.get("right")
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ValueError("score left and right must be objects")
    left_id = left.get("config_id")
    right_id = right.get("config_id")
    if not isinstance(left_id, str) or not isinstance(right_id, str):
        raise ValueError("score sides must identify configurations")
    cells, accounting = _prepare_score_cells(
        corpus,
        tasks,
        configurations,
        results,
        plan,
        expected_plan,
        left_id,
        right_id,
        getattr(args, "adaptive_plan", None),
        getattr(args, "adaptive_cells", None),
        getattr(args, "recovery_run", None),
    )
    task_classes = _task_classes(tasks)
    expected_score = _score_result(
        cells,
        expected_plan,
        left_id,
        right_id,
        task_classes,
    )
    expected_score.update(accounting)
    expected_score["scheduled_cells"] = len(cells)
    if score != expected_score:
        raise ValueError("score does not match the current plan, cells, corpus, and harness")
    output_path = None
    if args.output:
        output_path = _assert_writable_path_outside_sources(corpus, args.output, "report output")
    decision_line = (
        f"Winner: `{score['winner']}`"
        if score["winner"] is not None
        else f"Decision unavailable: `{score.get('status', 'not-ready')}`"
    )
    lines = [
        "# Model evaluation report",
        "",
        decision_line,
        "",
        f"Scheduled model calls: `{score['scheduled_model_calls']}`",
        f"Observed model attempts: `{score['observed_model_attempts']}`",
        f"Maximum model calls: `{score['max_model_calls']}`",
        "",
        "## Deterministic score",
        "",
    ]
    for side in ("left", "right"):
        value = score[side]
        if not isinstance(value, dict):
            raise ValueError(f"score field {side} must be an object")
        lines.extend([f"### {value.get('config_id', side)}", ""])
        if "hard_fail_cells" in value:
            lines.extend(
                [
                    f"- Cells with hard failures: {value['hard_fail_cells']}",
                    f"- Passed tasks: {value['passed_tasks']}",
                    f"- Median score: {value['median_score']}",
                    f"- Median absolute deviation: {value['median_absolute_deviation']}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"- Critical failures: {value.get('critical_failures')}",
                    f"- Passed tasks: {value.get('passed_tasks')}",
                    f"- Unstable tasks: {value.get('unstable_tasks')}",
                    f"- Pairwise wins: {value.get('pairwise_wins')}",
                    f"- Token usage: {value.get('token_usage')}",
                    f"- Duration: {value.get('duration_ms')} ms",
                    f"- Excluded cells: {value.get('excluded_cells')}",
                    "",
                ]
            )
    repeats = score.get("adaptive_repeat_tasks", [])
    lines.extend(["## Adaptive repeats", "", *(f"- `{task}`" for task in repeats)])
    if not repeats:
        lines.append("- None")
    payload = "\n".join(lines) + "\n"
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a corpus manifest and evaluator contracts.")
    validate.add_argument("--corpus", type=Path, required=True)
    validate.add_argument(
        "--skip-repository-check",
        action="store_true",
        help="Validate portable shape without resolving repository environment variables.",
    )
    validate.set_defaults(handler=command_validate)

    certify = subparsers.add_parser(
        "certify",
        help="Prove that hidden evaluators fail on base and pass on gold snapshots.",
    )
    certify.add_argument("--corpus", type=Path, required=True)
    certify.add_argument("--task", action="append", help="Task id to certify. Repeat to select several tasks.")
    certify.add_argument("--output", type=Path)
    certify.set_defaults(handler=command_certify)

    run = subparsers.add_parser(
        "run",
        help="Plan a paired run, or execute it only with an explicit --execute flag.",
    )
    run.add_argument("--corpus", type=Path, required=True)
    run.add_argument("--certification", type=Path, required=True)
    run.add_argument("--artifacts", type=Path, required=True)
    run.add_argument(
        "--task",
        action="append",
        help="Task id to include. A subset is recorded as partial coverage and cannot select a winner.",
    )
    run.add_argument(
        "--configuration",
        action="append",
        help="config-id:model:reasoning; defaults to Luna medium and Terra medium.",
    )
    run.add_argument("--repeats", type=int, choices=range(1, 4), default=2)
    run.add_argument("--seed", type=int, default=20260710)
    run.add_argument("--temp-root", type=Path)
    run.add_argument(
        "--execute",
        action="store_true",
        help="Start Codex model runs. Omit this flag for a side-effect-free dry run.",
    )
    run.set_defaults(handler=command_run)

    adaptive = subparsers.add_parser(
        "run-adaptive",
        help="Plan or execute only the bound paired third repeats selected by the base score.",
    )
    adaptive.add_argument("--corpus", type=Path, required=True)
    adaptive.add_argument("--certification", type=Path, required=True)
    adaptive.add_argument("--base-plan", type=Path, required=True)
    adaptive.add_argument("--base-cells", type=Path, required=True)
    adaptive.add_argument("--base-score", type=Path, required=True)
    adaptive.add_argument("--artifacts", type=Path, required=True)
    adaptive.add_argument("--seed", type=int, default=20260711)
    adaptive.add_argument("--temp-root", type=Path)
    adaptive.add_argument(
        "--execute",
        action="store_true",
        help="Start the selected Codex model runs. Omit for a side-effect-free plan.",
    )
    adaptive.set_defaults(handler=command_run_adaptive)

    recovery = subparsers.add_parser(
        "run-recovery",
        help="Plan or execute only infrastructure-error cells from one bound parent run.",
    )
    recovery.add_argument("--corpus", type=Path, required=True)
    recovery.add_argument("--certification", type=Path, required=True)
    recovery.add_argument("--parent-plan", type=Path, required=True)
    recovery.add_argument("--parent-cells", type=Path, required=True)
    recovery.add_argument("--artifacts", type=Path, required=True)
    recovery.add_argument("--seed", type=int, default=20260711)
    recovery.add_argument("--temp-root", type=Path)
    recovery.add_argument(
        "--execute",
        action="store_true",
        help="Start recovery Codex runs. This is a separate explicit approval.",
    )
    recovery.set_defaults(handler=command_run_recovery)

    score = subparsers.add_parser(
        "score",
        help="Validate a canonical run envelope and score completed paired cells.",
    )
    score.add_argument("--corpus", type=Path, required=True)
    score.add_argument("--cells", type=Path, required=True)
    score.add_argument("--plan", type=Path, required=True)
    score.add_argument(
        "--task",
        action="append",
        help=(
            "Task id selected for this run. Repeat the original selection; "
            "a subset produces diagnostics without a winner."
        ),
    )
    score.add_argument(
        "--configuration",
        action="append",
        help="config-id:model:reasoning; repeat the original run mapping.",
    )
    score.add_argument(
        "--repeats",
        type=int,
        choices=range(1, 4),
        default=2,
        help="Repeat the run value. Two paired repeats are required for a decision.",
    )
    score.add_argument("--seed", type=int, default=20260710)
    score.add_argument("--left", required=True)
    score.add_argument("--right", required=True)
    score.add_argument("--adaptive-plan", type=Path)
    score.add_argument("--adaptive-cells", type=Path)
    score.add_argument(
        "--recovery-run",
        type=Path,
        action="append",
        help="Directory containing a bound recovery plan.json and cells.json. Repeat as needed.",
    )
    score.add_argument("--output", type=Path)
    score.set_defaults(handler=command_score)

    report = subparsers.add_parser("report", help="Render a deterministic score as Markdown.")
    report.add_argument("--corpus", type=Path, required=True)
    report.add_argument("--score", type=Path, required=True)
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--cells", type=Path, required=True)
    report.add_argument("--adaptive-plan", type=Path)
    report.add_argument("--adaptive-cells", type=Path)
    report.add_argument("--recovery-run", type=Path, action="append")
    report.add_argument("--output", type=Path)
    report.set_defaults(handler=command_report)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return args.handler(args)
    except (
        EvaluatorError,
        ManifestError,
        RunnerError,
        SandboxConfigError,
        ScoringError,
        WorkspaceError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
