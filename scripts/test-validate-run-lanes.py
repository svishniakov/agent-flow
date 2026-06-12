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
ARCHITECTURE_CONTRACT_SECTIONS = [
    "Selected Architecture",
    "Rejected Alternatives",
    "Module Boundaries",
    "Data And State Flow",
    "Public Contracts",
    "Worker Ownership",
    "Forbidden Changes",
    "QA Gates",
    "Reviewer Checklist",
    "Stop Conditions",
]
ARCHITECTURE_COMPLIANCE_SECTION = "Architecture Compliance"
ARCHITECTURE_INVARIANTS_SECTION = "Architecture Invariants"
ARCHITECTURE_MATRIX_MISMATCHES_SECTION = "Architecture Matrix Mismatches"
CONTRACT_DRIFT_SECTION = "Contract Drift"
ARCHITECTURE_CONTEXT_AXES = [
    "product_context",
    "application_surface",
    "architecture_pattern",
    "stack_runtime",
    "risk_gates",
    "verification_gates",
]
DEFAULT_ARCHITECTURE_CONTEXT = {
    "product_context": ["saas-service"],
    "application_surface": ["backend-service"],
    "architecture_pattern": ["monolith"],
    "stack_runtime": ["go"],
    "risk_gates": ["migrations"],
    "verification_gates": ["unit", "integration"],
}
OMIT = object()


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


def architecture_context(**overrides: Any) -> dict[str, Any]:
    context = {
        axis: list(facets)
        for axis, facets in DEFAULT_ARCHITECTURE_CONTEXT.items()
    }
    for axis, value in overrides.items():
        if value is OMIT:
            context.pop(axis, None)
        else:
            context[axis] = value
    return context


def architecture_context_facets(context: dict[str, Any] | None = None) -> list[str]:
    selected_context = context or DEFAULT_ARCHITECTURE_CONTEXT
    facets: list[str] = []
    for axis in ARCHITECTURE_CONTEXT_AXES:
        value = selected_context.get(axis, [])
        if isinstance(value, list):
            facets.extend(facet for facet in value if isinstance(facet, str))
    return facets


def architecture_contract_text(
    missing_sections: set[str] | None = None,
    *,
    selected_facets: list[str] | None = None,
) -> str:
    missing = missing_sections or set()
    facets = architecture_context_facets() if selected_facets is None else selected_facets
    sections = []
    for section in ARCHITECTURE_CONTRACT_SECTIONS:
        if section in missing:
            continue
        body = f"Fixture {section.lower()}."
        if section == "Selected Architecture":
            facet_lines = "\n".join(f"- `{facet}`" for facet in facets)
            body = f"Matrix facets:\n{facet_lines}" if facet_lines else "Matrix facets: none."
        sections.append(f"## {section}\n\n{body}\n")
    return "# Architecture Contract\n\n" + "\n".join(sections)


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
                if lane.get("type") == "architecture":
                    missing = set(lane.get("missing_contract_sections", []))
                    selected_facets = lane.get("architecture_selected_facets")
                    if selected_facets is not None and not isinstance(selected_facets, list):
                        selected_facets = []
                    path.write_text(
                        architecture_contract_text(
                            missing,
                            selected_facets=selected_facets,
                        ),
                        encoding="utf-8",
                    )
                else:
                    sections = [
                        f"## {section}\n\nFixture {section.lower()}.\n"
                        for section in lane.get("handoff_sections", [])
                    ]
                    path.write_text(
                        f"# {lane['id']} handoff\n\n" + "\n".join(sections),
                        encoding="utf-8",
                    )
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
    handoff_sections: list[str] | None = None,
) -> dict[str, Any]:
    lane_data = {
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
    if handoff_sections is not None:
        lane_data["handoff_sections"] = handoff_sections
    return lane_data


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
    selected_facets: list[str] | None = None,
) -> dict[str, Any]:
    lane_data = lane(
        lane_id,
        status=status,
        execution_mode=execution_mode,
        lane_type="architecture",
        role="architect",
        critical=critical,
        wave=wave,
    )
    if selected_facets is not None:
        lane_data["architecture_selected_facets"] = selected_facets
    return lane_data


