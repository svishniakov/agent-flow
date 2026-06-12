#!/usr/bin/env python3
"""Fixture tests for validate-run Lane Sharding checks."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"
REQUIRED_FILES = [
    "manifest.md",
    "context.md",
    "route.md",
    "plan.md",
    "definition-of-done.md",
    "decisions.md",
]


def timeline_event(
    stage: str,
    role: str = "orchestrator",
    status: str = "pass",
    summary: str = "Fixture event.",
    next_step: str = "",
    artifacts: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    event = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "stage": stage,
        "role": role,
        "stable_agent_name": role,
        "stable_agent_slug": role,
        "status": status,
        "summary": summary,
        "artifacts": artifacts or [],
        "next_step": next_step,
    }
    event.update(extra)
    return event


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def write_run(
    root: Path,
    *,
    verdict: str = "ship",
    lanes: list[dict[str, Any]] | None = None,
    trace_events: dict[str, list[dict[str, Any]]] | None = None,
    lane_map_extra: dict[str, Any] | None = None,
) -> Path:
    run_dir = root / "run"
    (run_dir / "handoffs").mkdir(parents=True)
    (run_dir / "checks").mkdir()
    (run_dir / "artifacts").mkdir()
    (run_dir / "checks" / "smoke.md").write_text("# Smoke\n\npass\n", encoding="utf-8")

    for name in REQUIRED_FILES:
        content = "# Fixture\n\n"
        if name == "manifest.md":
            content += f"Verdict: {verdict}\n"
        (run_dir / name).write_text(content, encoding="utf-8")
    (run_dir / "final.md").write_text(f"# Final\n\nVerdict: {verdict}\n", encoding="utf-8")
    (run_dir / "artifacts.json").write_text("[]\n", encoding="utf-8")

    timeline_events: list[dict[str, Any]] = []
    for role, events in (trace_events or {}).items():
        trace_path = run_dir / "agents" / role / "trace.jsonl"
        trace_path.parent.mkdir(parents=True)
        write_jsonl(trace_path, events)
        timeline_events.extend(events)

    if lanes is not None:
        for lane in lanes:
            handoff = lane.get("handoff")
            if isinstance(handoff, str):
                path = run_dir / handoff
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {lane['id']} handoff\n", encoding="utf-8")
            for evidence in lane.get("evidence", []):
                if isinstance(evidence, str):
                    path = run_dir / evidence
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"# {lane['id']} evidence\n", encoding="utf-8")

        lane_map = {"schema_version": 1, "lanes": lanes}
        lane_map.update(lane_map_extra or {})
        (run_dir / "lane-map.json").write_text(
            json.dumps(lane_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    timeline_events.append(
        timeline_event("final", summary="Fixture final event.", next_step="handoff")
    )
    write_jsonl(run_dir / "timeline.jsonl", timeline_events)
    return run_dir


def validate(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE_RUN), "--run-dir", str(run_dir)],
        text=True,
        capture_output=True,
        check=False,
    )


def expect_pass(name: str, run_dir: Path) -> None:
    result = validate(run_dir)
    if result.returncode != 0:
        raise AssertionError(f"{name} expected pass\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def expect_fail(name: str, run_dir: Path, needle: str) -> None:
    result = validate(run_dir)
    if result.returncode == 0:
        raise AssertionError(f"{name} expected fail")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name} missing '{needle}'\nOutput:\n{output}")


def lane(
    lane_id: str,
    *,
    status: str = "pass",
    execution_mode: str = "role-lane",
    replacement: str | None = None,
    lane_type: str = "qa",
    role: str = "qa-verifier",
    critical: bool = True,
    wave: int = 3,
) -> dict[str, Any]:
    return {
        "id": lane_id,
        "type": lane_type,
        "role": role,
        "wave": wave,
        "critical": critical,
        "execution_mode": execution_mode,
        "status": status,
        "handoff": f"handoffs/{lane_id}.md" if status in {"pass", "pass-with-risks"} else None,
        "evidence": [f"checks/{lane_id}.md"] if status in {"pass", "pass-with-risks"} else [],
        "replacement": replacement,
    }


def spawned_trace(role: str, lane_id: str) -> dict[str, Any]:
    return timeline_event(
        "spawned",
        role=role,
        status="active",
        summary=f"Spawned {lane_id}.",
        next_step="handoff",
        execution_mode="subagent",
        agent_trace=f"agents/{role}/trace.jsonl",
        agent_artifact_dir=f"artifacts/agents/{role}",
        codex_thread_id=f"thread-{lane_id}",
        lane_id=lane_id,
        wave=3,
        critical=True,
    )


def architecture_lane(
    lane_id: str = "architecture-contract",
    *,
    status: str = "pass",
    execution_mode: str = "role-lane",
    critical: bool = True,
    wave: int = 1,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        execution_mode=execution_mode,
        lane_type="architecture",
        role="architect",
        critical=critical,
        wave=wave,
    )


def reviewer_lane(lane_id: str = "review-contract", *, wave: int = 4) -> dict[str, Any]:
    return lane(lane_id, lane_type="review", role="reviewer", wave=wave)


def qa_lane(lane_id: str = "qa-behavior", *, status: str = "pass", wave: int = 3) -> dict[str, Any]:
    return lane(lane_id, status=status, lane_type="qa", role="qa-verifier", wave=wave)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-lane-tests-") as temp_dir:
        temp = Path(temp_dir)

        expect_pass("no lane map keeps old behavior", write_run(temp / "no-lanes"))

        expect_pass(
            "valid subagent critical lane",
            write_run(
                temp / "valid-subagent",
                lanes=[lane("qa-live-feed", execution_mode="subagent")],
                trace_events={"qa-verifier": [spawned_trace("qa-verifier", "qa-live-feed")]},
            ),
        )

        expect_fail(
            "timed-out critical lane without replacement",
            write_run(temp / "timeout-no-replacement", lanes=[lane("qa-compose", status="timed-out")]),
            "timed-out lane requires replacement",
        )

        expect_pass(
            "timed-out critical lane with replacement",
            write_run(
                temp / "timeout-with-replacement",
                lanes=[
                    lane("qa-compose", status="timed-out", replacement="qa-compose-r2"),
                    lane("qa-compose-r2"),
                ],
            ),
        )

        expect_fail(
            "ship blocked by unresolved critical lane",
            write_run(temp / "unresolved-critical", lanes=[lane("qa-admin", status="active")]),
            "final Verdict ship is blocked by critical lane",
        )

        expect_fail(
            "subagent lane without spawned trace",
            write_run(temp / "subagent-no-trace", lanes=[lane("qa-live-feed", execution_mode="subagent")]),
            "missing trace file",
        )

        expect_pass(
            "role lane does not require codex thread",
            write_run(temp / "role-lane", lanes=[lane("qa-pii")]),
        )

        expect_pass(
            "schema v1 remains valid",
            write_run(temp / "schema-v1", lanes=[lane("qa-schema-v1")]),
        )

        expect_pass(
            "schema v2 accepts architecture lane",
            write_run(
                temp / "schema-v2-architecture",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
        )

        expect_pass(
            "architecture contract false does not require architecture lane",
            write_run(
                temp / "architecture-not-required",
                lanes=[qa_lane()],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": False},
            ),
        )

        expect_fail(
            "architecture contract required without architecture lane",
            write_run(
                temp / "architecture-required-missing",
                lanes=[qa_lane()],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
            "architecture_contract_required requires a critical architecture lane",
        )

        expect_fail(
            "required architecture lane must be critical",
            write_run(
                temp / "architecture-required-not-critical",
                lanes=[architecture_lane(critical=False), qa_lane()],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
            "architecture lane must be critical",
        )

        for bad_status in ["fail", "blocked", "timed-out"]:
            expect_fail(
                f"required architecture lane status {bad_status} blocks ship",
                write_run(
                    temp / f"architecture-{bad_status}",
                    lanes=[architecture_lane(status=bad_status), qa_lane()],
                    lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
                ),
                "architecture lane must pass before ship",
            )

        expect_fail(
            "successful architecture lane requires evidence",
            write_run(
                temp / "architecture-success-no-evidence",
                lanes=[
                    {
                        **architecture_lane(),
                        "handoff": None,
                        "evidence": [],
                    },
                    qa_lane(),
                ],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
            "successful critical lane requires handoff",
        )

        expect_fail(
            "reviewer lane without architecture contract fails when required",
            write_run(
                temp / "review-without-architecture",
                lanes=[reviewer_lane()],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
            "reviewer lane requires successful architecture contract",
        )

        expect_fail(
            "qa evidence cannot pass before architecture lane",
            write_run(
                temp / "qa-before-architecture",
                lanes=[architecture_lane(wave=3), qa_lane(wave=2)],
                lane_map_extra={"schema_version": 2, "architecture_contract_required": True},
            ),
            "qa lane must run after architecture lane",
        )

        expect_pass(
            "standard multi-lane role-lane architecture fallback allowed",
            write_run(
                temp / "architecture-role-lane-fallback",
                lanes=[architecture_lane(execution_mode="role-lane"), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "architecture_contract_required": True,
                    "architecture_contract_independent": False,
                },
            ),
        )

        expect_fail(
            "release independent architecture cannot silently downgrade to role lane",
            write_run(
                temp / "architecture-independent-role-lane",
                lanes=[architecture_lane(execution_mode="role-lane"), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "architecture_contract_required": True,
                    "architecture_contract_independent": True,
                },
            ),
            "independent architecture contract requires subagent execution",
        )

        expect_pass(
            "independent architecture subagent lane passes with spawned trace",
            write_run(
                temp / "architecture-independent-subagent",
                lanes=[architecture_lane(execution_mode="subagent"), qa_lane()],
                trace_events={"architect": [spawned_trace("architect", "architecture-contract")]},
                lane_map_extra={
                    "schema_version": 2,
                    "architecture_contract_required": True,
                    "architecture_contract_independent": True,
                },
            ),
        )

    print("PASS validate-run lane fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
