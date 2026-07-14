#!/usr/bin/env python3
"""Fixture tests for the exact Codex CLI model-evaluation adapter."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from model_eval_adapter import (
    AdapterError,
    AdapterConfig,
    CODEX_CLI_VERSION,
    MAX_LANE_ATTEMPTS,
    MAX_ROLLOUT_BYTES,
    SelectionEvidenceError,
    _isolated_process_environment,
    build_codex_command,
    build_codex_resume_command,
    find_rollout_path,
    parse_rollout_selection,
    redact_jsonl,
    redact_text,
    resume_cli_lane,
    run_cli_lane,
    validate_agent_output,
    verify_prompt_context,
)


def no_context_probe(_config, _environment) -> None:
    return None


def valid_output() -> dict:
    return {
        "status": "pass",
        "summary": "Implemented the fixture.",
        "changed_paths": ["src/value.ts"],
        "checks": [
            {
                "command": "bun test",
                "status": "pass",
                "evidence": "1 test passed",
            }
        ],
        "handoff_path": None,
    }


def valid_predictability_output() -> dict:
    return {
        **valid_output(),
        "decision_id": None,
        "missing_decision": None,
        "affected_requirements": [],
        "decision_evidence": [],
        "requirement_results": [
            {
                "id": "required-behavior",
                "status": "pass",
                "evidence": "Hidden behavior check passed.",
            }
        ],
    }


def valid_decision_request() -> dict:
    return {
        "status": "decision_request",
        "summary": "A material contract decision is missing.",
        "changed_paths": [],
        "checks": [],
        "handoff_path": None,
        "decision_id": "event-contract",
        "missing_decision": "Which event type is canonical?",
        "affected_requirements": ["required-behavior"],
        "decision_evidence": ["Repository contracts disagree."],
        "requirement_results": [],
    }


THREAD_IDS = [f"01900000-0000-7000-8000-{index:012d}" for index in range(1, 40)]


def trace_stdout(
    thread_id: str,
    *,
    usage: dict[str, int] | None = None,
    reroutes: tuple[tuple[str, str, str], ...] = (),
    include_completed: bool = True,
    duplicate_completed: bool = False,
) -> str:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
    ]
    for index, (from_model, to_model, reason) in enumerate(reroutes):
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": f"item_{index}",
                    "type": "error",
                    "message": f"model rerouted: {from_model} -> {to_model} ({reason})",
                },
            }
        )
    if include_completed:
        completed = {
            "type": "turn.completed",
            "usage": usage if usage is not None else {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 5,
                "reasoning_output_tokens": 2,
            },
        }
        events.append(completed)
        if duplicate_completed:
            events.append(json.loads(json.dumps(completed)))
    return "".join(json.dumps(event) + "\n" for event in events)


def rollout_records(
    thread_id: str,
    *,
    turn_id: str = "01900000-0000-7000-9000-000000000001",
    model: str = "gpt-5.6-luna",
    effort: str | None = "medium",
    cli_version: str = CODEX_CLI_VERSION,
    provider: str = "openai",
    source: str = "exec",
    include_session: bool = True,
    include_context: bool = True,
    repeat_context: int = 1,
    extra_records: tuple[dict, ...] = (),
) -> list[dict]:
    records: list[dict] = []
    if include_session:
        records.append(
            {
                "timestamp": "2026-07-11T12:00:00Z",
                "type": "session_meta",
                "payload": {
                    "session_id": thread_id,
                    "id": thread_id,
                    "timestamp": "2026-07-11T12:00:00Z",
                    "cwd": "/synthetic",
                    "originator": "codex_exec",
                    "cli_version": cli_version,
                    "source": source,
                    "model_provider": provider,
                },
            }
        )
    if include_context:
        context = {
            "timestamp": "2026-07-11T12:00:01Z",
            "type": "turn_context",
            "payload": {
                "turn_id": turn_id,
                "model": model,
                "effort": effort,
                "summary": "auto",
            },
        }
        records.extend(
            json.loads(json.dumps(context)) for _ in range(repeat_context)
        )
    records.extend(extra_records)
    return records


def serialize_records(records: list[dict]) -> list[str]:
    return [json.dumps(record) + "\n" for record in records]


def continued_rollout(thread_id: str, turn_count: int) -> list[dict]:
    if turn_count < 1:
        raise AssertionError("turn_count must be positive")
    records = rollout_records(thread_id)
    for index in range(2, turn_count + 1):
        records.extend(
            rollout_records(
                thread_id,
                turn_id=f"01900000-0000-7000-9000-{index:012d}",
                include_session=False,
            )
        )
    return records


def expect_selection_error(
    records: list[dict],
    thread_id: str,
    expected_kind: str,
) -> None:
    try:
        parse_rollout_selection(
            serialize_records(records),
            expected_thread_id=thread_id,
        )
    except SelectionEvidenceError as exc:
        if exc.error_kind != expected_kind:
            raise AssertionError(
                f"expected {expected_kind}, got {exc.error_kind}: {exc}"
            ) from exc
    else:
        raise AssertionError(f"selection evidence unexpectedly passed: {expected_kind}")


def create_config(root: Path) -> AdapterConfig:
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    schema = Path(__file__).resolve().parents[1] / "testdata" / "model-evals" / "agent-output.schema.json"
    auth_source = root / "host-codex" / "auth.json"
    auth_source.parent.mkdir(parents=True)
    auth_source.write_text("{}\n", encoding="utf-8")
    return AdapterConfig(
        lane_id="worker",
        role="bun-worker",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        primary_workspace=workspace,
        additional_workspaces=(),
        output_schema=schema,
        output_path=root / "artifacts" / "agent-output.json",
        scratch_path=root / "scratch",
        client_codex_home=root / "client-codex",
        auth_source=auth_source,
        timeout_seconds=30,
    )


def create_predictability_config(root: Path) -> AdapterConfig:
    schema = (
        Path(__file__).resolve().parents[1]
        / "testdata"
        / "model-evals"
        / "agent-output-predictability.schema.json"
    )
    return replace(
        create_config(root),
        output_schema=schema,
        predictability_output=True,
    )


class FakeRunner:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if not self.responses:
            raise AssertionError("fake runner exhausted")
        response = self.responses.pop(0)
        self.calls.append({"command": command, **kwargs})
        output_index = command.index("--output-last-message") + 1
        output_path = Path(command[output_index])
        if "output" in response:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(response["output"]), encoding="utf-8")
        if "rollout" in response:
            thread_id = response["thread_id"]
            codex_home = Path(kwargs["env"]["CODEX_HOME"])
            rollout_path = (
                codex_home
                / "sessions"
                / "2026"
                / "07"
                / "11"
                / f"rollout-2026-07-11T12-00-00-{thread_id}.jsonl"
            )
            rollout_path.parent.mkdir(parents=True, exist_ok=True)
            rollout_path.write_text(
                "".join(serialize_records(response["rollout"])),
                encoding="utf-8",
            )
        for extra_thread_id, records in response.get("extra_rollouts", {}).items():
            codex_home = Path(kwargs["env"]["CODEX_HOME"])
            extra_path = (
                codex_home
                / "sessions"
                / "2026"
                / "07"
                / "11"
                / f"rollout-2026-07-11T13-00-00-{extra_thread_id}.jsonl"
            )
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_path.write_text(
                "".join(serialize_records(records)),
                encoding="utf-8",
            )
        if response.get("timed_out"):
            raise subprocess.TimeoutExpired(
                command,
                kwargs.get("timeout", 0),
                output=response.get("stdout", ""),
                stderr=response.get("stderr", ""),
            )
        return subprocess.CompletedProcess(
            command,
            response.get("returncode", 0),
            response.get("stdout", ""),
            response.get("stderr", ""),
        )


def success_response(
    thread_id: str,
    output: dict | None = None,
    *,
    rollout: list[dict] | None = None,
    usage: dict[str, int] | None = None,
    reroutes: tuple[tuple[str, str, str], ...] = (),
) -> dict:
    return {
        "thread_id": thread_id,
        "rollout": rollout or rollout_records(thread_id),
        "output": output or valid_output(),
        "stdout": trace_stdout(thread_id, usage=usage, reroutes=reroutes),
    }


def test_command_and_success(root: Path) -> None:
    config = create_config(root)
    command = build_codex_command(config)
    if command[:3] != ["codex", "--model", "gpt-5.6-luna"]:
        raise AssertionError("model override is missing")
    if "--ignore-user-config" not in command or "--strict-config" not in command:
        raise AssertionError("config isolation flags are missing")
    if "--ephemeral" in command:
        raise AssertionError("rollout-backed proof cannot use a pathless ephemeral thread")
    catalog_overrides = [
        value for value in command if value.startswith("model_catalog_json=")
    ]
    if catalog_overrides != [
        f'model_catalog_json="{(config.client_codex_home / "model-catalog.json").resolve()}"'
    ]:
        raise AssertionError("pinned model catalog override is missing")
    isolation_overrides = (
        "features.multi_agent=false",
        (
            "features.multi_agent_v2={enabled=false,max_concurrent_threads_per_session=1,"
            'root_agent_usage_hint_text="",subagent_usage_hint_text=""}'
        ),
        "features.enable_fanout=false",
        "agents.max_threads=1",
    )
    exec_index = command.index("exec")
    for override in isolation_overrides:
        if command.count(override) != 1 or command.index(override) > exec_index:
            raise AssertionError(f"eval isolation override is missing: {override}")
    if "--sandbox" in command or "default_permissions" not in " ".join(command):
        raise AssertionError("least-privilege permission profile is missing")
    if command[-1] != "-":
        raise AssertionError("prompt must be read from stdin")

    runner = FakeRunner([success_response(THREAD_IDS[0])])
    result = run_cli_lane(
        config,
        "Implement the task.",
        runner=runner,
        context_probe=no_context_probe,
    )
    if result.lane_result["status"] != "pass":
        raise AssertionError("successful output did not pass")
    if result.lane_result["usage"] != {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 5,
        "reasoning_output_tokens": 2,
    }:
        raise AssertionError("usage was not extracted")
    if result.lane_result["selection_status"] != "exact":
        raise AssertionError("rollout-backed exact selection was not accepted")
    evidence = result.lane_result["selection_evidence"]
    if len(evidence) != 1 or evidence[0]["configured_model"] != "gpt-5.6-luna":
        raise AssertionError("minimal rollout selection evidence was not retained")
    stdout_events = [json.loads(line) for line in result.stdout_jsonl.splitlines()]
    if any("model" in event or "reasoning_effort" in event for event in stdout_events):
        raise AssertionError("fixture stdout does not match the real Codex exec schema")
    if runner.calls[0]["input"] != "Implement the task.":
        raise AssertionError("prompt was not passed through stdin")
    if runner.calls[0].get("shell") is not None:
        raise AssertionError("adapter must not use a shell")
    environment = runner.calls[0]["env"]
    if any(key.startswith("EVAL_REPO_") or key.endswith(("_KEY", "_TOKEN", "_SECRET")) for key in environment):
        raise AssertionError("host task paths or credentials leaked into the Codex process")
    if environment.get("CODEX_HOME") != str(config.client_codex_home.resolve()):
        raise AssertionError("Codex process did not use its auth-only ephemeral home")
    if config.auth_source.parent == Path(environment["CODEX_HOME"]):
        raise AssertionError("Codex process used the host context home")
    catalog = json.loads(
        (config.client_codex_home / "model-catalog.json").read_text(encoding="utf-8")
    )
    if any(model.get("multi_agent_version") is not None for model in catalog["models"]):
        raise AssertionError("disposable model catalog retained multi-agent metadata")


def test_same_thread_predictability_continuation(root: Path) -> None:
    config = create_predictability_config(root / "predictability-continuation")
    thread_id = THREAD_IDS[20]
    runner = FakeRunner(
        [
            success_response(thread_id, valid_decision_request()),
            success_response(
                thread_id,
                valid_predictability_output(),
                rollout=continued_rollout(thread_id, 2),
                usage={
                    "input_tokens": 12,
                    "cached_input_tokens": 6,
                    "output_tokens": 7,
                    "reasoning_output_tokens": 3,
                },
            ),
        ]
    )
    initial = run_cli_lane(
        config,
        "Implement the incomplete contract.",
        runner=runner,
        context_probe=no_context_probe,
    )
    if initial.lane_result["status"] != "decision_request":
        raise AssertionError("predictability initial turn lost the decision request")
    if initial.lane_result["thread_id"] != thread_id or initial.lane_result["turn_count"] != 1:
        raise AssertionError("initial predictability turn evidence is incomplete")

    command = build_codex_resume_command(config, thread_id)
    resume_index = command.index("resume")
    if command[resume_index + 1] != "--ignore-user-config":
        raise AssertionError("resume command did not preserve config isolation")
    if command[-2:] != [thread_id, "-"]:
        raise AssertionError("resume command did not pin the thread or stdin prompt")

    terminal = resume_cli_lane(
        config,
        "Architect decision: use the canonical event contract.",
        thread_id,
        runner=runner,
    )
    if terminal.lane_result["status"] != "pass":
        raise AssertionError("same-thread continuation did not return a terminal result")
    if terminal.lane_result["thread_id"] != thread_id or terminal.lane_result["turn_count"] != 2:
        raise AssertionError("continuation did not prove the same two-turn thread")
    if terminal.lane_result["turns"][0]["usage"]["input_tokens"] != 12:
        raise AssertionError("continuation usage was not retained per logical turn")
    if runner.calls[1]["input"] != "Architect decision: use the canonical event contract.":
        raise AssertionError("architect response was not passed to the resumed thread")
    if runner.calls[0]["env"]["CODEX_HOME"] != runner.calls[1]["env"]["CODEX_HOME"]:
        raise AssertionError("continuation changed the isolated Codex home")


def test_continuation_rejects_changed_thread(root: Path) -> None:
    config = create_predictability_config(root / "predictability-wrong-thread")
    thread_id = THREAD_IDS[21]
    wrong_thread = THREAD_IDS[22]
    initial_runner = FakeRunner([success_response(thread_id, valid_decision_request())])
    run_cli_lane(
        config,
        "Implement the incomplete contract.",
        runner=initial_runner,
        context_probe=no_context_probe,
    )
    wrong_runner = FakeRunner(
        [
            success_response(
                wrong_thread,
                valid_predictability_output(),
                rollout=rollout_records(wrong_thread),
            )
        ]
    )
    result = resume_cli_lane(
        config,
        "Architect decision.",
        thread_id,
        runner=wrong_runner,
    )
    if result.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("continuation accepted a different thread id")


def test_continuation_format_retry_stays_in_thread(root: Path) -> None:
    config = create_predictability_config(root / "predictability-format-retry")
    thread_id = THREAD_IDS[23]
    initial_runner = FakeRunner([success_response(thread_id, valid_decision_request())])
    run_cli_lane(
        config,
        "Implement the incomplete contract.",
        runner=initial_runner,
        context_probe=no_context_probe,
    )
    retry_runner = FakeRunner(
        [
            success_response(
                thread_id,
                valid_output(),
                rollout=continued_rollout(thread_id, 2),
                usage={
                    "input_tokens": 11,
                    "cached_input_tokens": 5,
                    "output_tokens": 6,
                    "reasoning_output_tokens": 2,
                },
            ),
            success_response(
                thread_id,
                valid_predictability_output(),
                rollout=continued_rollout(thread_id, 3),
                usage={
                    "input_tokens": 13,
                    "cached_input_tokens": 7,
                    "output_tokens": 8,
                    "reasoning_output_tokens": 3,
                },
            ),
        ]
    )
    result = resume_cli_lane(
        config,
        "Architect decision.",
        thread_id,
        runner=retry_runner,
    )
    if result.lane_result["status"] != "pass" or result.lane_result["attempts"] != 2:
        raise AssertionError("continuation format retry did not recover")
    if result.lane_result["thread_id"] != thread_id or result.lane_result["turn_count"] != 3:
        raise AssertionError("format retry left the original thread")
    if result.lane_result["usage"]["input_tokens"] != 24:
        raise AssertionError("continuation retry usage was not fully accounted")
    if any(call["command"][-2] != thread_id for call in retry_runner.calls):
        raise AssertionError("continuation retry did not pin the original thread")


def test_multi_agent_context_and_rollouts_are_rejected(root: Path) -> None:
    config = create_config(root / "multi-agent-context-probe")
    environment = {"CODEX_HOME": str(config.client_codex_home)}
    commands: list[list[str]] = []

    def clean_probe(command: list[str], **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "[]", "")

    verify_prompt_context(config, environment, runner=clean_probe)
    if not commands or commands[0][1:3] != ["--model", config.model]:
        raise AssertionError("context probe did not use the scored model")
    if not any(
        value.startswith("features.multi_agent_v2={enabled=false,")
        for value in commands[0]
    ):
        raise AssertionError("context probe omitted the hardened V2 override")

    def local_reference_probe(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "role": "user",
                        "content": (
                            "Project instructions reference "
                            "/Users/example/.codex/AGENTS.md without loading it."
                        ),
                    }
                ]
            ),
            "",
        )

    verify_prompt_context(config, environment, runner=local_reference_probe)

    def global_context_probe(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps([{"role": "user", "content": "Dynamic Rule Loading"}]),
            "",
        )

    try:
        verify_prompt_context(config, environment, runner=global_context_probe)
    except AdapterError as exc:
        if "user-global" not in str(exc):
            raise AssertionError(f"wrong user-global context error: {exc}") from exc
    else:
        raise AssertionError("user-global instructions leaked into the eval prompt")

    def multi_agent_probe(command: list[str], **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps([{"tool": "spawn_agent"}]),
            "",
        )

    try:
        verify_prompt_context(config, environment, runner=multi_agent_probe)
    except AdapterError as exc:
        if "multi-agent" not in str(exc):
            raise AssertionError(f"wrong multi-agent context error: {exc}") from exc
    else:
        raise AssertionError("multi-agent tool leaked into the eval prompt")

    root_thread = THREAD_IDS[28]
    child_thread = THREAD_IDS[29]
    response = success_response(root_thread)
    response["extra_rollouts"] = {
        child_thread: rollout_records(child_thread),
    }
    result = run_cli_lane(
        create_config(root / "child-rollout"),
        "Implement.",
        runner=FakeRunner([response]),
        context_probe=no_context_probe,
    )
    if result.lane_result["status"] != "infrastructure-error":
        raise AssertionError("child rollout was scored")
    if result.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("child rollout had the wrong error kind")
    if result.lane_result["selection_status"] != "unverified":
        raise AssertionError("child rollout retained scoreable selection evidence")
    if result.lane_result["summary"] != valid_output()["summary"]:
        raise AssertionError("child rollout failure discarded root agent output")


def test_output_schema_compatibility(root: Path) -> None:
    config = create_config(root / "supported-schema")
    build_codex_command(config)

    unsupported_cases = {
        "uniqueItems": ("changed_paths", True),
        "minLength": ("summary", 1),
    }
    for keyword, (field, constraint) in unsupported_cases.items():
        schema = json.loads(config.output_schema.read_text(encoding="utf-8"))
        schema["properties"][field][keyword] = constraint
        unsupported_path = root / f"unsupported-{keyword}.schema.json"
        unsupported_path.write_text(json.dumps(schema), encoding="utf-8")
        try:
            build_codex_command(replace(config, output_schema=unsupported_path))
        except AdapterError as exc:
            if keyword not in str(exc):
                raise AssertionError(f"unsupported keyword error was unclear: {exc}") from exc
        else:
            raise AssertionError(f"unsupported Structured Outputs keyword was accepted: {keyword}")

    invalid_outputs = []
    duplicate_paths = valid_output()
    duplicate_paths["changed_paths"] = ["src/value.ts", "src/value.ts"]
    invalid_outputs.append(("duplicate paths", duplicate_paths))
    empty_summary = valid_output()
    empty_summary["summary"] = ""
    invalid_outputs.append(("empty summary", empty_summary))
    empty_path = valid_output()
    empty_path["changed_paths"] = [""]
    invalid_outputs.append(("empty path", empty_path))
    empty_command = valid_output()
    empty_command["checks"][0]["command"] = ""
    invalid_outputs.append(("empty check command", empty_command))

    for label, output in invalid_outputs:
        try:
            validate_agent_output(output)
        except AdapterError:
            continue
        raise AssertionError(f"runtime validation accepted {label}")


def test_predictability_output_contract(root: Path) -> None:
    config = create_config(root / "predictability-schema")
    predictability_schema = (
        Path(__file__).resolve().parents[1]
        / "testdata"
        / "model-evals"
        / "agent-output-predictability.schema.json"
    )
    build_codex_command(replace(config, output_schema=predictability_schema))

    terminal = validate_agent_output(valid_predictability_output(), predictability=True)
    if terminal["requirement_results"][0]["id"] != "required-behavior":
        raise AssertionError("predictability terminal output lost requirement evidence")
    decision = validate_agent_output(valid_decision_request(), predictability=True)
    if decision["status"] != "decision_request":
        raise AssertionError("valid decision request was not accepted")

    invalid_cases: list[tuple[str, dict]] = []
    decision_with_patch = valid_decision_request()
    decision_with_patch["changed_paths"] = ["src/value.ts"]
    invalid_cases.append(("decision patch", decision_with_patch))
    decision_without_evidence = valid_decision_request()
    decision_without_evidence["decision_evidence"] = []
    invalid_cases.append(("decision evidence", decision_without_evidence))
    duplicate_requirements = valid_predictability_output()
    duplicate_requirements["requirement_results"] *= 2
    invalid_cases.append(("duplicate requirements", duplicate_requirements))
    terminal_with_decision = valid_predictability_output()
    terminal_with_decision["decision_id"] = "event-contract"
    invalid_cases.append(("terminal decision", terminal_with_decision))
    terminal_without_requirements = valid_predictability_output()
    terminal_without_requirements["requirement_results"] = []
    invalid_cases.append(("terminal requirements", terminal_without_requirements))

    for name, value in invalid_cases:
        try:
            validate_agent_output(value, predictability=True)
        except AdapterError:
            continue
        raise AssertionError(f"invalid predictability output passed: {name}")

    try:
        validate_agent_output(valid_predictability_output())
    except AdapterError as exc:
        if "unknown fields" not in str(exc):
            raise AssertionError(f"legacy validator failed unclearly: {exc}") from exc
    else:
        raise AssertionError("legacy validator accepted predictability-only fields")


def test_transient_and_format_retries(root: Path) -> None:
    transient_config = create_config(root / "transient")
    transient_failure = success_response(THREAD_IDS[1])
    transient_failure.update({"returncode": 1, "stderr": "HTTP 429 rate limit"})
    transient = FakeRunner(
        [
            transient_failure,
            success_response(THREAD_IDS[2]),
        ]
    )
    result = run_cli_lane(
        transient_config,
        "Implement.",
        runner=transient,
        context_probe=no_context_probe,
    )
    if result.lane_result["attempts"] != 2 or result.lane_result["status"] != "pass":
        raise AssertionError("transient retry failed")
    if result.lane_result["attempts"] != MAX_LANE_ATTEMPTS:
        raise AssertionError("lane attempt cap is not reflected in retry evidence")
    if result.lane_result["usage"]["input_tokens"] != 20:
        raise AssertionError("transient retry discarded an attempt's usage")

    unaccounted = FakeRunner(
        [
            {"returncode": 1, "stderr": "HTTP 429 rate limit"},
            success_response(THREAD_IDS[3]),
        ]
    )
    result = run_cli_lane(
        create_config(root / "unaccounted-transient"),
        "Implement.",
        runner=unaccounted,
        context_probe=no_context_probe,
    )
    if len(unaccounted.calls) != 1:
        raise AssertionError("transient attempt without usage was retried")
    if result.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("unaccounted transient attempt was not fail-closed")

    usage_without_selection = FakeRunner(
        [
            {
                "returncode": 1,
                "stderr": "HTTP 429 rate limit",
                "stdout": json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 4,
                            "output_tokens": 5,
                            "reasoning_output_tokens": 2,
                        },
                    }
                )
                + "\n",
            },
            success_response(THREAD_IDS[4]),
        ]
    )
    result = run_cli_lane(
        create_config(root / "unverified-transient"),
        "Implement.",
        runner=usage_without_selection,
        context_probe=no_context_probe,
    )
    if len(usage_without_selection.calls) != 1:
        raise AssertionError("transient attempt without selection evidence was retried")
    if result.lane_result["error_kind"] != "model-unverified":
        raise AssertionError("unverified transient attempt was not fail-closed")

    format_config = create_config(root / "format")
    invalid_output = valid_output()
    invalid_output.pop("summary")
    format_runner = FakeRunner(
        [
            success_response(THREAD_IDS[4], invalid_output),
            success_response(THREAD_IDS[5]),
        ]
    )
    result = run_cli_lane(
        format_config,
        "Implement.",
        runner=format_runner,
        context_probe=no_context_probe,
    )
    if result.lane_result["attempts"] != 2 or result.lane_result["status"] != "pass":
        raise AssertionError("format retry failed")
    if "previous response" not in format_runner.calls[1]["input"]:
        raise AssertionError("format retry prompt did not explain the schema failure")
    if len(result.lane_result["selection_evidence"]) != 2:
        raise AssertionError("format retry discarded an attempt's selection evidence")
    if result.lane_result["usage"] != {
        "input_tokens": 20,
        "cached_input_tokens": 8,
        "output_tokens": 10,
        "reasoning_output_tokens": 4,
    }:
        raise AssertionError("retry usage was not aggregated")


def test_success_requires_complete_usage(root: Path) -> None:
    cases = (
        (
            "missing",
            trace_stdout(THREAD_IDS[20], include_completed=False),
        ),
        (
            "duplicate",
            trace_stdout(THREAD_IDS[21], duplicate_completed=True),
        ),
        (
            "incomplete",
            trace_stdout(
                THREAD_IDS[22],
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 5,
                },
            ),
        ),
        (
            "negative",
            trace_stdout(
                THREAD_IDS[23],
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 5,
                    "reasoning_output_tokens": -1,
                },
            ),
        ),
        (
            "zero",
            trace_stdout(
                THREAD_IDS[24],
                usage={
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                },
            ),
        ),
        (
            "cached-over-input",
            trace_stdout(
                THREAD_IDS[25],
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 11,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                },
            ),
        ),
        (
            "reasoning-over-output",
            trace_stdout(
                THREAD_IDS[26],
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 6,
                },
            ),
        ),
    )
    for index, (label, stdout) in enumerate(cases, start=20):
        thread_id = THREAD_IDS[index]
        runner = FakeRunner(
            [
                {
                    "thread_id": thread_id,
                    "rollout": rollout_records(thread_id),
                    "output": valid_output(),
                    "stdout": stdout,
                }
            ]
        )
        result = run_cli_lane(
            create_config(root / label),
            "Implement.",
            runner=runner,
            context_probe=no_context_probe,
        )
        if result.lane_result["status"] != "infrastructure-error":
            raise AssertionError(f"{label} usage trace was scored")
        if result.lane_result["error_kind"] != "invalid-trace":
            raise AssertionError(f"{label} usage trace had the wrong error kind")
        if result.lane_result["selection_status"] != "unverified":
            raise AssertionError(f"{label} usage trace retained a scoreable selection")
        if result.lane_result["summary"] != valid_output()["summary"]:
            raise AssertionError(f"{label} usage failure discarded valid agent output")


def test_terminal_retry_requires_current_selection_evidence(root: Path) -> None:
    first_thread = THREAD_IDS[24]
    second_thread = THREAD_IDS[25]
    first = success_response(first_thread)
    first.update({"returncode": 1, "stderr": "HTTP 500 service unavailable"})
    runner = FakeRunner(
        [
            first,
            {
                "thread_id": second_thread,
                "timed_out": True,
                "stdout": trace_stdout(second_thread, include_completed=False),
            },
        ]
    )
    result = run_cli_lane(
        create_config(root / "thread-without-rollout"),
        "Implement.",
        runner=runner,
        context_probe=no_context_probe,
    )
    if result.lane_result["status"] != "infrastructure-error":
        raise AssertionError("terminal retry without rollout was scored as a model timeout")
    if result.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("terminal retry without rollout had the wrong error kind")
    if result.lane_result["selection_status"] != "unverified":
        raise AssertionError("terminal retry inherited prior exact selection")
    if len(result.lane_result["selection_evidence"]) != 1:
        raise AssertionError("terminal retry discarded the prior attempt evidence")
    if result.lane_result["selected_model"] is not None:
        raise AssertionError("terminal retry claimed a selected model without current evidence")

    no_thread_runner = FakeRunner(
        [
            first,
            {
                "timed_out": True,
                "stdout": "",
            },
        ]
    )
    no_thread = run_cli_lane(
        create_config(root / "timeout-without-thread"),
        "Implement.",
        runner=no_thread_runner,
        context_probe=no_context_probe,
    )
    if no_thread.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("terminal timeout without thread was not fail-closed")
    if no_thread.lane_result["selection_status"] != "unverified":
        raise AssertionError("terminal timeout without thread inherited prior selection")


def test_context_warning_and_redaction(root: Path) -> None:
    config = create_config(root / "context")
    runner = FakeRunner(
        [
            {
                **success_response(THREAD_IDS[4]),
                "stderr": "skills context budget exceeded; skill descriptions removed",
            }
        ]
    )
    result = run_cli_lane(
        config,
        "Implement.",
        runner=runner,
        context_probe=no_context_probe,
    )
    if result.lane_result["error_kind"] != "invalid-context":
        raise AssertionError("skills context warning did not invalidate the lane")

    raw = 'Bearer abc.def OPENAI_API_KEY=secret sk-1234567890 {"api_key":"hidden"}'
    redacted = redact_text(raw)
    for secret in ["abc.def", "secret", "sk-1234567890", "hidden"]:
        if secret in redacted:
            raise AssertionError(f"redaction leaked {secret}")

    raw_jsonl = json.dumps(
        {
            "type": "error",
            "message": "Bearer abc.def sk-1234567890",
            "api_key": "hidden",
        }
    )
    structured = redact_jsonl(raw_jsonl)
    parsed = json.loads(structured)
    if parsed["api_key"] != "[REDACTED]":
        raise AssertionError("structured redaction missed a secret field")
    if any(secret in structured for secret in ["abc.def", "sk-1234567890", "hidden"]):
        raise AssertionError("structured redaction leaked a secret")


def test_model_timeout_and_invalid_output_are_task_failures(root: Path) -> None:
    timeout_config = create_config(root / "timeout")
    first_timeout = success_response(THREAD_IDS[26])
    first_timeout["timed_out"] = True
    second_timeout = success_response(THREAD_IDS[27])
    second_timeout["timed_out"] = True
    timeout_runner = FakeRunner([first_timeout, second_timeout])

    timeout = run_cli_lane(
        timeout_config,
        "Implement.",
        runner=timeout_runner,
        context_probe=no_context_probe,
    )
    if timeout.lane_result["status"] != "fail" or timeout.lane_result["error_kind"] != "model-timeout":
        raise AssertionError(f"exhausted model timeout was not scored as failure: {timeout.lane_result}")

    invalid_config = create_config(root / "invalid")
    invalid = valid_output()
    invalid.pop("summary")
    invalid_runner = FakeRunner(
        [
            success_response(THREAD_IDS[5], invalid),
            success_response(THREAD_IDS[6], invalid),
        ]
    )
    invalid_result = run_cli_lane(
        invalid_config,
        "Implement.",
        runner=invalid_runner,
        context_probe=no_context_probe,
    )
    if invalid_result.lane_result["status"] != "fail" or invalid_result.lane_result["error_kind"] != "invalid-output":
        raise AssertionError(f"invalid model output was not scored as failure: {invalid_result.lane_result}")


def test_rollout_parser_contract() -> None:
    thread_id = THREAD_IDS[7]
    records = rollout_records(
        thread_id,
        repeat_context=2,
        extra_records=(
            {
                "timestamp": "2026-07-11T12:00:03Z",
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "DO_NOT_RETAIN"},
            },
        ),
    )
    evidence = parse_rollout_selection(
        serialize_records(records),
        expected_thread_id=thread_id,
    )
    if evidence.configured_model != "gpt-5.6-luna" or evidence.configured_reasoning != "medium":
        raise AssertionError("repeated exact turn context was not accepted")
    if "DO_NOT_RETAIN" in json.dumps(evidence.to_dict()):
        raise AssertionError("selection evidence retained rollout conversation content")

    conflicting = rollout_records(thread_id, repeat_context=2)
    conflicting[2]["payload"]["model"] = "gpt-5.6-terra"
    expect_selection_error(conflicting, thread_id, "invalid-trace")

    two_turns = rollout_records(thread_id, repeat_context=2)
    two_turns[2]["payload"]["turn_id"] = "01900000-0000-7000-9000-000000000002"
    expect_selection_error(two_turns, thread_id, "invalid-trace")

    missing_context = rollout_records(thread_id, include_context=False)
    expect_selection_error(missing_context, thread_id, "model-unverified")
    missing_effort = rollout_records(thread_id, effort=None)
    expect_selection_error(missing_effort, thread_id, "reasoning-unverified")
    missing_turn_id = rollout_records(thread_id)
    missing_turn_id[1]["payload"]["turn_id"] = None
    expect_selection_error(missing_turn_id, thread_id, "invalid-trace")

    bad_version = rollout_records(thread_id, cli_version="0.143.0")
    expect_selection_error(bad_version, thread_id, "invalid-trace")
    bad_provider = rollout_records(thread_id, provider="custom")
    expect_selection_error(bad_provider, thread_id, "invalid-trace")
    bad_thread = rollout_records(thread_id)
    bad_thread[0]["payload"]["session_id"] = THREAD_IDS[8]
    expect_selection_error(bad_thread, thread_id, "invalid-trace")
    duplicate_meta = rollout_records(thread_id)
    duplicate_meta.insert(1, json.loads(json.dumps(duplicate_meta[0])))
    expect_selection_error(duplicate_meta, thread_id, "invalid-trace")


def test_rollout_discovery_guards(root: Path) -> None:
    missing_home = root / "rollout-missing"
    try:
        find_rollout_path(missing_home, THREAD_IDS[9])
    except SelectionEvidenceError as exc:
        if exc.error_kind != "model-unverified":
            raise AssertionError("missing rollout had the wrong error kind") from exc
    else:
        raise AssertionError("missing rollout was accepted")

    home = root / "rollout-found"
    first = (
        home
        / "sessions"
        / "2026"
        / "07"
        / "11"
        / f"rollout-2026-07-11T12-00-00-{THREAD_IDS[9]}.jsonl"
    )
    first.parent.mkdir(parents=True)
    first.write_text("{}\n", encoding="utf-8")
    if find_rollout_path(home, THREAD_IDS[9]) != first.resolve():
        raise AssertionError("rollout path was not discovered")

    second = (
        home
        / "sessions"
        / "2026"
        / "07"
        / "12"
        / f"rollout-2026-07-12T00-00-00-{THREAD_IDS[9]}.jsonl"
    )
    second.parent.mkdir(parents=True)
    second.write_text("{}\n", encoding="utf-8")
    try:
        find_rollout_path(home, THREAD_IDS[9])
    except SelectionEvidenceError as exc:
        if exc.error_kind != "invalid-trace":
            raise AssertionError("ambiguous rollout had the wrong error kind") from exc
    else:
        raise AssertionError("ambiguous rollout was accepted")

    symlink_home = root / "rollout-symlink"
    target = root / "outside-rollout.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    symlink = (
        symlink_home
        / "sessions"
        / "2026"
        / "07"
        / "11"
        / f"rollout-2026-07-11T12-00-00-{THREAD_IDS[10]}.jsonl"
    )
    symlink.parent.mkdir(parents=True)
    symlink.symlink_to(target)
    try:
        find_rollout_path(symlink_home, THREAD_IDS[10])
    except SelectionEvidenceError as exc:
        if exc.error_kind != "invalid-trace":
            raise AssertionError("symlink rollout had the wrong error kind") from exc
    else:
        raise AssertionError("symlink rollout was accepted")

    oversized_home = root / "rollout-oversized"
    oversized = (
        oversized_home
        / "sessions"
        / "2026"
        / "07"
        / "11"
        / f"rollout-2026-07-11T12-00-00-{THREAD_IDS[11]}.jsonl"
    )
    oversized.parent.mkdir(parents=True)
    oversized.write_text("01234567890", encoding="utf-8")
    try:
        find_rollout_path(oversized_home, THREAD_IDS[11], max_bytes=10)
    except SelectionEvidenceError as exc:
        if exc.error_kind != "invalid-trace":
            raise AssertionError("oversized rollout had the wrong error kind") from exc
    else:
        raise AssertionError("oversized rollout was accepted")
    if MAX_ROLLOUT_BYTES < 1024:
        raise AssertionError("production rollout bound is unexpectedly small")


def test_rollout_must_prove_exact_selection(root: Path) -> None:
    missing_config = create_config(root / "missing-rollout")
    missing = FakeRunner(
        [
            {
                "thread_id": THREAD_IDS[12],
                "output": valid_output(),
                "stdout": trace_stdout(THREAD_IDS[12]),
            }
        ]
    )
    missing_result = run_cli_lane(
        missing_config,
        "Implement.",
        runner=missing,
        context_probe=no_context_probe,
    )
    if missing_result.lane_result["error_kind"] != "model-unverified":
        raise AssertionError("missing rollout was accepted")
    if missing_result.lane_result["selected_model"] is not None:
        raise AssertionError("unverified selection claimed the requested model")
    if missing_result.lane_result["selected_reasoning"] is not None:
        raise AssertionError("unverified selection claimed the requested reasoning effort")
    if missing_result.lane_result["usage"] != {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 5,
        "reasoning_output_tokens": 2,
    }:
        raise AssertionError("proof failure discarded usage evidence")
    if missing_result.lane_result["summary"] != valid_output()["summary"]:
        raise AssertionError("proof failure discarded valid agent output evidence")

    injected_stdout = "".join(
        json.dumps(
            {**json.loads(line), "model": "gpt-5.6-luna"}
        )
        + "\n"
        for line in trace_stdout(THREAD_IDS[13]).splitlines()
    )
    injected = FakeRunner(
        [
            {
                "thread_id": THREAD_IDS[13],
                "output": valid_output(),
                "stdout": injected_stdout,
            }
        ]
    )
    injected_result = run_cli_lane(
        create_config(root / "stdout-injection"),
        "Implement.",
        runner=injected,
        context_probe=no_context_probe,
    )
    if injected_result.lane_result["error_kind"] != "model-unverified":
        raise AssertionError("untrusted stdout model field replaced rollout proof")

    wrong_model = FakeRunner(
        [
            success_response(
                THREAD_IDS[14],
                rollout=rollout_records(THREAD_IDS[14], model="gpt-5.6-terra"),
            )
        ]
    )
    wrong_model_result = run_cli_lane(
        create_config(root / "wrong-model"),
        "Implement.",
        runner=wrong_model,
        context_probe=no_context_probe,
    )
    if wrong_model_result.lane_result["error_kind"] != "model-unverified":
        raise AssertionError("wrong rollout model was accepted")
    if wrong_model_result.lane_result["selected_model"] != "gpt-5.6-terra":
        raise AssertionError("wrong selected model was hidden from failure evidence")

    wrong_effort = FakeRunner(
        [
            success_response(
                THREAD_IDS[15],
                rollout=rollout_records(THREAD_IDS[15], effort="high"),
            )
        ]
    )
    wrong_effort_result = run_cli_lane(
        create_config(root / "wrong-effort"),
        "Implement.",
        runner=wrong_effort,
        context_probe=no_context_probe,
    )
    if wrong_effort_result.lane_result["error_kind"] != "reasoning-unverified":
        raise AssertionError("wrong rollout reasoning effort was accepted")


def test_reroute_is_substituted_and_retry_cannot_hide_it(root: Path) -> None:
    reroute = (
        ("gpt-5.6-luna", "gpt-5.6-terra", "HighRiskCyberActivity"),
    )
    rerouted_runner = FakeRunner(
        [
            success_response(
                THREAD_IDS[16],
                reroutes=reroute,
            )
        ]
    )
    rerouted = run_cli_lane(
        create_config(root / "rerouted"),
        "Implement.",
        runner=rerouted_runner,
        context_probe=no_context_probe,
    )
    if rerouted.lane_result["selection_status"] != "substituted":
        raise AssertionError("rerouted model was scored as exact")
    if rerouted.lane_result["selected_model"] != "gpt-5.6-terra":
        raise AssertionError("rerouted selected model was not retained")
    if rerouted.lane_result["fallback_reason"] != "model-rerouted":
        raise AssertionError("reroute reason was not reflected in the lane result")

    invalid = valid_output()
    invalid.pop("summary")
    retry_runner = FakeRunner(
        [
            success_response(
                THREAD_IDS[17],
                invalid,
                reroutes=reroute,
            ),
            success_response(THREAD_IDS[18]),
        ]
    )
    retried = run_cli_lane(
        create_config(root / "rerouted-retry"),
        "Implement.",
        runner=retry_runner,
        context_probe=no_context_probe,
    )
    if retried.lane_result["selection_status"] != "substituted":
        raise AssertionError("retry hid an earlier model reroute")
    if len(retried.lane_result["selection_evidence"]) != 2:
        raise AssertionError("retry did not retain every attempt's selection evidence")

    broken_runner = FakeRunner(
        [
            success_response(
                THREAD_IDS[19],
                reroutes=(("gpt-5.6-terra", "gpt-5.6-sol", "PolicyFallback"),),
            )
        ]
    )
    broken = run_cli_lane(
        create_config(root / "broken-reroute"),
        "Implement.",
        runner=broken_runner,
        context_probe=no_context_probe,
    )
    if broken.lane_result["error_kind"] != "invalid-trace":
        raise AssertionError("broken model reroute chain was accepted")


def test_real_prompt_context_isolation(root: Path) -> None:
    if shutil.which("codex") is None:
        return
    for model in ("gpt-5.6-luna", "gpt-5.6-terra"):
        config = replace(
            create_config(root / f"context-probe-{model.rsplit('-', 1)[-1]}"),
            model=model,
        )
        environment = _isolated_process_environment(config)
        client_home = config.client_codex_home.resolve()
        verify_prompt_context(config, environment)
        if (client_home / "AGENTS.md").exists():
            raise AssertionError("prompt probe copied host AGENTS.md into disposable CODEX_HOME")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-adapter-") as raw_root:
        root = Path(raw_root)
        test_command_and_success(root)
        test_same_thread_predictability_continuation(root)
        test_continuation_rejects_changed_thread(root)
        test_continuation_format_retry_stays_in_thread(root)
        test_multi_agent_context_and_rollouts_are_rejected(root)
        test_output_schema_compatibility(root)
        test_predictability_output_contract(root)
        test_transient_and_format_retries(root)
        test_success_requires_complete_usage(root)
        test_terminal_retry_requires_current_selection_evidence(root)
        test_context_warning_and_redaction(root)
        test_model_timeout_and_invalid_output_are_task_failures(root)
        test_rollout_parser_contract()
        test_rollout_discovery_guards(root)
        test_rollout_must_prove_exact_selection(root)
        test_reroute_is_substituted_and_retry_cannot_hide_it(root)
        test_real_prompt_context_isolation(root)
    print("PASS model eval adapter fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
