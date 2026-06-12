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
ARCHITECTURE_DESIGN_BRIEF_SECTIONS = [
    "Problem Shape",
    "Selected Matrix Facets",
    "System Boundaries",
    "Data And State Model",
    "Public Interfaces",
    "Execution Plan",
    "Risk Model",
    "Verification Strategy",
    "Open Questions",
    "Decision",
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
DEFAULT_ARCHITECTURE_CAPABILITIES = [
    "saas-platform-architecture",
    "go-backend-service-architecture",
    "modular-monolith-architecture",
]
DEFAULT_WORKER_MATRIX_FACETS = ["backend-service", "monolith"]
OMIT = object()
DEFAULT = object()


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


def architecture_context_axis_facets(axis: str, context: dict[str, Any] | None = None) -> list[str]:
    selected_context = context or DEFAULT_ARCHITECTURE_CONTEXT
    value = selected_context.get(axis, [])
    if not isinstance(value, list):
        return []
    return [facet for facet in value if isinstance(facet, str)]


def facet_lines(facets: list[str]) -> str:
    return "\n".join(f"- `{facet}`" for facet in facets)


def architecture_capabilities(
    selected: Any = DEFAULT,
    notes: Any = "Selected capabilities cover the fixture architecture context.",
) -> dict[str, Any]:
    return {
        "selected": list(DEFAULT_ARCHITECTURE_CAPABILITIES)
        if selected is DEFAULT
        else selected,
        "notes": notes,
    }


def architecture_contract_text(
    missing_sections: set[str] | None = None,
    *,
    selected_facets: list[str] | None = None,
    selected_capabilities: list[str] | None = None,
) -> str:
    missing = missing_sections or set()
    facets = architecture_context_facets() if selected_facets is None else selected_facets
    capabilities = (
        DEFAULT_ARCHITECTURE_CAPABILITIES
        if selected_capabilities is None
        else selected_capabilities
    )
    sections = []
    for section in ARCHITECTURE_CONTRACT_SECTIONS:
        if section in missing:
            continue
        body = f"Fixture {section.lower()}."
        if section == "Selected Architecture":
            lines = facet_lines(facets)
            capability_lines = facet_lines(capabilities)
            body = (
                "Matrix facets:\n"
                f"{lines if lines else '- none'}\n\n"
                "Architecture capabilities:\n"
                f"{capability_lines if capability_lines else '- none'}"
            )
        sections.append(f"## {section}\n\n{body}\n")
    return "# Architecture Contract\n\n" + "\n".join(sections)


def architecture_design_brief_text(
    missing_sections: set[str] | None = None,
    *,
    selected_facets: list[str] | None = None,
    decision_status: str | None = "approved",
    extra_decision_statuses: list[str] | None = None,
    section_overrides: dict[str, str] | None = None,
    selected_capabilities: list[str] | None = None,
) -> str:
    missing = missing_sections or set()
    facets = architecture_context_facets() if selected_facets is None else selected_facets
    capabilities = (
        DEFAULT_ARCHITECTURE_CAPABILITIES
        if selected_capabilities is None
        else selected_capabilities
    )
    overrides = section_overrides or {}
    sections = []
    for section in ARCHITECTURE_DESIGN_BRIEF_SECTIONS:
        if section in missing:
            continue
        body = overrides.get(section, f"Fixture {section.lower()}.")
        if section == "Selected Matrix Facets" and section not in overrides:
            lines = facet_lines(facets)
            body = f"Matrix facets:\n{lines}" if lines else "Matrix facets: none."
        if section == "Execution Plan" and section not in overrides:
            lines = facet_lines(capabilities)
            body = (
                "Architecture capabilities:\n"
                f"{lines if lines else '- none'}\n\n"
                "Fixture execution plan."
            )
        if section == "Decision" and section not in overrides:
            lines = []
            if decision_status is not None:
                lines.append(f"Status: {decision_status}")
            for status in extra_decision_statuses or []:
                lines.append(f"Status: {status}")
            body = "\n".join(lines) if lines else "Fixture decision without status."
        sections.append(f"## {section}\n\n{body}\n")
    return "# Architecture Design Brief\n\n" + "\n".join(sections)


def default_worker_handoff_bodies(
    matrix_facets: list[str] | None = None,
) -> dict[str, str]:
    facets = DEFAULT_WORKER_MATRIX_FACETS if matrix_facets is None else matrix_facets
    return {
        ARCHITECTURE_COMPLIANCE_SECTION: "Matrix facets:\n" + facet_lines(facets),
    }


def default_qa_handoff_bodies(
    context: dict[str, Any] | None = None,
) -> dict[str, str]:
    facets = [
        *architecture_context_axis_facets("risk_gates", context),
        *architecture_context_axis_facets("verification_gates", context),
    ]
    body = "Covered gates:\n" + facet_lines(facets) if facets else "Covered gates: none."
    return {ARCHITECTURE_INVARIANTS_SECTION: body}


def default_reviewer_handoff_bodies(
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, str]:
    facets = architecture_context_facets(context)
    selected_capabilities = DEFAULT_ARCHITECTURE_CAPABILITIES if capabilities is None else capabilities
    return {
        ARCHITECTURE_MATRIX_MISMATCHES_SECTION: (
            "Checked facets:\n"
            + facet_lines(facets)
            + "\n\nChecked capabilities:\n"
            + facet_lines(selected_capabilities)
        ),
        CONTRACT_DRIFT_SECTION: (
            "No contract drift for selected Architecture Matrix facets and "
            "architecture capabilities."
        ),
    }


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
                    selected_capabilities = lane.get("architecture_selected_capabilities")
                    if (
                        selected_capabilities is not None
                        and not isinstance(selected_capabilities, list)
                    ):
                        selected_capabilities = []
                    path.write_text(
                        architecture_contract_text(
                            missing,
                            selected_facets=selected_facets,
                            selected_capabilities=selected_capabilities,
                        ),
                        encoding="utf-8",
                    )
                    design_brief = lane.get("architecture_design_brief")
                    if (
                        isinstance(design_brief, str)
                        and design_brief
                        and lane.get("create_architecture_design_brief", True)
                    ):
                        design_path = run_dir / design_brief
                        design_path.parent.mkdir(parents=True, exist_ok=True)
                        design_missing = set(lane.get("missing_design_brief_sections", []))
                        design_selected_facets = lane.get("design_brief_selected_facets")
                        if (
                            design_selected_facets is not None
                            and not isinstance(design_selected_facets, list)
                        ):
                            design_selected_facets = []
                        design_selected_capabilities = lane.get(
                            "design_brief_selected_capabilities"
                        )
                        if (
                            design_selected_capabilities is not None
                            and not isinstance(design_selected_capabilities, list)
                        ):
                            design_selected_capabilities = []
                        design_text = lane.get("architecture_design_brief_text")
                        if not isinstance(design_text, str):
                            extra_statuses = lane.get("extra_design_brief_decision_statuses")
                            if not isinstance(extra_statuses, list):
                                extra_statuses = None
                            section_overrides = lane.get("design_brief_section_overrides")
                            if not isinstance(section_overrides, dict):
                                section_overrides = None
                            design_text = architecture_design_brief_text(
                                design_missing,
                                selected_facets=design_selected_facets,
                                selected_capabilities=design_selected_capabilities,
                                decision_status=lane.get(
                                    "design_brief_decision_status",
                                    "approved",
                                ),
                                extra_decision_statuses=extra_statuses,
                                section_overrides=section_overrides,
                            )
                        design_path.write_text(design_text, encoding="utf-8")
                else:
                    handoff_section_bodies = lane.get("handoff_section_bodies", {})
                    if not isinstance(handoff_section_bodies, dict):
                        handoff_section_bodies = {}
                    sections = [
                        f"## {section}\n\n"
                        f"{handoff_section_bodies.get(section, f'Fixture {section.lower()}.')}\n"
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


def add_agent_placeholder(run_dir: Path, relative_path: str) -> Path:
    path = run_dir / relative_path
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + "\nTODO(agent): finish architecture artifact.\n", encoding="utf-8")
    return run_dir


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


def expect_fail_without(name: str, run_dir: Path, needle: str, forbidden: str) -> None:
    result = validate(run_dir)
    if result.returncode == 0:
        raise AssertionError(f"{name} expected fail")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name} missing '{needle}'\nOutput:\n{output}")
    if forbidden in output:
        raise AssertionError(f"{name} unexpectedly contained '{forbidden}'\nOutput:\n{output}")


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
    handoff_section_bodies: dict[str, str] | None = None,
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
    if handoff_section_bodies is not None:
        lane_data["handoff_section_bodies"] = handoff_section_bodies
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
    architecture_design_brief: Any = DEFAULT,
    create_architecture_design_brief: bool = True,
    design_selected_facets: list[str] | None = None,
    design_decision_status: str | None = "approved",
    extra_design_decision_statuses: list[str] | None = None,
    missing_design_sections: list[str] | None = None,
    design_section_overrides: dict[str, str] | None = None,
    design_text: str | None = None,
    selected_capabilities: list[str] | None = None,
    design_selected_capabilities: list[str] | None = None,
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
    if selected_capabilities is not None:
        lane_data["architecture_selected_capabilities"] = selected_capabilities
    if architecture_design_brief is DEFAULT:
        if status in {"pass", "pass-with-risks"}:
            lane_data["architecture_design_brief"] = f"handoffs/{lane_id}-design.md"
    elif architecture_design_brief is not OMIT:
        lane_data["architecture_design_brief"] = architecture_design_brief
    if "architecture_design_brief" in lane_data:
        lane_data["create_architecture_design_brief"] = create_architecture_design_brief
        if design_selected_facets is not None:
            lane_data["design_brief_selected_facets"] = design_selected_facets
        if design_selected_capabilities is not None:
            lane_data["design_brief_selected_capabilities"] = design_selected_capabilities
        lane_data["design_brief_decision_status"] = design_decision_status
        if extra_design_decision_statuses is not None:
            lane_data["extra_design_brief_decision_statuses"] = extra_design_decision_statuses
        if missing_design_sections is not None:
            lane_data["missing_design_brief_sections"] = missing_design_sections
        if design_section_overrides is not None:
            lane_data["design_brief_section_overrides"] = design_section_overrides
        if design_text is not None:
            lane_data["architecture_design_brief_text"] = design_text
    return lane_data


