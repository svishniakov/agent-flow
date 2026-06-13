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
DELEGATION_TRACE_SECTION = "Delegation Trace"
RISK_MITIGATIONS_SECTION = "Risk Mitigations"
RISK_MITIGATION_REVIEW_SECTION = "Risk Mitigation Review"
RISK_RESOLUTIONS_SECTION = "Risk Resolutions"
RISK_RESOLUTION_VERIFICATION_SECTION = "Risk Resolution Verification"
RISK_RESOLUTION_REVIEW_SECTION = "Risk Resolution Review"
SENIOR_QA_TEST_DESIGN_REVIEW_SECTION = "Senior QA Test Design Review"
RESOLUTION_ARCHITECT_REVIEW_SECTION = "Resolution Architect Review"
SUPERVISING_ARCHITECT_REVIEW_SECTION = "Supervising Architect Review"
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
DEFAULT_RISK_ID = "browser-proof-gap"
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
    risk_resolution_ids: list[str] | None = None,
) -> dict[str, str]:
    facets = [
        *architecture_context_axis_facets("risk_gates", context),
        *architecture_context_axis_facets("verification_gates", context),
    ]
    body = "Covered gates:\n" + facet_lines(facets) if facets else "Covered gates: none."
    bodies = {ARCHITECTURE_INVARIANTS_SECTION: body}
    if risk_resolution_ids:
        bodies[RISK_RESOLUTION_VERIFICATION_SECTION] = (
            "Verified risk resolutions:\n" + facet_lines(risk_resolution_ids)
        )
    return bodies


