#!/usr/bin/env python3
"""Fixture tests for hidden evaluator injection and execution."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import model_eval_evaluator
from model_eval_evaluator import EvaluatorError, inject_evaluator_files, run_evaluator, run_setup_commands
from model_eval_workspace import (
    cleanup_synthetic_workspace,
    create_synthetic_workspace,
    initialize_temp_root,
)


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def create_repository(root: Path) -> tuple[str, str]:
    root.mkdir()
    run(["git", "init", "--quiet"], root)
    run(["git", "config", "user.name", "Agent Flow Test"], root)
    run(["git", "config", "user.email", "agent-flow@example.invalid"], root)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    (root / "tests" / "behavior.py").write_text("print('base test')\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-qm", "base"], root)
    base = run(["git", "rev-parse", "HEAD"], root)
    (root / "src" / "value.txt").write_text("gold\n", encoding="utf-8")
    (root / "tests" / "behavior.py").write_text(
        "from pathlib import Path\n"
        "if Path('src/value.txt').read_text(encoding='utf-8') != 'gold\\n':\n"
        "    raise SystemExit('gold behavior missing')\n"
        "print('gold behavior passed')\n",
        encoding="utf-8",
    )
    run(["git", "commit", "-qam", "gold"], root)
    gold = run(["git", "rev-parse", "HEAD"], root)
    return base, gold


def create_evaluator(root: Path) -> Path:
    evaluator_dir = root / "evaluator"
    evaluator_dir.mkdir()
    hidden = evaluator_dir / "hidden_check.py"
    hidden.write_text(
        """from pathlib import Path
import os
import sys

if "OPENAI_API_KEY" in os.environ:
    raise SystemExit("secret environment leaked")
if os.environ.get("NO_PROXY") != "127.0.0.1,localhost,::1":
    raise SystemExit("localhost proxy bypass is missing")
if Path("src/value.txt").read_text(encoding="utf-8") != "gold\\n":
    raise SystemExit("gold behavior is missing")
