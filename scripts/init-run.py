#!/usr/bin/env python3
"""Create Agent Flow traceable run skeletons, including Architecture Artifact Authoring Automation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from architecture_capabilities import (
    ARCHITECTURE_CONTEXT_AXES,
    load_matrix_facets,
    validate_architecture_capabilities_shape,
    validate_architecture_capability_registry,
)


RUN_FILES = {
    "manifest.md": "# Manifest\n\nStatus: active\nVerdict: pending\n\n",
    "context.md": "# Context\n\n## Initial Worktree Snapshot\n\n",
    "route.md": "# Route\n\n",
    "plan.md": "# Plan\n\n",
    "definition-of-done.md": "# Definition Of Done\n\n",
    "decisions.md": "# Decisions\n\n",
    "final.md": (
        "# Final\n\n"
        "Verdict: pending\n\n"
        "## Delegation Trace\n\n"
        "Subagents Used: no\n"
        "Role Lanes Used: no\n"
        "Subagent Lanes: none\n"
        "Role Lanes: none\n"
        "Subagent Trace Evidence: none\n\n"
        "## Boundary Evidence\n\n"
        "TODO(agent): summarize worker lane boundary artifacts and out-of-bound product-code status.\n\n"
        "## Acceptance Traceability\n\n"
        "TODO(agent): summarize acceptance-traceability.json and contract negative/drift fixture coverage.\n\n"
        "## Worktree Hygiene\n\n"
    ),
}
LANE_MAP = {
    "schema_version": 1,
    "lanes": [],
}
AGENT_TODO_PLACEHOLDER = "TODO(agent):"
ENGINEERING_SIMPLICITY_CHECKS = [
    "no-extra-work",
    "stdlib-native-first",
    "existing-helper-first",
    "dependency-justified",
    "abstraction-justified",
    "smallest-working-diff",
    "tests-fit-risk",
]
TRACE_BUDGETS = {"standard", "release"}
WORKER_LANE_TYPES = {"implementation", "integration"}
VERIFICATION_READINESS_PATH = "verification-readiness.json"
CLAIM_EVIDENCE_PATH = "claim-evidence.json"
DEFAULT_CLAIM_ID = "architecture-contract-claim"
ACCEPTANCE_TRACEABILITY_PATH = "acceptance-traceability.json"
DEFAULT_ACCEPTANCE_ID = "architecture-contract-acceptance"
CONTRACT_NEGATIVE_FIXTURE_TYPES = ["gate", "cli", "query", "storage", "config", "parser"]
ENGINEERING_SIMPLICITY_SCOPE_EVIDENCE = "checks/engineering-simplicity-scope.md"
LANE_BOUNDARY_NOTES = "Allowed paths come from Architecture Contract Worker Ownership."
COVERAGE_MATRIX = """# Coverage Matrix

Use this file as the human-readable coverage summary for Lane Sharding runs.
The machine-readable source of truth is `lane-map.json`.

