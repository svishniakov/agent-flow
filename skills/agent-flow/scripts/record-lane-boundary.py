#!/usr/bin/env python3
"""Capture changed-path evidence for a worker lane boundary.

Registered workspaces use the complete baseline-to-candidate delta.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from journal_io import write_entry, encode_json, JournalError, JournalSnapshot, logical_path
from task_workspace import registered_workspace, verify_candidate
from verification_evidence import verify_author_declaration, author_boundary_seal


def capture_boundary(
    *,
    run_dir: Path,
    lane_id: str,
    repo_root: Path | None,
    base_ref: str,
    head_ref: str,
    identifier: str | None = None,
    declaration: dict | None = None,
) -> Path:
    resolved_run_dir = run_dir.resolve()
    if type(lane_id) is not str or not lane_id.strip() or "/" in lane_id:
        raise JournalError("lane_id must be a non-empty path segment")
    logical_path("checks/lane-boundary-" + lane_id + ".json")
    snapshot = JournalSnapshot.open(resolved_run_dir)
    workspace = registered_workspace(snapshot)
    if workspace is None:
        raise JournalError("prepare and seal workspace before boundary capture")
    sealed = verify_candidate(workspace)
    if repo_root is not None and repo_root.absolute() != Path(workspace["working_root"]):
        raise JournalError("boundary root must be the registered working root")
    if base_ref != "HEAD" or head_ref != "working-tree":
        raise JournalError("workspace boundary uses the full baseline-to-candidate delta")
    artifact = {"version": 1, "lane_id": lane_id, "status": "captured", "base_ref": "workspace-baseline",
                "head_ref": sealed["candidate_id"], "changed_paths": sealed["changed_paths"],
                "tracked_changed_paths": [], "untracked_paths": [],
                "command": "task-workspace full baseline-to-candidate inventory",
                "workspace_id": workspace["workspace_id"], "candidate_id": sealed["candidate_id"],
                "baseline_digest": workspace["baseline_digest"], "candidate_digest": sealed["candidate_digest"],
                "notes": f"Full workspace boundary evidence for {lane_id}."}
    if declaration is None:
        raise JournalError("registered result requires explicit author declaration version 2")
    assignment = verify_author_declaration(snapshot, declaration, lane_id=lane_id, presentation_required=False)
    artifact.update(version=2, **declaration, assignment=assignment)
    author_boundary_seal(snapshot, workspace, artifact)
    previous_path = f"checks/lane-boundary-{lane_id}.json"
    if snapshot.exists(previous_path):
        previous = json.loads(snapshot.read_text(previous_path))
        if previous != artifact:
            raise JournalError("author contribution is immutable; use a new assignment for corrections")

    output_path = resolved_run_dir / "checks" / f"lane-boundary-{lane_id}.json"
    def publish(current):
        registered = registered_workspace(current)
        if registered is None or verify_candidate(registered)["candidate_id"] != artifact["candidate_id"]:
            raise JournalError("workspace candidate changed before boundary publication")
        if verify_author_declaration(current, declaration, lane_id=lane_id, presentation_required=False) != artifact["assignment"]:
            raise JournalError("author declaration assignment changed before publication")
        if current.exists(previous_path):
            previous = json.loads(current.read_text(previous_path))
            if previous != artifact:
                raise JournalError("author contribution is immutable; use a new assignment for corrections")
        return {output_path.relative_to(resolved_run_dir).as_posix(): encode_json(artifact, pretty=True) + "\n"}, {}
    write_entry(resolved_run_dir, "event", {"summary": artifact["notes"], "status": "active", "stage": "boundary-captured", "role": "orchestrator"},
                identifier=identifier, payload=artifact, mutation=publish, replay_guard=publish)
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Traceable run directory.")
    parser.add_argument("--lane-id", required=True, help="Worker lane id.")
    parser.add_argument("--operation-id", help="Saved machine request ID for retry.")
    parser.add_argument("--declaration-json", help="Published author declaration path in this journal.")
    parser.add_argument("--repo-root", help="Git repository root. Defaults to the registered working root.")
    parser.add_argument("--base-ref", default="HEAD", help="Accepted only as HEAD; workspace boundaries use the full delta.")
    parser.add_argument("--head-ref", default="working-tree", help="Accepted only as working-tree; workspace boundaries use the full delta.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        snapshot = JournalSnapshot.open(Path(args.run_dir))
        declaration = json.loads(snapshot.read_text(args.declaration_json)) if args.declaration_json else None
        output_path = capture_boundary(
            run_dir=Path(args.run_dir),
            lane_id=args.lane_id,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            identifier=args.operation_id,
            declaration=declaration,
        )
    except (RuntimeError, JournalError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