print("behavior passed")
""",
        encoding="utf-8",
    )
    evaluator = {
        "schema_version": 1,
        "injections": [
            {
                "repository_id": "fixture",
                "source": "hidden_check.py",
                "target": "agent_flow_eval_hidden_check.py",
            }
        ],
        "commands": [
            {
                "id": "behavior",
                "repository_id": "fixture",
                "scope": "lane",
                "argv": ["python3", "agent_flow_eval_hidden_check.py"],
            }
        ],
        "positive_checks": ["gold behavior passes"],
        "negative_checks": ["base behavior fails"],
    }
    path = evaluator_dir / "evaluator.json"
    path.write_text(json.dumps(evaluator, indent=2) + "\n", encoding="utf-8")
    return path


def test_base_fails_and_gold_passes(root: Path) -> None:
    source = root / "source"
    base, gold = create_repository(source)
    evaluator = create_evaluator(root)
    temp_root = initialize_temp_root(root / "eval-root", "certification")
    base_workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "certification",
        "base-cell",
        "worker",
    )
    gold_workspace = create_synthetic_workspace(
        source,
        gold,
        "fixture",
        temp_root,
        "certification",
        "gold-cell",
        "worker",
    )
    previous = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "must-not-leak"
    try:
        base_result = run_evaluator(evaluator, {"fixture": base_workspace.path}, 30)
        gold_result = run_evaluator(evaluator, {"fixture": gold_workspace.path}, 30)
    finally:
        if previous is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous
    if base_result["status"] != "fail":
        raise AssertionError(f"base evaluator must fail: {base_result}")
    if gold_result["status"] != "pass":
        raise AssertionError(f"gold evaluator must pass: {gold_result}")
    for workspace in (base_workspace, gold_workspace):
        if not (workspace.path / "agent_flow_eval_hidden_check.py").is_file():
            raise AssertionError("hidden evaluator file was not present in its disposable workspace")
        cleanup_synthetic_workspace(workspace)


def test_gold_test_replacement_is_restored(root: Path) -> None:
    source = root / "source-gold"
    base, gold = create_repository(source)
    evaluator_dir = root / "gold-evaluator"
    evaluator_dir.mkdir()
    evaluator_path = evaluator_dir / "evaluator.json"
    evaluator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "injections": [
                    {
                        "repository_id": "fixture",
                        "gold_path": "tests/behavior.py",
                        "target": "tests/behavior.py",
                        "replace": True,
                    }
                ],
                "commands": [
                    {
                        "id": "behavior",
                        "repository_id": "fixture",
                        "scope": "lane",
                        "argv": ["python3", "tests/behavior.py"],
                    }
                ],
                "positive_checks": ["gold behavior passes"],
                "negative_checks": ["base behavior fails"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temp_root = initialize_temp_root(root / "eval-root-gold", "gold-certification")
    workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "gold-certification",
        "base-cell",
        "worker",
    )
    result = run_evaluator(
        evaluator_path,
        {"fixture": workspace.path},
        30,
        gold_sources={"fixture": (source, gold)},
    )
    if result["status"] != "fail":
        raise AssertionError(f"gold test must reject base behavior: {result}")
    if b"gold behavior passed" not in (workspace.path / "tests" / "behavior.py").read_bytes():
        raise AssertionError("gold evaluator test was not injected")
    cleanup_synthetic_workspace(workspace)


def test_replace_or_create_restores_both_states(root: Path) -> None:
    source = root / "source-replace-or-create"
    base, _ = create_repository(source)
    evaluator_dir = root / "replace-or-create-evaluator"
    evaluator_dir.mkdir()
    (evaluator_dir / "stub.py").write_text("VALUE = 'stub'\n", encoding="utf-8")
    evaluator_path = evaluator_dir / "evaluator.json"
    evaluator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "injections": [
                    {
                        "repository_id": "fixture",
                        "source": "stub.py",
                        "target": "node_modules/bullmq/__init__.py",
                        "replace_or_create": True,
                    }
                ],
                "commands": [
                    {
                        "id": "stub",
                        "repository_id": "fixture",
                        "scope": "lane",
                        "argv": [
                            "python3",
                            "-c",
                            "from pathlib import Path; assert \"VALUE = 'stub'\" in Path('node_modules/bullmq/__init__.py').read_text()",
                        ],
                    }
                ],
                "positive_checks": ["stub is visible"],
                "negative_checks": ["host dependency is never retained"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temp_root = initialize_temp_root(root / "eval-root-replace-or-create", "replace-or-create")
    workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "replace-or-create",
        "base-cell",
        "worker",
    )
    target = workspace.path / "node_modules" / "bullmq" / "__init__.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 'installed'\n", encoding="utf-8")
    result = run_evaluator(evaluator_path, {"fixture": workspace.path}, 30)
    if result["status"] != "pass" or target.read_text(encoding="utf-8") != "VALUE = 'stub'\n":
        raise AssertionError("replace-or-create did not replace an existing dependency")
    cleanup_synthetic_workspace(workspace)

    second_workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "replace-or-create",
        "second-cell",
        "worker",
    )
    second_target = second_workspace.path / "node_modules" / "bullmq" / "__init__.py"
    result = run_evaluator(evaluator_path, {"fixture": second_workspace.path}, 30)
    if result["status"] != "pass" or not second_target.is_file():
        raise AssertionError("replace-or-create did not create a missing dependency")
    cleanup_synthetic_workspace(second_workspace)


def test_timeout_attribution(root: Path) -> None:
    workspace = root / "timeout-workspace"
    workspace.mkdir()
    evaluator_path = root / "timeout-evaluator.json"

    def timeout_runner(command: list[str], **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    evaluator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "commands": [
                    {
                        "id": "lane-timeout",
                        "repository_id": "fixture",
                        "scope": "lane",
                        "argv": ["python3", "check.py"],
                    }
                ],
                "positive_checks": ["lane completes"],
                "negative_checks": ["lane timeout is a task failure"],
            }
        ),
        encoding="utf-8",
    )
    lane = run_evaluator(
        evaluator_path,
        {"fixture": workspace},
        1,
        runner=timeout_runner,
    )
    if lane["status"] != "fail" or lane["checks"][0]["status"] != "fail":
        raise AssertionError(f"lane timeout was not attributed to the task: {lane}")

    def missing_tool_runner(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(command, 127, "", "command not found")

    missing_tool = run_evaluator(
        evaluator_path,
        {"fixture": workspace},
        1,
        runner=missing_tool_runner,
    )
    if missing_tool["status"] != "infrastructure-error":
        raise AssertionError(f"missing evaluator tool was attributed to the task: {missing_tool}")

    evaluator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "setup_commands": [
                    {
                        "id": "setup-timeout",
                        "repository_id": "fixture",
                        "argv": ["python3", "setup.py"],
                    }
                ],
                "commands": [
                    {
                        "id": "lane",
                        "repository_id": "fixture",
                        "scope": "lane",
                        "argv": ["python3", "check.py"],
                    }
                ],
                "positive_checks": ["setup completes"],
                "negative_checks": ["setup timeout stays infrastructure"],
            }
        ),
        encoding="utf-8",
    )
    setup = run_setup_commands(
        evaluator_path,
        {"fixture": workspace},
        1,
        runner=timeout_runner,
    )
    if setup["status"] != "infrastructure-error":
        raise AssertionError(f"setup timeout was attributed to the task: {setup}")


def test_untrusted_symlink_is_never_restored_by_host(root: Path) -> None:
    workspace = root / "symlink-workspace"
    workspace.mkdir()
    target = workspace / "replace-me.txt"
    target.write_text("original\n", encoding="utf-8")
    outside = root / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    evaluator_dir = root / "symlink-evaluator"
    evaluator_dir.mkdir()
    (evaluator_dir / "hidden.txt").write_text("hidden\n", encoding="utf-8")
    evaluator_path = evaluator_dir / "evaluator.json"
    evaluator_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "injections": [
                    {
                        "repository_id": "fixture",
                        "source": "hidden.txt",
                        "target": "replace-me.txt",
                        "replace": True,
                    }
                ],
                "commands": [
                    {
                        "id": "symlink-swap",
                        "repository_id": "fixture",
                        "scope": "lane",
                        "argv": ["python3", "check.py"],
                    }
                ],
                "positive_checks": ["workspace is disposable"],
                "negative_checks": ["host restore never follows an untrusted symlink"],
            }
        ),
        encoding="utf-8",
    )

    def symlink_runner(command: list[str], **_kwargs):
        target.unlink()
        target.symlink_to(outside)
        return subprocess.CompletedProcess(command, 0, "", "")

    result = run_evaluator(
        evaluator_path,
        {"fixture": workspace},
        30,
        runner=symlink_runner,
    )
    if result["status"] != "pass" or outside.read_text(encoding="utf-8") != "outside\n":
        raise AssertionError("host cleanup followed a symlink created by evaluator code")


def test_injection_does_not_follow_symlink_races(root: Path) -> None:
    workspace = root / "injection-race-workspace"
    workspace.mkdir()
    target = workspace / "replace-me.txt"
    target.write_text("workspace\n", encoding="utf-8")
    outside = root / "injection-race-outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    evaluator_dir = root / "injection-race-evaluator"
    evaluator_dir.mkdir()
    (evaluator_dir / "hidden.txt").write_text("hidden\n", encoding="utf-8")
    evaluator_path = evaluator_dir / "evaluator.json"
    evaluator = {
        "schema_version": 1,
        "injections": [
            {
                "repository_id": "fixture",
                "source": "hidden.txt",
                "target": "replace-me.txt",
                "replace": True,
            }
        ],
    }
    evaluator_path.write_text(json.dumps(evaluator), encoding="utf-8")

    original_metadata = model_eval_evaluator._target_metadata

    def swap_after_metadata(parent_descriptor: int, filename: str):
        metadata = original_metadata(parent_descriptor, filename)
        target.unlink()
        target.symlink_to(outside)
        return metadata

    model_eval_evaluator._target_metadata = swap_after_metadata
    try:
        try:
            inject_evaluator_files(evaluator_path, evaluator, {"fixture": workspace})
        except EvaluatorError:
            pass
        else:
            raise AssertionError("injection accepted a target swapped to a symlink")
    finally:
        model_eval_evaluator._target_metadata = original_metadata
    if outside.read_text(encoding="utf-8") != "outside\n":
        raise AssertionError("injection followed a target symlink outside the workspace")

    target.unlink()
    outside_directory = root / "injection-race-outside-directory"
    outside_directory.mkdir()
    (workspace / "nested").symlink_to(outside_directory, target_is_directory=True)
    evaluator["injections"][0].update({"target": "nested/created.txt", "replace": False})
    try:
        inject_evaluator_files(evaluator_path, evaluator, {"fixture": workspace})
    except EvaluatorError:
        pass
    else:
        raise AssertionError("injection accepted a symlinked parent directory")
    if (outside_directory / "created.txt").exists():
        raise AssertionError("injection followed a parent symlink outside the workspace")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-evaluator-") as raw_root:
        root = Path(raw_root)
        test_base_fails_and_gold_passes(root)
        test_gold_test_replacement_is_restored(root)
        test_replace_or_create_restores_both_states(root)
        test_timeout_attribution(root)
        test_untrusted_symlink_is_never_restored_by_host(root)
        test_injection_does_not_follow_symlink_races(root)
    print("PASS model eval evaluator fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
