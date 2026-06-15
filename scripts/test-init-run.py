#!/usr/bin/env python3
"""Fixture tests for init-run Architecture Artifact Authoring Automation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INIT_RUN = ROOT / "scripts" / "init-run.py"
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"
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
ENGINEERING_SIMPLICITY_CHECKS = [
    "no-extra-work",
    "stdlib-native-first",
    "existing-helper-first",
    "dependency-justified",
    "abstraction-justified",
    "smallest-working-diff",
    "tests-fit-risk",
]
CONTRACT_NEGATIVE_FIXTURE_TYPES = ["gate", "cli", "query", "storage", "config", "parser"]
AGENT_TODO_PLACEHOLDER = "TODO(agent):"
EXPECTED_ARCHITECTURE_GATE_FILES = [
    "delegation-summary.json",
    "verification-readiness.json",
    "claim-evidence.json",
    "acceptance-traceability.json",
    "handoffs/architecture-design.md",
    "handoffs/architecture-contract.md",
    "handoffs/verification-readiness.md",
    "handoffs/worker-a.md",
    "handoffs/qa-behavior.md",
    "handoffs/review-contract.md",
    "checks/architecture-contract.md",
    "checks/verification-readiness.md",
    "checks/engineering-simplicity-scope.md",
    "checks/worker-a.md",
    "checks/lane-boundary-worker-a.json",
    "checks/qa-behavior.md",
    "checks/review-contract.md",
]


def run_init(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INIT_RUN), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def architecture_args(repo: Path, slug: str, *extra: str) -> list[str]:
    return [
        "--repo",
        str(repo),
        "--slug",
        slug,
        "--date",
        "2026-06-12",
        "--with-lanes",
        "--architecture-gate",
        "--budget",
        "standard",
        "--architecture-context-json",
        json.dumps(DEFAULT_ARCHITECTURE_CONTEXT),
        "--architecture-capabilities",
        ",".join(DEFAULT_ARCHITECTURE_CAPABILITIES),
        "--worker-lane",
        "worker-a:implementation:typescript-worker",
        *extra,
    ]


def without_option(args: list[str], option: str, value_count: int = 1) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == option:
            index += 1 + value_count
            continue
        result.append(args[index])
        index += 1
    return result


def replace_option(args: list[str], option: str, values: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == option:
            result.extend([option, *values])
            index += 2
            continue
        result.append(args[index])
        index += 1
    return result


def expect_fail(name: str, args: list[str], needle: str) -> None:
    result = run_init(args)
    if result.returncode == 0:
        raise AssertionError(f"{name} expected failure")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name} missing {needle!r}\nOutput:\n{output}")


def expect_valid_pending_run(name: str, run_dir: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATE_RUN),
            "--run-dir",
            str(run_dir),
            "--allow-pending",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{name} expected validate-run --allow-pending pass\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


def load_lane_map(run_dir: Path) -> dict[str, Any]:
    lane_map = json.loads((run_dir / "lane-map.json").read_text(encoding="utf-8"))
    if not isinstance(lane_map, dict):
        raise AssertionError("lane-map.json must be an object")
    return lane_map


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-init-run-tests-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()

        base = architecture_args(repo, "arch-authoring")

        expect_fail(
            "architecture gate requires lane skeleton",
            without_option(base, "--with-lanes", 0),
            "--architecture-gate requires --with-lanes",
        )
        expect_fail(
            "architecture gate requires budget",
            without_option(architecture_args(repo, "missing-budget"), "--budget"),
            "--architecture-gate requires --budget",
        )
        expect_fail(
            "architecture gate requires architecture context",
            without_option(
                architecture_args(repo, "missing-context"),
                "--architecture-context-json",
            ),
            "--architecture-gate requires --architecture-context-json",
        )
        expect_fail(
            "architecture gate requires architecture capabilities",
            without_option(
                architecture_args(repo, "missing-capabilities"),
                "--architecture-capabilities",
            ),
            "--architecture-gate requires --architecture-capabilities",
        )
        expect_fail(
            "unknown architecture capability fails",
            replace_option(
                architecture_args(repo, "unknown-capability"),
                "--architecture-capabilities",
                ["unknown-capability"],
            ),
            "unknown architecture capability: unknown-capability",
        )
        expect_fail(
            "malformed worker lane fails",
            replace_option(
                architecture_args(repo, "malformed-worker"),
                "--worker-lane",
                ["worker-a:implementation"],
            ),
            "--worker-lane must use lane-id:type:role",
        )

        result = run_init(base)
        if result.returncode != 0:
            raise AssertionError(
                "valid architecture-gate init expected pass\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        run_dir = Path(result.stdout.strip())
        for relative_path in EXPECTED_ARCHITECTURE_GATE_FILES:
            if not (run_dir / relative_path).exists():
                raise AssertionError(f"missing generated file: {relative_path}")
            if relative_path == "delegation-summary.json":
                continue
            if AGENT_TODO_PLACEHOLDER not in (run_dir / relative_path).read_text(encoding="utf-8"):
                raise AssertionError(f"generated file must contain {AGENT_TODO_PLACEHOLDER}: {relative_path}")

        lane_map = load_lane_map(run_dir)
        if lane_map.get("schema_version") != 2:
            raise AssertionError("generated lane-map.json must use schema v2")
        if lane_map.get("architecture_context") != DEFAULT_ARCHITECTURE_CONTEXT:
            raise AssertionError("generated lane-map.json must include architecture_context")
        if lane_map.get("architecture_capabilities", {}).get("selected") != DEFAULT_ARCHITECTURE_CAPABILITIES:
            raise AssertionError("generated lane-map.json must include architecture_capabilities")
        simplicity_scope = lane_map.get("engineering_simplicity_scope")
        if not isinstance(simplicity_scope, dict):
            raise AssertionError("generated lane-map.json must include engineering_simplicity_scope")
        if "primary_surfaces" not in simplicity_scope or "secondary_surfaces" not in simplicity_scope:
            raise AssertionError("generated engineering_simplicity_scope must include surfaces")
        if simplicity_scope.get("evidence") != ["checks/engineering-simplicity-scope.md"]:
            raise AssertionError("generated engineering_simplicity_scope must include evidence path")
        worker_lane = next(
            (lane for lane in lane_map.get("lanes", []) if lane.get("id") == "worker-a"),
            None,
        )
        if not isinstance(worker_lane, dict):
            raise AssertionError("generated lane-map.json must include worker-a lane")
        boundary = worker_lane.get("boundary")
        if not isinstance(boundary, dict):
            raise AssertionError("generated worker lane must include boundary skeleton")
        if "allowed_paths" not in boundary or "forbidden_paths" not in boundary:
            raise AssertionError("generated boundary skeleton must include path arrays")
        if boundary.get("changed_paths_artifact") != "checks/lane-boundary-worker-a.json":
            raise AssertionError("generated boundary skeleton must include artifact path")
        if "checks/lane-boundary-worker-a.json" not in worker_lane.get("evidence", []):
            raise AssertionError("generated worker evidence must include boundary artifact")
        boundary_artifact = json.loads(
            (run_dir / "checks/lane-boundary-worker-a.json").read_text(encoding="utf-8")
        )
        if boundary_artifact.get("version") != 1 or boundary_artifact.get("lane_id") != "worker-a":
            raise AssertionError("generated boundary artifact must match schema and lane id")
        if boundary_artifact.get("changed_paths") != []:
            raise AssertionError("generated boundary artifact must start with empty changed paths")

        worker_handoff = (run_dir / "handoffs/worker-a.md").read_text(encoding="utf-8")
        if "## Engineering Simplicity" not in worker_handoff:
            raise AssertionError("worker handoff missing Engineering Simplicity section")
        if "## Boundary Evidence" not in worker_handoff:
            raise AssertionError("worker handoff missing Boundary Evidence section")
        if "## Acceptance Traceability" not in worker_handoff:
            raise AssertionError("worker handoff missing Acceptance Traceability section")
        if "acceptance-traceability.json" not in worker_handoff:
            raise AssertionError("worker handoff missing acceptance traceability artifact")
        if "Lane Boundary Evidence Gate" not in worker_handoff:
            raise AssertionError("worker handoff missing Lane Boundary Evidence Gate label")
        if "record-lane-boundary.py" not in worker_handoff:
            raise AssertionError("worker handoff missing lane boundary recorder instruction")
        if "boundary.allowed_paths" not in worker_handoff or "boundary.forbidden_paths" not in worker_handoff:
            raise AssertionError("worker handoff missing boundary path field instructions")
        if "scope_coverage" not in worker_handoff:
            raise AssertionError("worker handoff missing Engineering Simplicity scope_coverage")
        if "Simplicity Scope Coverage" not in worker_handoff:
            raise AssertionError("worker handoff missing Simplicity Scope Coverage")
        if "Primary scope must be audited before peripheral fixes can close the Gate" not in worker_handoff:
            raise AssertionError("worker handoff missing primary scope closure rule")
        for check in ENGINEERING_SIMPLICITY_CHECKS:
            if check not in worker_handoff:
                raise AssertionError(f"worker handoff missing engineering simplicity check: {check}")
        if AGENT_TODO_PLACEHOLDER not in worker_handoff:
            raise AssertionError("worker handoff missing Engineering Simplicity TODO(agent)")
        if "fix now if fixable" not in worker_handoff:
            raise AssertionError("worker handoff missing Engineering Simplicity remediation instruction")

        qa_handoff = (run_dir / "handoffs/qa-behavior.md").read_text(encoding="utf-8")
        if "## Engineering Simplicity Scope" not in qa_handoff:
            raise AssertionError("QA handoff missing Engineering Simplicity Scope section")
        if "Boundary Evidence" not in qa_handoff:
            raise AssertionError("QA handoff missing Boundary Evidence invariant")
        if "Acceptance Criteria Traceability Gate" not in qa_handoff:
            raise AssertionError("QA handoff missing Acceptance Criteria Traceability Gate")
        if "Contract Negative Fixture Gate" not in qa_handoff:
            raise AssertionError("QA handoff missing Contract Negative Fixture Gate")

        reviewer_handoff = (run_dir / "handoffs/review-contract.md").read_text(encoding="utf-8")
        if "peripheral-only closure" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing peripheral-only closure rule")
        if "Boundary Evidence" not in reviewer_handoff or "worker-a" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing Boundary Evidence worker lane id")
        if "Acceptance Criteria Traceability" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing Acceptance Criteria Traceability")
        if "Contract Negative Fixture" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing Contract Negative Fixture")
        if "## Mandatory Independent QA Review" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing Mandatory Independent QA Review section")
        if "real reviewer.qa subagent" not in reviewer_handoff:
            raise AssertionError("reviewer handoff missing reviewer.qa subagent instruction")

        acceptance_traceability = json.loads(
            (run_dir / "acceptance-traceability.json").read_text(encoding="utf-8")
        )
        acceptance_records = acceptance_traceability.get("acceptance")
        if not isinstance(acceptance_records, list) or not acceptance_records:
            raise AssertionError("generated acceptance-traceability.json must include records")
        acceptance_record = acceptance_records[0]
        if acceptance_record.get("id") != "architecture-contract-acceptance":
            raise AssertionError("generated acceptance traceability must include default acceptance id")
        if acceptance_record.get("contract_types") != ["gate"]:
            raise AssertionError("generated acceptance traceability must include contract_types")
        if "supported" not in acceptance_record.get("notes", ""):
            raise AssertionError("generated acceptance traceability must explain supported status")
        if "markers" not in acceptance_record.get("notes", ""):
            raise AssertionError("generated acceptance traceability must explain evidence markers")
        if "negative_fixture_evidence" not in acceptance_record:
            raise AssertionError("generated acceptance traceability must include negative_fixture_evidence")
        for contract_type in CONTRACT_NEGATIVE_FIXTURE_TYPES:
            if contract_type not in (run_dir / "handoffs/architecture-contract.md").read_text(encoding="utf-8"):
                raise AssertionError(f"architecture contract missing contract fixture type: {contract_type}")

        architecture_contract = (run_dir / "handoffs/architecture-contract.md").read_text(encoding="utf-8")
        if "Acceptance Criteria: `architecture-contract-acceptance`" not in architecture_contract:
            raise AssertionError("architecture contract missing default Acceptance Criteria id")

        delegation_summary = json.loads((run_dir / "delegation-summary.json").read_text(encoding="utf-8"))
        if delegation_summary.get("subagents_used") is not False:
            raise AssertionError("generated delegation-summary.json must start with subagents_used=false")
        if delegation_summary.get("role_lanes_used") is not False:
            raise AssertionError("generated delegation-summary.json must start with role_lanes_used=false")

        final_text = (run_dir / "final.md").read_text(encoding="utf-8")
        for expected_line in [
            "## Delegation Trace",
            "Subagents Used: no",
            "Role Lanes Used: no",
            "Subagent Trace Evidence: none",
            "## Mandatory Independent QA Review",
            "Mandatory Independent QA Review Gate",
            "terminal handoff artifact",
            "role-lane",
            "launch-failure",
            "runtime-failure",
        ]:
            if expected_line not in final_text:
                raise AssertionError(f"generated final.md missing delegation trace line: {expected_line}")

        lane_ids = {lane.get("id") for lane in lane_map.get("lanes", []) if isinstance(lane, dict)}
        expected_lane_ids = {
            "architecture-contract",
            "verification-readiness-1",
            "worker-a",
            "qa-behavior",
            "review-contract",
        }
        missing_lane_ids = expected_lane_ids - lane_ids
        if missing_lane_ids:
            raise AssertionError(f"generated lane-map.json missing lanes: {sorted(missing_lane_ids)}")
        reviewer_lane = next(
            (lane for lane in lane_map.get("lanes", []) if lane.get("id") == "review-contract"),
            None,
        )
        if not isinstance(reviewer_lane, dict):
            raise AssertionError("generated lane-map.json must include review-contract lane")
        if reviewer_lane.get("execution_mode") != "subagent":
            raise AssertionError("generated reviewer lane must use execution_mode=subagent")
        mandatory_review = lane_map.get("mandatory_independent_qa_review")
        if not isinstance(mandatory_review, dict):
            raise AssertionError("generated lane-map.json must include mandatory_independent_qa_review")
        if mandatory_review.get("reviewer_lane") != "review-contract":
            raise AssertionError("mandatory_independent_qa_review must reference review-contract")

        expect_valid_pending_run("generated pending run", run_dir)

    print("PASS init-run fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
