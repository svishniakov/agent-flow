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
ENGINEERING_SIMPLICITY_SECTION = "Engineering Simplicity"
ENGINEERING_SIMPLICITY_SCOPE_SECTION = "Engineering Simplicity Scope"
LANE_BOUNDARY_SECTION = "Boundary Evidence"
ENGINEERING_SIMPLICITY_CHECKS = [
    "no-extra-work",
    "stdlib-native-first",
    "existing-helper-first",
    "dependency-justified",
    "abstraction-justified",
    "smallest-working-diff",
    "tests-fit-risk",
]
ARCHITECTURE_INVARIANTS_SECTION = "Architecture Invariants"
ARCHITECTURE_MATRIX_MISMATCHES_SECTION = "Architecture Matrix Mismatches"
CONTRACT_DRIFT_SECTION = "Contract Drift"
DELEGATION_TRACE_SECTION = "Delegation Trace"
MANDATORY_INDEPENDENT_QA_REVIEW_SECTION = "Mandatory Independent QA Review"
CONTINUATION_SUMMARY_PATH = "continuation-summary.json"
CONTINUATION_SUMMARY_SECTION = "Continuation Summary"
CONTINUATION_REVALIDATION_SECTION = "Continuation Revalidation"
CONTINUATION_REVIEW_SECTION = "Continuation Review"
HARNESS_EVALUATION_PATH = "harness-evaluation.json"
HARNESS_EVALUATION_SECTION = "Harness Evaluation"
HARNESS_EVALUATION_REVIEW_SECTION = "Harness Evaluation Review"
CLAIM_EVIDENCE_PATH = "claim-evidence.json"
CLAIM_EVIDENCE_LABEL = "Claim Evidence"
ACCEPTANCE_TRACEABILITY_PATH = "acceptance-traceability.json"
ACCEPTANCE_CRITERIA_LABEL = "Acceptance Criteria"
RISK_MITIGATIONS_SECTION = "Risk Mitigations"
RISK_MITIGATION_REVIEW_SECTION = "Risk Mitigation Review"
RISK_RESOLUTIONS_SECTION = "Risk Resolutions"
RISK_RESOLUTION_VERIFICATION_SECTION = "Risk Resolution Verification"
RISK_RESOLUTION_REVIEW_SECTION = "Risk Resolution Review"
SENIOR_QA_TEST_DESIGN_REVIEW_SECTION = "Senior QA Test Design Review"
RESOLUTION_ARCHITECT_REVIEW_SECTION = "Resolution Architect Review"
SUPERVISING_ARCHITECT_REVIEW_SECTION = "Supervising Architect Review"
VERIFICATION_GATE_RESULTS_SECTION = "Verification Gate Results"
VERIFICATION_READINESS_PATH = "verification-readiness.json"
VERIFICATION_READINESS_LANE_ID = "verification-readiness-1"
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
DEFAULT_PRIMARY_SURFACES = ["api-service"]
DEFAULT_SECONDARY_SURFACES = ["smoke-tests"]
DEFAULT_SIMPLICITY_SCOPE_EVIDENCE = "checks/engineering-simplicity-scope.md"
DEFAULT_BOUNDARY_ALLOWED_PATHS = ["apps/api-service/src/**", "apps/shared/src/**"]
DEFAULT_BOUNDARY_FORBIDDEN_PATHS = [
    "references/**",
    "registries/**",
    "testdata/golden-traces/**",
]
DEFAULT_BOUNDARY_CHANGED_PATH = "apps/api-service/src/routes/settings.ts"
DEFAULT_CLAIM_ID = "architecture-contract-claim"
DEFAULT_ACCEPTANCE_ID = "architecture-contract-acceptance"
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


def claim_evidence_lines(claim_ids: list[str] | None = None) -> str:
    ids = [DEFAULT_CLAIM_ID] if claim_ids is None else claim_ids
    if not ids:
        return ""
    return "\n\nClaim evidence:\n" + "\n".join(
        f"- Claim Evidence: `{claim_id}`" for claim_id in ids
    )


def acceptance_criteria_lines(acceptance_ids: list[str] | None = None) -> str:
    ids = [DEFAULT_ACCEPTANCE_ID] if acceptance_ids is None else acceptance_ids
    if not ids:
        return ""
    return "\n\nAcceptance criteria:\n" + "\n".join(
        f"- Acceptance Criteria: `{acceptance_id}`" for acceptance_id in ids
    )


def selected_verification_gate_facets(
    context: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    selected_context = context or DEFAULT_ARCHITECTURE_CONTEXT
    gates: list[tuple[str, str]] = []
    for axis in ["risk_gates", "verification_gates"]:
        value = selected_context.get(axis, [])
        if isinstance(value, list):
            gates.extend((axis, facet) for facet in value if isinstance(facet, str))
    return gates


def verification_gate_result_lines(
    context: dict[str, Any] | None = None,
    status: str = "pass",
) -> str:
    gates = selected_verification_gate_facets(context)
    if not gates:
        return "Verification gates: none."
    return "\n".join(f"- `{axis}` `{facet}` `{status}`" for axis, facet in gates)


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
    claim_ids: list[str] | None = None,
    acceptance_ids: list[str] | None = None,
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
        if section in {"QA Gates", "Reviewer Checklist"}:
            body += claim_evidence_lines(claim_ids)
            body += acceptance_criteria_lines(acceptance_ids)
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
    primary_surfaces: list[str] | None = None,
    secondary_surfaces: list[str] | None = None,
    lane_id: str = "worker-a",
    changed_paths: list[str] | None = None,
) -> dict[str, str]:
    facets = DEFAULT_WORKER_MATRIX_FACETS if matrix_facets is None else matrix_facets
    primary = DEFAULT_PRIMARY_SURFACES if primary_surfaces is None else primary_surfaces
    secondary = DEFAULT_SECONDARY_SURFACES if secondary_surfaces is None else secondary_surfaces
    boundary_paths = [DEFAULT_BOUNDARY_CHANGED_PATH] if changed_paths is None else changed_paths
    changed_path_text = facet_lines(boundary_paths) if boundary_paths else "- none"
    return {
        ARCHITECTURE_COMPLIANCE_SECTION: "Matrix facets:\n" + facet_lines(facets),
        ENGINEERING_SIMPLICITY_SECTION: (
            "Checks:\n"
            + facet_lines(ENGINEERING_SIMPLICITY_CHECKS)
            + "\n\nPrimary surfaces:\n"
            + facet_lines(primary)
            + "\n\nSecondary surfaces:\n"
            + facet_lines(secondary)
            + "\n\nStatus: pass\n"
            "Notes: No needless dependency, abstraction, or scope expansion found."
        ),
        LANE_BOUNDARY_SECTION: (
            f"Lane: `{lane_id}`\n"
            f"Artifact: `{lane_boundary_artifact_path(lane_id)}`\n"
            "Changed paths:\n"
            f"{changed_path_text}\n\n"
            "No out-of-bound product changes found."
        ),
    }


def default_qa_handoff_bodies(
    context: dict[str, Any] | None = None,
    risk_resolution_ids: list[str] | None = None,
    include_verification_results: bool = False,
    verification_status: str = "pass",
    continuation_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    primary_surfaces: list[str] | None = None,
    boundary_worker_ids: list[str] | None = None,
) -> dict[str, str]:
    primary = DEFAULT_PRIMARY_SURFACES if primary_surfaces is None else primary_surfaces
    boundary_workers = ["worker-a"] if boundary_worker_ids is None else boundary_worker_ids
    facets = [
        *architecture_context_axis_facets("risk_gates", context),
        *architecture_context_axis_facets("verification_gates", context),
    ]
    body = "Covered gates:\n" + facet_lines(facets) if facets else "Covered gates: none."
    boundary_text = (
        "\n\nBoundary Evidence: no out-of-bound product changes for worker lanes:\n"
        + (facet_lines(boundary_workers) if boundary_workers else "- none")
    )
    bodies = {
        ARCHITECTURE_INVARIANTS_SECTION: (
            body + boundary_text + claim_evidence_lines(claim_ids)
        ),
        ENGINEERING_SIMPLICITY_SCOPE_SECTION: (
            "Verified primary simplicity surfaces:\n" + facet_lines(primary)
        ),
    }
    if risk_resolution_ids:
        bodies[RISK_RESOLUTION_VERIFICATION_SECTION] = (
            "Verified risk resolutions:\n" + facet_lines(risk_resolution_ids)
        )
    if include_verification_results:
        bodies[VERIFICATION_GATE_RESULTS_SECTION] = verification_gate_result_lines(
            context,
            verification_status,
        )
    if continuation_ids:
        bodies[CONTINUATION_REVALIDATION_SECTION] = (
            "Revalidated continuation items:\n" + facet_lines(continuation_ids)
        )
    return bodies


def verification_readiness(
    *,
    status: str = "ready",
    context: dict[str, Any] | None = None,
    attempts: Any = DEFAULT,
    approval_requests: Any = DEFAULT,
    approval_executions: Any = DEFAULT,
) -> dict[str, Any]:
    gate_readiness = (
        "ready"
        if status == "ready"
        else "needs-approval"
        if status in {"needs-approval", "paused-blocked"}
        else "blocked"
    )
    attempt_status = (
        "ready"
        if status == "ready"
        else "needs-approval"
        if status in {"needs-approval", "paused-blocked"}
        else "blocked"
    )
    gates = [
        {
            "axis": axis,
            "facet": facet,
            "readiness": gate_readiness,
            "check": f"check {facet}",
            "evidence": ["checks/verification-readiness.md"],
            "notes": f"{facet} readiness fixture.",
        }
        for axis, facet in selected_verification_gate_facets(context)
    ]
    if attempts is DEFAULT:
        attempts = [
            {
                "id": "readiness-1",
                "lane": VERIFICATION_READINESS_LANE_ID,
                "status": attempt_status,
                "gates": gates,
                "blockers": [] if status == "ready" else ["verification-env-missing"],
                "approval_requests": [] if status == "ready" else ["start-dev-stack"],
            }
        ]
    if approval_requests is DEFAULT:
        approval_requests = [] if status == "ready" else [
            {
                "id": "start-dev-stack",
                "status": "pending",
                "reason": "Fixture verification needs documented local runtime.",
                "commands": [
                    {
                        "cwd": "/repo",
                        "command": "documented safe command",
                        "source": "README.md",
                        "requires_user_approval": True,
                    }
                ],
                "manual_instruction": "Start the documented stack, then reply: Готово.",
                "resume_phrase": "Готово",
                "affected_gates": [facet for _axis, facet in selected_verification_gate_facets(context)],
            }
        ]
    if approval_executions is DEFAULT:
        approval_executions = []
    return {
        "version": 1,
        "status": status,
        "attempts": attempts,
        "approval_requests": approval_requests,
        "approval_executions": approval_executions,
    }


def verification_readiness_section(blocker_ids: list[str] | None = None) -> str:
    ids = blocker_ids or ["verification-env-missing"]
    return (
        "\n## Verification Readiness\n\n"
        "Blocked readiness ids:\n"
        f"{facet_lines(ids)}\n\n"
        "Manual resume phrase: `Готово`.\n"
    )


def qa_verification_results(
    *,
    status: str = "pass",
    context: dict[str, Any] | None = None,
    gate_status: str = "pass",
) -> dict[str, Any]:
    return {
        "status": status,
        "gates": [
            {
                "axis": axis,
                "facet": facet,
                "status": gate_status,
                "evidence": ["checks/qa-behavior.md"],
                "notes": f"{facet} verification fixture.",
            }
            for axis, facet in selected_verification_gate_facets(context)
        ],
    }


def default_reviewer_handoff_bodies(
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    risk_ids: list[str] | None = None,
    risk_resolution_ids: list[str] | None = None,
    continuation_ids: list[str] | None = None,
    harness_review_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    primary_surfaces: list[str] | None = None,
    boundary_worker_ids: list[str] | None = None,
) -> dict[str, str]:
    facets = architecture_context_facets(context)
    selected_capabilities = DEFAULT_ARCHITECTURE_CAPABILITIES if capabilities is None else capabilities
    primary = DEFAULT_PRIMARY_SURFACES if primary_surfaces is None else primary_surfaces
    boundary_workers = ["worker-a"] if boundary_worker_ids is None else boundary_worker_ids
    bodies = {
        ARCHITECTURE_MATRIX_MISMATCHES_SECTION: (
            "Checked facets:\n"
            + facet_lines(facets)
            + "\n\nChecked capabilities:\n"
            + facet_lines(selected_capabilities)
        ),
        CONTRACT_DRIFT_SECTION: (
            "Engineering Simplicity checked. "
            + "Primary surfaces: "
            + ", ".join(primary)
            + ". Rejected peripheral-only closure. "
            + "Boundary Evidence checked for worker lanes: "
            + ", ".join(boundary_workers)
            + ". "
            +
            "No contract drift for selected Architecture Matrix facets and "
            "architecture capabilities."
            + claim_evidence_lines(claim_ids)
        ),
    }
    if risk_ids:
        bodies[RISK_MITIGATION_REVIEW_SECTION] = "Reviewed risks:\n" + facet_lines(risk_ids)
    if risk_resolution_ids:
        bodies[RISK_RESOLUTION_REVIEW_SECTION] = (
            "Reviewed risk resolutions:\n" + facet_lines(risk_resolution_ids)
        )
    if continuation_ids:
        bodies[CONTINUATION_REVIEW_SECTION] = (
            "Reviewed continuation items:\n" + facet_lines(continuation_ids)
        )
    if harness_review_ids:
        bodies[HARNESS_EVALUATION_REVIEW_SECTION] = (
            "Reviewed harness evaluation items:\n" + facet_lines(harness_review_ids)
        )
    return bodies


def claim_evidence(
    *,
    version: int = 1,
    claims: Any = DEFAULT,
    claim_id: str = DEFAULT_CLAIM_ID,
    owner_lane: str = "qa-behavior",
    reviewed_by: str = "review-contract",
    section: str = ARCHITECTURE_INVARIANTS_SECTION,
    status: str = "supported",
    claim: str = "Fixture architecture claim is backed by exact evidence marker.",
    subjects: list[str] | None = None,
    evidence: Any = DEFAULT,
) -> dict[str, Any]:
    if evidence is DEFAULT:
        evidence = [
            {
                "path": "checks/qa-behavior.md",
                "markers": ["# qa-behavior evidence"],
            }
        ]
    if claims is DEFAULT:
        claims = [
            {
                "id": claim_id,
                "owner_lane": owner_lane,
                "reviewed_by": reviewed_by,
                "section": section,
                "status": status,
                "claim": claim,
                "subjects": subjects or ["fixture-subject"],
                "evidence": evidence,
            }
        ]
    return {"version": version, "claims": claims}