def default_reviewer_handoff_bodies(
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    risk_ids: list[str] | None = None,
    risk_resolution_ids: list[str] | None = None,
) -> dict[str, str]:
    facets = architecture_context_facets(context)
    selected_capabilities = DEFAULT_ARCHITECTURE_CAPABILITIES if capabilities is None else capabilities
    bodies = {
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
    if risk_ids:
        bodies[RISK_MITIGATION_REVIEW_SECTION] = "Reviewed risks:\n" + facet_lines(risk_ids)
    if risk_resolution_ids:
        bodies[RISK_RESOLUTION_REVIEW_SECTION] = (
            "Reviewed risk resolutions:\n" + facet_lines(risk_resolution_ids)
        )
    return bodies


def risk_mitigations(
    *,
    risks: Any = DEFAULT,
    version: Any = 1,
) -> dict[str, Any]:
    if risks is DEFAULT:
        risks = [
            {
                "id": DEFAULT_RISK_ID,
                "status": "identified",
                "detected_by": "qa-behavior",
                "category": "verification-gap",
                "problem": "Browser proof did not visibly show the target state.",
                "impact": "Final confidence is lower because the visual proof is incomplete.",
                "affected_scope": "checks/browser-proof.md and final verification evidence.",
                "evidence": ["checks/smoke.md"],
                "next_gate": "resolution",
                "owner_lane": "worker-a",
            }
        ]
    return {"version": version, "risks": risks}


def risk_mitigation_section(risk_ids: list[str] | None = None) -> str:
    ids = [DEFAULT_RISK_ID] if risk_ids is None else risk_ids
    return f"\n## {RISK_MITIGATIONS_SECTION}\n\nIdentified risks:\n{facet_lines(ids)}\n"


def risk_resolutions(
    *,
    resolutions: Any = DEFAULT,
    version: Any = 1,
) -> dict[str, Any]:
    if resolutions is DEFAULT:
        resolutions = [
            {
                "risk_id": DEFAULT_RISK_ID,
                "status": "mitigated",
                "resolution_type": "evidence-added",
                "owner_lane": "worker-a",
                "resolution": "Added concrete evidence for the identified proof gap.",
                "evidence": ["checks/smoke.md"],
                "verification": "QA verified the added evidence in this run.",
                "verified_by": "qa-behavior",
                "reviewed_by": "review-contract",
            }
        ]
    return {"version": version, "resolutions": resolutions}


def resolution_attempt(
    attempt: int,
    *,
    status: str = "blocked",
    owner_lane: str = "worker-a",
    verified_by: str = "qa-behavior",
    reviewed_by: str = "review-contract",
    evidence: list[str] | None = None,
    rollback: Any = DEFAULT,
    blocked_lesson: Any = DEFAULT,
    blocked_reason: Any = "insufficient-evidence",
) -> dict[str, Any]:
    data = {
        "attempt": attempt,
        "status": status,
        "resolution_type": "evidence-added" if status == "blocked" else "test-added",
        "owner_lane": owner_lane,
        "resolution": f"Attempt {attempt} resolution action.",
        "evidence": evidence or ["checks/smoke.md"],
        "verification": f"Attempt {attempt} verification.",
        "verified_by": verified_by,
        "reviewed_by": reviewed_by,
    }
    if status == "blocked":
        if blocked_reason is not OMIT:
            data["blocked_reason"] = blocked_reason
        if rollback is DEFAULT:
            data["rollback"] = {
                "status": "rolled-back",
                "summary": f"Rolled back attempt {attempt} changes.",
                "evidence": ["checks/smoke.md"],
            }
        elif rollback is not OMIT:
            data["rollback"] = rollback
        if blocked_lesson is DEFAULT:
            data["blocked_lesson"] = {
                "status": "quarantined",
                "classification": "insufficient-evidence",
                "summary": "Do not repeat the blocked evidence-only approach.",
                "forbidden_repeat": ["evidence-only-claim"],
            }
        elif blocked_lesson is not OMIT:
            data["blocked_lesson"] = blocked_lesson
    return data


def blocked_recovery(
    *,
    include_senior_qa: bool = True,
    include_architect: bool = True,
    include_supervising: bool = False,
    senior_qa_overrides: dict[str, Any] | None = None,
    architect_overrides: dict[str, Any] | None = None,
    supervising_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recovery: dict[str, Any] = {}
    if include_senior_qa:
        senior_qa = {
            "lane": "senior-qa-test-design",
            "status": "criteria-expanded",
            "covered_acceptance_criteria": ["Original criterion was checked."],
            "missing_acceptance_criteria": ["Visible browser proof was missing."],
            "ambiguous_acceptance_criteria": [],
            "added_acceptance_criteria": ["Show visible browser proof."],
            "test_cases": ["Happy path visible proof."],
            "edge_cases": ["Rendered state changes after async load."],
            "negative_cases": ["Proof file missing visible target."],
            "regression_cases": ["Existing smoke proof still passes."],
            "integration_cases": ["Worker evidence links to QA proof."],
            "data_state_cases": ["Evidence state is present in final artifact."],
            "environment_cases": ["Headless browser output is readable."],
            "external_blockers": [],
            "recheck_result": "blocked",
            "evidence": ["checks/smoke.md"],
        }
        senior_qa.update(senior_qa_overrides or {})
        recovery["senior_qa_test_design_review"] = senior_qa
    if include_architect:
        architect = {
            "lane": "architect-resolution-review",
            "decision": "revised-approach",
            "instruction": "Add visible proof before repeating the resolution.",
            "forbidden_repeat": ["evidence-only-claim"],
            "evidence": ["checks/smoke.md"],
        }
        architect.update(architect_overrides or {})
        recovery["architect_review"] = architect
    if include_supervising:
        supervising = {
            "lane": "supervising-architect-review",
            "decision": "revised-approach",
            "instruction": "Use a stronger proof path and do not repeat failed attempt 2.",
            "forbidden_repeat": ["evidence-only-claim", "attempt-2-proof-gap"],
            "evidence": ["checks/smoke.md"],
        }
        supervising.update(supervising_overrides or {})
        recovery["supervising_architect_review"] = supervising
    return recovery


def resolution_with_attempts(
    attempts: list[dict[str, Any]],
    *,
    status: str = "mitigated",
    owner_lane: str = "worker-b",
    verified_by: str = "qa-retry",
    reviewed_by: str = "review-retry",
    blocked_recovery_data: Any = DEFAULT,
) -> dict[str, Any]:
    resolution = risk_resolutions()["resolutions"][0] | {
        "status": status,
        "owner_lane": owner_lane,
        "resolution": "Final resolution after recovery.",
        "evidence": ["checks/smoke.md"],
        "verification": "QA verified the final recovery attempt.",
        "verified_by": verified_by,
        "reviewed_by": reviewed_by,
        "attempts": attempts,
    }
    if blocked_recovery_data is DEFAULT:
        resolution["blocked_recovery"] = blocked_recovery()
    elif blocked_recovery_data is not OMIT:
        resolution["blocked_recovery"] = blocked_recovery_data
    return resolution


def risk_resolution_section(risk_ids: list[str] | None = None) -> str:
    ids = [DEFAULT_RISK_ID] if risk_ids is None else risk_ids
    return f"\n## {RISK_RESOLUTIONS_SECTION}\n\nResolved risks:\n{facet_lines(ids)}\n"


def delegation_summary(lanes: list[dict[str, Any]]) -> dict[str, Any]:
    subagents: list[dict[str, str]] = []
    role_lanes: list[dict[str, str]] = []
    for lane_data in lanes:
        if lane_data.get("status") in {"planned", "timed-out", "replaced"}:
            continue
        lane_id = lane_data["id"]
        role = lane_data["role"]
        if lane_data.get("execution_mode") == "subagent":
            subagents.append(
                {
                    "lane_id": lane_id,
                    "role": role,
                    "codex_thread_id": f"thread-{lane_id}",
                    "trace": f"agents/{role}/trace.jsonl",
                    "handoff": lane_data.get("handoff") or f"handoffs/{lane_id}.md",
                }
            )
        elif lane_data.get("execution_mode") == "role-lane":
            role_lanes.append(
                {
                    "lane_id": lane_id,
                    "role": role,
                    "reason": f"{lane_id} executed as role-lane.",
                }
            )
    return {
        "version": 1,
        "subagents_used": bool(subagents),
        "role_lanes_used": bool(role_lanes),
        "subagents": subagents,
        "role_lanes": role_lanes,
        "notes": "Generated fixture delegation summary.",
    }


def delegation_trace_section(summary: dict[str, Any]) -> str:
    subagents = [
        item.get("lane_id", "")
        for item in summary.get("subagents", [])
        if isinstance(item, dict) and item.get("lane_id")
    ]
    role_lanes = [
        item.get("lane_id", "")
        for item in summary.get("role_lanes", [])
        if isinstance(item, dict) and item.get("lane_id")
    ]
    traces = [
        item.get("trace", "")
        for item in summary.get("subagents", [])
        if isinstance(item, dict) and item.get("trace")
    ]
    return (
        f"\n## {DELEGATION_TRACE_SECTION}\n\n"
        f"Subagents Used: {'yes' if summary.get('subagents_used') else 'no'}\n"
        f"Role Lanes Used: {'yes' if summary.get('role_lanes_used') else 'no'}\n"
        f"Subagent Lanes: {', '.join(subagents) if subagents else 'none'}\n"
        f"Role Lanes: {', '.join(role_lanes) if role_lanes else 'none'}\n"
        f"Subagent Trace Evidence: {', '.join(traces) if traces else 'none'}\n"
    )


def write_run(
    root: Path,
    *,
    verdict: str = "ship",
    lanes: list[dict[str, Any]] | None = None,
    trace_events: dict[str, list[dict[str, Any]]] | None = None,
    lane_map_extra: dict[str, Any] | None = None,
    delegation_summary_data: Any = DEFAULT,
    final_extra: str = "",
    route_extra: str = "",
    risk_mitigations_data: Any = DEFAULT,
    final_risk_ids: Any = DEFAULT,
    risk_resolutions_data: Any = DEFAULT,
    final_resolution_ids: Any = DEFAULT,
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
    final_text = f"# Final\n\nVerdict: {verdict}\n"
    if lanes is not None and delegation_summary_data is DEFAULT:
        final_text += delegation_trace_section(delegation_summary(lanes))
    elif isinstance(delegation_summary_data, dict):
        final_text += delegation_trace_section(delegation_summary_data)
    final_text += final_extra
    if (
        verdict == "pass-with-risks"
        and risk_mitigations_data is not OMIT
        and final_risk_ids is not OMIT
    ):
        final_text += risk_mitigation_section(
            None if final_risk_ids is DEFAULT else final_risk_ids
        )
    if (
        (verdict == "pass-with-risks" or final_resolution_ids is not DEFAULT)
        and risk_resolutions_data is not OMIT
        and final_resolution_ids is not OMIT
    ):
        final_text += risk_resolution_section(
            None if final_resolution_ids is DEFAULT else final_resolution_ids
        )
    (run_dir / "final.md").write_text(final_text, encoding="utf-8")
    (run_dir / "artifacts.json").write_text("[]\n", encoding="utf-8")
    if verdict == "pass-with-risks" and risk_mitigations_data is DEFAULT:
        (run_dir / "risk-mitigations.json").write_text(
            json.dumps(risk_mitigations(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif risk_mitigations_data is not OMIT and risk_mitigations_data is not DEFAULT:
        if isinstance(risk_mitigations_data, str):
            (run_dir / "risk-mitigations.json").write_text(
                risk_mitigations_data,
                encoding="utf-8",
            )
        else:
            (run_dir / "risk-mitigations.json").write_text(
                json.dumps(risk_mitigations_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    if verdict == "pass-with-risks" and risk_resolutions_data is DEFAULT:
        (run_dir / "risk-resolutions.json").write_text(
            json.dumps(risk_resolutions(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif risk_resolutions_data is not OMIT and risk_resolutions_data is not DEFAULT:
        if isinstance(risk_resolutions_data, str):
            (run_dir / "risk-resolutions.json").write_text(
                risk_resolutions_data,
                encoding="utf-8",
            )
        else:
            (run_dir / "risk-resolutions.json").write_text(
                json.dumps(risk_resolutions_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    timeline_events: list[dict[str, Any]] = []
    for role, events in (trace_events or {}).items():
        trace_path = run_dir / "agents" / role / "trace.jsonl"
        trace_path.parent.mkdir(parents=True)
        write_jsonl(trace_path, events)
        timeline_events.extend(events)

    if lanes is not None:
        if delegation_summary_data is DEFAULT:
            (run_dir / "delegation-summary.json").write_text(
                json.dumps(delegation_summary(lanes), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        elif delegation_summary_data is not OMIT:
            if isinstance(delegation_summary_data, str):
                (run_dir / "delegation-summary.json").write_text(
                    delegation_summary_data,
                    encoding="utf-8",
                )
            else:
                (run_dir / "delegation-summary.json").write_text(
                    json.dumps(delegation_summary_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        for lane in lanes:
            handoff = lane.get("handoff")
            if isinstance(handoff, str):
                path = run_dir / handoff
                path.parent.mkdir(parents=True, exist_ok=True)
                if lane.get("type") == "architecture" and "handoff_sections" not in lane:
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

    if route_extra:
        route_path = run_dir / "route.md"
        route_path.write_text(route_path.read_text(encoding="utf-8") + route_extra, encoding="utf-8")

    timeline_events.append(
        timeline_event("final", summary="Fixture final event.", next_step="handoff")
    )
    write_jsonl(run_dir / "timeline.jsonl", timeline_events)
    return run_dir


def write_compact_run(
    root: Path,
    *,
    verdict: str = "pass-with-risks",
    risk_mitigations_data: Any = DEFAULT,
    final_risk_ids: Any = DEFAULT,
    risk_resolutions_data: Any = DEFAULT,
    final_resolution_ids: Any = DEFAULT,
) -> Path:
    run_dir = root / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "artifacts").mkdir()
    (run_dir / "checks").mkdir()
    (run_dir / "checks" / "smoke.md").write_text("# Smoke\n\npass\n", encoding="utf-8")
    (run_dir / "run.md").write_text("# Compact run\n", encoding="utf-8")
    (run_dir / "checks.md").write_text("# Checks\n\npass\n", encoding="utf-8")
    final_text = f"# Final\n\nVerdict: {verdict}\n"
    if (
        verdict == "pass-with-risks"
        and risk_mitigations_data is not OMIT
        and final_risk_ids is not OMIT
    ):
        final_text += risk_mitigation_section(
            None if final_risk_ids is DEFAULT else final_risk_ids
        )
    if (
        (verdict == "pass-with-risks" or final_resolution_ids is not DEFAULT)
        and risk_resolutions_data is not OMIT
        and final_resolution_ids is not OMIT
    ):
        final_text += risk_resolution_section(
            None if final_resolution_ids is DEFAULT else final_resolution_ids
        )
    (run_dir / "final.md").write_text(final_text, encoding="utf-8")
    if verdict == "pass-with-risks" and risk_mitigations_data is DEFAULT:
        (run_dir / "risk-mitigations.json").write_text(
            json.dumps(risk_mitigations(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif risk_mitigations_data is not OMIT and risk_mitigations_data is not DEFAULT:
        (run_dir / "risk-mitigations.json").write_text(
            json.dumps(risk_mitigations_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if verdict == "pass-with-risks" and risk_resolutions_data is DEFAULT:
        (run_dir / "risk-resolutions.json").write_text(
            json.dumps(risk_resolutions(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif risk_resolutions_data is not OMIT and risk_resolutions_data is not DEFAULT:
        (run_dir / "risk-resolutions.json").write_text(
            json.dumps(risk_resolutions_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
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


def subagent_handoff_trace(role: str, lane_id: str) -> dict[str, Any]:
    return timeline_event(
        "handoff",
        role=role,
        status="pass",
        summary=f"{lane_id} handed off completed work.",
        next_step="orchestrator review",
        artifacts=[f"handoffs/{lane_id}.md"],
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
    risk_resolution_ids: list[str] | None = None,
) -> dict[str, Any]:
    return qa_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [
            ARCHITECTURE_INVARIANTS_SECTION,
            *([RISK_RESOLUTION_VERIFICATION_SECTION] if risk_resolution_ids else []),
        ],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_qa_handoff_bodies(context, risk_resolution_ids),
    )


def reviewer_control_lane(
    *,
    wave: int = 4,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    risk_ids: list[str] | None = None,
    risk_resolution_ids: list[str] | None = None,
) -> dict[str, Any]:
    return reviewer_lane(
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [
            ARCHITECTURE_MATRIX_MISMATCHES_SECTION,
            CONTRACT_DRIFT_SECTION,
            *([RISK_MITIGATION_REVIEW_SECTION] if risk_ids else []),
            *([RISK_RESOLUTION_REVIEW_SECTION] if risk_resolution_ids else []),
        ],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_reviewer_handoff_bodies(
            context,
            capabilities,
            risk_ids,
            risk_resolution_ids,
        ),
    )


def senior_qa_lane(
    *,
    status: str = "pass",
    wave: int = 5,
    lane_id: str = "senior-qa-test-design",
    role: str = "senior-qa-verifier",
    lane_type: str = "qa",
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        lane_type=lane_type,
        role=role,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [SENIOR_QA_TEST_DESIGN_REVIEW_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else {
            SENIOR_QA_TEST_DESIGN_REVIEW_SECTION: (
                f"Risk `{DEFAULT_RISK_ID}` reviewed with expanded test design."
            )
        },
    )


def architect_resolution_review_lane(
    *,
    status: str = "pass",
    wave: int = 6,
    lane_id: str = "architect-resolution-review",
    role: str = "architect",
    critical: bool = False,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        lane_type="architecture",
        role=role,
        critical=critical,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [RESOLUTION_ARCHITECT_REVIEW_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else {
            RESOLUTION_ARCHITECT_REVIEW_SECTION: (
                f"Risk `{DEFAULT_RISK_ID}` instruction: Add visible proof before "
                "repeating the resolution. "
                "avoid evidence-only-claim."
            )
        },
    )


def supervising_architect_review_lane(
    *,
    status: str = "pass",
    wave: int = 10,
    lane_id: str = "supervising-architect-review",
    role: str = "supervising-architect",
    critical: bool = False,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        status=status,
        lane_type="architecture",
        role=role,
        critical=critical,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [SUPERVISING_ARCHITECT_REVIEW_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else {
            SUPERVISING_ARCHITECT_REVIEW_SECTION: (
                f"Risk `{DEFAULT_RISK_ID}` instruction: Use a stronger proof path "
                "and do not repeat failed attempt 2. "
                "avoid attempt-2-proof-gap."
            )
        },
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
                trace_events={
                    "qa-verifier": [
                        spawned_trace("qa-verifier", "qa-live-feed"),
                        subagent_handoff_trace("qa-verifier", "qa-live-feed"),
                    ]
                },
            ),
        )

        expect_fail(
            "positive lane-map run requires delegation summary",
            write_run(
                temp / "lane-map-no-delegation-summary",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                delegation_summary_data=OMIT,
            ),
            "delegation-summary.json is required for positive lane-map run",
        )

        expect_pass(
            "role-lane only delegation summary passes",
            write_run(
                temp / "role-lane-delegation-summary",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
            ),
        )

        expect_fail(
            "subagents false cannot narrate explorer sidecar",
            write_run(
                temp / "sidecar-claim-without-subagent",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                final_extra="\nExplorer sidecar found a broader auth/session improvement.\n",
            ),
            "claims subagent/sidecar without spawned trace evidence",
        )

        summary_claims_unknown = delegation_summary([lane("qa-trace")])
        summary_claims_unknown["role_lanes"][0]["lane_id"] = "missing-lane"
        expect_fail(
            "delegation summary unknown lane id fails",
            write_run(
                temp / "delegation-summary-unknown-lane",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                delegation_summary_data=summary_claims_unknown,
            ),
            "delegation-summary.json: unknown lane id: missing-lane",
        )

        summary_claims_subagent = delegation_summary([lane("qa-trace")])
        summary_claims_subagent["subagents_used"] = True
        summary_claims_subagent["subagents"] = [
            {
                "lane_id": "qa-trace",
                "role": "qa-verifier",
                "codex_thread_id": "thread-qa-trace",
                "trace": "agents/qa-verifier/trace.jsonl",
                "handoff": "handoffs/qa-trace.md",
            }
        ]
        summary_claims_subagent["role_lanes"] = []
        summary_claims_subagent["role_lanes_used"] = False
        expect_fail(
            "delegation summary cannot call role lane a subagent",
            write_run(
                temp / "delegation-summary-role-lane-as-subagent",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                delegation_summary_data=summary_claims_subagent,
            ),
            "delegation-summary.json: lane qa-trace is not execution_mode=subagent",
        )

        expect_fail(
            "subagent spawned trace without terminal handoff fails",
            write_run(
                temp / "subagent-no-terminal-handoff",
                lanes=[lane("qa-live-feed", execution_mode="subagent")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                trace_events={"qa-verifier": [spawned_trace("qa-verifier", "qa-live-feed")]},
            ),
            "missing terminal handoff trace event",
        )

        expect_pass(
            "subagent spawned trace with terminal handoff passes",
            write_run(
                temp / "subagent-terminal-handoff",
                lanes=[lane("qa-live-feed", execution_mode="subagent")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                trace_events={
                    "qa-verifier": [
                        spawned_trace("qa-verifier", "qa-live-feed"),
                        subagent_handoff_trace("qa-verifier", "qa-live-feed"),
                    ]
                },
            ),
        )

        expect_fail(
            "subagent terminal handoff requires matching lane id",
            write_run(
                temp / "subagent-terminal-wrong-lane",
                lanes=[lane("qa-live-feed", execution_mode="subagent")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                trace_events={
                    "qa-verifier": [
                        spawned_trace("qa-verifier", "qa-live-feed"),
                        subagent_handoff_trace("qa-verifier", "other-lane"),
                    ]
                },
            ),
            "missing terminal handoff trace event",
        )

        terminal_without_handoff_artifact = subagent_handoff_trace("qa-verifier", "qa-live-feed")
        terminal_without_handoff_artifact["artifacts"] = []
        expect_fail(
            "subagent terminal handoff requires handoff artifact",
            write_run(
                temp / "subagent-terminal-no-handoff-artifact",
                lanes=[lane("qa-live-feed", execution_mode="subagent")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                trace_events={
                    "qa-verifier": [
                        spawned_trace("qa-verifier", "qa-live-feed"),
                        terminal_without_handoff_artifact,
                    ]
                },
            ),
            "terminal handoff trace event missing handoff artifact",
        )

        expect_fail(
            "route cannot call role lane a subagent",
            write_run(
                temp / "route-role-lane-called-subagent",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                route_extra="\nThe qa-trace subagent returned clean evidence.\n",
            ),
            "claims subagent/sidecar without spawned trace evidence",
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

        expect_fail(
            "pass-with-risks requires risk mitigations file",
            write_run(
                temp / "pass-with-risks-no-risk-mitigations",
                verdict="pass-with-risks",
                risk_mitigations_data=OMIT,
            ),
            "risk-mitigations.json is required for Verdict: pass-with-risks",
        )

        expect_pass(
            "pass-with-risks with identified risk passes",
            write_run(
                temp / "pass-with-risks-identified-risk",
                verdict="pass-with-risks",
            ),
        )

        expect_fail(
            "compact pass-with-risks requires risk mitigations file",
            write_compact_run(
                temp / "compact-pass-with-risks-no-risk-mitigations",
                risk_mitigations_data=OMIT,
            ),
            "risk-mitigations.json is required for Verdict: pass-with-risks",
        )

        expect_pass(
            "compact pass-with-risks with identified risk passes",
            write_compact_run(temp / "compact-pass-with-risks-identified-risk"),
        )

        expect_fail(
            "risk mitigations invalid json fails",
            write_run(
                temp / "risk-mitigations-invalid-json",
                verdict="pass-with-risks",
                risk_mitigations_data="{",
            ),
            "risk-mitigations.json invalid JSON",
        )

        expect_fail(
            "risk mitigations invalid version fails",
            write_run(
                temp / "risk-mitigations-invalid-version",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(version=2),
            ),
            "risk-mitigations.json field 'version' must be 1",
        )

        expect_fail(
            "risk mitigations empty risks fails",
            write_run(
                temp / "risk-mitigations-empty-risks",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(risks=[]),
            ),
            "risk-mitigations.json field 'risks' must be a non-empty array",
        )

        duplicate_risk = risk_mitigations()["risks"][0]
        expect_fail(
            "risk mitigations duplicate risk id fails",
            write_run(
                temp / "risk-mitigations-duplicate-id",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[duplicate_risk, {**duplicate_risk}]
                ),
            ),
            "risk-mitigations.json duplicate risk id: browser-proof-gap",
        )

        expect_fail(
            "risk mitigations non kebab risk id fails",
            write_run(
                temp / "risk-mitigations-non-kebab-id",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "id": "BrowserProofGap"}]
                ),
            ),
            "risk-mitigations.json risks[0].id must be kebab-case",
        )

        missing_problem_risk = {**duplicate_risk}
        missing_problem_risk.pop("problem")
        expect_fail(
            "risk mitigations missing required field fails",
            write_run(
                temp / "risk-mitigations-missing-field",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(risks=[missing_problem_risk]),
            ),
            "risk-mitigations.json risks[0] missing field: problem",
        )

        for field in ["problem", "impact", "affected_scope"]:
            expect_fail(
                f"risk mitigations empty {field} fails",
                write_run(
                    temp / f"risk-mitigations-empty-{field}",
                    verdict="pass-with-risks",
                    risk_mitigations_data=risk_mitigations(
                        risks=[{**duplicate_risk, field: ""}]
                    ),
                ),
                f"risk-mitigations.json risks[0].{field} must be a non-empty string",
            )

        expect_fail(
            "risk mitigations invalid status fails",
            write_run(
                temp / "risk-mitigations-invalid-status",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "status": "resolved"}]
                ),
            ),
            "risk-mitigations.json risks[0].status must be identified",
        )

        expect_fail(
            "risk mitigations invalid category fails",
            write_run(
                temp / "risk-mitigations-invalid-category",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "category": "surprise-risk"}]
                ),
            ),
            "risk-mitigations.json risks[0].category invalid",
        )

        expect_fail(
            "risk mitigations invalid next gate fails",
            write_run(
                temp / "risk-mitigations-invalid-next-gate",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "next_gate": "later"}]
                ),
            ),
            "risk-mitigations.json risks[0].next_gate must be resolution",
        )

        expect_fail(
            "risk mitigations missing evidence path fails",
            write_run(
                temp / "risk-mitigations-missing-evidence",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "evidence": ["checks/missing.md"]}]
                ),
            ),
            "risk-mitigations.json risks[0].evidence[0] not found: checks/missing.md",
        )

        expect_fail(
            "final missing risk mitigations section fails",
            write_run(
                temp / "risk-mitigations-final-missing-section",
                verdict="pass-with-risks",
                final_risk_ids=OMIT,
            ),
            "final.md missing section: Risk Mitigations",
        )

        expect_fail(
            "final missing risk id fails",
            write_run(
                temp / "risk-mitigations-final-missing-risk-id",
                verdict="pass-with-risks",
                final_risk_ids=[],
            ),
            "final.md Risk Mitigations missing risk id: browser-proof-gap",
        )

        expect_fail(
            "risk mitigations unknown detected_by lane fails",
            write_run(
                temp / "risk-mitigations-unknown-detected-by",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                    reviewer_lane(
                        handoff_sections=[RISK_MITIGATION_REVIEW_SECTION],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                        },
                    ),
                ],
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "detected_by": "missing-qa"}]
                ),
            ),
            "risk-mitigations.json risks[0].detected_by lane not found: missing-qa",
        )

        expect_fail(
            "risk mitigations unknown owner_lane fails",
            write_run(
                temp / "risk-mitigations-unknown-owner-lane",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                    reviewer_lane(
                        handoff_sections=[RISK_MITIGATION_REVIEW_SECTION],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                        },
                    ),
                ],
                risk_mitigations_data=risk_mitigations(
                    risks=[{**duplicate_risk, "owner_lane": "missing-worker"}]
                ),
            ),
            "risk-mitigations.json risks[0].owner_lane lane not found: missing-worker",
        )

        expect_fail(
            "lane-map pass-with-risks requires reviewer mitigation review",
            write_run(
                temp / "risk-mitigations-no-reviewer",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                ],
            ),
            "lane-map.json: Verdict pass-with-risks requires successful reviewer lane for Mitigation Gate",
        )

        expect_fail(
            "reviewer missing mitigation review section fails",
            write_run(
                temp / "risk-mitigations-reviewer-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                    reviewer_lane(),
                ],
            ),
            "handoff missing section: Risk Mitigation Review",
        )

        expect_fail(
            "reviewer missing risk id fails",
            write_run(
                temp / "risk-mitigations-reviewer-missing-risk-id",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                    reviewer_lane(
                        handoff_sections=[RISK_MITIGATION_REVIEW_SECTION],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: "Reviewed risks: none.",
                        },
                    ),
                ],
            ),
            "reviewer handoff Risk Mitigation Review missing risk id: browser-proof-gap",
        )

        expect_pass(
            "lane-map pass-with-risks with mitigation review passes",
            write_run(
                temp / "risk-mitigations-reviewer-covers-risk-id",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_control_lane(risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_lane(
                        handoff_sections=[
                            RISK_MITIGATION_REVIEW_SECTION,
                            RISK_RESOLUTION_REVIEW_SECTION,
                        ],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                            RISK_RESOLUTION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                        },
                    ),
                ],
            ),
        )

        expect_fail(
            "pass-with-risks requires risk resolutions file",
            write_run(
                temp / "pass-with-risks-no-risk-resolutions",
                verdict="pass-with-risks",
                risk_resolutions_data=OMIT,
            ),
            "risk-resolutions.json is required for Verdict: pass-with-risks",
        )

        expect_pass(
            "pass-with-risks with identified risk resolution passes",
            write_run(
                temp / "pass-with-risks-identified-risk-resolution",
                verdict="pass-with-risks",
            ),
        )

        expect_pass(
            "compact pass-with-risks with identified risk resolution passes",
            write_compact_run(temp / "compact-pass-with-risks-identified-risk-resolution"),
        )

        default_resolution = risk_resolutions()["resolutions"][0]
        expect_fail(
            "risk resolutions invalid json fails",
            write_run(
                temp / "risk-resolutions-invalid-json",
                verdict="pass-with-risks",
                risk_resolutions_data="{",
            ),
            "risk-resolutions.json invalid JSON",
        )

        expect_fail(
            "risk resolutions invalid version fails",
            write_run(
                temp / "risk-resolutions-invalid-version",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(version=2),
            ),
            "risk-resolutions.json field 'version' must be 1",
        )

        expect_fail(
            "risk resolutions empty resolutions fails",
            write_run(
                temp / "risk-resolutions-empty-resolutions",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(resolutions=[]),
            ),
            "risk-resolutions.json field 'resolutions' must be a non-empty array",
        )

        expect_fail(
            "risk resolutions duplicate risk id fails",
            write_run(
                temp / "risk-resolutions-duplicate-id",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[default_resolution, {**default_resolution}]
                ),
            ),
            "risk-resolutions.json duplicate risk_id: browser-proof-gap",
        )

        expect_fail(
            "risk resolutions unknown risk id fails",
            write_run(
                temp / "risk-resolutions-unknown-risk-id",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[{**default_resolution, "risk_id": "unknown-risk"}]
                ),
            ),
            "risk-resolutions.json resolutions[0].risk_id unknown risk: unknown-risk",
        )

        second_risk = {**duplicate_risk, "id": "api-proof-gap"}
        expect_fail(
            "risk resolutions missing identified risk coverage fails",
            write_run(
                temp / "risk-resolutions-missing-risk-coverage",
                verdict="pass-with-risks",
                risk_mitigations_data=risk_mitigations(
                    risks=[duplicate_risk, second_risk]
                ),
                risk_resolutions_data=risk_resolutions(
                    resolutions=[default_resolution]
                ),
            ),
            "risk-resolutions.json missing resolution for risk id: api-proof-gap",
        )

        expect_fail(
            "risk resolutions invalid status fails",
            write_run(
                temp / "risk-resolutions-invalid-status",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[{**default_resolution, "status": "done"}]
                ),
            ),
            "risk-resolutions.json resolutions[0].status invalid",
        )

        expect_fail(
            "risk resolutions unresolved pass-with-risks fails",
            write_run(
                temp / "risk-resolutions-unresolved-pass-with-risks",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {
                            **default_resolution,
                            "status": "unresolved",
                            "resolution_type": "not-resolved",
                        }
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].status unresolved is not allowed for Verdict: pass-with-risks",
        )

        expect_pass(
            "risk resolutions unresolved blocked passes",
            write_run(
                temp / "risk-resolutions-unresolved-blocked",
                verdict="blocked",
                final_resolution_ids=[DEFAULT_RISK_ID],
                risk_mitigations_data=risk_mitigations(),
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {
                            **default_resolution,
                            "status": "unresolved",
                            "resolution_type": "not-resolved",
                        }
                    ]
                ),
            ),
        )

        expect_fail(
            "risk resolutions invalid resolution type fails",
            write_run(
                temp / "risk-resolutions-invalid-type",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {**default_resolution, "resolution_type": "follow-up-ticket"}
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].resolution_type invalid",
        )

        for field in ["resolution", "verification"]:
            expect_fail(
                f"risk resolutions empty {field} fails",
                write_run(
                    temp / f"risk-resolutions-empty-{field}",
                    verdict="pass-with-risks",
                    risk_resolutions_data=risk_resolutions(
                        resolutions=[{**default_resolution, field: ""}]
                    ),
                ),
                f"risk-resolutions.json resolutions[0].{field} must be a non-empty string",
            )

        expect_fail(
            "risk resolutions missing evidence path fails",
            write_run(
                temp / "risk-resolutions-missing-evidence",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {**default_resolution, "evidence": ["checks/missing.md"]}
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].evidence[0] not found: checks/missing.md",
        )

        expect_fail(
            "final missing risk resolutions section fails",
            write_run(
                temp / "risk-resolutions-final-missing-section",
                verdict="pass-with-risks",
                final_resolution_ids=OMIT,
            ),
            "final.md missing section: Risk Resolutions",
        )

        expect_fail(
            "final missing resolution risk id fails",
            write_run(
                temp / "risk-resolutions-final-missing-risk-id",
                verdict="pass-with-risks",
                final_resolution_ids=[],
            ),
            "final.md Risk Resolutions missing risk id: browser-proof-gap",
        )

        resolution_lane_coverage = [
            worker_lane(),
            qa_control_lane(risk_resolution_ids=[DEFAULT_RISK_ID]),
            reviewer_control_lane(
                risk_ids=[DEFAULT_RISK_ID],
                risk_resolution_ids=[DEFAULT_RISK_ID],
            ),
        ]

        expect_fail(
            "risk resolutions unknown owner lane fails",
            write_run(
                temp / "risk-resolutions-unknown-owner-lane",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {**default_resolution, "owner_lane": "missing-worker"}
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].owner_lane lane not found: missing-worker",
        )

        expect_fail(
            "risk resolutions unknown verified_by lane fails",
            write_run(
                temp / "risk-resolutions-unknown-verified-by",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[{**default_resolution, "verified_by": "missing-qa"}]
                ),
            ),
            "risk-resolutions.json resolutions[0].verified_by lane not found: missing-qa",
        )

        expect_fail(
            "risk resolutions unknown reviewed_by lane fails",
            write_run(
                temp / "risk-resolutions-unknown-reviewed-by",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        {**default_resolution, "reviewed_by": "missing-review"}
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].reviewed_by lane not found: missing-review",
        )

        expect_fail(
            "risk resolutions verified_by must be qa",
            write_run(
                temp / "risk-resolutions-verified-by-not-qa",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[{**default_resolution, "verified_by": "worker-a"}]
                ),
            ),
            "risk-resolutions.json resolutions[0].verified_by must reference a successful qa lane: worker-a",
        )

        expect_fail(
            "risk resolutions verified_by failed qa fails",
            write_run(
                temp / "risk-resolutions-verified-by-failed-qa",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(status="fail"),
                    reviewer_control_lane(
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
                    ),
                ],
            ),
            "risk-resolutions.json resolutions[0].verified_by must reference a successful qa lane: qa-behavior",
        )

        expect_fail(
            "risk resolutions reviewed_by must be review",
            write_run(
                temp / "risk-resolutions-reviewed-by-not-review",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[{**default_resolution, "reviewed_by": "qa-behavior"}]
                ),
            ),
            "risk-resolutions.json resolutions[0].reviewed_by must reference a successful review lane: qa-behavior",
        )

        expect_fail(
            "risk resolutions reviewed_by failed review fails",
            write_run(
                temp / "risk-resolutions-reviewed-by-failed-review",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_control_lane(risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_lane(
                        handoff_sections=[
                            RISK_MITIGATION_REVIEW_SECTION,
                            RISK_RESOLUTION_REVIEW_SECTION,
                        ],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                            RISK_RESOLUTION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                        },
                    )
                    | {"status": "fail", "handoff": None, "evidence": []},
                ],
            ),
            "risk-resolutions.json resolutions[0].reviewed_by must reference a successful review lane: review-contract",
        )

        expect_fail(
            "risk resolution owner must not run after qa",
            write_run(
                temp / "risk-resolutions-owner-after-qa",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(wave=4),
                    qa_control_lane(wave=3, risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_control_lane(
                        wave=5,
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
                    ),
                ],
            ),
            "risk-resolutions.json resolutions[0] owner_lane must not run after verified_by",
        )

        expect_fail(
            "risk resolution qa must not run after reviewer",
            write_run(
                temp / "risk-resolutions-qa-after-reviewer",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(wave=2),
                    qa_control_lane(wave=5, risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_control_lane(
                        wave=4,
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
                    ),
                ],
            ),
            "risk-resolutions.json resolutions[0] verified_by must not run after reviewed_by",
        )

        expect_fail(
            "qa missing risk resolution verification section fails",
            write_run(
                temp / "risk-resolutions-qa-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(),
                    reviewer_control_lane(
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
                    ),
                ],
            ),
            "handoff missing section: Risk Resolution Verification",
        )

        expect_fail(
            "reviewer missing risk resolution review section fails",
            write_run(
                temp / "risk-resolutions-reviewer-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_control_lane(risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_control_lane(risk_ids=[DEFAULT_RISK_ID]),
                ],
            ),
            "handoff missing section: Risk Resolution Review",
        )

        expect_fail(
            "qa missing risk resolution id fails",
            write_run(
                temp / "risk-resolutions-qa-missing-risk-id",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_lane(
                        handoff_sections=[RISK_RESOLUTION_VERIFICATION_SECTION],
                        handoff_section_bodies={
                            RISK_RESOLUTION_VERIFICATION_SECTION: "Verified risks: none.",
                        },
                    ),
                    reviewer_control_lane(
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
                    ),
                ],
            ),
            "QA handoff Risk Resolution Verification missing risk id: browser-proof-gap",
        )

        expect_fail(
            "reviewer missing risk resolution id fails",
            write_run(
                temp / "risk-resolutions-reviewer-missing-risk-id",
                verdict="pass-with-risks",
                lanes=[
                    worker_lane(),
                    qa_control_lane(risk_resolution_ids=[DEFAULT_RISK_ID]),
                    reviewer_lane(
                        handoff_sections=[
                            RISK_MITIGATION_REVIEW_SECTION,
                            RISK_RESOLUTION_REVIEW_SECTION,
                        ],
                        handoff_section_bodies={
                            RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                            RISK_RESOLUTION_REVIEW_SECTION: "Reviewed risks: none.",
                        },
                    ),
                ],
            ),
            "reviewer handoff Risk Resolution Review missing risk id: browser-proof-gap",
        )

        expect_pass(
            "lane-map pass-with-risks with resolution verification and review passes",
            write_run(
                temp / "risk-resolutions-lane-map-valid-path",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
            ),
        )

        attempt_1_blocked = resolution_attempt(1)
        attempt_2_success = resolution_attempt(
            2,
            status="fixed",
            owner_lane="worker-b",
            verified_by="qa-retry",
            reviewed_by="review-retry",
        )
        attempt_2_blocked = resolution_attempt(
            2,
            status="blocked",
            owner_lane="worker-b",
            verified_by="qa-retry",
            reviewed_by="review-retry",
            blocked_lesson={
                "status": "confirmed",
                "classification": "insufficient-evidence",
                "summary": "Attempt 2 repeated an insufficient proof path.",
                "forbidden_repeat": ["attempt-2-proof-gap"],
            },
        )
        attempt_3_success = resolution_attempt(
            3,
            status="contained",
            owner_lane="worker-c",
            verified_by="qa-final",
            reviewed_by="review-final",
        )
        attempt_3_blocked = resolution_attempt(
            3,
            status="blocked",
            owner_lane="worker-c",
            verified_by="qa-final",
            reviewed_by="review-final",
            blocked_lesson={
                "status": "confirmed",
                "classification": "architecture-mismatch",
                "summary": "Final attempt could not satisfy the architecture constraint.",
                "forbidden_repeat": ["attempt-2-proof-gap", "attempt-3-architecture-mismatch"],
            },
        )
        recovery_attempt_2_lanes = [
            worker_lane("worker-a", wave=2),
            qa_lane("qa-behavior", wave=3),
            reviewer_lane(
                "review-contract",
                wave=4,
                handoff_sections=[RISK_MITIGATION_REVIEW_SECTION],
                handoff_section_bodies={
                    RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
            senior_qa_lane(wave=5),
            architect_resolution_review_lane(wave=6),
            worker_lane("worker-b", wave=7),
            qa_lane(
                "qa-retry",
                wave=8,
                handoff_sections=[RISK_RESOLUTION_VERIFICATION_SECTION],
                handoff_section_bodies={
                    RISK_RESOLUTION_VERIFICATION_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
            reviewer_lane(
                "review-retry",
                wave=9,
                handoff_sections=[
                    RISK_MITIGATION_REVIEW_SECTION,
                    RISK_RESOLUTION_REVIEW_SECTION,
                ],
                handoff_section_bodies={
                    RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                    RISK_RESOLUTION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
        ]
        recovery_attempt_3_lanes = [
            *recovery_attempt_2_lanes[:7],
            reviewer_lane(
                "review-retry",
                wave=9,
                handoff_sections=[RISK_MITIGATION_REVIEW_SECTION],
                handoff_section_bodies={
                    RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
            supervising_architect_review_lane(wave=10),
            worker_lane("worker-c", wave=11),
            qa_lane(
                "qa-final",
                wave=12,
                handoff_sections=[RISK_RESOLUTION_VERIFICATION_SECTION],
                handoff_section_bodies={
                    RISK_RESOLUTION_VERIFICATION_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
            reviewer_lane(
                "review-final",
                wave=13,
                handoff_sections=[
                    RISK_MITIGATION_REVIEW_SECTION,
                    RISK_RESOLUTION_REVIEW_SECTION,
                ],
                handoff_section_bodies={
                    RISK_MITIGATION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                    RISK_RESOLUTION_REVIEW_SECTION: f"- `{DEFAULT_RISK_ID}`",
                },
            ),
        ]

        expect_pass(
            "flat existing risk resolution remains valid",
            write_run(
                temp / "risk-resolutions-flat-compatible",
                verdict="pass-with-risks",
                lanes=resolution_lane_coverage,
            ),
        )

        expect_fail(
            "risk resolution attempts non-contiguous fail",
            write_run(
                temp / "risk-resolutions-attempts-non-contiguous",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_3_success],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts must be contiguous from 1",
        )

        expect_fail(
            "risk resolution attempts max three fail",
            write_run(
                temp / "risk-resolutions-attempts-too-many",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [
                                attempt_1_blocked,
                                attempt_2_blocked,
                                attempt_3_blocked,
                                resolution_attempt(4, status="fixed"),
                            ],
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts must not contain more than 3 attempts",
        )

        expect_fail(
            "risk resolution attempt invalid status fails",
            write_run(
                temp / "risk-resolutions-attempt-invalid-status",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [{**attempt_1_blocked, "status": "mystery"}],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts[0].status invalid",
        )

        expect_fail(
            "blocked attempt without lesson fails",
            write_run(
                temp / "risk-resolutions-blocked-attempt-no-lesson",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [resolution_attempt(1, blocked_lesson=OMIT), attempt_2_success],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts[0] missing blocked_lesson",
        )

        expect_fail(
            "blocked attempt without rollback fails",
            write_run(
                temp / "risk-resolutions-blocked-attempt-no-rollback",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [resolution_attempt(1, rollback=OMIT), attempt_2_success],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts[0] missing rollback",
        )

        expect_fail(
            "rolled back rollback without evidence fails",
            write_run(
                temp / "risk-resolutions-rollback-no-evidence",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [
                                resolution_attempt(
                                    1,
                                    rollback={
                                        "status": "rolled-back",
                                        "summary": "Rollback happened.",
                                        "evidence": [],
                                    },
                                ),
                                attempt_2_success,
                            ],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts[0].rollback.evidence must be a non-empty array",
        )

        expect_fail(
            "not possible rollback blocks pass-with-risks",
            write_run(
                temp / "risk-resolutions-rollback-not-possible-pass-with-risks",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [
                                resolution_attempt(
                                    1,
                                    rollback={
                                        "status": "not-possible",
                                        "summary": "Rollback was unsafe.",
                                    },
                                ),
                                attempt_2_success,
                            ],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].attempts[0].rollback.status not-possible is not allowed for Verdict: pass-with-risks",
        )

        expect_fail(
            "blocked attempt without senior qa recovery fails",
            write_run(
                temp / "risk-resolutions-no-senior-qa-recovery",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_success],
                            blocked_recovery_data=blocked_recovery(include_senior_qa=False),
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].blocked_recovery missing senior_qa_test_design_review",
        )

        expect_fail(
            "senior qa lane missing fails",
            write_run(
                temp / "risk-resolutions-senior-qa-lane-missing",
                verdict="pass-with-risks",
                lanes=[lane for lane in recovery_attempt_2_lanes if lane["id"] != "senior-qa-test-design"],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "senior_qa_test_design_review lane not found: senior-qa-test-design",
        )

        expect_fail(
            "senior qa lane wrong role fails",
            write_run(
                temp / "risk-resolutions-senior-qa-wrong-role",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_2_lanes
                        if lane["id"] != "senior-qa-test-design"
                    ],
                    senior_qa_lane(role="qa-verifier"),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "senior_qa_test_design_review must reference a successful senior-qa-verifier qa lane",
        )

        expect_fail(
            "senior qa handoff missing section fails",
            write_run(
                temp / "risk-resolutions-senior-qa-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_2_lanes
                        if lane["id"] != "senior-qa-test-design"
                    ],
                    senior_qa_lane(handoff_sections=[]),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "handoff missing section: Senior QA Test Design Review",
        )

        for field in ["test_cases", "edge_cases", "negative_cases", "evidence"]:
            expect_fail(
                f"senior qa review missing {field} fails",
                write_run(
                    temp / f"risk-resolutions-senior-qa-missing-{field}",
                    verdict="pass-with-risks",
                    risk_resolutions_data=risk_resolutions(
                        resolutions=[
                            resolution_with_attempts(
                                [attempt_1_blocked, attempt_2_success],
                                blocked_recovery_data=blocked_recovery(
                                    senior_qa_overrides={field: []},
                                ),
                            )
                        ]
                    ),
                ),
                f"risk-resolutions.json resolutions[0].blocked_recovery.senior_qa_test_design_review.{field} must be a non-empty array",
            )

        expect_fail(
            "blocked attempt without architect review fails",
            write_run(
                temp / "risk-resolutions-no-architect-recovery",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_success],
                            blocked_recovery_data=blocked_recovery(include_architect=False),
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].blocked_recovery missing architect_review",
        )

        architect_missing_instruction = blocked_recovery()
        architect_missing_instruction["architect_review"].pop("instruction")
        expect_fail(
            "architect review without instruction fails",
            write_run(
                temp / "risk-resolutions-architect-no-instruction",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_success],
                            blocked_recovery_data=architect_missing_instruction,
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].blocked_recovery.architect_review.instruction must be a non-empty string",
        )

        expect_fail(
            "architect review lane critical fails",
            write_run(
                temp / "risk-resolutions-architect-critical",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_2_lanes
                        if lane["id"] != "architect-resolution-review"
                    ],
                    architect_resolution_review_lane(critical=True),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "architect_review must reference a successful non-critical architect architecture lane",
        )

        expect_fail(
            "architect review handoff missing section fails",
            write_run(
                temp / "risk-resolutions-architect-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_2_lanes
                        if lane["id"] != "architect-resolution-review"
                    ],
                    architect_resolution_review_lane(handoff_sections=[]),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "handoff missing section: Resolution Architect Review",
        )

        expect_fail(
            "attempt 2 worker before architect review fails",
            write_run(
                temp / "risk-resolutions-attempt-2-before-architect",
                verdict="pass-with-risks",
                lanes=[
                    lane if lane["id"] != "worker-b" else worker_lane("worker-b", wave=5)
                    for lane in recovery_attempt_2_lanes
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
            "attempt 2 owner_lane must run after architect_review",
        )

        expect_fail(
            "attempt 2 blocked without supervising architect fails",
            write_run(
                temp / "risk-resolutions-attempt-2-no-supervising",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_success],
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].blocked_recovery missing supervising_architect_review",
        )

        expect_fail(
            "supervising architect wrong role fails",
            write_run(
                temp / "risk-resolutions-supervising-wrong-role",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_3_lanes
                        if lane["id"] != "supervising-architect-review"
                    ],
                    supervising_architect_review_lane(role="architect"),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_success],
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
            "supervising_architect_review must reference a successful non-critical supervising-architect architecture lane",
        )

        expect_fail(
            "supervising architect handoff missing section fails",
            write_run(
                temp / "risk-resolutions-supervising-missing-section",
                verdict="pass-with-risks",
                lanes=[
                    *[
                        lane
                        for lane in recovery_attempt_3_lanes
                        if lane["id"] != "supervising-architect-review"
                    ],
                    supervising_architect_review_lane(handoff_sections=[]),
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_success],
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
            "handoff missing section: Supervising Architect Review",
        )

        expect_fail(
            "attempt 3 worker before supervising architect fails",
            write_run(
                temp / "risk-resolutions-attempt-3-before-supervising",
                verdict="pass-with-risks",
                lanes=[
                    lane if lane["id"] != "worker-c" else worker_lane("worker-c", wave=9)
                    for lane in recovery_attempt_3_lanes
                ],
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_success],
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
            "attempt 3 owner_lane must run after supervising_architect_review",
        )

        expect_fail(
            "third blocked attempt cannot pass with risks",
            write_run(
                temp / "risk-resolutions-third-blocked-pass-with-risks",
                verdict="pass-with-risks",
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_blocked],
                            status="unresolved",
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
            "risk-resolutions.json resolutions[0].status unresolved is not allowed for Verdict: pass-with-risks",
        )

        expect_pass(
            "third blocked attempt allows final blocked",
            write_run(
                temp / "risk-resolutions-third-blocked-final-blocked",
                verdict="blocked",
                final_resolution_ids=[DEFAULT_RISK_ID],
                risk_mitigations_data=risk_mitigations(),
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_blocked],
                            status="unresolved",
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
        )

        expect_pass(
            "blocked recovery attempt 2 passes",
            write_run(
                temp / "risk-resolutions-recovered-attempt-2",
                verdict="pass-with-risks",
                lanes=recovery_attempt_2_lanes,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts([attempt_1_blocked, attempt_2_success])
                    ]
                ),
            ),
        )

        expect_pass(
            "blocked recovery attempt 3 passes",
            write_run(
                temp / "risk-resolutions-recovered-attempt-3",
                verdict="pass-with-risks",
                lanes=recovery_attempt_3_lanes,
                risk_resolutions_data=risk_resolutions(
                    resolutions=[
                        resolution_with_attempts(
                            [attempt_1_blocked, attempt_2_blocked, attempt_3_success],
                            owner_lane="worker-c",
                            verified_by="qa-final",
                            reviewed_by="review-final",
                            blocked_recovery_data=blocked_recovery(include_supervising=True),
                        )
                    ]
                ),
            ),
        )

        for verdict in ["ship", "blocked", "fail"]:
            expect_pass(
                f"{verdict} does not require risk mitigations",
                write_run(temp / f"{verdict}-no-risk-mitigations", verdict=verdict),
            )
            expect_pass(
                f"{verdict} does not require risk resolutions",
                write_run(
                    temp / f"{verdict}-no-risk-resolutions",
                    verdict=verdict,
                    risk_resolutions_data=OMIT,
                ),
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
                    reviewer_control_lane(wave=4, risk_ids=[DEFAULT_RISK_ID]),
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
                trace_events={
                    "architect": [
                        spawned_trace("architect", "architecture-contract"),
                        subagent_handoff_trace("architect", "architecture-contract"),
                    ]
                },
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