def architecture_compliance(
    *,
    status: str = "compliant",
    contract_sections: list[str] | None = None,
    notes: str = "Fixture architecture compliance.",
    recheck_lane: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "contract_sections": contract_sections or ["Module Boundaries"],
        "notes": notes,
        "recheck_lane": recheck_lane,
    }


def worker_lane(
    lane_id: str = "worker-a",
    *,
    status: str = "pass",
    wave: int = 2,
    lane_type: str = "implementation",
    role: str = "typescript-worker",
    architecture_compliance_data: Any = OMIT,
    handoff_sections: list[str] | None = None,
) -> dict[str, Any]:
    lane_data = lane(
        lane_id,
        status=status,
        lane_type=lane_type,
        role=role,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [ARCHITECTURE_COMPLIANCE_SECTION],
    )
    if architecture_compliance_data is OMIT:
        lane_data["architecture_compliance"] = architecture_compliance()
    elif architecture_compliance_data is not None:
        lane_data["architecture_compliance"] = architecture_compliance_data
    return lane_data


def reviewer_lane(
    lane_id: str = "review-contract",
    *,
    wave: int = 4,
    handoff_sections: list[str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        lane_type="review",
        role="reviewer",
        wave=wave,
        handoff_sections=handoff_sections,
    )


def qa_lane(
    lane_id: str = "qa-behavior",
    *,
    status: str = "pass",
    wave: int = 3,
    handoff_sections: list[str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        lane_type="qa",
        role="qa-verifier",
        wave=wave,
        handoff_sections=handoff_sections,
    )


def qa_control_lane(*, wave: int = 3, handoff_sections: list[str] | None = None) -> dict[str, Any]:
    return qa_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [ARCHITECTURE_INVARIANTS_SECTION],
    )


def reviewer_control_lane(*, wave: int = 4, handoff_sections: list[str] | None = None) -> dict[str, Any]:
    return reviewer_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
    )