| Lane | Acceptance Area | Evidence | Status | Notes |
| --- | --- | --- | --- | --- |
"""


def empty_delegation_summary() -> dict:
    return {
        "version": 1,
        "subagents_used": False,
        "role_lanes_used": False,
        "subagents": [],
        "role_lanes": [],
        "notes": "No lanes have executed yet. Update before any positive final verdict.",
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "agent-flow-run"


def kebab_id(value: str) -> str:
    return slugify(value).replace("_", "-")


def markdown_id_list(values: list[str]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- `{value}`" for value in values)


def lane_boundary_artifact_path(lane_id: str) -> str:
    return f"checks/lane-boundary-{lane_id}.json"


def lane_boundary_template(lane_id: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "lane_id": lane_id,
            "status": "captured",
            "base_ref": "TODO(agent): base git ref before worker changes.",
            "head_ref": "working-tree",
            "changed_paths": [],
            "tracked_changed_paths": [],
            "untracked_paths": [],
            "command": "TODO(agent): run scripts/record-lane-boundary.py for this lane.",
            "notes": f"Boundary evidence for {lane_id}.",
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def selected_context_facets(context: dict[str, list[str]]) -> list[str]:
    facets: list[str] = []
    for axis in ARCHITECTURE_CONTEXT_AXES:
        facets.extend(context.get(axis, []))
    return facets


def selected_verification_gate_facets(context: dict[str, list[str]]) -> list[str]:
    return [
        *context.get("risk_gates", []),
        *context.get("verification_gates", []),
    ]


def parse_architecture_context(parser: argparse.ArgumentParser, raw_value: str | None) -> dict[str, list[str]]:
    if raw_value is None:
        parser.error("--architecture-gate requires --architecture-context-json")

    source = raw_value
    candidate_path = Path(raw_value).expanduser()
    if candidate_path.exists():
        source = candidate_path.read_text(encoding="utf-8")

    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        parser.error(f"--architecture-context-json invalid JSON: {exc}")

    if not isinstance(data, dict):
        parser.error("--architecture-context-json must be a JSON object")

    matrix_facets, matrix_errors = load_matrix_facets()
    if matrix_errors:
        parser.error("; ".join(matrix_errors))

    expected_axes = set(ARCHITECTURE_CONTEXT_AXES)
    for axis in sorted(set(data) - expected_axes):
        parser.error(f"architecture_context unknown axis: {axis}")

    context: dict[str, list[str]] = {}
    selected_facets: list[str] = []
    for axis in ARCHITECTURE_CONTEXT_AXES:
        if axis not in data:
            parser.error(f"architecture_context missing axis: {axis}")
        value = data[axis]
        if not isinstance(value, list):
            parser.error(f"architecture_context.{axis} must be an array")

        axis_facets: list[str] = []
        for index, facet in enumerate(value):
            if not isinstance(facet, str) or not facet.strip():
                parser.error(f"architecture_context.{axis}[{index}] must be a non-empty string")
            if facet not in matrix_facets.get(axis, set()):
                parser.error(f"architecture_context.{axis}[{index}] unknown Architecture Matrix facet: {facet}")
            axis_facets.append(facet)
            selected_facets.append(facet)
        context[axis] = axis_facets

    if not selected_facets:
        parser.error("architecture_context must select at least one facet")
    return context


def parse_architecture_capabilities(
    parser: argparse.ArgumentParser,
    raw_value: str | None,
    context: dict[str, list[str]],
) -> list[str]:
    if raw_value is None:
        parser.error("--architecture-gate requires --architecture-capabilities")

    selected = [item.strip() for item in raw_value.split(",") if item.strip()]
    capabilities_by_id, registry_errors = validate_architecture_capability_registry(
        validate_skills=False,
        require_full_matrix_coverage=False,
    )
    if registry_errors:
        parser.error("; ".join(registry_errors))

    selected_capabilities, capability_errors = validate_architecture_capabilities_shape(
        {
            "architecture_capabilities": {
                "selected": selected,
                "notes": "Generated architecture capability routing.",
            }
        },
        capabilities_by_id,
        selected_context_facets(context),
        required=True,
    )
    if capability_errors:
        parser.error("; ".join(capability_errors))
    return selected_capabilities


def parse_worker_lanes(parser: argparse.ArgumentParser, raw_values: list[str]) -> list[dict[str, str]]:
    workers: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw_value in raw_values:
        parts = raw_value.split(":")
        if len(parts) != 3 or not all(part.strip() for part in parts):
            parser.error("--worker-lane must use lane-id:type:role")
        lane_id, lane_type, role = (part.strip() for part in parts)
        lane_id = kebab_id(lane_id)
        if lane_type not in WORKER_LANE_TYPES:
            allowed = ", ".join(sorted(WORKER_LANE_TYPES))
            parser.error(f"--worker-lane type must be one of: {allowed}")
        if lane_id in seen_ids:
            parser.error(f"duplicate --worker-lane id: {lane_id}")
        seen_ids.add(lane_id)
        workers.append({"id": lane_id, "type": lane_type, "role": role})
    return workers


def architecture_design_template(
    context: dict[str, list[str]],
    capabilities: list[str],
) -> str:
    selected_facets = selected_context_facets(context)
    return f"""# Architecture Design Brief

Selected Architecture Matrix facets:
{markdown_id_list(selected_facets)}

Selected architecture capabilities:
{markdown_id_list(capabilities)}

## Problem Shape

{AGENT_TODO_PLACEHOLDER} Describe the product problem, scope, and constraints for this run.

## Selected Matrix Facets

{markdown_id_list(selected_facets)}

