#!/usr/bin/env python3
"""Synthetic tests for the generic model-evaluation cell runner."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from model_eval_adapter import AdapterRun
from model_eval_manifest import CorpusManifest
from model_eval_runner import (
    EvalConfiguration,
    _infrastructure_cell,
    _selection_status,
    run_eval_cell,
)
from model_eval_score import validate_cell
from model_eval_workspace import assert_repository_state, snapshot_repository_state


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def create_repository(root: Path, name: str) -> tuple[str, str]:
    root.mkdir()
    run(["git", "init", "--quiet"], root)
    run(["git", "config", "user.name", "Agent Flow Test"], root)
    run(["git", "config", "user.email", "agent-flow@example.invalid"], root)
    (root / "src").mkdir()
    (root / "src" / "value.txt").write_text(f"base-{name}\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored-output.txt\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-qm", "base"], root)
    base = run(["git", "rev-parse", "HEAD"], root)
    (root / "src" / "value.txt").write_text(f"gold-{name}\n", encoding="utf-8")
    run(["git", "commit", "-qam", "gold"], root)
    gold = run(["git", "rev-parse", "HEAD"], root)
    (root / "local-note.txt").write_text("pre-existing dirty state\n", encoding="utf-8")
    return base, gold


def task_facts() -> dict:
    return {
        "role": "bun-worker",
        "changes_files": True,
        "repo_count": 2,
        "surfaces": ["backend"],
        "task_classes": ["cross-repo", "public-contract"],
        "primary_task_class": "public-contract",
        "public_contract": True,
        "migration": False,
        "external_write": False,
        "production_risk": "normal",
    }


def create_corpus(root: Path) -> tuple[CorpusManifest, dict[str, Path]]:
    root.mkdir(parents=True)
    source_one = root / "source-one"
    source_two = root / "source-two"
    one_base, one_gold = create_repository(source_one, "one")
    two_base, two_gold = create_repository(source_two, "two")
    corpus_root = root / "corpus"
    (corpus_root / "tasks" / "fixture-task").mkdir(parents=True)
    (corpus_root / "tasks" / "fixture-task" / "prompt.md").write_text(
        "Implement the fixture behavior in each owned repository.\n",
        encoding="utf-8",
    )
    evaluator_root = corpus_root / "evaluators" / "fixture-task"
    evaluator_root.mkdir(parents=True)
    evaluator = {
        "schema_version": 1,
        "setup_commands": [
            {"id": "fixture-setup", "repository_id": "repo_one", "argv": ["fixture-setup"]}
        ],
        "commands": [
            {
                "id": "fixture-integration",
                "repository_id": "repo_one",
                "scope": "integration",
                "argv": ["fixture-evaluator"],
            }
        ],
        "positive_checks": ["both repositories implement the fixture"],
        "negative_checks": ["base repositories do not implement the fixture"],
    }
    (evaluator_root / "evaluator.json").write_text(
        json.dumps(evaluator, indent=2) + "\n",
        encoding="utf-8",
    )
    task = {
        "id": "fixture-task",
        "product_id": "fixture",
        "repositories": ["repo_one", "repo_two"],
        "revisions": {
            "repo_one": {"base": one_base, "gold": one_gold},
            "repo_two": {"base": two_base, "gold": two_gold},
        },
        "lanes": [
            {
                "id": "worker-one",
                "role": "bun-worker",
                "repository_ids": ["repo_one"],
                "primary_repository": "repo_one",
                "task_facts": task_facts(),
            },
            {
                "id": "worker-two",
                "role": "bun-worker",
                "repository_ids": ["repo_two"],
                "primary_repository": "repo_two",
                "task_facts": task_facts(),
            },
        ],
        "prompt": "tasks/fixture-task/prompt.md",
        "evaluator": "evaluators/fixture-task/evaluator.json",
        "allowed_paths": {"repo_one": ["src/**"], "repo_two": ["src/**"]},
        "forbidden_paths": {
            "repo_one": [".git/**", "secrets/**"],
            "repo_two": [".git/**", "secrets/**"],
        },
        "timeout_seconds": 30,
        "repeat_policy": "paired-adaptive",
        "criticality": "critical",
    }
    corpus = CorpusManifest(
        root=corpus_root,
        data={
            "schema_version": 1,
            "corpus_id": "fixture-suite",
            "repositories": {
                "repo_one": {"path_env": "FIXTURE_REPO_ONE"},
                "repo_two": {"path_env": "FIXTURE_REPO_TWO"},
            },
            "tasks": [task],
        },
        repositories={"repo_one": source_one, "repo_two": source_two},
    )
    return corpus, {"repo_one": source_one, "repo_two": source_two}


def create_predictability_corpus(root: Path) -> tuple[CorpusManifest, Path]:
    corpus, sources = create_corpus(root)
    task = corpus.data["tasks"][0]
    task["repositories"] = ["repo_one"]
    task["revisions"] = {"repo_one": task["revisions"]["repo_one"]}
    task["lanes"] = [task["lanes"][0]]
    task["allowed_paths"] = {"repo_one": ["src/**"]}
    task["forbidden_paths"] = {"repo_one": [".git/**", "secrets/**"]}
    response_path = corpus.root / "tasks" / "fixture-task" / "architect-response.md"
    response_path.write_text(
        "decision_id: fixture-contract\nUse the repository's canonical event type.\n",
        encoding="utf-8",
    )
    task["predictability"] = {
        "class": "ambiguity",
        "required_behaviors": [
            {"id": "update-value", "points": 20, "check_ids": ["fixture-integration"]},
            {"id": "verify-value", "points": 20, "check_ids": ["fixture-integration"]},
        ],
        "forbidden_behaviors": [
            {
                "id": "no-extra-files",
                "severity": "hard",
                "deduction": 15,
                "check_ids": ["fixture-integration"],
            }
        ],
        "ambiguity": {
            "decision_id": "fixture-contract",
            "affected_requirements": ["update-value"],
            "architect_response": "tasks/fixture-task/architect-response.md",
        },
        "claim_check_ids": ["fixture-integration"],
    }
    corpus.data["repositories"] = {"repo_one": corpus.data["repositories"]["repo_one"]}
    return CorpusManifest(
        root=corpus.root,
        data=corpus.data,
        repositories={"repo_one": sources["repo_one"]},
    ), sources["repo_one"]


class FakeCommandRunner:
    def __init__(
        self,
        events: list[str],
        fail_setup: bool = False,
        create_dependencies: bool = False,
        late_mutation_workspaces: list[Path] | None = None,
    ) -> None:
        self.events = events
        self.fail_setup = fail_setup
        self.create_dependencies = create_dependencies
        self.late_mutation_workspaces = late_mutation_workspaces
        self.setup_calls = 0

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if kwargs.get("shell") is not None:
            raise AssertionError("evaluator commands must not use a shell")
        environment = kwargs["env"]
        if "OPENAI_API_KEY" in environment:
            raise AssertionError("secret environment leaked into evaluator command")
        if environment.get("HTTP_PROXY") != "http://127.0.0.1:9":
            raise AssertionError("external network was not blocked")
        if environment.get("NO_PROXY") != "127.0.0.1,localhost,::1":
            raise AssertionError("localhost proxy bypass is missing")
        if argv[:2] != ["codex", "sandbox"] or "--" not in argv:
            raise AssertionError("evaluator command did not use the permission-profile sandbox")
        command = argv[argv.index("--") + 1 :]
        self.events.append(command[0])
        profile = " ".join(argv)
        if command == ["fixture-evaluator"]:
            if '"."="read"' not in profile:
                raise AssertionError("evaluator workspace was not mounted read-only")
        elif '"."="write"' not in profile:
            raise AssertionError("trusted setup workspace was not writable")
        if command == ["fixture-setup"]:
            self.setup_calls += 1
            if (
                self.setup_calls == 2
                and self.late_mutation_workspaces
            ):
                old_workspace = self.late_mutation_workspaces[0]
                (old_workspace / "src" / "value.txt").write_text(
                    "late-detached-mutation\n",
                    encoding="utf-8",
                )
        if self.create_dependencies and command == ["fixture-setup"]:
            vendor = Path(kwargs["cwd"]) / "vendor" / "example"
            vendor.mkdir(parents=True)
            (vendor / "dependency.txt").write_text("dependency\n", encoding="utf-8")
        if command == ["fixture-evaluator"] and self.late_mutation_workspaces:
            current_workspace = Path(kwargs["cwd"])
            if current_workspace in self.late_mutation_workspaces:
                raise AssertionError("hidden evaluator reused a model workspace")
            if (current_workspace / "src" / "value.txt").read_text(encoding="utf-8") != (
                "implemented-worker-one\n"
            ):
                raise AssertionError("fresh evaluator clone did not receive the captured patch")
        returncode = 1 if self.fail_setup and command == ["fixture-setup"] else 0
        return subprocess.CompletedProcess(
            argv,
            returncode,
            "OPENAI_API_KEY=setup-secret\n",
            "",
        )


class FakeLaneRunner:
    def __init__(
        self,
        events: list[str],
        violating_lane: str | None = None,
        leak_marker: str | None = None,
        metadata_lane: str | None = None,
        ignored_lane: str | None = None,
        hostile_filter_lane: str | None = None,
        hostile_filter_marker: Path | None = None,
        unsafe_symlink_lane: str | None = None,
    ) -> None:
        self.events = events
        self.violating_lane = violating_lane
        self.leak_marker = leak_marker
        self.metadata_lane = metadata_lane
        self.ignored_lane = ignored_lane
        self.hostile_filter_lane = hostile_filter_lane
        self.hostile_filter_marker = hostile_filter_marker
        self.unsafe_symlink_lane = unsafe_symlink_lane
        self.prompts: list[str] = []
        self.workspace_paths: list[Path] = []

    def __call__(self, config, prompt: str) -> AdapterRun:
        if not self.events or self.events[0] != "fixture-setup":
            raise AssertionError("dependency setup did not run before the model lane")
        if "fixture-evaluator" in self.events:
            raise AssertionError("hidden evaluator ran before the model lane")
        if config.selected_skills:
            raise AssertionError("optional skills were enabled")
        if prompt.startswith("---") or "reasoning_effort:" in prompt.split("# Task", 1)[0]:
            raise AssertionError("role YAML frontmatter leaked into the prompt")
        if "Implement the fixture behavior" not in prompt or "# Lane scope" not in prompt:
            raise AssertionError("task prompt or lane scope is missing")
        self.prompts.append(prompt)
        self.workspace_paths.append(config.primary_workspace)
        self.events.append(config.lane_id)
        if config.lane_id == self.violating_lane:
            target = config.primary_workspace / "secrets" / "token.txt"
            target.parent.mkdir()
            target.write_text("fixture violation\n", encoding="utf-8")
            changed_paths = ["secrets/token.txt"]
        else:
            target = config.primary_workspace / "src" / "value.txt"
            target.write_text(f"implemented-{config.lane_id}\n", encoding="utf-8")
            changed_paths = ["src/value.txt"]
        if config.lane_id == self.metadata_lane:
            with (config.primary_workspace / ".git" / "config").open("a", encoding="utf-8") as output:
                output.write("\n[agentflow-test]\n\tchanged = true\n")
        if config.lane_id == self.ignored_lane:
            (config.primary_workspace / "ignored-output.txt").write_text(
                "hidden implementation\n",
                encoding="utf-8",
            )
        if config.lane_id == self.unsafe_symlink_lane:
            (config.primary_workspace / "src" / "unsafe-link").symlink_to(
                "target\n+++ b/injected"
            )
            changed_paths.append("src/unsafe-link")
        if config.lane_id == self.hostile_filter_lane:
            if self.hostile_filter_marker is None:
                raise AssertionError("hostile filter marker is missing")
            install_hostile_process_filter(
                config.primary_workspace,
                self.hostile_filter_marker,
            )
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_text('{"summary":"sk-raw-output-secret"}', encoding="utf-8")
        lane_result = {
            "lane_id": config.lane_id,
            "role": config.role,
            "adapter": "cli",
            "requested_model": config.model,
            "requested_reasoning": config.reasoning_effort,
            "selected_model": config.model,
            "selected_reasoning": config.reasoning_effort,
            "selection_status": "exact",
            "selection_evidence": [
                {
                    "source": "codex-rollout",
                    "thread_id": f"fixture-{config.lane_id}",
                    "turn_id": f"fixture-turn-{config.lane_id}",
                    "cli_version": "0.144.1",
                    "model_provider": "openai",
                    "configured_model": config.model,
                    "configured_reasoning": config.reasoning_effort,
                    "selected_model": config.model,
                    "selected_reasoning": config.reasoning_effort,
                    "reroutes": [],
                }
            ],
            "fallback_reason": None,
            "verification_level": "normal",
            "selected_skills": [],
            "active_gates": [],
            "status": "pass",
            "error_kind": None,
            "summary": "Implemented with sk-1234567890",
            "changed_paths": changed_paths,
            "checks": [],
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 5,
                "reasoning_output_tokens": 2,
            },
            "duration_ms": 20,
            "attempts": 1,
            "handoff_path": None,
        }
        return AdapterRun(
            lane_result=lane_result,
            stdout_jsonl=(
                '{"authorization":"Bearer lane-secret"}\n'
                + (self.leak_marker + "\n" if self.leak_marker else "")
            ),
            stderr="OPENAI_API_KEY=lane-secret\n",
        )


class FakePredictabilityLaneRunner:
    def __init__(self, events: list[str], *, mutate_before_decision: bool = False) -> None:
        self.events = events
        self.mutate_before_decision = mutate_before_decision
        self.thread_id = "fixture-predictability-thread"
        self.initial_prompt = ""
        self.continuation_prompt = ""

    def _evidence(self, config, turn: int) -> dict:
        return {
            "source": "codex-rollout",
            "thread_id": self.thread_id,
            "turn_id": f"fixture-predictability-turn-{turn}",
            "cli_version": "0.144.1",
            "model_provider": "openai",
            "configured_model": config.model,
            "configured_reasoning": config.reasoning_effort,
            "selected_model": config.model,
            "selected_reasoning": config.reasoning_effort,
            "reroutes": [],
        }

    def _base_result(self, config, turn: int) -> dict:
        evidence = self._evidence(config, turn)
        return {
            "lane_id": config.lane_id,
            "role": config.role,
            "adapter": "cli",
            "requested_model": config.model,
            "requested_reasoning": config.reasoning_effort,
            "selected_model": config.model,
            "selected_reasoning": config.reasoning_effort,
            "selection_status": "exact",
            "selection_evidence": [evidence],
            "fallback_reason": None,
            "verification_level": "normal",
            "selected_skills": [],
            "active_gates": [],
            "error_kind": None,
            "checks": [],
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 5,
                "reasoning_output_tokens": 2,
            },
            "duration_ms": 20,
            "attempts": 1,
            "handoff_path": None,
            "thread_id": self.thread_id,
            "turn_count": turn,
            "turns": [
                {
                    "order": turn,
                    "thread_id": self.thread_id,
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 4,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    },
                    "duration_ms": 20,
                    "attempts": 1,
                    "selection_evidence": [evidence],
                }
            ],
        }

    def __call__(self, config, prompt: str) -> AdapterRun:
        if not config.predictability_output:
            raise AssertionError("runner did not select predictability output")
        if "# Required behavior" not in prompt or "# Decision protocol" not in prompt:
            raise AssertionError("predictability task packet is incomplete")
        if "Use the repository's canonical event type" in prompt:
            raise AssertionError("architect response leaked into the initial prompt")
        self.initial_prompt = prompt
        self.events.append("predictability-turn-1")
        if self.mutate_before_decision:
            (config.primary_workspace / "src" / "value.txt").write_text(
                "premature-change\n",
                encoding="utf-8",
            )
        result = {
            **self._base_result(config, 1),
            "status": "decision_request",
            "summary": "A contract decision is required.",
            "changed_paths": [],
            "decision_id": "fixture-contract",
            "missing_decision": "Which event type is canonical?",
            "affected_requirements": ["update-value"],
            "decision_evidence": ["Two contracts disagree."],
            "requirement_results": [],
        }
        return AdapterRun(result, '{"type":"turn.completed"}\n', "")

    def resume(self, config, prompt: str, thread_id: str) -> AdapterRun:
        if thread_id != self.thread_id:
            raise AssertionError("runner resumed a different thread")
        if "decision_id: fixture-contract" not in prompt:
            raise AssertionError("architect response is missing from continuation")
        self.continuation_prompt = prompt
        self.events.append("predictability-turn-2")
        target = config.primary_workspace / "src" / "value.txt"
        target.write_text("implemented-worker-one\n", encoding="utf-8")
        result = {
            **self._base_result(config, 2),
            "status": "pass",
            "summary": "Implemented after the architect decision.",
            "changed_paths": ["src/value.txt"],
            "decision_id": None,
            "missing_decision": None,
            "affected_requirements": [],
            "decision_evidence": [],
            "requirement_results": [
                {"id": "update-value", "status": "pass", "evidence": "Value updated."},
                {"id": "verify-value", "status": "pass", "evidence": "Check passed."},
            ],
        }
        return AdapterRun(result, '{"type":"turn.completed"}\n', "")


class InvalidEvidenceLaneRunner:
    def __init__(self, delegate: FakeLaneRunner, mode: str) -> None:
        self.delegate = delegate
        self.mode = mode

    def __call__(self, config, prompt: str) -> AdapterRun:
        result = self.delegate(config, prompt)
        lane_result = dict(result.lane_result)
        if self.mode == "usage":
            lane_result["usage"] = {"input_tokens": 10, "output_tokens": 5}
        elif self.mode == "selection":
            lane_result.pop("selection_evidence")
        elif self.mode == "duplicate-turn":
            first = lane_result["selection_evidence"][0]
            second = {**first, "thread_id": first["thread_id"] + "-retry"}
            lane_result["selection_evidence"] = [first, second]
            lane_result["attempts"] = 2
        else:
            raise AssertionError(f"unknown invalid-evidence mode: {self.mode}")
        return AdapterRun(lane_result, result.stdout_jsonl, result.stderr)


def run_fixture(
    root: Path,
    corpus: CorpusManifest,
    lane_runner,
    command_runner,
    name: str,
):
    return run_eval_cell(
        corpus,
        "fixture-task",
        EvalConfiguration("luna-medium", "gpt-5.6-luna", "medium"),
        1,
        root / f"temp-{name}",
        root / f"artifacts-{name}",
        lane_runner=lane_runner,
        command_runner=command_runner,
    )


def test_missing_lane_evidence_is_unverified() -> None:
    if _selection_status([]) != "unverified":
        raise AssertionError("empty lane evidence was reported as exact")
    configuration = EvalConfiguration("luna-medium", "gpt-5.6-luna", "medium")
    cell = _infrastructure_cell(
        {"id": "fixture-task", "lanes": [{"id": "worker"}]},
        configuration,
        1,
    )
    if cell["selection_status"] != "unverified":
        raise AssertionError("pre-lane infrastructure failure claimed exact selection")


def assert_no_secret_artifacts(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        value = path.read_text(encoding="utf-8", errors="replace")
        for secret in ("setup-secret", "lane-secret", "sk-1234567890"):
            if secret in value:
                raise AssertionError(f"secret leaked into {path.name}")


def install_hostile_process_filter(repository: Path, marker: Path) -> None:
    script = repository / "hostile-filter"
    script.write_text(
        "#!/bin/sh\n"
        f"printf 'filter ran\\n' >> {shlex.quote(str(marker))}\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    (repository / ".gitattributes").write_text(
        "src/value.txt filter=agent-flow-hostile\n",
        encoding="utf-8",
    )
    include = repository / ".git" / "hostile-filter-config"
    include.write_text(
        "[filter \"agent-flow-hostile\"]\n"
        f"\tprocess = {json.dumps(str(script))}\n"
        "\trequired = false\n",
        encoding="utf-8",
    )
    with (repository / ".git" / "config").open("a", encoding="utf-8") as output:
        output.write(
            "\n[include]\n"
            f"\tpath = {json.dumps(str(include))}\n"
        )


def test_multi_repo_success(root: Path) -> None:
    corpus, sources = create_corpus(root / "success")
    source_states = {
        repository_id: snapshot_repository_state(source)
        for repository_id, source in sources.items()
    }
    events: list[str] = []
    lane_runner = FakeLaneRunner(events)
    previous = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = "host-secret"
    try:
        result = run_fixture(
            root,
            corpus,
            lane_runner,
            FakeCommandRunner(events),
            "success",
        )
    finally:
        if previous is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = previous
    validate_cell(result.cell)
    if result.cell["status"] != "pass" or result.cell["integration_pass"] is not True:
        raise AssertionError(f"successful multi-repo cell did not pass: {result.cell}")
    if result.cell["lane_statuses"] != ["pass", "pass"]:
        raise AssertionError("both sequential lanes must pass")
    if result.cell["model_attempts"] != 2:
        raise AssertionError("successful cell lost its per-lane model attempts")
    if result.cell["token_usage"] != 30 or result.cell["duration_ms"] < 40:
        raise AssertionError("lane usage or duration was not aggregated")
    if events != [
        "fixture-setup",
        "worker-one",
        "worker-two",
        "fixture-setup",
        "fixture-evaluator",
    ]:
        raise AssertionError(f"cell execution order is wrong: {events}")
    if result.details["evaluator_setup"]["status"] != "pass":
        raise AssertionError("fresh evaluator clone did not repeat trusted setup")
    for prompt in lane_runner.prompts:
        if "evaluator.json" in prompt:
            raise AssertionError("hidden evaluator path leaked into a model prompt")
        for revisions in corpus.tasks[0]["revisions"].values():
            if revisions["gold"] in prompt:
                raise AssertionError("gold revision leaked into a model prompt")
    artifacts = root / "artifacts-success"
    for repository_id in ("repo_one", "repo_two"):
        patch = artifacts / "patches" / f"{repository_id}.patch"
        if not patch.is_file() or "implemented-worker" not in patch.read_text(encoding="utf-8"):
            raise AssertionError(f"separate patch is missing for {repository_id}")
    if any(artifacts.glob("lanes/*/agent-output.json")):
        raise AssertionError("raw model output was retained")
    assert_no_secret_artifacts(artifacts)
    if any((root / "temp-success" / "cells").iterdir()):
        raise AssertionError("synthetic workspaces were not cleaned")
    if not result.details["source_state_preserved"]:
        raise AssertionError("runner did not preserve source repository state")
    for repository_id, source in sources.items():
        assert_repository_state(source, source_states[repository_id])


def test_fresh_evaluator_clone_ignores_late_model_workspace_mutation(root: Path) -> None:
    corpus, _ = create_corpus(root / "fresh-clone")
    events: list[str] = []
    lane_runner = FakeLaneRunner(events)
    command_runner = FakeCommandRunner(
        events,
        late_mutation_workspaces=lane_runner.workspace_paths,
    )
    result = run_fixture(
        root,
        corpus,
        lane_runner,
        command_runner,
        "fresh-clone",
    )
    if result.cell["status"] != "pass":
        raise AssertionError(f"late old-workspace mutation reached evaluator: {result.cell}")
    if not lane_runner.workspace_paths:
        raise AssertionError("fixture did not record model workspaces")


def test_scope_violation_fails_cell(root: Path) -> None:
    corpus, _ = create_corpus(root / "scope")
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events, violating_lane="worker-one"),
        FakeCommandRunner(events),
        "scope",
    )
    validate_cell(result.cell)
    if result.cell["status"] != "fail" or result.cell["lane_statuses"][0] != "fail":
        raise AssertionError(f"scope violation did not fail the lane: {result.cell}")
    if result.cell["critical_failures"] < 1:
        raise AssertionError("scope violation was not counted as critical")
    violations = result.details["lanes"][0]["boundary"]["violations"]
    kinds = {violation["kind"] for violation in violations}
    if not {"outside-allowed-paths", "forbidden-path"} <= kinds:
        raise AssertionError(f"scope evidence is incomplete: {violations}")


def test_setup_failure_skips_model(root: Path) -> None:
    corpus, _ = create_corpus(root / "setup")
    events: list[str] = []

    def forbidden_lane_runner(config, prompt):
        raise AssertionError("model lane ran after dependency setup failed")

    result = run_fixture(
        root,
        corpus,
        forbidden_lane_runner,
        FakeCommandRunner(events, fail_setup=True),
        "setup",
    )
    validate_cell(result.cell)
    if result.cell["status"] != "infrastructure-error":
        raise AssertionError("setup failure was attributed to the model")
    if result.cell["integration_required"] is not True or result.cell["integration_pass"] is not False:
        raise AssertionError("infrastructure cell lost its integration contract")
    if events != ["fixture-setup"]:
        raise AssertionError(f"unexpected work ran after setup failure: {events}")
    setup = json.loads((root / "artifacts-setup" / "setup.json").read_text(encoding="utf-8"))
    if setup["error_kind"] != "setup-failed":
        raise AssertionError("setup failure evidence was not saved")


def test_hidden_marker_is_redacted_and_excluded(root: Path) -> None:
    corpus, _ = create_corpus(root / "isolation")
    marker = corpus.tasks[0]["revisions"]["repo_one"]["gold"]
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events, leak_marker=marker),
        FakeCommandRunner(events),
        "isolation",
    )
    validate_cell(result.cell)
    if result.cell["status"] != "infrastructure-error":
        raise AssertionError("hidden marker leak was attributed to the model")
    if result.cell["model_attempts"] != 1:
        raise AssertionError("isolation failure lost the executed model attempt")
    errors = result.details["runtime_errors"]
    if not errors or errors[0]["kind"] != "isolation-violation":
        raise AssertionError(f"isolation evidence is missing: {errors}")
    if errors[0]["findings"][0]["kind"] != "gold-sha":
        raise AssertionError(f"wrong isolation marker classification: {errors}")
    for path in (root / "artifacts-isolation").rglob("*"):
        if path.is_file() and marker in path.read_text(encoding="utf-8", errors="replace"):
            raise AssertionError(f"hidden marker was persisted in {path.name}")
    if "fixture-evaluator" in events:
        raise AssertionError("evaluator ran after an isolation violation")


def test_invalid_lane_evidence_is_not_scorable(root: Path) -> None:
    for mode in ("usage", "selection", "duplicate-turn"):
        corpus, _ = create_corpus(root / mode)
        events: list[str] = []
        result = run_fixture(
            root,
            corpus,
            InvalidEvidenceLaneRunner(FakeLaneRunner(events), mode),
            FakeCommandRunner(events),
            f"invalid-{mode}",
        )
        if result.cell["status"] != "infrastructure-error":
            raise AssertionError(f"invalid {mode} evidence remained scorable")
        expected_attempts = 2 if mode == "duplicate-turn" else 1
        if result.cell["model_attempts"] != expected_attempts:
            raise AssertionError(f"invalid {mode} evidence lost its model attempt")
        if "fixture-evaluator" in events:
            raise AssertionError(f"evaluator ran after invalid {mode} evidence")


def test_git_config_change_fails_boundary(root: Path) -> None:
    corpus, _ = create_corpus(root / "metadata")
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events, metadata_lane="worker-one"),
        FakeCommandRunner(events),
        "metadata",
    )
    validate_cell(result.cell)
    if result.cell["status"] != "fail" or result.cell["lane_statuses"][0] != "fail":
        raise AssertionError("git config change did not fail the owning lane")
    kinds = {
        violation["kind"]
        for violation in result.details["lanes"][0]["boundary"]["violations"]
    }
    if "git-metadata-changed" not in kinds:
        raise AssertionError("git config fingerprint change was not recorded")


def test_ignored_file_change_fails_boundary(root: Path) -> None:
    corpus, _ = create_corpus(root / "ignored")
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events, ignored_lane="worker-one"),
        FakeCommandRunner(events),
        "ignored",
    )
    validate_cell(result.cell)
    if result.cell["status"] != "fail" or result.cell["lane_statuses"][0] != "fail":
        raise AssertionError("ignored file change did not fail the owning lane")
    kinds = {
        violation["kind"]
        for violation in result.details["lanes"][0]["boundary"]["violations"]
    }
    if "ignored-path-changed" not in kinds:
        raise AssertionError("ignored file change was not recorded")


def test_setup_dependency_tree_is_not_model_patch(root: Path) -> None:
    corpus, _ = create_corpus(root / "dependencies")
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events),
        FakeCommandRunner(events, create_dependencies=True),
        "dependencies",
    )
    if result.cell["status"] != "pass":
        raise AssertionError(f"trusted setup dependency tree failed the cell: {result.cell}")
    for patch in (root / "artifacts-dependencies" / "patches").glob("*.patch"):
        if "vendor/example" in patch.read_text(encoding="utf-8"):
            raise AssertionError("setup dependency tree leaked into the model patch")


def test_git_evidence_neutralizes_included_process_filter(root: Path) -> None:
    control = root / "git-filter-control"
    control.mkdir()
    run(["git", "init", "--quiet"], control)
    run(["git", "config", "user.name", "Agent Flow Test"], control)
    run(["git", "config", "user.email", "agent-flow@example.invalid"], control)
    (control / "src").mkdir()
    (control / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    run(["git", "add", "."], control)
    run(["git", "commit", "-qm", "base"], control)
    control_marker = root / "vulnerable-git-filter-ran"
    install_hostile_process_filter(control, control_marker)
    (control / "src" / "value.txt").write_text("changed\n", encoding="utf-8")
    vulnerable_environment = os.environ.copy()
    for key in list(vulnerable_environment):
        if key.startswith("GIT_"):
            vulnerable_environment.pop(key)
    vulnerable_environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    vulnerable = subprocess.run(
        ["git", "--no-pager", "-C", str(control), "diff", "--name-only", "HEAD", "--"],
        env=vulnerable_environment,
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    if vulnerable.returncode != 0 or not control_marker.is_file():
        raise AssertionError("control Git diff did not demonstrate process-filter execution")
    control_marker.unlink()

    marker = root / "hardened-git-filter-ran"
    corpus, _ = create_corpus(root / "git-filter-hardened")
    events: list[str] = []
    hostile_environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "filter.agent-flow-hostile.process",
        "GIT_CONFIG_VALUE_0": str(control / "hostile-filter"),
        "GIT_EXTERNAL_DIFF": str(control / "hostile-filter"),
    }
    previous = {key: os.environ.get(key) for key in hostile_environment}
    os.environ.update(hostile_environment)
    try:
        result = run_fixture(
            root,
            corpus,
            FakeLaneRunner(
                events,
                hostile_filter_lane="worker-one",
                hostile_filter_marker=marker,
            ),
            FakeCommandRunner(events),
            "git-filter-hardened",
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if marker.exists():
        raise AssertionError("hardened Git evidence executed a repository process filter")
    if control_marker.exists():
        raise AssertionError("hardened Git evidence inherited a hostile host Git environment")
    if result.cell["status"] != "fail":
        raise AssertionError("hostile Git metadata and attributes did not fail the boundary")
    kinds = {
        violation["kind"]
        for violation in result.details["lanes"][0]["boundary"]["violations"]
    }
    if "git-metadata-changed" not in kinds:
        raise AssertionError("hostile repository config mutation was not recorded")


def test_patch_capture_rejects_control_character_symlink_target(root: Path) -> None:
    corpus, _ = create_corpus(root / "unsafe-symlink")
    events: list[str] = []
    result = run_fixture(
        root,
        corpus,
        FakeLaneRunner(events, unsafe_symlink_lane="worker-one"),
        FakeCommandRunner(events),
        "unsafe-symlink",
    )
    if result.cell["status"] != "infrastructure-error":
        raise AssertionError("unsafe symlink patch was not rejected as infrastructure")
    if "fixture-evaluator" in events:
        raise AssertionError("hidden evaluator ran after unsafe patch capture")
    errors = result.details["runtime_errors"]
    if not errors or "symlink target contains a control character" not in errors[0]["summary"]:
        raise AssertionError(f"unsafe symlink rejection was not recorded: {errors}")


def test_predictability_decision_protocol_resumes_after_clean_workspace(root: Path) -> None:
    corpus, source = create_predictability_corpus(root / "predictability")
    source_state = snapshot_repository_state(source)
    events: list[str] = []
    lane_runner = FakePredictabilityLaneRunner(events)
    artifacts = root / "artifacts-predictability"
    result = run_eval_cell(
        corpus,
        "fixture-task",
        EvalConfiguration("luna-medium", "gpt-5.6-luna", "medium"),
        1,
        root / "temp-predictability",
        artifacts,
        lane_runner=lane_runner,
        resume_lane_runner=lane_runner.resume,
        command_runner=FakeCommandRunner(events),
    )
    if result.cell["status"] != "pass" or result.cell["model_attempts"] != 2:
        raise AssertionError(f"predictability continuation failed: {result.cell}")
    protocol = json.loads(
        (artifacts / "lanes" / "worker-one" / "decision-protocol.json").read_text(
            encoding="utf-8"
        )
    )
    if not protocol["resume_allowed"] or not protocol["resumed"]:
        raise AssertionError("valid decision request did not resume")
    if not protocol["workspace_before_response"]["clean"]:
        raise AssertionError("runner did not prove a clean pre-decision workspace")
    if not protocol["terminal_requirement_ids_match"]:
        raise AssertionError("terminal result did not cover the exact requirement ids")
    if not (artifacts / "lanes" / "worker-one" / "turn-1" / "lane-result.json").is_file():
        raise AssertionError("first turn artifact is missing")
    if not (artifacts / "lanes" / "worker-one" / "turn-2" / "lane-result.json").is_file():
        raise AssertionError("second turn artifact is missing")
    assert_repository_state(source, source_state)


def test_predictability_premature_patch_is_model_failure(root: Path) -> None:
    corpus, _ = create_predictability_corpus(root / "predictability-premature")
    events: list[str] = []
    lane_runner = FakePredictabilityLaneRunner(events, mutate_before_decision=True)
    artifacts = root / "artifacts-predictability-premature"
    result = run_eval_cell(
        corpus,
        "fixture-task",
        EvalConfiguration("luna-medium", "gpt-5.6-luna", "medium"),
        1,
        root / "temp-predictability-premature",
        artifacts,
        lane_runner=lane_runner,
        resume_lane_runner=lane_runner.resume,
        command_runner=FakeCommandRunner(events),
    )
    if result.cell["status"] != "fail":
        raise AssertionError("premature decision patch was not a model failure")
    if result.details["runtime_errors"]:
        raise AssertionError("premature patch was misclassified as infrastructure")
    protocol = result.details["lanes"][0]["decision_protocol"]
    if protocol["resumed"] or protocol["workspace_before_response"]["clean"]:
        raise AssertionError("runner resumed after a premature patch")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-runner-") as raw_root:
        root = Path(raw_root)
        test_missing_lane_evidence_is_unverified()
        test_multi_repo_success(root)
        test_fresh_evaluator_clone_ignores_late_model_workspace_mutation(root)
        test_scope_violation_fails_cell(root)
        test_setup_failure_skips_model(root)
        test_hidden_marker_is_redacted_and_excluded(root)
        test_invalid_lane_evidence_is_not_scorable(root)
        test_git_config_change_fails_boundary(root)
        test_ignored_file_change_fails_boundary(root)
        test_setup_dependency_tree_is_not_model_patch(root)
        test_git_evidence_neutralizes_included_process_filter(root)
        test_patch_capture_rejects_control_character_symlink_target(root)
        test_predictability_decision_protocol_resumes_after_clean_workspace(root)
        test_predictability_premature_patch_is_model_failure(root)
    print("PASS model eval runner fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