def architecture_control_extra() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "budget": "standard",
        "architecture_contract_required": True,
        "architecture_context": architecture_context(),
    }


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
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "architecture context is required when architecture contract is required",
            write_run(
                temp / "architecture-context-missing",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": True,
                },
            ),
            "lane-map.json field 'architecture_context' is required",
        )

        expect_fail(
            "architecture context missing axis fails",
            write_run(
                temp / "architecture-context-missing-axis",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": architecture_context(verification_gates=OMIT),
                },
            ),
            "lane-map.json architecture_context missing axis: verification_gates",
        )

        expect_fail(
            "architecture context unknown axis fails",
            write_run(
                temp / "architecture-context-unknown-axis",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": {
                        **architecture_context(),
                        "unknown_axis": [],
                    },
                },
            ),
            "lane-map.json architecture_context unknown axis: unknown_axis",
        )

        expect_fail(
            "architecture context axis must be array",
            write_run(
                temp / "architecture-context-axis-not-array",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": architecture_context(product_context="saas-service"),
                },
            ),
            "lane-map.json architecture_context.product_context must be an array",
        )

        expect_fail(
            "architecture context cannot be empty across all axes",
            write_run(
                temp / "architecture-context-empty",
                lanes=[architecture_lane(selected_facets=[]), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": {axis: [] for axis in ARCHITECTURE_CONTEXT_AXES},
                },
            ),
            "lane-map.json architecture_context must select at least one facet",
        )

        expect_fail(
            "architecture context unknown facet fails",
            write_run(
                temp / "architecture-context-unknown-facet",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": architecture_context(product_context=["unknown-product"]),
                },
            ),
            "unknown Architecture Matrix facet",
        )

        expect_fail(
            "architecture context facet from wrong axis fails",
            write_run(
                temp / "architecture-context-wrong-axis",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "architecture_context": architecture_context(product_context=["backend-service"]),
                },
            ),
            "unknown Architecture Matrix facet",
        )

        expect_fail(
            "selected architecture must include selected matrix facets",
            write_run(
                temp / "architecture-context-selected-facet-missing",
                lanes=[
                    architecture_lane(
                        selected_facets=[
                            "saas-service",
                            "monolith",
                            "go",
                            "migrations",
                            "unit",
                            "integration",
                        ]
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Selected Architecture missing Architecture Matrix facet: backend-service",
        )

        expect_pass(
            "architecture context with selected facets passes",
            write_run(
                temp / "architecture-context-valid",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "schema v2 requires budget",
            write_run(
                temp / "schema-v2-no-budget",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "architecture_contract_required": True,
                    "architecture_context": architecture_context(),
                },
            ),
            "lane-map.json field 'budget' is required for schema v2",
        )

        expect_pass(
            "architecture contract false does not require architecture lane",
            write_run(
                temp / "architecture-not-required",
                lanes=[qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

        expect_fail(
            "standard multi-lane requires architecture contract",
            write_run(
                temp / "standard-multi-lane-no-architecture-contract",
                lanes=[
                    lane("worker-a", lane_type="implementation", role="typescript-worker", wave=2),
                    lane("worker-b", lane_type="integration", role="backend-worker", wave=3),
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
            "standard budget with 2 worker lanes requires architecture_contract_required=true",
        )

        expect_pass(
            "standard single worker lane does not require architecture contract",
            write_run(
                temp / "standard-single-worker-no-architecture-contract",
                lanes=[lane("worker-a", lane_type="implementation", role="typescript-worker", wave=2)],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

        expect_fail(
            "release requires architecture contract",
            write_run(
                temp / "release-no-architecture-contract",
                lanes=[qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "release",
                    "architecture_contract_required": False,
                },
            ),
            "release budget requires architecture_contract_required=true",
        )

        expect_fail(
            "architecture contract required without architecture lane",
            write_run(
                temp / "architecture-required-missing",
                lanes=[qa_lane()],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_contract_required requires a critical architecture lane",
        )

        expect_fail(
            "required architecture lane must be critical",
            write_run(
                temp / "architecture-required-not-critical",
                lanes=[architecture_lane(critical=False), qa_lane()],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture lane must be critical",
        )

        for bad_status in ["fail", "blocked", "timed-out"]:
            expect_fail(
                f"required architecture lane status {bad_status} blocks ship",
                write_run(
                    temp / f"architecture-{bad_status}",
                    lanes=[architecture_lane(status=bad_status), qa_lane()],
                    lane_map_extra=architecture_control_extra(),
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
                lane_map_extra=architecture_control_extra(),
            ),
            "successful critical lane requires handoff",
        )

        expect_fail(
            "successful architecture handoff requires contract sections",
            write_run(
                temp / "architecture-missing-contract-section",
                lanes=[
                    {
                        **architecture_lane(),
                        "missing_contract_sections": ["Reviewer Checklist"],
                    },
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture contract handoff missing section: Reviewer Checklist",
        )

        expect_fail(
            "reviewer lane without architecture contract fails when required",
            write_run(
                temp / "review-without-architecture",
                lanes=[reviewer_lane()],
                lane_map_extra=architecture_control_extra(),
            ),
            "reviewer lane requires successful architecture contract",
        )

        expect_fail(
            "qa evidence cannot pass before architecture lane",
            write_run(
                temp / "qa-before-architecture",
                lanes=[architecture_lane(wave=3), qa_lane(wave=2)],
                lane_map_extra=architecture_control_extra(),
            ),
            "qa lane must run after architecture lane",
        )

        expect_pass(
            "standard multi-lane role-lane architecture fallback allowed",
            write_run(
                temp / "architecture-role-lane-fallback",
                lanes=[architecture_lane(execution_mode="role-lane"), qa_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
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
                    **architecture_control_extra(),
                    "budget": "release",
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
                    **architecture_control_extra(),
                    "budget": "release",
                    "architecture_contract_independent": True,
                },
            ),
        )

        expect_fail(
            "worker without architecture compliance fails",
            write_run(
                temp / "worker-no-architecture-compliance",
                lanes=[
                    architecture_lane(),
                    worker_lane(architecture_compliance_data=None),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "lane worker-a missing architecture_compliance",
        )

        expect_fail(
            "worker handoff without architecture compliance section fails",
            write_run(
                temp / "worker-no-compliance-section",
                lanes=[
                    architecture_lane(),
                    worker_lane(handoff_sections=[]),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Architecture Compliance",
        )

        expect_fail(
            "worker architecture compliance invalid status fails",
            write_run(
                temp / "worker-compliance-invalid-status",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(status="unknown")
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "invalid architecture_compliance.status",
        )

        expect_fail(
            "worker architecture compliance empty sections fail",
            write_run(
                temp / "worker-compliance-empty-sections",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data={
                            **architecture_compliance(),
                            "contract_sections": [],
                        }
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_compliance.contract_sections must be a non-empty array",
        )

        expect_fail(
            "worker architecture compliance unknown section fails",
            write_run(
                temp / "worker-compliance-unknown-section",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            contract_sections=["Unknown Section"]
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "unknown architecture contract section",
        )

        expect_fail(
            "worker architecture compliance empty notes fail",
            write_run(
                temp / "worker-compliance-empty-notes",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(notes="")
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_compliance.notes must be a non-empty string",
        )

        expect_fail(
            "compliant worker cannot set recheck lane",
            write_run(
                temp / "worker-compliant-with-recheck",
                lanes=[
                    architecture_lane(),
                    architecture_lane("architecture-recheck", wave=3),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            recheck_lane="architecture-recheck"
                        )
                    ),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "compliant architecture_compliance must not set recheck_lane",
        )

        expect_fail(
            "drift without recheck lane fails",
            write_run(
                temp / "worker-drift-no-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(status="drift")
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture drift requires recheck_lane",
        )

        expect_fail(
            "drift with missing recheck lane fails",
            write_run(
                temp / "worker-drift-missing-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "recheck_lane not found",
        )

        expect_fail(
            "drift with non-architecture recheck lane fails",
            write_run(
                temp / "worker-drift-non-architecture-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="qa-behavior",
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "recheck_lane must reference an architecture lane",
        )

        expect_fail(
            "drift with non-critical recheck lane fails",
            write_run(
                temp / "worker-drift-non-critical-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        )
                    ),
                    architecture_lane("architecture-recheck", critical=False, wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "recheck_lane must be critical",
        )

        expect_fail(
            "drift with failed recheck lane fails",
            write_run(
                temp / "worker-drift-failed-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        )
                    ),
                    architecture_lane("architecture-recheck", status="fail", wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "recheck_lane must pass",
        )

        expect_fail(
            "drift recheck must run after worker lane",
            write_run(
                temp / "worker-drift-recheck-too-early",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        wave=2,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        ),
                    ),
                    architecture_lane("architecture-recheck", wave=2),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "recheck_lane must run after drifting worker lane",
        )

        expect_pass(
            "drift with later architecture recheck passes",
            write_run(
                temp / "worker-drift-with-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        wave=2,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        ),
                    ),
                    architecture_lane("architecture-recheck", wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "ship with worker lanes requires qa lane",
            write_run(
                temp / "worker-no-qa",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "final Verdict ship requires successful qa lane",
        )

        expect_fail(
            "qa must run after worker lanes",
            write_run(
                temp / "qa-before-worker",
                lanes=[
                    architecture_lane(),
                    qa_control_lane(wave=2),
                    worker_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "qa lane must run after worker lanes",
        )

        expect_fail(
            "qa handoff requires architecture invariants section",
            write_run(
                temp / "qa-no-architecture-invariants",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3, handoff_sections=[]),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Architecture Invariants",
        )

        expect_fail(
            "ship with worker lanes requires reviewer lane",
            write_run(
                temp / "worker-no-reviewer",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "final Verdict ship requires successful reviewer lane",
        )

        expect_fail(
            "reviewer must run after qa lane",
            write_run(
                temp / "reviewer-before-qa",
                lanes=[
                    architecture_lane(),
                    worker_lane(wave=2),
                    reviewer_control_lane(wave=3),
                    qa_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "reviewer lane must run after qa lane",
        )

        expect_fail(
            "reviewer handoff requires architecture matrix mismatches section",
            write_run(
                temp / "reviewer-no-matrix-mismatches",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4, handoff_sections=[CONTRACT_DRIFT_SECTION]),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Architecture Matrix Mismatches",
        )

        expect_fail(
            "reviewer handoff requires contract drift section",
            write_run(
                temp / "reviewer-no-contract-drift",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_sections=[ARCHITECTURE_MATRIX_MISMATCHES_SECTION],
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Contract Drift",
        )

        expect_pass(
            "schema v1 worker lane does not require architecture compliance",
            write_run(
                temp / "schema-v1-worker-no-architecture-compliance",
                lanes=[worker_lane(architecture_compliance_data=None)],
            ),
        )

    print("PASS validate-run lane fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