{AGENT_TODO_PLACEHOLDER} Explain why these Matrix facets are the active design context.

## System Boundaries

{AGENT_TODO_PLACEHOLDER} Define in-scope systems, out-of-scope systems, and ownership boundaries.

## Data And State Model

{AGENT_TODO_PLACEHOLDER} Describe data ownership, state transitions, persistence, queues, caches, and migrations.

## Public Interfaces

{AGENT_TODO_PLACEHOLDER} List APIs, events, UI contracts, external providers, schemas, and compatibility rules.

## Execution Plan

Architecture capabilities:
{markdown_id_list(capabilities)}

{AGENT_TODO_PLACEHOLDER} Convert the selected capabilities into implementation lanes and sequencing.

## Risk Model

{AGENT_TODO_PLACEHOLDER} Name security, privacy, data, release, and product risks with mitigations.

## Verification Strategy

{AGENT_TODO_PLACEHOLDER} Map risks and verification gates to concrete checks.

## Open Questions

{AGENT_TODO_PLACEHOLDER} List unresolved decisions, owner, and stop condition for each question.

## Decision

Status: needs-revision

{AGENT_TODO_PLACEHOLDER} Replace with the final design decision before workers start.
"""


def architecture_contract_template(
    context: dict[str, list[str]],
    capabilities: list[str],
    workers: list[dict[str, str]],
) -> str:
    selected_facets = selected_context_facets(context)
    worker_lines = [f"- `{worker['id']}` owns {worker['type']} work as `{worker['role']}`." for worker in workers]
    return f"""# Architecture Contract

## Selected Architecture

Architecture Matrix facets:
{markdown_id_list(selected_facets)}

Architecture capabilities:
{markdown_id_list(capabilities)}

{AGENT_TODO_PLACEHOLDER} State the selected architecture and why it fits this run.

## Rejected Alternatives

{AGENT_TODO_PLACEHOLDER} Record rejected designs and the concrete reason each was rejected.

## Module Boundaries

{AGENT_TODO_PLACEHOLDER} Define module, package, service, UI, and integration boundaries.

## Data And State Flow

{AGENT_TODO_PLACEHOLDER} Define data flow, state ownership, events, queues, persistence, and rollback rules.

## Public Contracts

{AGENT_TODO_PLACEHOLDER} Define API, event, schema, UI, provider, and compatibility contracts.

## Worker Ownership

{chr(10).join(worker_lines) if worker_lines else '- no worker lanes generated'}

{AGENT_TODO_PLACEHOLDER} Assign exact files, modules, and boundaries to each lane.

## Forbidden Changes

{AGENT_TODO_PLACEHOLDER} List changes workers must not make without architect re-check.

## QA Gates

Claim evidence:
- Claim Evidence: `architecture-contract-claim`

Acceptance criteria:
- Acceptance Criteria: `architecture-contract-acceptance`

Contract negative/drift fixture types:
{markdown_id_list(CONTRACT_NEGATIVE_FIXTURE_TYPES)}

{AGENT_TODO_PLACEHOLDER} Define mandatory behavior, architecture, risk, and verification checks.

## Reviewer Checklist

Claim evidence:
- Claim Evidence: `architecture-contract-claim`

Acceptance criteria:
- Acceptance Criteria: `architecture-contract-acceptance`

Contract negative/drift fixture types:
{markdown_id_list(CONTRACT_NEGATIVE_FIXTURE_TYPES)}

{AGENT_TODO_PLACEHOLDER} List architecture invariants the reviewer must confirm.

## Stop Conditions

{AGENT_TODO_PLACEHOLDER} Define drift, ambiguity, failing check, and external-risk conditions that stop ship.
"""


def worker_handoff_template(
    worker: dict[str, str],
    context: dict[str, list[str]],
    capabilities: list[str],
) -> str:
    selected_facets = selected_context_facets(context)
    return f"""# {worker['id']} Handoff

## Architecture Compliance

Selected Matrix facets available to this worker:
{markdown_id_list(selected_facets)}

Selected architecture capabilities:
{markdown_id_list(capabilities)}

{AGENT_TODO_PLACEHOLDER} Record actual touched facets, contract sections, compliance status, drift, and evidence.

## Engineering Simplicity

Lane-map field: `architecture_compliance.engineering_simplicity`

