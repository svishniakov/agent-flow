#!/usr/bin/env python3
"""Capture changed-path evidence for a worker lane boundary.

Registered workspaces use the complete baseline-to-candidate delta.
Historical diagnostic runs retain the record-lane-boundary.py Git format:
`git diff --name-only` and `git ls-files --others --exclude-standard`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from journal_io import operation_id, write_entry, encode_json, JournalError, JournalSnapshot, logical_path
from task_workspace import registered_workspace, verify_candidate, git_command
from verification_evidence import result_contract, verify_author_declaration, retained_accepted_boundary_v1


def run_git(repo_root: Path, args: list[str]) -> list[str]:
    result = git_command(repo_root, *args, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return [line for line in result.stdout.decode().splitlines() if line]


def find_git_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"no git root found above {start}")


def unique_preserving_order(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


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
    if workspace is not None:
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
            if identifier and snapshot.operation_receipt(identifier) is not None and retained_accepted_boundary_v1(snapshot):
                operation_id(resolved_run_dir, "lane-boundary", artifact, identifier=identifier)
                previous = json.loads(snapshot.read_text(f"checks/lane-boundary-{lane_id}.json"))
                if previous != artifact:
                    raise JournalError("legacy boundary replay differs from accepted artifact")
                return resolved_run_dir / "checks" / f"lane-boundary-{lane_id}.json"
            raise JournalError("registered result requires explicit author declaration version 2")
        assignment = verify_author_declaration(snapshot, declaration, lane_id=lane_id)
        if not set(declaration["authored_paths"]) <= set(sealed["changed_paths"]):
            raise JournalError("authored_paths must be part of full workspace delta")
        artifact.update(version=2, **declaration, assignment=assignment)
    else:
        if result_contract(snapshot).get("result_contract_version", 1) >= 2:
            raise JournalError("prepare and seal workspace before boundary capture")
        resolved_repo_root = repo_root.resolve() if repo_root else find_git_root(resolved_run_dir)

        if head_ref == "working-tree":
            diff_args = ["diff", "--name-only", base_ref, "--"]
            untracked_args = ["ls-files", "--others", "--exclude-standard"]
            tracked_changed_paths = run_git(resolved_repo_root, diff_args)
            untracked_paths = run_git(resolved_repo_root, untracked_args)
            command = f"git {' '.join(diff_args)}; git {' '.join(untracked_args)}"
        else:
            diff_args = ["diff", "--name-only", base_ref, head_ref, "--"]
            tracked_changed_paths = run_git(resolved_repo_root, diff_args)
            untracked_paths = []
            command = f"git {' '.join(diff_args)}"

        changed_paths = unique_preserving_order([*tracked_changed_paths, *untracked_paths])
        artifact = {
            "version": 1,
            "lane_id": lane_id,
            "status": "captured",
            "base_ref": base_ref,
            "head_ref": head_ref,
            "changed_paths": changed_paths,
            "tracked_changed_paths": tracked_changed_paths,
            "untracked_paths": untracked_paths,
            "command": command,
            "notes": f"Boundary evidence for {lane_id}.",
        }

    output_path = resolved_run_dir / "checks" / f"lane-boundary-{lane_id}.json"
    def publish(current):
        registered = registered_workspace(current)
        if workspace is not None:
            if registered is None or verify_candidate(registered)["candidate_id"] != artifact["candidate_id"]:
                raise JournalError("workspace candidate changed before boundary publication")
            if verify_author_declaration(current, declaration, lane_id=lane_id) != artifact["assignment"]:
                raise JournalError("author declaration assignment changed before publication")
        elif registered is not None:
            raise JournalError("workspace registered before legacy boundary publication")
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
    parser.add_argument("--repo-root", help="Git repository root. Defaults to nearest git root above run-dir.")
    parser.add_argument("--base-ref", default="HEAD", help="Base git ref for diff. Defaults to HEAD.")
    parser.add_argument("--head-ref", default="working-tree", help="Head ref. Defaults to working-tree.")
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