def architecture_compliance(
    *,
    status: str = "compliant",
    contract_sections: list[str] | None = None,
    matrix_facets: list[str] | None = None,
    notes: str = "Fixture architecture compliance.",
    recheck_lane: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "contract_sections": contract_sections or ["Module Boundaries"],
        "matrix_facets": list(DEFAULT_WORKER_MATRIX_FACETS)
        if matrix_facets is None
        else matrix_facets,
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
    handoff_section_bodies: dict[str, str] | None = None,
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
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_worker_handoff_bodies(),
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
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        lane_type="review",
        role="reviewer",
        wave=wave,
        handoff_sections=handoff_sections,
        handoff_section_bodies=handoff_section_bodies,
    )


def qa_lane(
    lane_id: str = "qa-behavior",
    *,
    status: str = "pass",
    wave: int = 3,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        lane_type="qa",
        role="qa-verifier",
        wave=wave,
        handoff_sections=handoff_sections,
        handoff_section_bodies=handoff_section_bodies,
    )


def qa_control_lane(
    *,
    wave: int = 3,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return qa_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [ARCHITECTURE_INVARIANTS_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_qa_handoff_bodies(context),
    )


def reviewer_control_lane(
    *,
    wave: int = 4,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    return reviewer_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [ARCHITECTURE_MATRIX_MISMATCHES_SECTION, CONTRACT_DRIFT_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_reviewer_handoff_bodies(context, capabilities),
    )


def architecture_control_extra(
    context: dict[str, Any] | None = None,
    capabilities: Any = DEFAULT,
) -> dict[str, Any]:
    extra = {
        "schema_version": 2,
        "budget": "standard",
        "architecture_contract_required": True,
        "architecture_context": context or architecture_context(),
    }
    if capabilities is DEFAULT:
        extra["architecture_capabilities"] = architecture_capabilities()
    elif capabilities is not OMIT:
        extra["architecture_capabilities"] = capabilities
    return extra


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
            "architecture capabilities are required when architecture contract is required",
            write_run(
                temp / "architecture-capabilities-missing",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra=architecture_control_extra(capabilities=OMIT),
            ),
            "lane-map.json field 'architecture_capabilities' is required",
        )

        expect_fail(
            "architecture capabilities must be an object",
            write_run(
                temp / "architecture-capabilities-not-object",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra=architecture_control_extra(capabilities=[]),
            ),
            "lane-map.json field 'architecture_capabilities' must be an object",
        )

        expect_fail(
            "architecture capabilities selected must be non-empty",
            write_run(
                temp / "architecture-capabilities-selected-empty",
                lanes=[
                    architecture_lane(
                        selected_capabilities=[],
                        design_selected_capabilities=[],
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(
                    capabilities=architecture_capabilities(selected=[]),
                ),
            ),
            "lane-map.json architecture_capabilities.selected must be a non-empty array",
        )

        expect_fail(
            "architecture capabilities notes must be non-empty",
            write_run(
                temp / "architecture-capabilities-notes-empty",
                lanes=[architecture_lane(), qa_lane()],
                lane_map_extra=architecture_control_extra(
                    capabilities=architecture_capabilities(notes=""),
                ),
            ),
            "lane-map.json architecture_capabilities.notes must be a non-empty string",
        )

        expect_fail(
            "unknown architecture capability fails",
            write_run(
                temp / "architecture-capabilities-unknown",
                lanes=[
                    architecture_lane(
                        selected_capabilities=["unknown-capability"],
                        design_selected_capabilities=["unknown-capability"],
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(
                    capabilities=architecture_capabilities(
                        selected=["unknown-capability"],
                    ),
                ),
            ),
            "unknown architecture capability: unknown-capability",
        )

        expect_fail(
            "selected matrix facet must be covered by architecture capabilities",
            write_run(
                temp / "architecture-capabilities-missing-facet-coverage",
                lanes=[
                    architecture_lane(
                        selected_capabilities=["saas-platform-architecture"],
                        design_selected_capabilities=["saas-platform-architecture"],
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(
                    capabilities=architecture_capabilities(
                        selected=["saas-platform-architecture"],
                    ),
                ),
            ),
            "architecture_capabilities do not cover Architecture Matrix facet: backend-service",
        )

        expect_fail(
            "architecture design brief execution plan must include selected capabilities",
            write_run(
                temp / "architecture-capabilities-design-brief-missing",
                lanes=[
                    architecture_lane(
                        design_section_overrides={
                            "Execution Plan": (
                                "Architecture capabilities:\n"
                                "- `saas-platform-architecture`\n"
                                "- `go-backend-service-architecture`\n"
                            ),
                        },
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Execution Plan missing architecture capability: modular-monolith-architecture",
        )

        expect_fail(
            "architecture contract selected architecture must include selected capabilities",
            write_run(
                temp / "architecture-capabilities-contract-missing",
                lanes=[
                    architecture_lane(
                        selected_capabilities=[
                            "saas-platform-architecture",
                            "go-backend-service-architecture",
                        ],
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Selected Architecture missing architecture capability: modular-monolith-architecture",
        )

        expect_pass(
            "schema v2 without architecture contract does not require architecture capabilities",
            write_run(
                temp / "schema-v2-no-contract-no-architecture-capabilities",
                lanes=[architecture_lane(architecture_design_brief=OMIT), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

        expect_pass(
            "schema v1 architecture lane does not require design brief",
            write_run(
                temp / "schema-v1-architecture-no-design-brief",
                lanes=[architecture_lane(architecture_design_brief=OMIT)],
            ),
        )

        expect_pass(
            "schema v2 without architecture contract does not require design brief",
            write_run(
                temp / "schema-v2-architecture-no-design-brief-not-required",
                lanes=[architecture_lane(architecture_design_brief=OMIT), qa_lane()],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

        expect_fail(
            "architecture design brief is required",
            write_run(
                temp / "architecture-design-brief-missing",
                lanes=[architecture_lane(architecture_design_brief=OMIT), qa_lane()],
                lane_map_extra=architecture_control_extra(),
            ),
            "missing architecture_design_brief",
        )

        for bad_value_name, bad_value in [
            ("null", None),
            ("empty", ""),
            ("array", ["handoffs/architecture-design.md"]),
            ("object", {"path": "handoffs/architecture-design.md"}),
            ("number", 12),
        ]:
            expect_fail(
                f"architecture design brief {bad_value_name} path fails",
                write_run(
                    temp / f"architecture-design-brief-{bad_value_name}",
                    lanes=[
                        architecture_lane(architecture_design_brief=bad_value),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "architecture_design_brief must be a non-empty string",
            )

        expect_fail(
            "architecture design brief missing file fails",
            write_run(
                temp / "architecture-design-brief-missing-file",
                lanes=[
                    architecture_lane(
                        architecture_design_brief="handoffs/missing-design.md",
                        create_architecture_design_brief=False,
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_design_brief not found",
        )

        expect_pass(
            "architecture design brief custom run relative path passes",
            write_run(
                temp / "architecture-design-brief-custom-path",
                lanes=[
                    architecture_lane(
                        architecture_design_brief="artifacts/architecture/design.md",
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        for missing_section in ARCHITECTURE_DESIGN_BRIEF_SECTIONS:
            expect_fail(
                f"architecture design brief missing {missing_section} section fails",
                write_run(
                    temp / f"architecture-design-brief-missing-{missing_section.lower().replace(' ', '-')}",
                    lanes=[
                        architecture_lane(missing_design_sections=[missing_section]),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                f"architecture design brief missing section: {missing_section}",
            )

        expect_fail(
            "architecture design brief wrong heading text fails",
            write_run(
                temp / "architecture-design-brief-wrong-heading",
                lanes=[
                    architecture_lane(
                        design_text=architecture_design_brief_text(
                            {"Problem Shape"},
                        )
                        + "\n## Problem Shapes\n\nWrong heading text.\n",
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture design brief missing section: Problem Shape",
        )

        expect_fail(
            "architecture design brief plain text section title fails",
            write_run(
                temp / "architecture-design-brief-plain-text-heading",
                lanes=[
                    architecture_lane(
                        design_text=architecture_design_brief_text(
                            {"Problem Shape"},
                        )
                        + "\nProblem Shape\n\nPlain text title.\n",
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture design brief missing section: Problem Shape",
        )

        expect_pass(
            "architecture design brief nested markdown headings pass",
            write_run(
                temp / "architecture-design-brief-nested-headings",
                lanes=[
                    architecture_lane(
                        design_text=architecture_design_brief_text().replace("## ", "### "),
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        for missing_facet in architecture_context_facets():
            expect_fail(
                f"architecture design brief missing facet {missing_facet} fails",
                write_run(
                    temp / f"architecture-design-brief-missing-facet-{missing_facet}",
                    lanes=[
                        architecture_lane(
                            design_selected_facets=[
                                facet
                                for facet in architecture_context_facets()
                                if facet != missing_facet
                            ],
                        ),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                f"Selected Matrix Facets missing Architecture Matrix facet: {missing_facet}",
            )

        expect_fail(
            "architecture design brief partial matrix facet does not pass",
            write_run(
                temp / "architecture-design-brief-partial-facet",
                lanes=[
                    architecture_lane(
                        design_section_overrides={
                            "Selected Matrix Facets": "Matrix facets:\n"
                            + facet_lines(
                                [
                                    "saas-service",
                                    "backend-service",
                                    "monolith",
                                    "golang",
                                    "migrations",
                                    "unit",
                                    "integration",
                                ]
                            )
                        },
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Selected Matrix Facets missing Architecture Matrix facet: go",
        )

        design_empty_gate_context = architecture_context(risk_gates=[], verification_gates=[])
        expect_pass(
            "architecture design brief allows empty risk and verification axes",
            write_run(
                temp / "architecture-design-brief-empty-risk-verification",
                lanes=[
                    architecture_lane(
                        selected_facets=architecture_context_facets(design_empty_gate_context),
                        design_selected_facets=architecture_context_facets(
                            design_empty_gate_context
                        ),
                    ),
                    worker_lane(),
                    qa_control_lane(wave=3, context=design_empty_gate_context),
                    reviewer_control_lane(wave=4, context=design_empty_gate_context),
                ],
                lane_map_extra=architecture_control_extra(design_empty_gate_context),
            ),
        )

        expect_fail(
            "architecture design brief missing decision status fails",
            write_run(
                temp / "architecture-design-brief-missing-decision-status",
                lanes=[
                    architecture_lane(design_decision_status=None),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Design Brief Decision must contain exactly one Status line",
        )

        expect_fail(
            "architecture design brief unknown decision status fails",
            write_run(
                temp / "architecture-design-brief-unknown-decision-status",
                lanes=[
                    architecture_lane(design_decision_status="pending"),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "invalid Architecture Design Brief Decision status",
        )

        expect_fail(
            "architecture design brief multiple decision statuses fail",
            write_run(
                temp / "architecture-design-brief-multiple-decision-statuses",
                lanes=[
                    architecture_lane(
                        design_decision_status="approved",
                        extra_design_decision_statuses=["rejected"],
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Design Brief Decision must contain exactly one Status line",
        )

        expect_fail(
            "architecture design brief status outside decision does not count",
            write_run(
                temp / "architecture-design-brief-status-outside-decision",
                lanes=[
                    architecture_lane(
                        design_section_overrides={
                            "Problem Shape": "Status: approved\nFixture problem.",
                            "Decision": "Decision text without status.",
                        },
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Design Brief Decision must contain exactly one Status line",
        )

        expect_fail(
            "architecture design brief decision status is case sensitive",
            write_run(
                temp / "architecture-design-brief-decision-case",
                lanes=[
                    architecture_lane(design_decision_status="Approved"),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "invalid Architecture Design Brief Decision status",
        )

        expect_fail(
            "architecture design brief decision status spacing is canonical",
            write_run(
                temp / "architecture-design-brief-decision-spacing",
                lanes=[
                    architecture_lane(
                        design_section_overrides={
                            "Decision": "Status : approved",
                        },
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Design Brief Decision must contain exactly one Status line",
        )

        for verdict in ["ship", "pass-with-risks"]:
            expect_fail(
                f"positive verdict {verdict} requires approved architecture design brief",
                write_run(
                    temp / f"architecture-design-brief-needs-revision-{verdict}",
                    verdict=verdict,
                    lanes=[
                        architecture_lane(design_decision_status="needs-revision"),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "positive final Verdict requires approved Architecture Design Brief",
            )
            expect_fail(
                f"positive verdict {verdict} rejects rejected architecture design brief",
                write_run(
                    temp / f"architecture-design-brief-rejected-{verdict}",
                    verdict=verdict,
                    lanes=[
                        architecture_lane(design_decision_status="rejected"),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "positive final Verdict requires approved Architecture Design Brief",
            )

        for verdict in ["blocked", "fail"]:
            expect_pass(
                f"non-positive verdict {verdict} allows needs-revision design brief",
                write_run(
                    temp / f"architecture-design-brief-needs-revision-{verdict}",
                    verdict=verdict,
                    lanes=[
                        architecture_lane(design_decision_status="needs-revision"),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
            )
            expect_pass(
                f"non-positive verdict {verdict} allows rejected design brief",
                write_run(
                    temp / f"architecture-design-brief-rejected-{verdict}",
                    verdict=verdict,
                    lanes=[
                        architecture_lane(design_decision_status="rejected"),
                        qa_lane(),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
            )

        expect_pass(
            "architecture pass-with-risks lane passes with approved design brief",
            write_run(
                temp / "architecture-pass-with-risks-approved-design",
                lanes=[
                    architecture_lane(status="pass-with-risks"),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "architecture pass-with-risks lane fails positive verdict without approved design",
            write_run(
                temp / "architecture-pass-with-risks-rejected-design",
                lanes=[
                    architecture_lane(
                        status="pass-with-risks",
                        design_decision_status="rejected",
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "positive final Verdict requires approved Architecture Design Brief",
        )

        expect_fail_without(
            "failed architecture lane without design brief does not emit design brief error",
            write_run(
                temp / "architecture-failed-without-design-brief",
                lanes=[
                    architecture_lane(
                        status="fail",
                        architecture_design_brief=OMIT,
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture lane must pass before ship",
            "architecture_design_brief",
        )

        expect_fail_without(
            "non-critical architecture lane without design brief does not satisfy gate",
            write_run(
                temp / "architecture-non-critical-without-design-brief",
                lanes=[
                    architecture_lane(
                        critical=False,
                        architecture_design_brief=OMIT,
                    ),
                    qa_lane(),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture lane must be critical",
            "architecture_design_brief",
        )

        expect_fail(
            "worker must run after approved architecture design brief",
            write_run(
                temp / "worker-before-approved-design-brief",
                lanes=[
                    architecture_lane(wave=2),
                    worker_lane(wave=2),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "worker lane must run after approved Architecture Design Brief",
        )

        expect_pass(
            "worker after approved architecture design brief passes",
            write_run(
                temp / "worker-after-approved-design-brief",
                lanes=[
                    architecture_lane(wave=1),
                    worker_lane(wave=2),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "all workers must run after approved architecture design brief",
            write_run(
                temp / "multiple-workers-one-before-approved-design-brief",
                lanes=[
                    architecture_lane(wave=2),
                    worker_lane("worker-a", wave=3),
                    worker_lane("worker-b", lane_type="integration", role="backend-worker", wave=2),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "worker lane must run after approved Architecture Design Brief",
        )

        expect_fail(
            "later approved recheck does not approve earlier worker retroactively",
            write_run(
                temp / "later-approved-recheck-does-not-approve-earlier-worker",
                lanes=[
                    architecture_lane(wave=1, design_decision_status="needs-revision"),
                    worker_lane(wave=2),
                    architecture_lane("architecture-recheck", wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "worker lane must run after approved Architecture Design Brief",
        )

        expect_fail(
            "architecture recheck requires design brief",
            write_run(
                temp / "architecture-recheck-missing-design-brief",
                lanes=[
                    architecture_lane(wave=1),
                    worker_lane(
                        wave=2,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        ),
                    ),
                    architecture_lane(
                        "architecture-recheck",
                        wave=3,
                        architecture_design_brief=OMIT,
                    ),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "missing architecture_design_brief",
        )

        expect_pass(
            "initial approved design and later architecture recheck pass",
            write_run(
                temp / "architecture-design-brief-with-later-recheck",
                lanes=[
                    architecture_lane(wave=1),
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
            "positive verdict with TODO agent in design brief fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-design-brief",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/architecture-contract-design.md",
            ),
            "TODO(agent):",
        )

        expect_fail(
            "positive verdict with TODO agent in contract handoff fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-contract",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/architecture-contract.md",
            ),
            "TODO(agent):",
        )

        expect_fail(
            "positive verdict with TODO agent in worker handoff fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-worker",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/worker-a.md",
            ),
            "TODO(agent):",
        )

        expect_fail(
            "positive verdict with TODO agent in QA handoff fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-qa",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/qa-behavior.md",
            ),
            "TODO(agent):",
        )

        expect_fail(
            "positive verdict with TODO agent in reviewer handoff fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-reviewer",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/review-contract.md",
            ),
            "TODO(agent):",
        )

        expect_fail(
            "positive verdict with TODO agent in evidence fails",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-evidence",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "checks/worker-a.md",
            ),
            "TODO(agent):",
        )

        expect_pass(
            "blocked verdict allows TODO agent placeholders",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-blocked",
                    verdict="blocked",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                "handoffs/architecture-contract-design.md",
            ),
        )

        expect_pass(
            "schema v2 without architecture contract ignores TODO agent placeholders",
            add_agent_placeholder(
                write_run(
                    temp / "placeholder-not-required",
                    lanes=[
                        lane(
                            "worker-a",
                            lane_type="implementation",
                            role="typescript-worker",
                            wave=2,
                        )
                    ],
                    lane_map_extra={
                        "schema_version": 2,
                        "budget": "standard",
                        "architecture_contract_required": False,
                    },
                ),
                "handoffs/worker-a.md",
            ),
        )

        expect_pass(
            "fully authored architecture artifacts pass",
            write_run(
                temp / "fully-authored-architecture-artifacts",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
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
            "worker architecture compliance missing matrix facets fails",
            write_run(
                temp / "worker-compliance-missing-matrix-facets",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data={
                            key: value
                            for key, value in architecture_compliance().items()
                            if key != "matrix_facets"
                        }
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_compliance.matrix_facets must be a non-empty array",
        )

        expect_fail(
            "worker architecture compliance empty matrix facets fail",
            write_run(
                temp / "worker-compliance-empty-matrix-facets",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(matrix_facets=[])
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture_compliance.matrix_facets must be a non-empty array",
        )

        expect_fail(
            "worker architecture compliance unknown matrix facet fails",
            write_run(
                temp / "worker-compliance-unknown-matrix-facet",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            matrix_facets=["unknown-facet"]
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(["unknown-facet"]),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "unknown architecture_context facet",
        )

        expect_fail(
            "worker architecture compliance unselected matrix facet fails",
            write_run(
                temp / "worker-compliance-unselected-matrix-facet",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            matrix_facets=["frontend-service"]
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(["frontend-service"]),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "unselected architecture_context facet",
        )

        expect_fail(
            "worker architecture compliance handoff missing declared matrix facet fails",
            write_run(
                temp / "worker-compliance-handoff-missing-matrix-facet",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            matrix_facets=["backend-service", "monolith"]
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(["backend-service"]),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Compliance missing Architecture Matrix facet: monolith",
        )

        expect_pass(
            "worker architecture compliance selected matrix facets pass",
            write_run(
                temp / "worker-compliance-selected-matrix-facets",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            matrix_facets=["backend-service", "monolith"]
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(
                            ["backend-service", "monolith"]
                        ),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
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
            "qa handoff missing selected risk gate fails",
            write_run(
                temp / "qa-missing-risk-gate",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(
                        wave=3,
                        handoff_section_bodies={
                            ARCHITECTURE_INVARIANTS_SECTION: "Covered gates:\n"
                            + facet_lines(["unit", "integration"])
                        },
                    ),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Invariants missing Architecture Matrix facet: migrations",
        )

        expect_fail(
            "qa handoff missing selected verification gate fails",
            write_run(
                temp / "qa-missing-verification-gate",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(
                        wave=3,
                        handoff_section_bodies={
                            ARCHITECTURE_INVARIANTS_SECTION: "Covered gates:\n"
                            + facet_lines(["migrations", "unit"])
                        },
                    ),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Architecture Invariants missing Architecture Matrix facet: integration",
        )

        empty_gate_context = architecture_context(risk_gates=[], verification_gates=[])
        expect_pass(
            "qa propagation allows empty risk and verification axes",
            write_run(
                temp / "qa-empty-risk-verification-gates",
                lanes=[
                    architecture_lane(selected_facets=architecture_context_facets(empty_gate_context)),
                    worker_lane(),
                    qa_control_lane(wave=3, context=empty_gate_context),
                    reviewer_control_lane(wave=4, context=empty_gate_context),
                ],
                lane_map_extra=architecture_control_extra(empty_gate_context),
            ),
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

        expect_fail(
            "reviewer handoff missing selected architecture context facet fails",
            write_run(
                temp / "reviewer-missing-selected-context-facet",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            ARCHITECTURE_MATRIX_MISMATCHES_SECTION: "Checked facets:\n"
                            + facet_lines(
                                [
                                    "backend-service",
                                    "monolith",
                                    "go",
                                    "migrations",
                                    "unit",
                                    "integration",
                                ]
                            ),
                            CONTRACT_DRIFT_SECTION: "No drift for checked facets.",
                        },
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "reviewer handoff missing Architecture Matrix facet: saas-service",
        )

        expect_pass(
            "reviewer handoff covers selected architecture context facets",
            write_run(
                temp / "reviewer-selected-context-facets",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "reviewer handoff must include selected architecture capabilities",
            write_run(
                temp / "reviewer-capability-coverage-missing",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            ARCHITECTURE_MATRIX_MISMATCHES_SECTION: (
                                "Checked capabilities:\n"
                                "- `saas-platform-architecture`\n"
                                "- `go-backend-service-architecture`\n"
                            ),
                            CONTRACT_DRIFT_SECTION: "No drift for covered capabilities.",
                        },
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "reviewer handoff missing architecture capability: modular-monolith-architecture",
        )

        expect_pass(
            "schema v1 worker lane does not require architecture compliance",
            write_run(
                temp / "schema-v1-worker-no-architecture-compliance",
                lanes=[worker_lane(architecture_compliance_data=None)],
            ),
        )

        expect_pass(
            "schema v2 without architecture contract does not require propagation fields",
            write_run(
                temp / "schema-v2-worker-no-propagation-not-required",
                lanes=[
                    lane(
                        "worker-a",
                        lane_type="implementation",
                        role="typescript-worker",
                        wave=2,
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

    print("PASS validate-run lane fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
