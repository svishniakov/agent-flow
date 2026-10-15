#!/usr/bin/env python3
"""Create Agent Flow traceable run skeletons, including Architecture Artifact Authoring Automation."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from journal_io import (now_iso, encode_json, encode_json_line, JournalSnapshot,
                        initialize_journal, export_snapshot, capture_flat, UNSUPPORTED_STORAGE_VERSION)
import tempfile
import sys
from pathlib import Path

from verification_evidence import EvidenceError, empty_verification, validate_summary_shape, ensure_result_contract

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
        "## Mandatory Independent QA Review\n\n"
        "Mandatory Independent QA Review Gate: pending source-confirmed QA and reviewer conclusions.\n\n"
        "## Boundary Evidence\n\n"
        "Pending native boundary evidence for product author lanes.\n\n"
        "## Acceptance Traceability\n\n"
        "Pending acceptance evidence and negative/drift fixture coverage.\n\n"
        "## Worktree Hygiene\n\n"
    ),
}
LANE_MAP = {
    "schema_version": 1,
    "lanes": [],
}
TRACE_BUDGETS = {"standard", "release"}
WORKER_LANE_TYPES = {"implementation", "integration"}
VERIFICATION_READINESS_PATH = "verification-readiness.json"
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
        "verification": empty_verification(),
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "agent-flow-run"


def kebab_id(value: str) -> str:
    return slugify(value).replace("_", "-")


def lane_boundary_artifact_path(lane_id: str) -> str:
    return f"checks/lane-boundary-{lane_id}.json"


def parse_architecture_context(parser: argparse.ArgumentParser, raw_value: str | None) -> dict[str, list[str]]:
    if raw_value is None:
        parser.error("--architecture-gate requires --architecture-context-json")

    try:
        try:
            data = json.loads(raw_value)
        except json.JSONDecodeError:
            if raw_value.lstrip().startswith(("{", "[", '"')):
                raise
            data = json.loads(Path(raw_value).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
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
        sorted({facet for values in context.values() for facet in values}),
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


def queued_handoff_state(lane: dict) -> dict:
    return {
        "version": 1,
        "mode": "task",
        "status": "queued",
        "to": lane["id"],
        "handoff": lane["handoff"],
    }


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
                "execution_mode": "subagent",
                "status": "planned",
                "handoff": "handoffs/review-contract.md",
                "evidence": ["checks/review-contract.md"],
                "replacement": None,
            },
        ]
    )
    for lane in lanes:
        lane["handoff_state"] = queued_handoff_state(lane)
    return {
        "schema_version": 2,
        "budget": budget,
        "architecture_contract_required": True,
        "architecture_contract_independent": False,
        "handoff_state_required": True,
        "architecture_context": context,
            "architecture_capabilities": {
                "selected": capabilities,
                "notes": "Generated from Architecture Matrix context; orchestrator must refine before implementation.",
            },
            "engineering_simplicity_scope": {
                "primary_surfaces": [],
                "secondary_surfaces": [],
                "evidence": [],
                "notes": "Pending scope declaration from task scope and Architecture Contract ownership.",
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
    from journal_structure import render_document
    selected = ", ".join(facet for values in context.values() for facet in values)
    write_if_missing(run_dir / "handoffs" / "architecture-design.md", render_document("architecture design", "Pending architecture design. Selected context: " + selected, "planned"))
    for name in ["architecture-contract", "verification-readiness", *[worker["id"] for worker in workers], "qa-behavior", "review-contract"]:
        write_if_missing(run_dir / "handoffs" / (name + ".md"), render_document("handoff", "Pending " + name + ". Record semantic conclusions and actual evidence through the journal writer. Selected context: " + selected, "planned"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Project repo path.")
    parser.add_argument("--summary", help="Semantic task scope; Python renders the run document.")
    parser.add_argument("--plan-summary", help="Semantic execution plan.")
    parser.add_argument("--slug", required=True, help="Short task slug.")
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to local date.")
    parser.add_argument("--reuse", action="store_true", help="Reuse an existing run directory.")
    parser.add_argument("--mode", choices=("compact", "full"), help="Journal format. Defaults to full for a new run; reuse preserves its format.")
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
    parser.add_argument("--source-root", help="Expected original project root for read-only --reuse provenance checks.")
    args = parser.parse_args(argv)
    if args.source_root and not args.reuse:
        parser.error("--source-root is limited to read-only --reuse provenance checks")

    if args.mode == "compact" and any((args.with_lanes, args.architecture_gate, args.budget,
                                       args.architecture_context_json, args.architecture_capabilities, args.worker_lane)):
        parser.error("--mode compact is incompatible with lane and architecture generation flags")

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
    from journal_structure import validate_request, render_document
    if args.summary is not None:
        validate_request("task", {"summary": args.summary, "status": "active"})
    if args.plan_summary is not None:
        validate_request("plan", {"summary": args.plan_summary, "status": "planned"})
    if args.reuse and (args.summary is not None or args.plan_summary is not None):
        parser.error("reuse is read-only; use journal record to update semantic text")
    date = args.date or datetime.now().astimezone().strftime("%Y-%m-%d")
    run_dir = repo / ".agent-work" / "runs" / f"{date}-{slugify(args.slug)}"

    if run_dir.exists() and not args.reuse:
        raise SystemExit(f"run dir already exists: {run_dir} (use --reuse or choose another slug/date)")

    mode = args.mode or "full"
    from journal_io import JournalError
    try:
        snapshot = JournalSnapshot.open(run_dir) if run_dir.exists() else None
    except JournalError as exc:
        parser.error(str(exc))
    if snapshot is not None and (snapshot.durable or snapshot.documents or not (run_dir / ".journal/state.sqlite3").exists()):
        existing_mode = "compact" if snapshot.exists("run.md") and not snapshot.exists("manifest.md") else "full"
        if args.mode and args.mode != existing_mode:
            parser.error("--reuse cannot change the existing journal format")
        mode = existing_mode
        if mode == "compact" and any((args.with_lanes, args.architecture_gate, args.budget,
                                       args.architecture_context_json, args.architecture_capabilities, args.worker_lane)):
            parser.error("compact --reuse is incompatible with lane and architecture generation flags")
        summary_path = run_dir / "delegation-summary.json"
        if not snapshot.exists("delegation-summary.json"):
            parser.error("delegation-summary.json is missing from the existing run; restore its original state before --reuse or initialize a new run with another slug")
        try:
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            validate_summary_shape(summary)
        except (EvidenceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            parser.error(f"delegation-summary.json is malformed; correct it before --reuse: {exc}")

        if not snapshot.durable:
            parser.error(UNSUPPORTED_STORAGE_VERSION)
        if args.source_root and Path(args.source_root).expanduser().resolve() != snapshot.source_root:
            parser.error(f"--source-root differs from committed journal provenance: {snapshot.source_root}")

        # Reuse is a read-only view.
        print(run_dir)
        print(f"view: {export_snapshot(snapshot)}", file=sys.stderr)
        return 0

    logical_run_dir = run_dir
    draft = tempfile.TemporaryDirectory(prefix="agent-flow-init-")
    run_dir = Path(draft.name).resolve()

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "handoffs").mkdir(exist_ok=True)
    (run_dir / "checks").mkdir(exist_ok=True)
    if mode == "full":
        (run_dir / "artifacts").mkdir(exist_ok=True)

    files = RUN_FILES if mode == "full" else {
        "run.md": "# Run\n\nStatus: active\n\n## Task Scope\n\n",
        "checks.md": "# Checks\n\n",
        "context.md": RUN_FILES["context.md"],
        "final.md": RUN_FILES["final.md"].split("## Boundary Evidence")[0],
    }
    for name, content in files.items():
        path = run_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    if args.summary is not None:
        (run_dir / "run.md").write_text(render_document("task", args.summary, "active"), encoding="utf-8")
        import subprocess
        observed = subprocess.run(["git", "status", "--porcelain=v1"], cwd=repo, capture_output=True)
        state = observed.stdout.decode("utf-8", errors="replace") if observed.returncode == 0 else "Git status unavailable; exit code " + str(observed.returncode)
        context = "# Context\n\n## Initial Worktree Snapshot\n\nCommand: git status --porcelain=v1\nExit code: " + str(observed.returncode) + "\n\n```text\n" + state + "\n```\n"
        (run_dir / "context.md").write_text(context, encoding="utf-8")
    if args.plan_summary is not None:
        (run_dir / "plan.md").write_text(render_document("plan", args.plan_summary, "planned"), encoding="utf-8")

    artifacts = run_dir / "artifacts.json"
    if mode == "full" and not artifacts.exists():
        artifacts.write_text("[]\n", encoding="utf-8")

    delegation_summary = run_dir / "delegation-summary.json"
    if not delegation_summary.exists():
        delegation_summary.write_text(encode_json(empty_delegation_summary(), pretty=True) + "\n", encoding="utf-8")

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
            lane_map.write_text(encode_json(lane_map_data, pretty=True) + "\n", encoding="utf-8")
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
            "timestamp": now_iso(),
            "stage": "intake",
            "role": "orchestrator",
            "stable_agent_name": "orchestrator",
            "stable_agent_slug": "orchestrator",
            "status": "active",
            "summary": "Traceable run initialized.",
            "artifacts": [],
            "next_step": "route",
        }
        timeline.write_text(encode_json_line(event) + "\n", encoding="utf-8")

    documents, _ = capture_flat(run_dir)
    snapshot = initialize_journal(logical_run_dir, documents, source_root=repo)
    draft.cleanup()
    print(logical_run_dir)
    print(f"view: {export_snapshot(snapshot)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