Required checks:
{markdown_id_list(ENGINEERING_SIMPLICITY_CHECKS)}

Selected architecture capabilities:
{markdown_id_list(capabilities)}

Scope coverage field: `architecture_compliance.engineering_simplicity.scope_coverage`

{AGENT_TODO_PLACEHOLDER} Simplicity Scope Coverage: record primary/secondary surfaces covered by this worker. Primary scope must be audited before peripheral fixes can close the Gate.

{AGENT_TODO_PLACEHOLDER} Record pass, fixed, or drift; fix now if fixable; route as drift only when architect re-check is needed; list findings/actions; cite selected capabilities for retained dependency or abstraction.

## Boundary Evidence

Lane Boundary Evidence Gate

Lane-map field: `boundary`

Artifact: `{lane_boundary_artifact_path(worker['id'])}`

{AGENT_TODO_PLACEHOLDER} Run `python3 scripts/record-lane-boundary.py --run-dir <run-dir> --lane-id {worker['id']}` after this worker's product-code changes, then confirm every changed product path is inside `boundary.allowed_paths` and outside `boundary.forbidden_paths`.

## Acceptance Traceability

Artifact: `{ACCEPTANCE_TRACEABILITY_PATH}`

{AGENT_TODO_PLACEHOLDER} List acceptance ids this worker implemented, their evidence/test markers, and any negative or drift fixtures for gate/CLI/query/storage/config/parser contracts.
"""


def qa_handoff_template(context: dict[str, list[str]]) -> str:
    gates = [
        *context.get("risk_gates", []),
        *context.get("verification_gates", []),
    ]
    return f"""# QA Behavior Handoff

## Architecture Invariants

Selected risk and verification gates:
{markdown_id_list(gates)}

Claim evidence:
- Claim Evidence: `architecture-contract-claim`

Acceptance criteria:
- Acceptance Criteria: `architecture-contract-acceptance`

{AGENT_TODO_PLACEHOLDER} Verify behavior plus architecture invariants for the selected gates.

{AGENT_TODO_PLACEHOLDER} Confirm Boundary Evidence for every worker lane and block closure if any product-code change falls outside the allowed paths.

{AGENT_TODO_PLACEHOLDER} Confirm Acceptance Criteria Traceability Gate and Contract Negative Fixture Gate: every required acceptance id has evidence markers, and gate/CLI/query/storage/config/parser contracts have negative or drift fixture evidence.

## Engineering Simplicity Scope

{AGENT_TODO_PLACEHOLDER} Verify that every primary surface from `engineering_simplicity_scope.primary_surfaces` was audited or remediated before QA closure.

## Verification Gate Results

Selected risk and verification gates:
{markdown_id_list(gates)}

{AGENT_TODO_PLACEHOLDER} Record pass or blocked status for every selected gate after workers run.
"""


def verification_readiness_handoff_template(context: dict[str, list[str]]) -> str:
    gates = selected_verification_gate_facets(context)
    return f"""# Verification Readiness Handoff

## Verification Gate Results

Selected risk and verification gates:
{markdown_id_list(gates)}

{AGENT_TODO_PLACEHOLDER} Probe required env, local runtime, service availability, browser/device readiness, and documented safe commands before workers start.
"""


def verification_readiness_template(context: dict[str, list[str]]) -> str:
    gates = [
        {
            "axis": "risk_gates" if facet in context.get("risk_gates", []) else "verification_gates",
            "facet": facet,
            "readiness": "needs-approval",
            "check": "TODO(agent): name the concrete readiness probe or command.",
            "evidence": ["checks/verification-readiness.md"],
            "notes": "TODO(agent): record readiness result before workers start.",
        }
        for facet in selected_verification_gate_facets(context)
    ]
    return json.dumps(
        {
            "version": 1,
            "status": "needs-approval",
            "attempts": [
                {
                    "id": "readiness-1",
                    "lane": "verification-readiness-1",
                    "status": "needs-approval",
                    "gates": gates,
                    "blockers": ["readiness-blocker"],
                    "approval_requests": ["readiness-approval-request"],
                }
            ],
            "approval_requests": [
                {
                    "id": "readiness-approval-request",
                    "status": "pending",
                    "reason": "TODO(agent): explain why documented command or manual setup is needed.",
                    "commands": [
                        {
                            "cwd": "TODO(agent): repo-relative or absolute cwd from docs.",
                            "command": "TODO(agent): documented safe command only.",
                            "source": "TODO(agent): documentation source path.",
                            "requires_user_approval": True,
                        }
                    ],
                    "manual_instruction": "TODO(agent): tell the user what to run manually, then reply: Готово.",
                    "resume_phrase": "Готово",
                    "affected_gates": selected_verification_gate_facets(context),
                }
            ],
            "approval_executions": [],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def reviewer_handoff_template(
    context: dict[str, list[str]],
    capabilities: list[str],
    workers: list[dict[str, str]],
) -> str:
    selected_facets = selected_context_facets(context)
    worker_ids = [worker["id"] for worker in workers]
    return f"""# Review Contract Handoff

