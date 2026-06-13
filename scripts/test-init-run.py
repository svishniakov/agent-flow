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
AGENT_TODO_PLACEHOLDER = "TODO(agent):"
EXPECTED_ARCHITECTURE_GATE_FILES = [
    "delegation-summary.json",
    "handoffs/architecture-design.md",
    "handoffs/architecture-contract.md",
    "handoffs/worker-a.md",
    "handoffs/qa-behavior.md",
    "handoffs/review-contract.md",
    "checks/architecture-contract.md",
    "checks/worker-a.md",
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
        ]:
            if expected_line not in final_text:
                raise AssertionError(f"generated final.md missing delegation trace line: {expected_line}")

        lane_ids = {lane.get("id") for lane in lane_map.get("lanes", []) if isinstance(lane, dict)}
        expected_lane_ids = {
            "architecture-contract",
            "worker-a",
            "qa-behavior",
            "review-contract",
        }
        missing_lane_ids = expected_lane_ids - lane_ids
        if missing_lane_ids:
            raise AssertionError(f"generated lane-map.json missing lanes: {sorted(missing_lane_ids)}")

        expect_valid_pending_run("generated pending run", run_dir)

    print("PASS init-run fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