def acceptance_traceability(
    *,
    version: int = 1,
    acceptance: Any = DEFAULT,
    acceptance_id: str = DEFAULT_ACCEPTANCE_ID,
    source: str = "Architecture Contract QA Gates",
    requirement: str = "Fixture architecture acceptance is backed by exact evidence marker.",
    subjects: list[str] | None = None,
    contract_types: list[str] | None = None,
    status: str = "supported",
    surface_expectations: Any = DEFAULT,
    evidence: Any = DEFAULT,
    negative_fixture_evidence: Any = DEFAULT,
) -> dict[str, Any]:
    if surface_expectations is DEFAULT:
        surface_expectations = [
            {
                "surface": "service",
                "polarity": "positive",
                "proof_kinds": ["unit-test"],
            },
            {
                "surface": "service",
                "polarity": "negative",
                "proof_kinds": ["unit-test"],
            },
        ]
    if evidence is DEFAULT:
        evidence = [
            {
                "surface": "service",
                "polarity": "positive",
                "proof_kind": "unit-test",
                "path": "checks/qa-behavior.md",
                "markers": ["# qa-behavior evidence"],
            }
        ]
    if negative_fixture_evidence is DEFAULT:
        negative_fixture_evidence = [
            {
                "surface": "service",
                "polarity": "negative",
                "proof_kind": "unit-test",
                "path": "checks/qa-behavior.md",
                "markers": ["# qa-behavior evidence"],
            }
        ]
    if acceptance is DEFAULT:
        item = {
            "id": acceptance_id,
            "source": source,
            "requirement": requirement,
            "subjects": subjects or ["fixture-subject"],
            "contract_types": ["gate"] if contract_types is None else contract_types,
            "status": status,
            "evidence": evidence,
        }
        if surface_expectations is not OMIT:
            item["surface_expectations"] = surface_expectations
        if negative_fixture_evidence is not OMIT:
            item["negative_fixture_evidence"] = negative_fixture_evidence
        acceptance = [item]
    return {"version": version, "acceptance": acceptance}


def continuation_summary(
    *,
    status: str = "resumed-ready",
    previous_checkpoint: Any = DEFAULT,
    resolved_blockers: Any = DEFAULT,
    readiness_lane: str = "verification-readiness-continuation",
    historical_worker_lanes: list[str] | None = None,
    new_worker_lanes: list[str] | None = None,
    revalidated_lanes: list[str] | None = None,
    qa_recheck_lane: str = "qa-behavior",
    reviewer_recheck_lane: str = "review-contract",
    notes: str = "No new worker lane ran before readiness.",
) -> dict[str, Any]:
    if previous_checkpoint is DEFAULT:
        previous_checkpoint = {
            "lane_id": "orchestrator-blocked-checkpoint",
            "verdict": "blocked",
            "snapshot": "artifacts/checkpoints/orchestrator-blocked-checkpoint/final.md",
        }
    if resolved_blockers is DEFAULT:
        resolved_blockers = [
            {
                "id": "pii-smoke-command-undocumented",
                "resolution": "Documented command exists and passes.",
                "evidence": ["checks/verification-readiness.md"],
            }
        ]
    return {
        "version": 1,
        "status": status,
        "previous_checkpoint": previous_checkpoint,
        "resolved_blockers": resolved_blockers,
        "readiness_lane": readiness_lane,
        "historical_worker_lanes": ["worker-a"] if historical_worker_lanes is None else historical_worker_lanes,
        "new_worker_lanes": [] if new_worker_lanes is None else new_worker_lanes,
        "revalidated_lanes": ["worker-a"] if revalidated_lanes is None else revalidated_lanes,
        "qa_recheck_lane": qa_recheck_lane,
        "reviewer_recheck_lane": reviewer_recheck_lane,
        "notes": notes,
    }


def continuation_ids(
    *,
    blocker_ids: list[str] | None = None,
    worker_ids: list[str] | None = None,
) -> list[str]:
    return [
        *(blocker_ids or ["pii-smoke-command-undocumented"]),
        *(worker_ids or ["worker-a"]),
    ]


def continuation_summary_section(ids: list[str] | None = None) -> str:
    items = continuation_ids() if ids is None else ids
    return (
        f"\n## {CONTINUATION_SUMMARY_SECTION}\n\n"
        "Continuation evidence:\n"
        f"{facet_lines(items)}\n"
    )


def harness_ids(
    *,
    finding_ids: list[str] | None = None,
    proposal_ids: list[str] | None = None,
) -> list[str]:
    return [
        *(finding_ids or ["readiness-before-workers-recovery"]),
        *(proposal_ids or ["promote-continuation-revalidation-evidence"]),
    ]


def harness_evaluation(
    *,
    status: str = "evaluated",
    learning_triggers: list[str] | None = None,
    source_artifacts: list[str] | None = None,
    findings: Any = DEFAULT,
    proposals: Any = DEFAULT,
    blocked_reason: Any = OMIT,
    blocked_evidence: Any = OMIT,
) -> dict[str, Any]:
    if findings is DEFAULT:
        findings = [
            {
                "id": "readiness-before-workers-recovery",
                "type": "recovery-success",
                "problem_class": "verification-readiness-ordering",
                "architecture_context": ["saas-service", "backend-service"],
                "architecture_capabilities": ["saas-platform-architecture"],
                "approach": (
                    "pause at blocked checkpoint, document readiness, "
                    "revalidate historical workers"
                ),
                "outcome": "success",
                "evidence": ["checks/verification-readiness.md"],
                "lesson": (
                    "Do not rewrite worker waves after resume; revalidate "
                    "historical workers instead."
                ),
                "reuse_when": "same-run resume after blocked readiness",
                "do_not_reuse_when": "new worker ran after checkpoint before ready readiness",
            }
        ]
    if proposals is DEFAULT:
        proposals = [
            {
                "id": "promote-continuation-revalidation-evidence",
                "type": "evidence-record",
                "status": "proposed",
                "target": "Evidence Records",
                "rationale": "The recovery pattern passed validation and has reusable boundaries.",
                "evidence": ["checks/verification-readiness.md"],
                "requires_human_approval": False,
            }
        ]
    data = {
        "version": 1,
        "status": status,
        "learning_triggers": learning_triggers or ["continuation"],
        "source_artifacts": source_artifacts or ["lane-map.json", "timeline.jsonl"],
        "findings": findings,
        "proposals": proposals,
    }
    if blocked_reason is not OMIT:
        data["blocked_reason"] = blocked_reason
    if blocked_evidence is not OMIT:
        data["blocked_evidence"] = blocked_evidence
    return data


def resolution_data_has_blocked_attempt(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    resolutions = data.get("resolutions")
    if not isinstance(resolutions, list):
        return False
    for resolution in resolutions:
        if not isinstance(resolution, dict):
            continue
        attempts = resolution.get("attempts")
        if not isinstance(attempts, list):
            continue
        if any(isinstance(attempt, dict) and attempt.get("status") == "blocked" for attempt in attempts):
            return True
    return False


def harness_evaluation_section(ids: list[str] | None = None) -> str:
    items = harness_ids() if ids is None else ids
    return (
        f"\n## {HARNESS_EVALUATION_SECTION}\n\n"
        "Harness findings and proposals:\n"
        f"{facet_lines(items)}\n"
    )


def write_harness_evaluation_file(
    run_dir: Path,
    data: Any = DEFAULT,
) -> Path:
    path = run_dir / HARNESS_EVALUATION_PATH
    if data is DEFAULT:
        data = harness_evaluation()
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_dir


def write_continuation_summary_file(
    run_dir: Path,
    data: Any = DEFAULT,
    harness_evaluation_data: Any = DEFAULT,
) -> Path:
    checkpoint_dir = run_dir / "artifacts" / "checkpoints" / "orchestrator-blocked-checkpoint"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "final.md").write_text(
        "# Final checkpoint\n\nVerdict: blocked\n",
        encoding="utf-8",
    )
    path = run_dir / CONTINUATION_SUMMARY_PATH
    if data is DEFAULT:
        data = continuation_summary()
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if harness_evaluation_data is DEFAULT:
        write_harness_evaluation_file(
            run_dir,
            harness_evaluation(
                learning_triggers=["continuation"],
                source_artifacts=[
                    "lane-map.json",
                    "timeline.jsonl",
                    CONTINUATION_SUMMARY_PATH,
                    VERIFICATION_READINESS_PATH,
                ],
            ),
        )
        final_path = run_dir / "final.md"
        if final_path.exists():
            final_text = final_path.read_text(encoding="utf-8")
            if HARNESS_EVALUATION_SECTION not in final_text:
                final_path.write_text(
                    final_text + harness_evaluation_section(),
                    encoding="utf-8",
                )
    elif harness_evaluation_data is not OMIT:
        write_harness_evaluation_file(run_dir, harness_evaluation_data)
    return run_dir


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


def mandatory_independent_qa_review_section(lane_id: str = "review-contract") -> str:
    return (
        f"\n## {MANDATORY_INDEPENDENT_QA_REVIEW_SECTION}\n\n"
        f"reviewer.qa lane `{lane_id}` ran as a real subagent.\n"
        f"Terminal handoff recorded in `handoffs/{lane_id}.md`.\n"
    )


def engineering_simplicity_final_section(
    primary_surfaces: list[str] | None = None,
) -> str:
    primary = DEFAULT_PRIMARY_SURFACES if primary_surfaces is None else primary_surfaces
    return (
        f"\n## {ENGINEERING_SIMPLICITY_SECTION}\n\n"
        "Primary surfaces covered:\n"
        f"{facet_lines(primary)}\n\n"
        "Peripheral-only closure rejected.\n"
    )