## Architecture Matrix Mismatches

Selected Matrix facets:
{markdown_id_list(selected_facets)}

Selected architecture capabilities:
{markdown_id_list(capabilities)}

{AGENT_TODO_PLACEHOLDER} Report any implementation mismatch for every selected facet and capability.

## Contract Drift

Selected Matrix facets:
{markdown_id_list(selected_facets)}

Selected architecture capabilities:
{markdown_id_list(capabilities)}

Claim evidence:
- Claim Evidence: `architecture-contract-claim`

Acceptance criteria:
- Acceptance Criteria: `architecture-contract-acceptance`

Boundary Evidence worker lanes:
{markdown_id_list(worker_ids)}

{AGENT_TODO_PLACEHOLDER} Report no drift or name the exact drift and required architect re-check. Mention Boundary Evidence for every worker lane id, mention Acceptance Criteria Traceability and Contract Negative Fixture coverage, mention every primary surface, and reject peripheral-only closure.
"""


def claim_evidence_template() -> str:
    return json.dumps(
        {
            "version": 1,
            "claims": [
                {
                    "id": DEFAULT_CLAIM_ID,
                    "owner_lane": "qa-behavior",
                    "reviewed_by": "review-contract",
                    "section": "Architecture Invariants",
                    "status": "gap",
                    "claim": "TODO(agent): state the exact QA/reviewer readiness claim.",
                    "subjects": ["TODO(agent): exact test name, scenario id, API method, UI state, or invariant"],
                    "evidence": [
                        {
                            "path": "checks/qa-behavior.md",
                            "markers": ["TODO(agent): exact marker from evidence file"],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def acceptance_traceability_template() -> str:
    return json.dumps(
        {
            "version": 1,
            "acceptance": [
                {
                    "id": DEFAULT_ACCEPTANCE_ID,
                    "source": "Architecture Contract QA Gates and Reviewer Checklist",
                    "requirement": "TODO(agent): state the exact acceptance contract from ADR, implementation plan, or spec.",
                    "subjects": [
                        "TODO(agent): exact gate, CLI command, query, storage behavior, config path, or parser behavior"
                    ],
                    "contract_types": ["gate"],
                    "status": "gap",
                    "notes": "TODO(agent): change status to supported only after evidence markers and negative/drift fixture markers are present.",
                    "evidence": [
                        {
                            "path": "checks/qa-behavior.md",
                            "markers": ["TODO(agent): exact positive evidence marker"],
                        }
                    ],
                    "negative_fixture_evidence": [
                        {
                            "path": "checks/qa-behavior.md",
                            "markers": ["TODO(agent): exact negative or drift fixture marker"],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def check_template(title: str) -> str:
    return f"""# {title}