def write_run(
    root: Path,
    *,
    verdict: str = "ship",
    lanes: list[dict[str, Any]] | None = None,
    trace_events: dict[str, list[dict[str, Any]]] | None = None,
    ordered_trace_events: list[dict[str, Any]] | None = None,
    lane_map_extra: dict[str, Any] | None = None,
    delegation_summary_data: Any = DEFAULT,
    final_extra: str = "",
    route_extra: str = "",
    risk_mitigations_data: Any = DEFAULT,
    final_risk_ids: Any = DEFAULT,
    risk_resolutions_data: Any = DEFAULT,
    final_resolution_ids: Any = DEFAULT,
    verification_readiness_data: Any = DEFAULT,
    harness_evaluation_data: Any = DEFAULT,
    final_harness_ids: Any = DEFAULT,
    claim_evidence_data: Any = DEFAULT,
    acceptance_traceability_data: Any = DEFAULT,
    include_simplicity_final: bool = True,
    include_boundary_final: bool = True,
    include_mandatory_qa_final: bool = True,
) -> Path:
    run_dir = root / "run"
    (run_dir / "handoffs").mkdir(parents=True)
    (run_dir / "checks").mkdir()
    (run_dir / "artifacts").mkdir()
    (run_dir / "checks" / "smoke.md").write_text("# Smoke\n\npass\n", encoding="utf-8")
    (run_dir / "checks" / "verification-readiness.md").write_text(
        "# Verification Readiness\n\npass\n",
        encoding="utf-8",
    )
    (run_dir / DEFAULT_SIMPLICITY_SCOPE_EVIDENCE).write_text(
        "# Engineering Simplicity Scope\n\n"
        "Primary surfaces:\n"
        f"{facet_lines(DEFAULT_PRIMARY_SURFACES)}\n\n"
        "Secondary surfaces:\n"
        f"{facet_lines(DEFAULT_SECONDARY_SURFACES)}\n",
        encoding="utf-8",
    )

    for name in REQUIRED_FILES:
        content = "# Fixture\n\n"
        if name == "manifest.md":
            content += f"Verdict: {verdict}\n"
        (run_dir / name).write_text(content, encoding="utf-8")

    if lanes is not None:
        lanes = list(lanes)
        worker_lanes_present = any(
            lane_data.get("type") in {"implementation", "integration"}
            for lane_data in lanes
        )
        architecture_gate_requested = bool(
            lane_map_extra
            and lane_map_extra.get("architecture_contract_required") is True
        )
        if (
            architecture_gate_requested
            and worker_lanes_present
            and verification_readiness_data is not OMIT
        ):
            if lane_map_extra is not None and "verification_readiness" not in lane_map_extra:
                lane_map_extra = {
                    **lane_map_extra,
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                }
            readiness_lane_ids = (
                lane_map_extra.get("verification_readiness", {}).get("lanes", [])
                if isinstance(lane_map_extra, dict)
                and isinstance(lane_map_extra.get("verification_readiness"), dict)
                else [VERIFICATION_READINESS_LANE_ID]
            )
            if not any(lane_data.get("id") in readiness_lane_ids for lane_data in lanes):
                lanes.insert(1, verification_readiness_lane())
        if (
            architecture_gate_requested
            and worker_lanes_present
            and verification_readiness_data is DEFAULT
        ):
            verification_readiness_data = verification_readiness(
                context=lane_map_extra.get("architecture_context")
                if isinstance(lane_map_extra, dict)
                else None
            )
        if trace_events is None:
            trace_events = {}
        traced_lanes = {
            (event.get("role"), event.get("lane_id"))
            for events in trace_events.values()
            for event in events
            if isinstance(event, dict)
        }
        for lane_data in lanes:
            if (
                lane_data.get("type") == "review"
                and lane_data.get("role") in {"reviewer", "reviewer.qa"}
                and lane_data.get("execution_mode") == "subagent"
                and lane_data.get("status") in {"pass", "pass-with-risks"}
                and (lane_data.get("role"), lane_data.get("id")) not in traced_lanes
            ):
                role = lane_data["role"]
                lane_id = lane_data["id"]
                trace_events.setdefault(role, []).extend(
                    [spawned_trace(role, lane_id), subagent_handoff_trace(role, lane_id)]
                )

    risk_learning_default_needed = (
        verdict == "pass-with-risks"
        or (risk_mitigations_data is not DEFAULT and risk_mitigations_data is not OMIT)
        or (risk_resolutions_data is not DEFAULT and risk_resolutions_data is not OMIT)
    )
    readiness_learning_default_needed = (
        isinstance(verification_readiness_data, dict)
        and (
            verification_readiness_data.get("status") in {"needs-approval", "paused-blocked", "blocked"}
            or bool(verification_readiness_data.get("approval_requests"))
            or bool(verification_readiness_data.get("approval_executions"))
            or len(verification_readiness_data.get("attempts", [])) > 1
        )
    )
    architecture_learning_default_needed = bool(
        lane_map_extra
        and lane_map_extra.get("architecture_contract_required") is True
        and verdict in {"blocked", "fail"}
    )
    architecture_drift_default_needed = any(
        isinstance(lane_data, dict)
        and isinstance(lane_data.get("architecture_compliance"), dict)
        and lane_data["architecture_compliance"].get("status") == "drift"
        for lane_data in (lanes or [])
    )
    harness_default_needed = (
        risk_learning_default_needed
        or readiness_learning_default_needed
        or architecture_learning_default_needed
        or architecture_drift_default_needed
    )
    if risk_learning_default_needed:
        default_harness_finding_id = "pass-with-risks-resolution-recorded"
        default_harness_proposal_id = "promote-risk-resolution-evidence"
    elif architecture_drift_default_needed:
        default_harness_finding_id = "architecture-drift-recheck-recorded"
        default_harness_proposal_id = "promote-architecture-drift-recheck-evidence"
    else:
        default_harness_finding_id = "readiness-recovery-recorded"
        default_harness_proposal_id = "promote-readiness-recovery-evidence"
    default_harness_ids_for_run = harness_ids(
        finding_ids=[default_harness_finding_id],
        proposal_ids=[default_harness_proposal_id],
    )
    final_text = f"# Final\n\nVerdict: {verdict}\n"
    if lanes is not None and delegation_summary_data is DEFAULT:
        final_text += delegation_trace_section(delegation_summary(lanes))
    elif isinstance(delegation_summary_data, dict):
        final_text += delegation_trace_section(delegation_summary_data)
    if (
        lanes is not None
        and lane_map_extra
        and lane_map_extra.get("architecture_contract_required") is True
        and verdict in {"ship", "pass-with-risks"}
        and include_simplicity_final
        and any(
            isinstance(lane_data, dict)
            and lane_data.get("type") in {"implementation", "integration"}
            for lane_data in lanes
        )
    ):
        raw_scope = lane_map_extra.get("engineering_simplicity_scope")
        primary_surfaces = (
            raw_scope.get("primary_surfaces")
            if isinstance(raw_scope, dict)
            and isinstance(raw_scope.get("primary_surfaces"), list)
            else DEFAULT_PRIMARY_SURFACES
        )
        final_text += engineering_simplicity_final_section(primary_surfaces)
    if (
        lanes is not None
        and lane_map_extra
        and lane_map_extra.get("architecture_contract_required") is True
        and verdict in {"ship", "pass-with-risks"}
        and include_boundary_final
    ):
        boundary_worker_ids = [
            lane_data["id"]
            for lane_data in lanes
            if isinstance(lane_data, dict)
            and lane_data.get("type") in {"implementation", "integration"}
            and lane_data.get("status") in {"pass", "pass-with-risks"}
            and isinstance(lane_data.get("id"), str)
        ]
        if boundary_worker_ids:
            final_text += boundary_evidence_final_section(boundary_worker_ids)
    if (
        include_mandatory_qa_final
        and lanes is not None
        and verdict in {"ship", "pass-with-risks"}
        and any(
            isinstance(lane_data, dict)
            and lane_data.get("type") in {"implementation", "integration"}
            for lane_data in lanes
        )
    ):
        reviewer_lane_id = next(
            (
                lane_data["id"]
                for lane_data in lanes
                if isinstance(lane_data, dict)
                and lane_data.get("type") == "review"
                and lane_data.get("role") in {"reviewer", "reviewer.qa"}
                and lane_data.get("execution_mode") == "subagent"
                and lane_data.get("status") in {"pass", "pass-with-risks"}
                and isinstance(lane_data.get("id"), str)
            ),
            "review-contract",
        )
        final_text += mandatory_independent_qa_review_section(reviewer_lane_id)
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
    if (
        harness_evaluation_data is not OMIT
        and final_harness_ids is not OMIT
        and (harness_evaluation_data is not DEFAULT or harness_default_needed)
    ):
        default_harness_ids = None
        if harness_evaluation_data is DEFAULT and harness_default_needed:
            default_harness_ids = default_harness_ids_for_run
        final_text += harness_evaluation_section(
            default_harness_ids if final_harness_ids is DEFAULT else final_harness_ids
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
    if harness_evaluation_data is DEFAULT:
        if harness_default_needed:
            default_learning_triggers = []
            if risk_learning_default_needed:
                default_learning_triggers.extend(["risk-mitigation", "risk-resolution"])
            if risk_learning_default_needed and resolution_data_has_blocked_attempt(risk_resolutions_data):
                default_learning_triggers.append("blocked-resolution")
            if readiness_learning_default_needed:
                default_learning_triggers.append("readiness-recovery")
            if architecture_learning_default_needed:
                default_learning_triggers.append("nonpositive-architecture-final")
            if architecture_drift_default_needed:
                default_learning_triggers.append("architecture-drift")
                if any(
                    isinstance(lane_data, dict)
                    and isinstance(lane_data.get("architecture_compliance"), dict)
                    and lane_data["architecture_compliance"].get("recheck_lane")
                    for lane_data in (lanes or [])
                ):
                    default_learning_triggers.append("architecture-recheck")
            default_finding = {
                **harness_evaluation()["findings"][0],
                "id": default_harness_finding_id,
                "architecture_context": [],
                "architecture_capabilities": [],
                "evidence": ["checks/smoke.md"],
            }
            if risk_learning_default_needed:
                default_finding.update(
                    {
                        "type": "local-practice-candidate",
                        "problem_class": "risk-resolution",
                        "approach": "identify risk and record current resolution evidence",
                    }
                )
            elif architecture_drift_default_needed:
                default_finding.update(
                    {
                        "type": "recovery-success",
                        "problem_class": "architecture-drift",
                        "approach": "route drift through architecture recheck before final",
                    }
                )
            else:
                default_finding.update(
                    {
                        "type": "readiness-gap",
                        "problem_class": "verification-readiness",
                        "approach": "stop before workers and record readiness blocker evidence",
                    }
                )
            write_harness_evaluation_file(
                run_dir,
                harness_evaluation(
                    learning_triggers=list(dict.fromkeys(default_learning_triggers)),
                    source_artifacts=["final.md", "checks/smoke.md"],
                    findings=[default_finding],
                    proposals=[
                        {
                            **harness_evaluation()["proposals"][0],
                            "id": default_harness_proposal_id,
                            "evidence": ["checks/smoke.md"],
                        }
                    ],
                ),
            )
    elif harness_evaluation_data is not OMIT:
        write_harness_evaluation_file(run_dir, harness_evaluation_data)

    claim_evidence_default_needed = bool(
        lanes is not None
        and lane_map_extra
        and lane_map_extra.get("architecture_contract_required") is True
        and verdict in {"ship", "pass-with-risks"}
        and any(lane_data.get("type") == "qa" for lane_data in lanes)
        and any(lane_data.get("type") == "review" for lane_data in lanes)
    )
    if claim_evidence_data is DEFAULT:
        if claim_evidence_default_needed:
            (run_dir / CLAIM_EVIDENCE_PATH).write_text(
                json.dumps(claim_evidence(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    elif claim_evidence_data is not OMIT:
        if isinstance(claim_evidence_data, str):
            (run_dir / CLAIM_EVIDENCE_PATH).write_text(
                claim_evidence_data,
                encoding="utf-8",
            )
        else:
            (run_dir / CLAIM_EVIDENCE_PATH).write_text(
                json.dumps(claim_evidence_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    acceptance_traceability_default_needed = bool(
        lanes is not None
        and lane_map_extra
        and lane_map_extra.get("architecture_contract_required") is True
        and verdict in {"ship", "pass-with-risks"}
    )
    if acceptance_traceability_data is DEFAULT:
        if acceptance_traceability_default_needed:
            (run_dir / ACCEPTANCE_TRACEABILITY_PATH).write_text(
                json.dumps(acceptance_traceability(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    elif acceptance_traceability_data is not OMIT:
        if isinstance(acceptance_traceability_data, str):
            (run_dir / ACCEPTANCE_TRACEABILITY_PATH).write_text(
                acceptance_traceability_data,
                encoding="utf-8",
            )
        else:
            (run_dir / ACCEPTANCE_TRACEABILITY_PATH).write_text(
                json.dumps(acceptance_traceability_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    timeline_events: list[dict[str, Any]] = []
    if ordered_trace_events:
        events_by_role: dict[str, list[dict[str, Any]]] = {}
        for event in ordered_trace_events:
            role = event.get("role")
            role_key = role if isinstance(role, str) and role else "orchestrator"
            events_by_role.setdefault(role_key, []).append(event)
        for role, events in events_by_role.items():
            trace_path = run_dir / "agents" / role / "trace.jsonl"
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(trace_path, events)
        timeline_events.extend(ordered_trace_events)
    for role, events in (trace_events or {}).items():
        trace_path = run_dir / "agents" / role / "trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(trace_path, events)
        timeline_events.extend(events)

    if lanes is not None:
        lanes = list(lanes)
        worker_lanes_present = any(
            lane_data.get("type") in {"implementation", "integration"}
            for lane_data in lanes
        )
        architecture_gate_requested = bool(
            lane_map_extra
            and lane_map_extra.get("architecture_contract_required") is True
        )
        if (
            architecture_gate_requested
            and worker_lanes_present
            and verification_readiness_data is not OMIT
        ):
            if lane_map_extra is not None and "verification_readiness" not in lane_map_extra:
                lane_map_extra = {
                    **lane_map_extra,
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                }
            readiness_lane_ids = (
                lane_map_extra.get("verification_readiness", {}).get("lanes", [])
                if isinstance(lane_map_extra, dict)
                and isinstance(lane_map_extra.get("verification_readiness"), dict)
                else [VERIFICATION_READINESS_LANE_ID]
            )
            if not any(lane_data.get("id") in readiness_lane_ids for lane_data in lanes):
                lanes.insert(1, verification_readiness_lane())
        if (
            architecture_gate_requested
            and worker_lanes_present
            and verification_readiness_data is DEFAULT
        ):
            verification_readiness_data = verification_readiness(
                context=lane_map_extra.get("architecture_context")
                if isinstance(lane_map_extra, dict)
                else None
            )

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
                    contract_text = lane.get("architecture_contract_text")
                    if not isinstance(contract_text, str):
                        contract_text = architecture_contract_text(
                            missing,
                            selected_facets=selected_facets,
                            selected_capabilities=selected_capabilities,
                            claim_ids=lane.get("claim_evidence_ids"),
                            acceptance_ids=lane.get("acceptance_criteria_ids"),
                        )
                    path.write_text(contract_text, encoding="utf-8")
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
                    if (
                        lane.get("type") == "review"
                        and harness_default_needed
                        and harness_evaluation_data is not OMIT
                        and final_harness_ids is not OMIT
                    ):
                        lane_sections = lane.setdefault("handoff_sections", [])
                        if (
                            isinstance(lane_sections, list)
                            and HARNESS_EVALUATION_REVIEW_SECTION not in lane_sections
                        ):
                            lane_sections.append(HARNESS_EVALUATION_REVIEW_SECTION)
                            handoff_section_bodies.setdefault(
                                HARNESS_EVALUATION_REVIEW_SECTION,
                                "Reviewed harness evaluation items:\n"
                                + facet_lines(default_harness_ids_for_run),
                            )
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
                    boundary = lane.get("boundary")
                    if (
                        lane.get("boundary_artifact_data") is OMIT
                        and isinstance(boundary, dict)
                        and evidence == boundary.get("changed_paths_artifact")
                    ):
                        continue
                    path = run_dir / evidence
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists():
                        continue
                    path.write_text(f"# {lane['id']} evidence\n", encoding="utf-8")
            boundary = lane.get("boundary")
            if isinstance(boundary, dict):
                artifact_path = boundary.get("changed_paths_artifact")
                if isinstance(artifact_path, str) and artifact_path:
                    path = run_dir / artifact_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_data = lane.get("boundary_artifact_data")
                    if artifact_data is OMIT:
                        continue
                    if artifact_data is DEFAULT or artifact_data is None:
                        artifact_data = lane_boundary_artifact(lane.get("id", "worker-a"))
                    if isinstance(artifact_data, str):
                        path.write_text(artifact_data, encoding="utf-8")
                    else:
                        path.write_text(
                            json.dumps(artifact_data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )

        serialized_lanes = []
        for lane_data in lanes:
            if isinstance(lane_data, dict):
                serialized_lane = dict(lane_data)
                serialized_lane.pop("boundary_artifact_data", None)
                serialized_lanes.append(serialized_lane)
            else:
                serialized_lanes.append(lane_data)

        lane_map = {"schema_version": 1, "lanes": serialized_lanes}
        lane_map.update(lane_map_extra or {})
        (run_dir / "lane-map.json").write_text(
            json.dumps(lane_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if verification_readiness_data is not OMIT and verification_readiness_data is not DEFAULT:
        if isinstance(verification_readiness_data, str):
            (run_dir / VERIFICATION_READINESS_PATH).write_text(
                verification_readiness_data,
                encoding="utf-8",
            )
        else:
            (run_dir / VERIFICATION_READINESS_PATH).write_text(
                json.dumps(verification_readiness_data, ensure_ascii=False, indent=2) + "\n",
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


def write_extra_file(run_dir: Path, relative_path: str, text: str) -> Path:
    path = run_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
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


def handoff_state(
    lane_id: str,
    *,
    status: str = "completed",
    mode: str = "task",
    handoff: str | None = None,
    queued_at: str | None = "2026-06-17T12:00:00+03:00",
    accepted_at: str | None = "2026-06-17T12:05:00+03:00",
    completed_at: str | None = "2026-06-17T12:30:00+03:00",
    batch: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "status": status,
        "to": lane_id,
        "handoff": handoff or f"handoffs/{lane_id}.md",
    }
    if queued_at is not None:
        state["queued_at"] = queued_at
    if accepted_at is not None:
        state["accepted_at"] = accepted_at
    if completed_at is not None:
        state["completed_at"] = completed_at
    if batch is not None:
        state["batch"] = batch
    if extra:
        state.update(extra)
    return state


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
    lane_handoff_state: dict[str, Any] | None = None,
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
    if lane_handoff_state is not None:
        lane_data["handoff_state"] = lane_handoff_state
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


def role_lane_trace(
    lane_data: dict[str, Any],
    *,
    stage: str = "handoff",
    status: str = "pass",
    artifacts: list[str] | None = None,
) -> dict[str, Any]:
    lane_id = lane_data["id"]
    role = lane_data["role"]
    return timeline_event(
        stage,
        role=role,
        status=status,
        summary=f"{lane_id} recorded {stage}.",
        next_step="continue",
        artifacts=artifacts if artifacts is not None else [lane_data.get("handoff") or ""],
        execution_mode=lane_data.get("execution_mode", "role-lane"),
        agent_trace=f"agents/{role}/trace.jsonl",
        agent_artifact_dir=f"artifacts/agents/{role}",
        lane_id=lane_id,
        wave=lane_data.get("wave"),
        critical=lane_data.get("critical", True),
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
    claim_evidence_ids: list[str] | None = None,
    acceptance_criteria_ids: list[str] | None = None,
    contract_text: str | None = None,
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
    if claim_evidence_ids is not None:
        lane_data["claim_evidence_ids"] = claim_evidence_ids
    if acceptance_criteria_ids is not None:
        lane_data["acceptance_criteria_ids"] = acceptance_criteria_ids
    if contract_text is not None:
        lane_data["architecture_contract_text"] = contract_text
    return lane_data


def architecture_compliance(
    *,
    status: str = "compliant",
    contract_sections: list[str] | None = None,
    matrix_facets: list[str] | None = None,
    notes: str = "Fixture architecture compliance.",
    recheck_lane: str | None = None,
    engineering_simplicity: Any = DEFAULT,
) -> dict[str, Any]:
    compliance = {
        "status": status,
        "contract_sections": contract_sections or ["Module Boundaries"],
        "matrix_facets": list(DEFAULT_WORKER_MATRIX_FACETS)
        if matrix_facets is None
        else matrix_facets,
        "notes": notes,
        "recheck_lane": recheck_lane,
    }
    if engineering_simplicity is DEFAULT:
        compliance["engineering_simplicity"] = engineering_simplicity_gate()
    elif engineering_simplicity is not OMIT:
        compliance["engineering_simplicity"] = engineering_simplicity
    return compliance


def engineering_simplicity_gate(
    *,
    status: str = "pass",
    checks: list[str] | None = None,
    findings: list[str] | None = None,
    actions: list[str] | None = None,
    notes: str = "No needless dependency, abstraction, or scope expansion found.",
    scope_coverage: Any = DEFAULT,
) -> dict[str, Any]:
    gate = {
        "status": status,
        "checks": list(ENGINEERING_SIMPLICITY_CHECKS) if checks is None else checks,
        "findings": [] if findings is None else findings,
        "actions": [] if actions is None else actions,
        "notes": notes,
    }
    if scope_coverage is DEFAULT:
        gate["scope_coverage"] = engineering_simplicity_scope_coverage()
    elif scope_coverage is not OMIT:
        gate["scope_coverage"] = scope_coverage
    return gate


def engineering_simplicity_scope() -> dict[str, Any]:
    return {
        "primary_surfaces": list(DEFAULT_PRIMARY_SURFACES),
        "secondary_surfaces": list(DEFAULT_SECONDARY_SURFACES),
        "evidence": [DEFAULT_SIMPLICITY_SCOPE_EVIDENCE],
        "notes": "Core task scope; secondary smoke coverage cannot close primary work.",
    }


def engineering_simplicity_scope_coverage(
    *,
    primary_surfaces: list[str] | None = None,
    secondary_surfaces: list[str] | None = None,
    evidence: list[str] | None = None,
    notes: str = "Audited primary implementation scope before peripheral fixes.",
) -> dict[str, Any]:
    return {
        "primary_surfaces": list(DEFAULT_PRIMARY_SURFACES)
        if primary_surfaces is None
        else primary_surfaces,
        "secondary_surfaces": []
        if secondary_surfaces is None
        else secondary_surfaces,
        "evidence": [DEFAULT_SIMPLICITY_SCOPE_EVIDENCE] if evidence is None else evidence,
        "notes": notes,
    }


def lane_boundary_artifact_path(lane_id: str = "worker-a") -> str:
    return f"checks/lane-boundary-{lane_id}.json"


def lane_boundary(
    lane_id: str = "worker-a",
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    changed_paths_artifact: str | None = None,
    notes: str = "Allowed paths come from Architecture Contract Worker Ownership.",
) -> dict[str, Any]:
    return {
        "allowed_paths": list(DEFAULT_BOUNDARY_ALLOWED_PATHS)
        if allowed_paths is None
        else allowed_paths,
        "forbidden_paths": list(DEFAULT_BOUNDARY_FORBIDDEN_PATHS)
        if forbidden_paths is None
        else forbidden_paths,
        "changed_paths_artifact": changed_paths_artifact or lane_boundary_artifact_path(lane_id),
        "notes": notes,
    }


def lane_boundary_artifact(
    lane_id: str = "worker-a",
    *,
    changed_paths: list[str] | None = None,
    tracked_changed_paths: list[str] | None = None,
    untracked_paths: list[str] | None = None,
) -> dict[str, Any]:
    tracked = (
        [DEFAULT_BOUNDARY_CHANGED_PATH]
        if tracked_changed_paths is None and changed_paths is None
        else tracked_changed_paths
    )
    untracked = [] if untracked_paths is None else untracked_paths
    if changed_paths is None:
        changed_paths = [*(tracked or []), *untracked]
    return {
        "version": 1,
        "lane_id": lane_id,
        "status": "captured",
        "base_ref": "HEAD",
        "head_ref": "working-tree",
        "changed_paths": changed_paths,
        "tracked_changed_paths": [] if tracked is None else tracked,
        "untracked_paths": untracked,
        "command": "git diff --name-only HEAD",
        "notes": f"Boundary evidence for {lane_id}.",
    }


def boundary_evidence_final_section(worker_ids: list[str]) -> str:
    return (
        f"\n## {LANE_BOUNDARY_SECTION}\n\n"
        "Boundary Evidence checked for worker lanes:\n"
        f"{facet_lines(worker_ids)}\n\n"
        "No out-of-bound product changes found.\n"
    )


def worker_lane(
    lane_id: str = "worker-a",
    *,
    status: str = "pass",
    wave: int = 2,
    lane_type: str = "implementation",
    role: str = "typescript-worker",
    architecture_compliance_data: Any = OMIT,
    boundary: Any = DEFAULT,
    boundary_artifact_data: Any = DEFAULT,
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
        else [
            ARCHITECTURE_COMPLIANCE_SECTION,
            ENGINEERING_SIMPLICITY_SECTION,
            LANE_BOUNDARY_SECTION,
        ],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_worker_handoff_bodies(lane_id=lane_id),
    )
    if architecture_compliance_data is OMIT:
        lane_data["architecture_compliance"] = architecture_compliance()
    elif architecture_compliance_data is not None:
        lane_data["architecture_compliance"] = architecture_compliance_data
    if boundary is DEFAULT and status in {"pass", "pass-with-risks"}:
        lane_data["boundary"] = lane_boundary(lane_id)
    elif boundary is not OMIT:
        lane_data["boundary"] = boundary
    if isinstance(lane_data.get("boundary"), dict):
        artifact = lane_data["boundary"].get("changed_paths_artifact")
        if isinstance(artifact, str) and artifact not in lane_data["evidence"]:
            lane_data["evidence"].append(artifact)
    if boundary_artifact_data is not DEFAULT:
        lane_data["boundary_artifact_data"] = boundary_artifact_data
    return lane_data


def reviewer_lane(
    lane_id: str = "review-contract",
    *,
    wave: int = 4,
    execution_mode: str = "role-lane",
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return lane(
        lane_id,
        lane_type="review",
        role="reviewer",
        wave=wave,
        execution_mode=execution_mode,
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


def verification_readiness_lane(
    lane_id: str = VERIFICATION_READINESS_LANE_ID,
    *,
    status: str = "pass",
    wave: int = 1,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    return qa_lane(
        lane_id,
        status=status,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [VERIFICATION_GATE_RESULTS_SECTION],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else {
            VERIFICATION_GATE_RESULTS_SECTION: verification_gate_result_lines(),
        },
    )


def qa_control_lane(
    *,
    status: str = "pass",
    wave: int = 3,
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
    risk_resolution_ids: list[str] | None = None,
    continuation_revalidation_ids: list[str] | None = None,
    verification_results_data: Any = DEFAULT,
) -> dict[str, Any]:
    include_verification_results = verification_results_data is not OMIT
    lane_data = qa_lane(
        status=status,
        wave=wave,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [
            ARCHITECTURE_INVARIANTS_SECTION,
            ENGINEERING_SIMPLICITY_SCOPE_SECTION,
            *([VERIFICATION_GATE_RESULTS_SECTION] if include_verification_results else []),
            *([RISK_RESOLUTION_VERIFICATION_SECTION] if risk_resolution_ids else []),
            *([CONTINUATION_REVALIDATION_SECTION] if continuation_revalidation_ids else []),
        ],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_qa_handoff_bodies(
            context,
            risk_resolution_ids,
            include_verification_results=include_verification_results,
            verification_status="blocked"
            if isinstance(verification_results_data, dict)
            and verification_results_data.get("status") == "blocked"
            else "pass",
            continuation_ids=continuation_revalidation_ids,
        ),
    )
    if status not in {"pass", "pass-with-risks"}:
        lane_data["handoff"] = "handoffs/qa-behavior.md"
        lane_data["evidence"] = ["checks/qa-behavior.md"]
    if verification_results_data is DEFAULT:
        lane_data["verification_results"] = qa_verification_results(context=context)
    elif verification_results_data is not OMIT:
        lane_data["verification_results"] = verification_results_data
    return lane_data


def reviewer_control_lane(
    *,
    wave: int = 4,
    execution_mode: str = "subagent",
    handoff_sections: list[str] | None = None,
    handoff_section_bodies: dict[str, str] | None = None,
    context: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    risk_ids: list[str] | None = None,
    risk_resolution_ids: list[str] | None = None,
    continuation_review_ids: list[str] | None = None,
    harness_review_ids: list[str] | None = None,
) -> dict[str, Any]:
    return reviewer_lane(
        wave=wave,
        execution_mode=execution_mode,
        handoff_sections=handoff_sections
        if handoff_sections is not None
        else [
            ARCHITECTURE_MATRIX_MISMATCHES_SECTION,
            CONTRACT_DRIFT_SECTION,
            *([RISK_MITIGATION_REVIEW_SECTION] if risk_ids else []),
            *([RISK_RESOLUTION_REVIEW_SECTION] if risk_resolution_ids else []),
            *([CONTINUATION_REVIEW_SECTION] if continuation_review_ids else []),
            *([HARNESS_EVALUATION_REVIEW_SECTION] if harness_review_ids else []),
        ],
        handoff_section_bodies=handoff_section_bodies
        if handoff_section_bodies is not None
        else default_reviewer_handoff_bodies(
            context,
            capabilities,
            risk_ids,
            risk_resolution_ids,
            continuation_review_ids,
            harness_review_ids,
        ),
    )


def continuation_lanes() -> list[dict[str, Any]]:
    return [
        architecture_lane(),
        verification_readiness_lane("verification-readiness-continuation", wave=2),
        worker_lane(wave=3),
        qa_control_lane(
            wave=4,
            continuation_revalidation_ids=continuation_ids(),
        ),
        reviewer_control_lane(
            wave=5,
            continuation_review_ids=continuation_ids(),
            harness_review_ids=harness_ids(),
        ),
    ]


def continuation_trace_events(
    *,
    worker_before_checkpoint: bool = True,
    worker_after_checkpoint_before_readiness: bool = False,
    worker_after_readiness: bool = False,
) -> list[dict[str, Any]]:
    lanes_by_id = {lane_data["id"]: lane_data for lane_data in continuation_lanes()}
    worker = lanes_by_id["worker-a"]
    readiness = lanes_by_id["verification-readiness-continuation"]
    qa = lanes_by_id["qa-behavior"]
    reviewer = lanes_by_id["review-contract"]

    events: list[dict[str, Any]] = []
    if worker_before_checkpoint:
        events.append(role_lane_trace(worker))
    events.append(
        timeline_event(
            "blocked-checkpoint",
            status="blocked",
            summary="Fixture blocked checkpoint.",
            execution_mode="role-lane",
            agent_trace="agents/orchestrator/trace.jsonl",
            agent_artifact_dir="artifacts/agents/orchestrator",
            lane_id="orchestrator-blocked-checkpoint",
            wave=5,
            critical=True,
        )
    )
    if worker_after_checkpoint_before_readiness:
        events.append(role_lane_trace(worker))
    events.append(role_lane_trace(readiness))
    if worker_after_readiness:
        events.append(role_lane_trace(worker))
    events.append(role_lane_trace(qa, stage="checks"))
    events.extend(
        [
            spawned_trace(reviewer["role"], reviewer["id"]),
            subagent_handoff_trace(reviewer["role"], reviewer["id"]),
        ]
    )

    return events


def continuation_readiness_data() -> dict[str, Any]:
    base = verification_readiness()
    return verification_readiness(
        attempts=[
            {
                "id": "readiness-continuation",
                "lane": "verification-readiness-continuation",
                "status": "ready",
                "gates": base["attempts"][0]["gates"],
                "blockers": [],
                "approval_requests": [],
            }
        ],
        approval_requests=[],
        approval_executions=[],
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
    simplicity_scope: Any = DEFAULT,
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
    if simplicity_scope is DEFAULT:
        extra["engineering_simplicity_scope"] = engineering_simplicity_scope()
    elif simplicity_scope is not OMIT:
        extra["engineering_simplicity_scope"] = simplicity_scope
    return extra


def claim_gate_lanes(
    *,
    architecture: dict[str, Any] | None = None,
    qa: dict[str, Any] | None = None,
    reviewer: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        architecture or architecture_lane(),
        worker_lane(),
        qa or qa_control_lane(wave=3),
        reviewer or reviewer_control_lane(wave=4),
    ]


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

        expect_pass(
            "old lane-map without handoff state flag still passes",
            write_run(
                temp / "handoff-state-legacy-no-flag",
                lanes=[lane("qa-legacy")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
            ),
        )

        expect_pass(
            "valid handoff state passes",
            write_run(
                temp / "handoff-state-valid",
                lanes=[
                    lane(
                        "qa-state",
                        lane_handoff_state=handoff_state("qa-state"),
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
        )

        expect_fail(
            "missing required handoff state fails",
            write_run(
                temp / "handoff-state-missing",
                lanes=[lane("qa-missing")],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane qa-missing missing handoff_state for Handoff State Gate",
        )

        expect_fail(
            "completed handoff state without completed_at fails",
            write_run(
                temp / "handoff-state-completed-missing-time",
                lanes=[
                    lane(
                        "qa-no-completed-at",
                        lane_handoff_state=handoff_state(
                            "qa-no-completed-at",
                            completed_at=None,
                        ),
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane qa-no-completed-at handoff_state.completed_at required for completed",
        )

        expect_fail(
            "pass lane with accepted handoff state fails",
            write_run(
                temp / "handoff-state-terminal-mismatch",
                lanes=[
                    lane(
                        "qa-accepted",
                        lane_handoff_state=handoff_state(
                            "qa-accepted",
                            status="accepted",
                            completed_at=None,
                        ),
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane qa-accepted status pass requires handoff_state.status=completed",
        )

        expect_fail(
            "handoff state mismatch fails",
            write_run(
                temp / "handoff-state-path-mismatch",
                lanes=[
                    lane(
                        "qa-path",
                        lane_handoff_state=handoff_state(
                            "qa-path",
                            handoff="handoffs/other.md",
                        ),
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane qa-path handoff_state.handoff must match lane handoff: handoffs/qa-path.md",
        )

        expect_fail(
            "batch item unknown fails",
            write_run(
                temp / "handoff-state-batch-unknown",
                lanes=[
                    lane(
                        "review-batch",
                        lane_type="review",
                        role="reviewer",
                        lane_handoff_state=handoff_state(
                            "review-batch",
                            mode="batch",
                            batch={"id": "batch-1", "items": ["missing-lane"]},
                        ),
                    )
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane review-batch batch item unknown: missing-lane",
        )

        expect_fail(
            "batch item not completed before batch lane fails",
            write_run(
                temp / "handoff-state-batch-order",
                lanes=[
                    lane(
                        "qa-item",
                        lane_handoff_state=handoff_state(
                            "qa-item",
                            completed_at="2026-06-17T12:30:00+03:00",
                        ),
                    ),
                    lane(
                        "review-batch",
                        lane_type="review",
                        role="reviewer",
                        wave=4,
                        lane_handoff_state=handoff_state(
                            "review-batch",
                            mode="batch",
                            accepted_at="2026-06-17T12:10:00+03:00",
                            completed_at="2026-06-17T12:40:00+03:00",
                            batch={"id": "batch-1", "items": ["qa-item"]},
                        ),
                    ),
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "handoff_state_required": True,
                },
            ),
            "lane-map.json: lane review-batch batch item qa-item must be completed before batch lane accepted",
        )

        continuation_lane_map_extra = {
            **architecture_control_extra(),
            "verification_readiness": {
                "artifact": VERIFICATION_READINESS_PATH,
                "lanes": ["verification-readiness-continuation"],
            },
        }
        expect_fail(
            "positive continuation requires continuation summary",
            write_run(
                temp / "continuation-missing-summary",
                lanes=continuation_lanes(),
                lane_map_extra=continuation_lane_map_extra,
                verification_readiness_data=continuation_readiness_data(),
                ordered_trace_events=continuation_trace_events(),
                final_extra=continuation_summary_section(),
            ),
            "continuation-summary.json is required for positive continuation run",
        )

        expect_fail(
            "continuation invalid json fails",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-invalid-json",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
                "{",
            ),
            "continuation-summary.json invalid JSON",
        )

        expect_fail(
            "continuation invalid version fails",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-invalid-version",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary() | {"version": 2},
            ),
            "continuation-summary.json field 'version' must be 1",
        )

        expect_fail(
            "continuation missing checkpoint snapshot fails",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-missing-checkpoint-snapshot",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(
                    previous_checkpoint={
                        "lane_id": "orchestrator-blocked-checkpoint",
                        "verdict": "blocked",
                        "snapshot": "artifacts/checkpoints/missing/final.md",
                    }
                ),
            ),
            "continuation-summary.json previous_checkpoint.snapshot not found",
        )

        expect_fail(
            "continuation checkpoint missing in timeline fails",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-missing-checkpoint-event",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(worker_before_checkpoint=False),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(
                    previous_checkpoint={
                        "lane_id": "missing-blocked-checkpoint",
                        "verdict": "blocked",
                        "snapshot": "artifacts/checkpoints/orchestrator-blocked-checkpoint/final.md",
                    }
                ),
            ),
            "continuation-summary.json previous_checkpoint.lane_id missing blocked-checkpoint timeline event",
        )

        expect_fail(
            "continuation historical worker requires declaration",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-historical-worker-not-declared",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(historical_worker_lanes=[], revalidated_lanes=[]),
            ),
            "continuation-summary.json missing historical worker lane: worker-a",
        )

        expect_fail(
            "continuation historical worker requires revalidation",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-historical-worker-not-revalidated",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(revalidated_lanes=[]),
            ),
            "continuation-summary.json missing revalidated historical worker lane: worker-a",
        )

        expect_fail(
            "continuation new worker before readiness fails",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-new-worker-before-readiness",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(
                        worker_before_checkpoint=False,
                        worker_after_checkpoint_before_readiness=True,
                    ),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(
                    historical_worker_lanes=[],
                    new_worker_lanes=["worker-a"],
                    revalidated_lanes=["worker-a"],
                ),
            ),
            "lane-map.json: continuation worker lane worker-a must run after ready Verification Readiness Gate",
        )

        expect_pass(
            "continuation new worker after readiness passes",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-new-worker-after-readiness",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(
                        worker_before_checkpoint=False,
                        worker_after_readiness=True,
                    ),
                    final_extra=continuation_summary_section(),
                ),
                continuation_summary(
                    historical_worker_lanes=[],
                    new_worker_lanes=["worker-a"],
                    revalidated_lanes=["worker-a"],
                ),
            ),
        )

        expect_fail(
            "continuation positive requires lane timeline events",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-missing-lane-timeline-event",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    trace_events={
                        "orchestrator": [
                            timeline_event(
                                "blocked-checkpoint",
                                status="blocked",
                                summary="Fixture blocked checkpoint.",
                                lane_id="orchestrator-blocked-checkpoint",
                            )
                        ]
                    },
                    final_extra=continuation_summary_section(),
                ),
            ),
            "lane-map.json: continuation timeline for lane verification-readiness-continuation missing event",
        )

        expect_fail(
            "continuation final requires summary section",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-final-missing-section",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                ),
            ),
            "final.md missing section: Continuation Summary",
        )

        expect_fail(
            "continuation qa requires revalidation section",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-qa-missing-section",
                    lanes=[
                        architecture_lane(),
                        verification_readiness_lane("verification-readiness-continuation", wave=2),
                        worker_lane(wave=3),
                        qa_control_lane(wave=4),
                        reviewer_control_lane(
                            wave=5,
                            continuation_review_ids=continuation_ids(),
                        ),
                    ],
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
            ),
            "lane-map.json: lane qa-behavior handoff missing section: Continuation Revalidation",
        )

        expect_fail(
            "continuation reviewer requires review section",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-reviewer-missing-section",
                    lanes=[
                        architecture_lane(),
                        verification_readiness_lane("verification-readiness-continuation", wave=2),
                        worker_lane(wave=3),
                        qa_control_lane(
                            wave=4,
                            continuation_revalidation_ids=continuation_ids(),
                        ),
                        reviewer_control_lane(wave=5),
                    ],
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
            ),
            "lane-map.json: lane review-contract handoff missing section: Continuation Review",
        )

        expect_pass(
            "continuation with historical workers and revalidation passes",
            write_continuation_summary_file(
                write_run(
                    temp / "continuation-historical-workers-revalidated",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
            ),
        )

        expect_fail(
            "triggered continuation requires harness evaluation",
            write_continuation_summary_file(
                write_run(
                    temp / "harness-continuation-missing",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                    harness_evaluation_data=OMIT,
                ),
                harness_evaluation_data=OMIT,
            ),
            "harness-evaluation.json is required for triggered learning run",
        )

        expect_fail(
            "triggered risk resolution requires harness evaluation",
            write_run(
                temp / "harness-risk-resolution-missing",
                verdict="pass-with-risks",
                harness_evaluation_data=OMIT,
            ),
            "harness-evaluation.json is required for triggered learning run",
        )

        expect_fail(
            "architecture drift requires harness evaluation",
            write_run(
                temp / "harness-architecture-drift-missing",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        wave=3,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                        ),
                    ),
                    architecture_lane("architecture-recheck", wave=4),
                    qa_control_lane(wave=5),
                    reviewer_control_lane(wave=6),
                ],
                lane_map_extra=architecture_control_extra(),
                harness_evaluation_data=OMIT,
            ),
            "harness-evaluation.json is required for triggered learning run",
        )

        expect_pass(
            "no-trigger schema v2 without harness evaluation passes",
            write_run(
                temp / "harness-no-trigger-schema-v2",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                harness_evaluation_data=OMIT,
            ),
        )

        expect_fail(
            "harness evaluation invalid json fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-invalid-json",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                "{",
            ),
            "harness-evaluation.json invalid JSON",
        )

        expect_fail(
            "harness evaluation invalid version fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-invalid-version",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation() | {"version": 2},
            ),
            "harness-evaluation.json field 'version' must be 1",
        )

        expect_fail(
            "harness evaluation invalid status fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-invalid-status",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(status="done"),
            ),
            "harness-evaluation.json status invalid",
        )

        default_finding = harness_evaluation()["findings"][0]
        default_proposal = harness_evaluation()["proposals"][0]
        expect_fail(
            "harness evaluation missing finding id fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-missing-finding-id",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(findings=[{k: v for k, v in default_finding.items() if k != "id"}]),
            ),
            "harness-evaluation.json findings[0] missing field: id",
        )

        expect_fail(
            "harness evaluation duplicate proposal id fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-duplicate-proposal-id",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(proposals=[default_proposal, {**default_proposal}]),
            ),
            "harness-evaluation.json duplicate proposal id: promote-continuation-revalidation-evidence",
        )

        expect_fail(
            "harness evaluation non-kebab finding id fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-non-kebab-finding-id",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(findings=[{**default_finding, "id": "ReadinessRecovery"}]),
            ),
            "harness-evaluation.json findings[0].id must be kebab-case",
        )

        expect_fail(
            "harness evaluation unknown trigger fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-unknown-trigger",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(learning_triggers=["mystery-trigger"]),
            ),
            "harness-evaluation.json unknown learning trigger: mystery-trigger",
        )

        expect_fail(
            "harness evaluation missing actual trigger fails",
            write_run(
                temp / "harness-missing-actual-trigger",
                lanes=[lane("qa-trace")],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
                harness_evaluation_data=harness_evaluation(learning_triggers=["continuation"]),
            ),
            "harness-evaluation.json learning trigger not present in run: continuation",
        )

        expect_fail(
            "harness evaluation missing evidence path fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-missing-evidence",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(
                    findings=[{**default_finding, "evidence": ["checks/missing.md"]}]
                ),
            ),
            "harness-evaluation.json findings[0].evidence[0] not found: checks/missing.md",
        )

        expect_fail(
            "harness evaluation unselected architecture context fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-unselected-context",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(
                    findings=[{**default_finding, "architecture_context": ["frontend-service"]}]
                ),
            ),
            "harness-evaluation.json findings[0].architecture_context[0] unselected facet: frontend-service",
        )

        expect_fail(
            "harness evaluation unknown capability fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-unknown-capability",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(
                    findings=[{**default_finding, "architecture_capabilities": ["missing-capability"]}]
                ),
            ),
            "harness-evaluation.json findings[0].architecture_capabilities[0] unknown capability: missing-capability",
        )

        expect_fail(
            "harness evaluation applied proposal fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-applied-proposal",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(proposals=[{**default_proposal, "status": "applied"}]),
            ),
            "harness-evaluation.json proposals[0].status must be proposed",
        )

        expect_fail(
            "harness evaluation runtime proposal type fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-runtime-proposal-type",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(proposals=[{**default_proposal, "type": "architecture-matrix"}]),
            ),
            "harness-evaluation.json proposals[0].type must be evidence-record",
        )

        expect_fail(
            "harness evaluation runtime proposal target fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-runtime-proposal-target",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(proposals=[{**default_proposal, "target": "Architecture Matrix"}]),
            ),
            "harness-evaluation.json proposals[0].target must be Evidence Records",
        )

        expect_fail(
            "harness evaluation evidence record human approval fails",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-evidence-record-human-approval",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                    ),
                ),
                harness_evaluation(proposals=[{**default_proposal, "requires_human_approval": True}]),
            ),
            "harness-evaluation.json proposals[0].requires_human_approval must be false",
        )

        expect_fail(
            "harness final section required",
            write_harness_evaluation_file(
                write_continuation_summary_file(
                    write_run(
                        temp / "harness-final-missing-section",
                        lanes=continuation_lanes(),
                        lane_map_extra=continuation_lane_map_extra,
                        verification_readiness_data=continuation_readiness_data(),
                        ordered_trace_events=continuation_trace_events(),
                        final_extra=continuation_summary_section(),
                        final_harness_ids=OMIT,
                    ),
                    harness_evaluation_data=OMIT,
                ),
            ),
            "final.md missing section: Harness Evaluation",
        )

        expect_fail(
            "harness reviewer section required for positive lane-map run",
            write_continuation_summary_file(
                write_run(
                    temp / "harness-reviewer-missing-section",
                    lanes=[
                        architecture_lane(),
                        verification_readiness_lane("verification-readiness-continuation", wave=2),
                        worker_lane(wave=3),
                        qa_control_lane(
                            wave=4,
                            continuation_revalidation_ids=continuation_ids(),
                        ),
                        reviewer_control_lane(
                            wave=5,
                            continuation_review_ids=continuation_ids(),
                        ),
                    ],
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
            ),
            "lane-map.json: lane review-contract handoff missing section: Harness Evaluation Review",
        )

        expect_pass(
            "valid continuation harness evaluation passes",
            write_continuation_summary_file(
                write_run(
                    temp / "harness-valid-continuation",
                    lanes=continuation_lanes(),
                    lane_map_extra=continuation_lane_map_extra,
                    verification_readiness_data=continuation_readiness_data(),
                    ordered_trace_events=continuation_trace_events(),
                    final_extra=continuation_summary_section(),
                ),
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
            "mandatory independent qa rejects implementation without reviewer subagent",
            write_run(
                temp / "mandatory-qa-missing-reviewer",
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
            "positive implementation/change run requires reviewer.qa subagent",
        )

        expect_fail(
            "mandatory independent qa rejects role-lane reviewer only",
            write_run(
                temp / "mandatory-qa-role-lane-reviewer-only",
                lanes=[
                    lane(
                        "worker-a",
                        lane_type="implementation",
                        role="typescript-worker",
                        wave=2,
                    ),
                    reviewer_lane(wave=3, execution_mode="role-lane"),
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
            "Mandatory Independent QA Review Gate rejects role-lane-only review",
        )

        expect_pass(
            "mandatory independent qa accepts reviewer subagent with terminal handoff",
            write_run(
                temp / "mandatory-qa-subagent-reviewer",
                lanes=[
                    lane(
                        "worker-a",
                        lane_type="implementation",
                        role="typescript-worker",
                        wave=2,
                    ),
                    reviewer_control_lane(wave=3),
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
        )

        expect_fail(
            "mandatory independent qa requires delegation summary for schema v1",
            write_run(
                temp / "mandatory-qa-schema-v1-missing-delegation-summary",
                lanes=[
                    lane(
                        "worker-a",
                        lane_type="implementation",
                        role="typescript-worker",
                        wave=2,
                    ),
                    reviewer_control_lane(wave=3),
                ],
                delegation_summary_data=OMIT,
            ),
            "Mandatory Independent QA Review Gate requires delegation-summary.json",
        )

        expect_pass(
            "blocked mandatory independent qa accepts recorded launch blocker",
            write_extra_file(
                write_run(
                    temp / "mandatory-qa-blocked-launch-failure",
                    verdict="blocked",
                    lanes=[
                        lane(
                            "worker-a",
                            status="blocked",
                            lane_type="implementation",
                            role="typescript-worker",
                            wave=2,
                        )
                    ],
                    lane_map_extra={
                        "schema_version": 2,
                        "budget": "standard",
                        "architecture_contract_required": False,
                        "mandatory_independent_qa_review": {
                            "required": True,
                            "status": "blocked",
                            "reviewer_lane": "review-contract",
                            "blocker": {
                                "kind": "launch-failure",
                                "summary": "spawn_agent failed before reviewer.qa could start.",
                                "evidence": ["checks/reviewer-qa-launch-blocker.md"],
                            },
                        },
                    },
                ),
                "checks/reviewer-qa-launch-blocker.md",
                "# reviewer.qa launch blocker\n\nspawn_agent failed before handoff.\n",
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
                    reviewer_control_lane(
                        risk_ids=[DEFAULT_RISK_ID],
                        risk_resolution_ids=[DEFAULT_RISK_ID],
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
                execution_mode="subagent",
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
                execution_mode="subagent",
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
                lanes=[architecture_lane(), qa_control_lane(wave=2), reviewer_control_lane(wave=3)],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "positive architecture run requires claim evidence artifact",
            write_run(
                temp / "claim-evidence-missing",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=OMIT,
            ),
            "claim-evidence.json is required for positive architecture contract run",
        )

        expect_fail(
            "architecture contract QA Gates require claim evidence ids",
            write_run(
                temp / "contract-qa-gates-no-claim-evidence",
                lanes=claim_gate_lanes(architecture=architecture_lane(claim_evidence_ids=[])),
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture contract handoff QA Gates missing Claim Evidence",
        )

        reviewer_missing_claim_contract = architecture_contract_text(claim_ids=[]).replace(
            "## QA Gates\n\nFixture content for QA Gates.\n",
            "## QA Gates\n\nFixture content for QA Gates."
            + claim_evidence_lines([DEFAULT_CLAIM_ID])
            + "\n",
        )
        expect_fail(
            "architecture contract Reviewer Checklist requires claim evidence ids",
            write_run(
                temp / "contract-reviewer-checklist-no-claim-evidence",
                lanes=claim_gate_lanes(
                    architecture=architecture_lane(
                        contract_text=reviewer_missing_claim_contract,
                    )
                ),
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture contract handoff Reviewer Checklist missing Claim Evidence",
        )

        expect_fail(
            "claim evidence missing required claim id fails",
            write_run(
                temp / "claim-evidence-missing-required-id",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(
                    claim_id="other-claim",
                    evidence=[
                        {
                            "path": "checks/qa-behavior.md",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                ),
            ),
            "claim-evidence.json missing required claim id: architecture-contract-claim",
        )

        expect_fail(
            "claim evidence duplicate id fails",
            write_run(
                temp / "claim-evidence-duplicate-id",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(
                    claims=[
                        claim_evidence()["claims"][0],
                        claim_evidence()["claims"][0],
                    ]
                ),
            ),
            "claim-evidence.json duplicate claim id: architecture-contract-claim",
        )

        expect_fail(
            "claim evidence non kebab id fails",
            write_run(
                temp / "claim-evidence-non-kebab-id",
                lanes=claim_gate_lanes(architecture=architecture_lane(claim_evidence_ids=["BadClaim"])),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(claim_id="BadClaim"),
            ),
            "claim-evidence.json claims[0].id must be kebab-case",
        )

        expect_fail(
            "claim evidence unknown owner lane fails",
            write_run(
                temp / "claim-evidence-unknown-owner",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(owner_lane="missing-qa"),
            ),
            "claim-evidence.json claims[0].owner_lane not found: missing-qa",
        )

        expect_fail(
            "claim evidence owner lane must be qa or review",
            write_run(
                temp / "claim-evidence-owner-worker",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(owner_lane="worker-a"),
            ),
            "claim-evidence.json claims[0].owner_lane must reference successful qa or review lane",
        )

        expect_fail(
            "claim evidence reviewer lane must be review",
            write_run(
                temp / "claim-evidence-reviewer-not-review",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(reviewed_by="qa-behavior"),
            ),
            "claim-evidence.json claims[0].reviewed_by must reference successful review lane",
        )

        expect_fail(
            "claim evidence missing owner handoff section fails",
            write_run(
                temp / "claim-evidence-missing-handoff-section",
                lanes=claim_gate_lanes(
                    qa=qa_control_lane(wave=3, handoff_sections=[VERIFICATION_GATE_RESULTS_SECTION])
                ),
                lane_map_extra=architecture_control_extra(),
            ),
            "claim-evidence.json claims[0].section missing from owner handoff: Architecture Invariants",
        )

        expect_fail(
            "claim evidence handoff section must mention claim id",
            write_run(
                temp / "claim-evidence-handoff-no-id",
                lanes=claim_gate_lanes(
                    qa=qa_control_lane(
                        wave=3,
                        handoff_section_bodies={
                            ARCHITECTURE_INVARIANTS_SECTION: (
                                "Covered gates:\n- `migrations`\n- `unit`\n- `integration`"
                            ),
                            VERIFICATION_GATE_RESULTS_SECTION: verification_gate_result_lines(),
                        },
                    )
                ),
                lane_map_extra=architecture_control_extra(),
            ),
            "claim-evidence.json claims[0].owner handoff section missing claim id: architecture-contract-claim",
        )

        expect_fail(
            "claim evidence missing evidence path fails",
            write_run(
                temp / "claim-evidence-missing-path",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(
                    evidence=[
                        {
                            "path": "checks/missing.md",
                            "markers": ["missing marker"],
                        }
                    ],
                ),
            ),
            "claim-evidence.json claims[0].evidence[0].path not found: checks/missing.md",
        )

        expect_fail(
            "claim evidence missing marker fails",
            write_run(
                temp / "claim-evidence-missing-marker",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(
                    evidence=[
                        {
                            "path": "checks/qa-behavior.md",
                            "markers": ["StreamIncomingEventsRejectsViewer"],
                        }
                    ],
                ),
            ),
            "claim-evidence.json claims[0].evidence[0].markers[0] not found in checks/qa-behavior.md",
        )

        crm_claim_id = "auth-first-unsupported-streams"
        crm_qa_body = (
            "Covered gates:\n"
            "- `migrations`\n"
            "- `unit`\n"
            "- `integration`\n\n"
            "Boundary Evidence: no out-of-bound product changes for worker lanes:\n"
            "- `worker-a`\n\n"
            "Claim evidence:\n"
            f"- Claim Evidence: `{crm_claim_id}`\n\n"
            "Viewer-forbidden unsupported stream endpoints are covered."
        )
        expect_fail(
            "CRM-style unsupported stream coverage claim requires exact evidence markers",
            write_extra_file(
                write_run(
                    temp / "crm-style-claim-no-marker-proof",
                    lanes=claim_gate_lanes(
                        architecture=architecture_lane(claim_evidence_ids=[crm_claim_id]),
                        qa=qa_control_lane(
                            wave=3,
                            handoff_section_bodies={
                                ARCHITECTURE_INVARIANTS_SECTION: crm_qa_body,
                                ENGINEERING_SIMPLICITY_SCOPE_SECTION: (
                                    "Verified primary simplicity surfaces:\n"
                                    + facet_lines(DEFAULT_PRIMARY_SURFACES)
                                ),
                                VERIFICATION_GATE_RESULTS_SECTION: verification_gate_result_lines(),
                            },
                        ),
                    ),
                    lane_map_extra=architecture_control_extra(),
                    claim_evidence_data=claim_evidence(
                        claim_id=crm_claim_id,
                        evidence=[
                            {
                                "path": "checks/connectrpc-unimplemented-contract.md",
                                "markers": [
                                    "TestUnsupportedStreamEndpointsRespectRolePermissionsBeforeUnimplemented",
                                    "StreamIncomingEventsRejectsViewer",
                                ],
                            }
                        ],
                    ),
                ),
                "checks/connectrpc-unimplemented-contract.md",
                "# ConnectRPC Evidence\n\n"
                "TestUnsupportedStreamEndpointsRespectRolePermissionsBeforeUnimplemented\n",
            ),
            "claim-evidence.json claims[0].evidence[0].markers[1] "
            "not found in checks/connectrpc-unimplemented-contract.md",
        )

        expect_fail(
            "claim evidence gap blocks ship",
            write_run(
                temp / "claim-evidence-gap-ship",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                claim_evidence_data=claim_evidence(status="gap"),
            ),
            "claim-evidence.json claims[0].status must be supported for positive final Verdict",
        )

        expect_pass(
            "valid marker backed claim evidence passes",
            write_run(
                temp / "claim-evidence-valid",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "positive architecture run requires acceptance traceability artifact",
            write_run(
                temp / "acceptance-traceability-missing",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=OMIT,
            ),
            "acceptance-traceability.json is required for positive architecture contract run",
        )

        expect_fail(
            "architecture contract QA Gates require acceptance criteria ids",
            write_run(
                temp / "contract-qa-gates-no-acceptance-criteria",
                lanes=claim_gate_lanes(architecture=architecture_lane(acceptance_criteria_ids=[])),
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture contract handoff QA Gates missing Acceptance Criteria",
        )

        reviewer_missing_acceptance_contract = architecture_contract_text(
            acceptance_ids=[]
        ).replace(
            "## QA Gates\n\nFixture content for QA Gates."
            + claim_evidence_lines([DEFAULT_CLAIM_ID])
            + "\n",
            "## QA Gates\n\nFixture content for QA Gates."
            + claim_evidence_lines([DEFAULT_CLAIM_ID])
            + acceptance_criteria_lines([DEFAULT_ACCEPTANCE_ID])
            + "\n",
        )
        expect_fail(
            "architecture contract Reviewer Checklist requires acceptance criteria ids",
            write_run(
                temp / "contract-reviewer-checklist-no-acceptance-criteria",
                lanes=claim_gate_lanes(
                    architecture=architecture_lane(contract_text=reviewer_missing_acceptance_contract)
                ),
                lane_map_extra=architecture_control_extra(),
            ),
            "architecture contract handoff Reviewer Checklist missing Acceptance Criteria",
        )

        expect_fail(
            "acceptance traceability missing required id fails",
            write_run(
                temp / "acceptance-traceability-missing-required-id",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(acceptance_id="other-acceptance"),
            ),
            "acceptance-traceability.json missing required acceptance id: architecture-contract-acceptance",
        )

        expect_fail(
            "acceptance traceability missing surface expectations fails",
            write_run(
                temp / "acceptance-traceability-missing-surface-expectations",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=OMIT,
                ),
            ),
            "acceptance-traceability.json acceptance[0].surface_expectations "
            "must be a non-empty array",
        )

        expect_fail(
            "acceptance traceability wrong surface evidence fails",
            write_run(
                temp / "acceptance-traceability-wrong-surface-evidence",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=[
                        {
                            "surface": "api",
                            "polarity": "positive",
                            "proof_kinds": ["unit-test"],
                        },
                        {
                            "surface": "api",
                            "polarity": "negative",
                            "proof_kinds": ["unit-test"],
                        },
                    ],
                    evidence=[
                        {
                            "surface": "storage",
                            "polarity": "positive",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                    negative_fixture_evidence=[
                        {
                            "surface": "api",
                            "polarity": "negative",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].evidence[0].surface "
            "not in surface_expectations: storage",
        )

        expect_fail(
            "acceptance traceability negative fixture rejects positive polarity",
            write_run(
                temp / "acceptance-traceability-positive-negative-fixture",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kinds": ["unit-test"],
                        }
                    ],
                    negative_fixture_evidence=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].negative_fixture_evidence[0].polarity "
            "must be negative or drift",
        )

        expect_fail(
            "acceptance traceability rejects unknown surface",
            write_run(
                temp / "acceptance-traceability-unknown-surface",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=[
                        {
                            "surface": "database",
                            "polarity": "positive",
                            "proof_kinds": ["unit-test"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].surface_expectations[0].surface "
            "must be one of:",
        )

        expect_fail(
            "acceptance traceability rejects unknown polarity",
            write_run(
                temp / "acceptance-traceability-unknown-polarity",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=[
                        {
                            "surface": "service",
                            "polarity": "affirmative",
                            "proof_kinds": ["unit-test"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].surface_expectations[0].polarity "
            "must be one of:",
        )

        expect_fail(
            "acceptance traceability rejects unknown proof kind",
            write_run(
                temp / "acceptance-traceability-unknown-proof-kind",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    surface_expectations=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kinds": ["video"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].surface_expectations[0].proof_kinds "
            "invalid: video",
        )

        expect_pass(
            "valid surface backed acceptance traceability passes",
            write_extra_file(
                write_run(
                    temp / "acceptance-traceability-surface-backed-valid",
                    lanes=claim_gate_lanes(),
                    lane_map_extra=architecture_control_extra(),
                    acceptance_traceability_data=acceptance_traceability(
                        surface_expectations=[
                            {
                                "surface": "api",
                                "polarity": "positive",
                                "proof_kinds": ["integration-test"],
                            },
                            {
                                "surface": "api",
                                "polarity": "negative",
                                "proof_kinds": ["smoke"],
                            },
                        ],
                        evidence=[
                            {
                                "surface": "api",
                                "polarity": "positive",
                                "proof_kind": "integration-test",
                                "path": "checks/api-contract.md",
                                "markers": ["ApiPositiveEvidenceMarker"],
                            }
                        ],
                        negative_fixture_evidence=[
                            {
                                "surface": "api",
                                "polarity": "negative",
                                "proof_kind": "smoke",
                                "path": "checks/api-contract.md",
                                "markers": ["ApiNegativeFixtureMarker"],
                            }
                        ],
                    ),
                ),
                "checks/api-contract.md",
                "# API Contract Evidence\n\n"
                "ApiPositiveEvidenceMarker\n"
                "ApiNegativeFixtureMarker\n",
            ),
        )

        expect_fail(
            "acceptance traceability missing marker fails",
            write_run(
                temp / "acceptance-traceability-missing-marker",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    evidence=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["ContractPositiveMarkerMissing"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].evidence[0].markers[0] "
            "not found in checks/qa-behavior.md",
        )

        expect_fail(
            "acceptance traceability empty markers fail",
            write_run(
                temp / "acceptance-traceability-empty-markers",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    evidence=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": [],
                        }
                    ],
                    negative_fixture_evidence=[
                        {
                            "surface": "service",
                            "polarity": "negative",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].evidence[0].markers must be a non-empty array",
        )

        expect_fail(
            "acceptance traceability evidence path must be file",
            write_run(
                temp / "acceptance-traceability-directory-evidence",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    evidence=[
                        {
                            "surface": "service",
                            "polarity": "positive",
                            "proof_kind": "unit-test",
                            "path": "checks",
                            "markers": ["# qa-behavior evidence"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].evidence[0].path must be a file: checks",
        )

        expect_fail(
            "contract acceptance requires negative fixture evidence",
            write_run(
                temp / "acceptance-traceability-negative-fixture-missing",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    negative_fixture_evidence=OMIT,
                ),
            ),
            "acceptance-traceability.json acceptance[0].negative_fixture_evidence must be an array",
        )

        expect_fail(
            "contract acceptance negative fixture marker must exist",
            write_run(
                temp / "acceptance-traceability-negative-fixture-marker-missing",
                lanes=claim_gate_lanes(),
                lane_map_extra=architecture_control_extra(),
                acceptance_traceability_data=acceptance_traceability(
                    negative_fixture_evidence=[
                        {
                            "surface": "service",
                            "polarity": "negative",
                            "proof_kind": "unit-test",
                            "path": "checks/qa-behavior.md",
                            "markers": ["NegativeDriftMarkerMissing"],
                        }
                    ],
                ),
            ),
            "acceptance-traceability.json acceptance[0].negative_fixture_evidence[0].markers[0] "
            "not found in checks/qa-behavior.md",
        )

        expect_pass(
            "valid acceptance traceability with negative fixture passes",
            write_extra_file(
                write_run(
                    temp / "acceptance-traceability-valid-negative-fixture",
                    lanes=claim_gate_lanes(),
                    lane_map_extra=architecture_control_extra(),
                    acceptance_traceability_data=acceptance_traceability(
                        contract_types=["parser"],
                        surface_expectations=[
                            {
                                "surface": "parser",
                                "polarity": "positive",
                                "proof_kinds": ["unit-test"],
                            },
                            {
                                "surface": "parser",
                                "polarity": "drift",
                                "proof_kinds": ["unit-test"],
                            },
                        ],
                        evidence=[
                            {
                                "surface": "parser",
                                "polarity": "positive",
                                "proof_kind": "unit-test",
                                "path": "checks/parser-contract.md",
                                "markers": ["ParserPositiveFixtureMarker"],
                            }
                        ],
                        negative_fixture_evidence=[
                            {
                                "surface": "parser",
                                "polarity": "drift",
                                "proof_kind": "unit-test",
                                "path": "checks/parser-contract.md",
                                "markers": ["ParserDriftFixtureMarker"],
                            }
                        ],
                    ),
                ),
                "checks/parser-contract.md",
                "# Parser Contract Evidence\n\n"
                "ParserPositiveFixtureMarker\n"
                "ParserDriftFixtureMarker\n",
            ),
        )

        expect_pass(
            "unsupported endpoint contract claim backed by exact markers passes",
            write_extra_file(
                write_run(
                    temp / "claim-evidence-unsupported-endpoints-valid",
                    lanes=claim_gate_lanes(
                        architecture=architecture_lane(claim_evidence_ids=[crm_claim_id]),
                        qa=qa_control_lane(
                            wave=3,
                            handoff_section_bodies={
                                ARCHITECTURE_INVARIANTS_SECTION: crm_qa_body,
                                ENGINEERING_SIMPLICITY_SCOPE_SECTION: (
                                    "Verified primary simplicity surfaces:\n"
                                    + facet_lines(DEFAULT_PRIMARY_SURFACES)
                                ),
                                VERIFICATION_GATE_RESULTS_SECTION: verification_gate_result_lines(),
                            },
                        ),
                    ),
                    lane_map_extra=architecture_control_extra(),
                    claim_evidence_data=claim_evidence(
                        claim_id=crm_claim_id,
                        evidence=[
                            {
                                "path": "checks/connectrpc-unimplemented-contract.md",
                                "markers": [
                                    "TestUnsupportedStreamEndpointsRespectRolePermissionsBeforeUnimplemented",
                                    "StreamIncomingEventsRejectsViewer",
                                ],
                            }
                        ],
                    ),
                ),
                "checks/connectrpc-unimplemented-contract.md",
                "# ConnectRPC Evidence\n\n"
                "TestUnsupportedStreamEndpointsRespectRolePermissionsBeforeUnimplemented\n"
                "StreamIncomingEventsRejectsViewer\n",
            ),
        )

        expect_pass(
            "schema v2 without architecture gate does not require claim evidence",
            write_run(
                temp / "claim-evidence-not-required-without-architecture-gate",
                lanes=[
                    worker_lane(architecture_compliance_data=None),
                    reviewer_control_lane(wave=3),
                ],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
                claim_evidence_data=OMIT,
            ),
        )

        expect_fail(
            "architecture worker run requires verification readiness",
            write_run(
                temp / "verification-readiness-missing",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                verification_readiness_data=OMIT,
            ),
            "lane-map.json field 'verification_readiness' is required",
        )

        expect_fail(
            "verification readiness artifact must exist",
            write_run(
                temp / "verification-readiness-missing-artifact",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                },
                verification_readiness_data=OMIT,
            ),
            "verification-readiness.json not found",
        )

        expect_fail(
            "verification readiness invalid status fails",
            write_run(
                temp / "verification-readiness-invalid-status",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                verification_readiness_data=verification_readiness(status="maybe"),
            ),
            "verification-readiness.json status invalid",
        )

        expect_fail(
            "verification readiness missing selected risk gate fails",
            write_run(
                temp / "verification-readiness-missing-risk-gate",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                verification_readiness_data=verification_readiness(
                    attempts=[
                        {
                            "id": "readiness-1",
                            "lane": VERIFICATION_READINESS_LANE_ID,
                            "status": "ready",
                            "gates": [
                                {
                                    "axis": "verification_gates",
                                    "facet": "unit",
                                    "readiness": "ready",
                                    "check": "unit",
                                    "evidence": ["checks/verification-readiness.md"],
                                    "notes": "ready",
                                },
                                {
                                    "axis": "verification_gates",
                                    "facet": "integration",
                                    "readiness": "ready",
                                    "check": "integration",
                                    "evidence": ["checks/verification-readiness.md"],
                                    "notes": "ready",
                                },
                            ],
                            "blockers": [],
                            "approval_requests": [],
                        }
                    ]
                ),
            ),
            "missing selected gate facet: migrations",
        )

        expect_fail(
            "verification readiness unknown facet fails",
            write_run(
                temp / "verification-readiness-unknown-facet",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                verification_readiness_data=verification_readiness(
                    attempts=[
                        {
                            "id": "readiness-1",
                            "lane": VERIFICATION_READINESS_LANE_ID,
                            "status": "ready",
                            "gates": [
                                *verification_readiness()["attempts"][0]["gates"],
                                {
                                    "axis": "risk_gates",
                                    "facet": "unknown-gate",
                                    "readiness": "ready",
                                    "check": "unknown",
                                    "evidence": ["checks/verification-readiness.md"],
                                    "notes": "unknown",
                                },
                            ],
                            "blockers": [],
                            "approval_requests": [],
                        }
                    ]
                ),
            ),
            "unknown selected gate facet: unknown-gate",
        )

        expect_fail(
            "needs approval cannot start workers",
            write_run(
                temp / "verification-readiness-needs-approval-worker",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                verification_readiness_data=verification_readiness(status="needs-approval"),
            ),
            "lane-map.json: worker lanes require ready Verification Readiness Gate",
        )

        declined_request = {
            "id": "start-dev-stack",
            "status": "declined",
            "reason": "User declined agent-run infra start.",
            "commands": [
                {
                    "cwd": "/repo",
                    "command": "documented safe command",
                    "source": "README.md",
                    "requires_user_approval": True,
                }
            ],
            "manual_instruction": "Start the documented stack, then reply: Готово.",
            "resume_phrase": "Готово",
            "affected_gates": ["migrations", "unit", "integration"],
        }
        expect_pass(
            "paused blocked readiness without workers passes",
            write_run(
                temp / "verification-readiness-paused-blocked",
                verdict="blocked",
                lanes=[architecture_lane(), verification_readiness_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                },
                verification_readiness_data=verification_readiness(
                    status="paused-blocked",
                    approval_requests=[declined_request],
                ),
                final_extra=verification_readiness_section(),
            ),
        )

        expect_fail(
            "declined readiness requires resume phrase",
            write_run(
                temp / "verification-readiness-declined-no-resume",
                verdict="blocked",
                lanes=[architecture_lane(), verification_readiness_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                },
                verification_readiness_data=verification_readiness(
                    status="paused-blocked",
                    approval_requests=[{**declined_request, "resume_phrase": ""}],
                ),
                final_extra=verification_readiness_section(),
            ),
            "verification-readiness.json approval_requests[0].resume_phrase must be Готово",
        )

        expect_fail(
            "approved request requires execution evidence before ready retry",
            write_run(
                temp / "verification-readiness-approved-no-execution",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    verification_readiness_lane("verification-readiness-2", wave=2),
                    worker_lane(wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": ["verification-readiness-1", "verification-readiness-2"],
                    },
                },
                verification_readiness_data=verification_readiness(
                    attempts=[
                        {
                            "id": "readiness-1",
                            "lane": "verification-readiness-1",
                            "status": "needs-approval",
                            "gates": verification_readiness(status="needs-approval")["attempts"][0]["gates"],
                            "blockers": ["verification-env-missing"],
                            "approval_requests": ["start-dev-stack"],
                        },
                        {
                            "id": "readiness-2",
                            "lane": "verification-readiness-2",
                            "status": "ready",
                            "gates": verification_readiness()["attempts"][0]["gates"],
                            "blockers": [],
                            "approval_requests": [],
                        },
                    ],
                    approval_requests=[{**declined_request, "status": "approved"}],
                ),
            ),
            "verification-readiness.json approval request start-dev-stack approved without execution evidence",
        )

        expect_pass(
            "approved execution then ready retry allows workers",
            write_run(
                temp / "verification-readiness-approved-ready-workers",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    verification_readiness_lane("verification-readiness-2", wave=2),
                    worker_lane(wave=3),
                    qa_control_lane(wave=4),
                    reviewer_control_lane(wave=5),
                ],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": ["verification-readiness-1", "verification-readiness-2"],
                    },
                },
                verification_readiness_data=verification_readiness(
                    attempts=[
                        {
                            "id": "readiness-1",
                            "lane": "verification-readiness-1",
                            "status": "needs-approval",
                            "gates": verification_readiness(status="needs-approval")["attempts"][0]["gates"],
                            "blockers": ["verification-env-missing"],
                            "approval_requests": ["start-dev-stack"],
                        },
                        {
                            "id": "readiness-2",
                            "lane": "verification-readiness-2",
                            "status": "ready",
                            "gates": verification_readiness()["attempts"][0]["gates"],
                            "blockers": [],
                            "approval_requests": [],
                        },
                    ],
                    approval_requests=[{**declined_request, "status": "approved"}],
                    approval_executions=[
                        {
                            "request_id": "start-dev-stack",
                            "status": "succeeded",
                            "evidence": ["checks/verification-readiness.md"],
                        }
                    ],
                ),
            ),
        )

        expect_fail(
            "worker before ready readiness fails",
            write_run(
                temp / "verification-readiness-worker-before-ready",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(wave=2),
                    worker_lane(wave=2),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "lane-map.json: worker lane worker-a must run after ready Verification Readiness Gate",
        )

        expect_fail(
            "positive verdict with paused readiness fails",
            write_run(
                temp / "verification-readiness-paused-positive",
                lanes=[architecture_lane(), verification_readiness_lane()],
                lane_map_extra={
                    **architecture_control_extra(),
                    "verification_readiness": {
                        "artifact": VERIFICATION_READINESS_PATH,
                        "lanes": [VERIFICATION_READINESS_LANE_ID],
                    },
                },
                verification_readiness_data=verification_readiness(
                    status="paused-blocked",
                    approval_requests=[declined_request],
                ),
            ),
            "lane-map.json: positive final Verdict requires ready Verification Readiness Gate",
        )

        expect_fail(
            "qa pass with blocked verification result fails",
            write_run(
                temp / "verification-results-qa-pass-blocked",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(
                        wave=3,
                        verification_results_data=qa_verification_results(
                            status="blocked",
                            gate_status="blocked",
                        ),
                    ),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "lane-map.json: lane qa-behavior pass requires verification_results.status=pass",
        )

        expect_pass(
            "blocked qa verification result allows final blocked",
            write_run(
                temp / "verification-results-qa-blocked-final-blocked",
                verdict="blocked",
                lanes=[
                    architecture_lane(),
                    verification_readiness_lane(),
                    worker_lane(),
                    qa_control_lane(
                        status="blocked",
                        wave=3,
                        verification_results_data=qa_verification_results(
                            status="blocked",
                            gate_status="blocked",
                        ),
                    ),
                    reviewer_control_lane(wave=4),
                ],
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
                lanes=[architecture_lane(), qa_control_lane(wave=2), reviewer_control_lane(wave=3)],
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
                    qa_control_lane(wave=3),
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
                        ),
                        reviewer_control_lane(wave=3),
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

        expect_fail(
            "standard single worker lane still requires mandatory qa reviewer",
            write_run(
                temp / "standard-single-worker-no-architecture-contract",
                lanes=[lane("worker-a", lane_type="implementation", role="typescript-worker", wave=2)],
                lane_map_extra={
                    "schema_version": 2,
                    "budget": "standard",
                    "architecture_contract_required": False,
                },
            ),
            "positive implementation/change run requires reviewer.qa subagent",
        )

        expect_pass(
            "standard single worker lane with reviewer subagent does not require architecture contract",
            write_run(
                temp / "standard-single-worker-with-reviewer-no-architecture-contract",
                lanes=[
                    lane("worker-a", lane_type="implementation", role="typescript-worker", wave=2),
                    reviewer_control_lane(wave=3),
                ],
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
            "worker without engineering simplicity fails",
            write_run(
                temp / "worker-no-engineering-simplicity",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=OMIT
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "missing architecture_compliance.engineering_simplicity",
        )

        expect_fail(
            "worker handoff without engineering simplicity section fails",
            write_run(
                temp / "worker-no-engineering-simplicity-section",
                lanes=[
                    architecture_lane(),
                    worker_lane(handoff_sections=[ARCHITECTURE_COMPLIANCE_SECTION]),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Engineering Simplicity",
        )

        expect_fail(
            "worker engineering simplicity missing required check fails",
            write_run(
                temp / "worker-engineering-simplicity-missing-check",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                checks=[
                                    check
                                    for check in ENGINEERING_SIMPLICITY_CHECKS
                                    if check != "tests-fit-risk"
                                ]
                            )
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "engineering_simplicity.checks missing required check: tests-fit-risk",
        )

        expect_fail(
            "fixed engineering simplicity without actions fails",
            write_run(
                temp / "worker-engineering-simplicity-fixed-no-actions",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="fixed",
                                findings=["Fixture scope expansion removed."],
                                actions=[],
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "fixed engineering_simplicity requires non-empty actions",
        )

        expect_fail(
            "engineering simplicity drift without parent drift fails",
            write_run(
                temp / "worker-engineering-simplicity-drift-without-parent-drift",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(status="drift")
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "engineering_simplicity drift requires architecture_compliance.status=drift",
        )

        expect_fail(
            "engineering simplicity retained dependency without capability citation fails",
            write_run(
                temp / "worker-engineering-simplicity-retained-dependency-no-capability",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                notes="Retained dependency after worker review."
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "retained dependency or abstraction must cite a selected architecture capability",
        )

        expect_pass(
            "worker engineering simplicity pass accepted",
            write_run(
                temp / "worker-engineering-simplicity-pass",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_pass(
            "engineering simplicity retained dependency with capability citation passes",
            write_run(
                temp / "worker-engineering-simplicity-retained-dependency-capability",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                notes=(
                                    "Retained dependency justified by "
                                    "`go-backend-service-architecture`."
                                )
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "positive architecture-gated run without engineering simplicity scope fails",
            write_run(
                temp / "simplicity-scope-missing",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(simplicity_scope=OMIT),
            ),
            "lane-map.json: positive architecture-gated worker run requires engineering_simplicity_scope",
        )

        expect_fail(
            "engineering simplicity scope empty primary surfaces fails",
            write_run(
                temp / "simplicity-scope-empty-primary",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(
                    simplicity_scope={
                        **engineering_simplicity_scope(),
                        "primary_surfaces": [],
                    }
                ),
            ),
            "engineering_simplicity_scope.primary_surfaces must be a non-empty array",
        )

        expect_fail(
            "engineering simplicity scope non-kebab primary surface fails",
            write_run(
                temp / "simplicity-scope-non-kebab-primary",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(
                    simplicity_scope={
                        **engineering_simplicity_scope(),
                        "primary_surfaces": ["API Service"],
                    }
                ),
            ),
            "engineering_simplicity_scope.primary_surfaces[0] must be kebab-case",
        )

        expect_fail(
            "engineering simplicity scope duplicate primary surface fails",
            write_run(
                temp / "simplicity-scope-duplicate-primary",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(
                    simplicity_scope={
                        **engineering_simplicity_scope(),
                        "primary_surfaces": ["api-service", "api-service"],
                    }
                ),
            ),
            "engineering_simplicity_scope.primary_surfaces duplicate surface: api-service",
        )

        expect_fail(
            "worker without engineering simplicity scope coverage fails",
            write_run(
                temp / "simplicity-worker-missing-scope-coverage",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                scope_coverage=OMIT
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "missing architecture_compliance.engineering_simplicity.scope_coverage",
        )

        expect_fail(
            "worker engineering simplicity covers undeclared surface fails",
            write_extra_file(
                write_run(
                    temp / "simplicity-worker-undeclared-surface",
                    lanes=[
                        architecture_lane(),
                        worker_lane(
                            architecture_compliance_data=architecture_compliance(
                                engineering_simplicity=engineering_simplicity_gate(
                                    scope_coverage=engineering_simplicity_scope_coverage(
                                        primary_surfaces=["unknown-surface"]
                                    )
                                )
                            ),
                            handoff_section_bodies=default_worker_handoff_bodies(
                                primary_surfaces=["unknown-surface"],
                                secondary_surfaces=[],
                            ),
                        ),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                DEFAULT_SIMPLICITY_SCOPE_EVIDENCE,
                "# Engineering Simplicity Scope\n\nunknown-surface\n",
            ),
            "scope_coverage covers undeclared surface: unknown-surface",
        )

        expect_fail(
            "required primary surface not covered by worker fails",
            write_run(
                temp / "simplicity-primary-surface-not-covered",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(
                        wave=3,
                        handoff_section_bodies=default_qa_handoff_bodies(
                            primary_surfaces=["api-service", "participant-miniapp"]
                        ),
                    ),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies=default_reviewer_handoff_bodies(
                            primary_surfaces=["api-service", "participant-miniapp"]
                        ),
                    ),
                ],
                lane_map_extra=architecture_control_extra(
                    simplicity_scope={
                        **engineering_simplicity_scope(),
                        "primary_surfaces": ["api-service", "participant-miniapp"],
                    }
                ),
            ),
            "engineering_simplicity_scope primary surface not covered by worker: participant-miniapp",
        )

        expect_fail(
            "worker scope coverage missing evidence path fails",
            write_run(
                temp / "simplicity-worker-missing-scope-evidence",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                scope_coverage=engineering_simplicity_scope_coverage(
                                    evidence=["checks/missing-core-scope.md"]
                                )
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "scope_coverage.evidence[0] not found: checks/missing-core-scope.md",
        )

        expect_fail(
            "worker scope coverage evidence without surface id fails",
            write_extra_file(
                write_run(
                    temp / "simplicity-worker-evidence-missing-surface",
                    lanes=[
                        architecture_lane(),
                        worker_lane(),
                        qa_control_lane(wave=3),
                        reviewer_control_lane(wave=4),
                    ],
                    lane_map_extra=architecture_control_extra(),
                ),
                DEFAULT_SIMPLICITY_SCOPE_EVIDENCE,
                "# Engineering Simplicity Scope\n\nNo surface marker here.\n",
            ),
            "scope_coverage evidence missing surface: api-service",
        )

        expect_fail(
            "worker handoff without covered surface id fails",
            write_run(
                temp / "simplicity-worker-handoff-missing-surface",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        handoff_section_bodies={
                            **default_worker_handoff_bodies(primary_surfaces=[]),
                            ENGINEERING_SIMPLICITY_SECTION: (
                                "Checks:\n"
                                + facet_lines(ENGINEERING_SIMPLICITY_CHECKS)
                                + "\n\nStatus: pass\n"
                                "Notes: Surface marker intentionally omitted."
                            ),
                        }
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Engineering Simplicity missing scope surface: api-service",
        )

        expect_fail(
            "qa handoff without engineering simplicity scope section fails",
            write_run(
                temp / "simplicity-qa-missing-scope-section",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(
                        wave=3,
                        handoff_sections=[ARCHITECTURE_INVARIANTS_SECTION],
                    ),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "handoff missing section: Engineering Simplicity Scope",
        )

        expect_fail(
            "reviewer contract drift without primary surface fails",
            write_run(
                temp / "simplicity-reviewer-missing-primary-surface",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            **default_reviewer_handoff_bodies(primary_surfaces=[]),
                            CONTRACT_DRIFT_SECTION: (
                                "Engineering Simplicity checked. "
                                "Rejected peripheral-only closure. "
                                "No contract drift."
                            ),
                        },
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Contract Drift missing Engineering Simplicity primary surface: api-service",
        )

        expect_fail(
            "final engineering simplicity without primary surface fails",
            write_run(
                temp / "simplicity-final-missing-primary-surface",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
                include_simplicity_final=False,
                final_extra="\n## Engineering Simplicity\n\nPeripheral-only closure rejected.\n",
            ),
            "final.md Engineering Simplicity missing primary surface: api-service",
        )

        expect_fail(
            "secondary-only simplicity closure fails",
            write_run(
                temp / "simplicity-secondary-only-closure",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                scope_coverage=engineering_simplicity_scope_coverage(
                                    primary_surfaces=[],
                                    secondary_surfaces=["smoke-tests"],
                                )
                            )
                        ),
                        handoff_section_bodies=default_worker_handoff_bodies(
                            primary_surfaces=[],
                            secondary_surfaces=["smoke-tests"],
                        ),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "engineering_simplicity_scope primary surface not covered by worker: api-service",
        )

        expect_pass(
            "primary surfaces audited with no fixable issue passes",
            write_run(
                temp / "simplicity-primary-surface-audited",
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
            "positive architecture-gated worker without lane boundary fails",
            write_run(
                temp / "lane-boundary-missing",
                lanes=[
                    architecture_lane(),
                    worker_lane(boundary=OMIT),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "lane-map.json: lane worker-a successful worker lane requires boundary",
        )

        expect_fail(
            "lane boundary missing allowed paths fails",
            write_run(
                temp / "lane-boundary-missing-allowed",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary={
                            **lane_boundary(),
                            "allowed_paths": [],
                        }
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "boundary.allowed_paths must be a non-empty array",
        )

        expect_fail(
            "lane boundary missing changed paths artifact fails",
            write_run(
                temp / "lane-boundary-missing-artifact",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary={
                            **lane_boundary(),
                            "changed_paths_artifact": "checks/missing-boundary.json",
                        },
                        boundary_artifact_data=OMIT,
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "boundary.changed_paths_artifact not found: checks/missing-boundary.json",
        )

        unlisted_artifact_lane = worker_lane()
        unlisted_artifact_lane["evidence"] = ["checks/worker-a.md"]
        expect_fail(
            "lane boundary artifact not listed in evidence fails",
            write_run(
                temp / "lane-boundary-artifact-not-evidence",
                lanes=[
                    architecture_lane(),
                    unlisted_artifact_lane,
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "boundary.changed_paths_artifact must be listed in lane evidence",
        )

        expect_fail(
            "lane boundary artifact wrong lane id fails",
            write_run(
                temp / "lane-boundary-wrong-lane-id",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary_artifact_data=lane_boundary_artifact(lane_id="worker-b")
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "lane-boundary-worker-a.json lane_id must match lane worker-a",
        )

        expect_fail(
            "lane boundary absolute changed path fails",
            write_run(
                temp / "lane-boundary-absolute-path",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary_artifact_data=lane_boundary_artifact(
                            changed_paths=["/apps/api-service/src/routes/settings.ts"],
                            tracked_changed_paths=[
                                "/apps/api-service/src/routes/settings.ts"
                            ],
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "changed_paths[0] must be a repo-relative POSIX path",
        )

        expect_fail(
            "lane boundary parent traversal changed path fails",
            write_run(
                temp / "lane-boundary-parent-traversal",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary_artifact_data=lane_boundary_artifact(
                            changed_paths=["apps/api-service/src/../settings.ts"],
                            tracked_changed_paths=[
                                "apps/api-service/src/../settings.ts"
                            ],
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "changed_paths[0] must be a repo-relative POSIX path",
        )

        expect_fail(
            "lane boundary outside allowed paths fails",
            write_run(
                temp / "lane-boundary-outside-allowed",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary_artifact_data=lane_boundary_artifact(
                            changed_paths=["docs/usage.md"],
                            tracked_changed_paths=["docs/usage.md"],
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "changed path outside allowed_paths: docs/usage.md",
        )

        expect_fail(
            "lane boundary forbidden path wins over allowed path",
            write_run(
                temp / "lane-boundary-forbidden-path",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        boundary=lane_boundary(
                            allowed_paths=["**"],
                            forbidden_paths=["references/**"],
                        ),
                        boundary_artifact_data=lane_boundary_artifact(
                            changed_paths=["references/orchestrator.md"],
                            tracked_changed_paths=["references/orchestrator.md"],
                        ),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "changed path matches forbidden_paths: references/orchestrator.md",
        )

        expect_pass(
            "valid lane boundary with changed paths passes",
            write_run(
                temp / "lane-boundary-valid-changed-paths",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_pass(
            "valid lane boundary with no changed paths passes",
            write_run(
                temp / "lane-boundary-valid-empty-changes",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        handoff_section_bodies=default_worker_handoff_bodies(
                            changed_paths=[]
                        ),
                        boundary_artifact_data=lane_boundary_artifact(
                            changed_paths=[],
                            tracked_changed_paths=[],
                            untracked_paths=[],
                        ),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_pass(
            "schema v1 worker without lane boundary remains compatible",
            write_run(
                temp / "lane-boundary-schema-v1-compatible",
                lanes=[worker_lane(boundary=OMIT), reviewer_control_lane(wave=3)],
            ),
        )

        expect_pass(
            "schema v2 without architecture gate keeps lane boundary optional",
            write_run(
                temp / "lane-boundary-schema-v2-no-architecture",
                lanes=[worker_lane(boundary=OMIT), reviewer_control_lane(wave=3)],
                lane_map_extra={"schema_version": 2, "budget": "standard"},
            ),
        )

        expect_pass(
            "blocked architecture-gated worker without lane boundary remains compatible",
            write_run(
                temp / "lane-boundary-blocked-compatible",
                verdict="blocked",
                lanes=[
                    architecture_lane(),
                    worker_lane(boundary=OMIT),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_fail(
            "engineering simplicity pass with fixable finding fails",
            write_run(
                temp / "worker-engineering-simplicity-pass-fixable-finding",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="pass",
                                findings=[
                                    "Fixable overengineering found: duplicated helper can be removed."
                                ],
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "engineering_simplicity status=pass cannot report fixable remediation findings",
        )

        expect_fail(
            "fixed engineering simplicity without action handoff coverage fails",
            write_run(
                temp / "worker-engineering-simplicity-fixed-action-missing-handoff",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="fixed",
                                findings=["Fixture extra helper removed."],
                                actions=[
                                    "Removed duplicated helper and reused existing tenant helper."
                                ],
                            )
                        )
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Engineering Simplicity missing action: Removed duplicated helper and reused existing tenant helper.",
        )

        fixed_action = "Removed duplicated helper and reused existing tenant helper."
        fixed_worker_handoff = default_worker_handoff_bodies()
        fixed_worker_handoff[ENGINEERING_SIMPLICITY_SECTION] = (
            "Checks:\n"
            + facet_lines(ENGINEERING_SIMPLICITY_CHECKS)
            + "\n\nPrimary surfaces:\n"
            + facet_lines(DEFAULT_PRIMARY_SURFACES)
            + "\n\nStatus: fixed\n"
            "Findings:\n"
            "- Fixture extra helper removed.\n"
            "Actions:\n"
            f"- {fixed_action}\n"
            "Notes: fix now if fixable; no architect re-check needed."
        )

        expect_fail(
            "fixed engineering simplicity reviewer missing worker lane id fails",
            write_run(
                temp / "worker-engineering-simplicity-fixed-reviewer-missing-lane",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="fixed",
                                findings=["Fixture extra helper removed."],
                                actions=[fixed_action],
                            )
                        ),
                        handoff_section_bodies=fixed_worker_handoff,
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Contract Drift missing fixed Engineering Simplicity lane: worker-a",
        )

        expect_pass(
            "worker engineering simplicity fixed accepted",
            write_run(
                temp / "worker-engineering-simplicity-fixed",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="fixed",
                                findings=["Fixture extra helper removed."],
                                actions=[fixed_action],
                            )
                        ),
                        handoff_section_bodies=fixed_worker_handoff,
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            **default_reviewer_handoff_bodies(),
                            CONTRACT_DRIFT_SECTION: (
                                "Engineering Simplicity fixed for worker-a. "
                                "Primary surfaces: api-service. "
                                "Rejected peripheral-only closure. "
                                "Boundary Evidence checked for worker lanes: worker-a. "
                                f"Remediation action: {fixed_action} "
                                "No contract drift for selected Architecture Matrix facets "
                                "and architecture capabilities."
                            ),
                        },
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
        )

        expect_pass(
            "fixed engineering simplicity reviewer coverage is order independent",
            write_run(
                temp / "worker-engineering-simplicity-fixed-reviewer-before-worker",
                lanes=[
                    architecture_lane(),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            **default_reviewer_handoff_bodies(),
                            CONTRACT_DRIFT_SECTION: (
                                "Engineering Simplicity fixed for worker-a. "
                                "Primary surfaces: api-service. "
                                "Rejected peripheral-only closure. "
                                "Boundary Evidence checked for worker lanes: worker-a. "
                                f"Remediation action: {fixed_action} "
                                "No contract drift for selected Architecture Matrix facets "
                                "and architecture capabilities."
                            ),
                        },
                    ),
                    worker_lane(
                        architecture_compliance_data=architecture_compliance(
                            engineering_simplicity=engineering_simplicity_gate(
                                status="fixed",
                                findings=["Fixture extra helper removed."],
                                actions=[fixed_action],
                            )
                        ),
                        handoff_section_bodies=fixed_worker_handoff,
                    ),
                    qa_control_lane(wave=3),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
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

        expect_pass(
            "engineering simplicity drift with later architecture recheck passes",
            write_run(
                temp / "worker-engineering-simplicity-drift-with-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        wave=2,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                            engineering_simplicity=engineering_simplicity_gate(
                                status="drift",
                                findings=["Fixture simplicity issue changes approved boundary."],
                                actions=["Escalated to architecture-recheck before QA."],
                                notes=(
                                    "Engineering Simplicity drift routed through "
                                    "architecture re-check."
                                ),
                            ),
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
            "engineering simplicity drift with missing recheck lane fails",
            write_run(
                temp / "worker-engineering-simplicity-drift-missing-recheck",
                lanes=[
                    architecture_lane(),
                    worker_lane(
                        wave=2,
                        architecture_compliance_data=architecture_compliance(
                            status="drift",
                            recheck_lane="architecture-recheck",
                            engineering_simplicity=engineering_simplicity_gate(
                                status="drift",
                                findings=["Fixture simplicity issue changes approved boundary."],
                                actions=["Escalated to missing architecture-recheck."],
                            ),
                        ),
                    ),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(wave=4),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "engineering_simplicity recheck_lane not found: architecture-recheck",
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
            "reviewer contract drift must cover engineering simplicity",
            write_run(
                temp / "reviewer-contract-drift-missing-engineering-simplicity",
                lanes=[
                    architecture_lane(),
                    worker_lane(),
                    qa_control_lane(wave=3),
                    reviewer_control_lane(
                        wave=4,
                        handoff_section_bodies={
                            ARCHITECTURE_MATRIX_MISMATCHES_SECTION: (
                                "Checked facets:\n"
                                + facet_lines(architecture_context_facets())
                                + "\n\nChecked capabilities:\n"
                                + facet_lines(DEFAULT_ARCHITECTURE_CAPABILITIES)
                            ),
                            CONTRACT_DRIFT_SECTION: (
                                "No contract drift for selected Architecture Matrix facets and "
                                "architecture capabilities."
                            ),
                        },
                    ),
                ],
                lane_map_extra=architecture_control_extra(),
            ),
            "Contract Drift missing Engineering Simplicity",
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
                lanes=[
                    worker_lane(architecture_compliance_data=None),
                    reviewer_control_lane(wave=3),
                ],
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
                    ),
                    reviewer_control_lane(wave=3),
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