{AGENT_TODO_PLACEHOLDER} Record command, result, artifact path, and owner.
"""


def architecture_gate_lane_map(
    budget: str,
    context: dict[str, list[str]],
    capabilities: list[str],
    workers: list[dict[str, str]],
) -> dict:
    lanes = [
        {
            "id": "architecture-contract",
            "type": "architecture",
            "role": "architect",
            "wave": 1,
            "critical": True,
            "execution_mode": "role-lane",
            "status": "planned",
            "handoff": "handoffs/architecture-contract.md",
            "evidence": ["checks/architecture-contract.md"],
            "replacement": None,
            "architecture_design_brief": "handoffs/architecture-design.md",
        }
    ]
    lanes.append(
        {
            "id": "verification-readiness-1",
            "type": "qa",
            "role": "qa-verifier",
            "wave": 2,
            "critical": True,
            "execution_mode": "role-lane",
            "status": "planned",
            "handoff": "handoffs/verification-readiness.md",
            "evidence": ["checks/verification-readiness.md"],
            "replacement": None,
        }
    )
    lanes.extend(
        {
            "id": worker["id"],
            "type": worker["type"],
            "role": worker["role"],
            "wave": 3,
            "critical": True,
            "execution_mode": "role-lane",
            "status": "planned",
            "handoff": f"handoffs/{worker['id']}.md",
            "evidence": [
                f"checks/{worker['id']}.md",
                lane_boundary_artifact_path(worker["id"]),
            ],
            "replacement": None,
            "boundary": {
                "allowed_paths": [],
                "forbidden_paths": [],
                "changed_paths_artifact": lane_boundary_artifact_path(worker["id"]),
                "notes": LANE_BOUNDARY_NOTES,
            },
        }
        for worker in workers
    )
    lanes.extend(
        [
            {
                "id": "qa-behavior",
                "type": "qa",
                "role": "qa-verifier",
                "wave": 4,
                "critical": True,
                "execution_mode": "role-lane",
                "status": "planned",
                "handoff": "handoffs/qa-behavior.md",
                "evidence": ["checks/qa-behavior.md"],
                "replacement": None,
            },
            {
                "id": "review-contract",
                "type": "review",
                "role": "reviewer",
                "wave": 5,
                "critical": True,
                "execution_mode": "role-lane",
                "status": "planned",
                "handoff": "handoffs/review-contract.md",
                "evidence": ["checks/review-contract.md"],
                "replacement": None,
            },
        ]
    )
    return {
        "schema_version": 2,
        "budget": budget,
        "architecture_contract_required": True,
        "architecture_contract_independent": False,
        "architecture_context": context,
            "architecture_capabilities": {
                "selected": capabilities,
                "notes": "Generated from Architecture Matrix context; orchestrator must refine before implementation.",
            },
            "engineering_simplicity_scope": {
                "primary_surfaces": [],
                "secondary_surfaces": [],
                "evidence": [ENGINEERING_SIMPLICITY_SCOPE_EVIDENCE],
                "notes": "TODO(agent): Simplicity Scope Coverage; choose primary surfaces from task scope and Architecture Contract ownership; secondary surfaces cannot close the gate by themselves.",
            },
            "verification_readiness": {
                "artifact": VERIFICATION_READINESS_PATH,
                "lanes": ["verification-readiness-1"],
        },
        "lanes": lanes,
    }


def write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_architecture_gate_artifacts(
    run_dir: Path,
    context: dict[str, list[str]],
    capabilities: list[str],
    workers: list[dict[str, str]],
) -> None:
    write_if_missing(
        run_dir / "handoffs" / "architecture-design.md",
        architecture_design_template(context, capabilities),
    )
    write_if_missing(
        run_dir / "handoffs" / "architecture-contract.md",
        architecture_contract_template(context, capabilities, workers),
    )
    write_if_missing(
        run_dir / "checks" / "architecture-contract.md",
        check_template("Architecture Contract Evidence"),
    )
    write_if_missing(
        run_dir / "handoffs" / "verification-readiness.md",
        verification_readiness_handoff_template(context),
    )
    write_if_missing(
        run_dir / "checks" / "verification-readiness.md",
        check_template("Verification Readiness Evidence"),
    )
    write_if_missing(
        run_dir / ENGINEERING_SIMPLICITY_SCOPE_EVIDENCE,
        check_template("Engineering Simplicity Scope Evidence"),
    )
    write_if_missing(
        run_dir / VERIFICATION_READINESS_PATH,
        verification_readiness_template(context),
    )
    write_if_missing(
        run_dir / CLAIM_EVIDENCE_PATH,
        claim_evidence_template(),
    )
    write_if_missing(
        run_dir / ACCEPTANCE_TRACEABILITY_PATH,
        acceptance_traceability_template(),
    )
    for worker in workers:
        write_if_missing(
            run_dir / "handoffs" / f"{worker['id']}.md",
            worker_handoff_template(worker, context, capabilities),
        )
        write_if_missing(
            run_dir / "checks" / f"{worker['id']}.md",
            check_template(f"{worker['id']} Evidence"),
        )
        write_if_missing(
            run_dir / lane_boundary_artifact_path(worker["id"]),
            lane_boundary_template(worker["id"]),
        )
    write_if_missing(run_dir / "handoffs" / "qa-behavior.md", qa_handoff_template(context))
    write_if_missing(run_dir / "checks" / "qa-behavior.md", check_template("QA Behavior Evidence"))
    write_if_missing(
        run_dir / "handoffs" / "review-contract.md",
        reviewer_handoff_template(context, capabilities, workers),
    )
    write_if_missing(
        run_dir / "checks" / "review-contract.md",
        check_template("Review Contract Evidence"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Project repo path.")
    parser.add_argument("--slug", required=True, help="Short task slug.")
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to local date.")
    parser.add_argument("--reuse", action="store_true", help="Reuse an existing run directory.")
    parser.add_argument("--with-lanes", action="store_true", help="Create Lane Sharding skeleton artifacts.")
    parser.add_argument("--architecture-gate", action="store_true", help="Create Architecture Artifact Authoring Automation skeleton.")
    parser.add_argument("--budget", choices=sorted(TRACE_BUDGETS), help="Trace budget for schema v2 architecture runs.")
    parser.add_argument("--architecture-context-json", help="Architecture Matrix context JSON object or file path.")
    parser.add_argument("--architecture-capabilities", help="Comma-separated Architecture Capability Router ids.")
    parser.add_argument(
        "--worker-lane",
        action="append",
        default=[],
        help="Worker lane in lane-id:type:role form. Type must be implementation or integration.",
    )
    args = parser.parse_args()

    architecture_context: dict[str, list[str]] | None = None
    architecture_capabilities: list[str] = []
    worker_lanes: list[dict[str, str]] = []
    if args.architecture_gate:
        if not args.with_lanes:
            parser.error("--architecture-gate requires --with-lanes")
        if args.budget is None:
            parser.error("--architecture-gate requires --budget")
        architecture_context = parse_architecture_context(parser, args.architecture_context_json)
        architecture_capabilities = parse_architecture_capabilities(
            parser,
            args.architecture_capabilities,
            architecture_context,
        )
        worker_lanes = parse_worker_lanes(parser, args.worker_lane)

    repo = Path(args.repo).expanduser().resolve()
    date = args.date or datetime.now().astimezone().strftime("%Y-%m-%d")
    run_dir = repo / ".agent-work" / "runs" / f"{date}-{slugify(args.slug)}"

    if run_dir.exists() and not args.reuse:
        raise SystemExit(f"run dir already exists: {run_dir} (use --reuse or choose another slug/date)")

    (run_dir / "handoffs").mkdir(parents=True, exist_ok=True)
    (run_dir / "checks").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    for name, content in RUN_FILES.items():
        path = run_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    artifacts = run_dir / "artifacts.json"
    if not artifacts.exists():
        artifacts.write_text("[]\n", encoding="utf-8")

    if args.with_lanes:
        lane_map = run_dir / "lane-map.json"
        if not lane_map.exists():
            lane_map_data = (
                architecture_gate_lane_map(
                    args.budget,
                    architecture_context,
                    architecture_capabilities,
                    worker_lanes,
                )
                if args.architecture_gate and architecture_context is not None and args.budget is not None
                else LANE_MAP
            )
            lane_map.write_text(json.dumps(lane_map_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        delegation_summary = run_dir / "delegation-summary.json"
        if not delegation_summary.exists():
            delegation_summary.write_text(
                json.dumps(empty_delegation_summary(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        coverage_matrix = run_dir / "checks" / "coverage-matrix.md"
        if not coverage_matrix.exists():
            coverage_matrix.write_text(COVERAGE_MATRIX, encoding="utf-8")
        if args.architecture_gate and architecture_context is not None:
            write_architecture_gate_artifacts(
                run_dir,
                architecture_context,
                architecture_capabilities,
                worker_lanes,
            )

    timeline = run_dir / "timeline.jsonl"
    if not timeline.exists():
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "stage": "intake",
            "role": "orchestrator",
            "stable_agent_name": "orchestrator",
            "stable_agent_slug": "orchestrator",
            "status": "active",
            "summary": "Traceable run initialized.",
            "artifacts": [],
            "next_step": "route",
        }
        timeline.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
