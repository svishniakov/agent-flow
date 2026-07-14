#!/usr/bin/env python3
"""Capture changed-path evidence for a worker lane boundary.

`record-lane-boundary.py` records `git diff --name-only` and
`git ls-files --others --exclude-standard` output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run_git(repo_root: Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return [line for line in result.stdout.splitlines() if line]


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
) -> Path:
    resolved_run_dir = run_dir.resolve()
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Traceable run directory.")
    parser.add_argument("--lane-id", required=True, help="Worker lane id.")
    parser.add_argument("--repo-root", help="Git repository root. Defaults to nearest git root above run-dir.")
    parser.add_argument("--base-ref", default="HEAD", help="Base git ref for diff. Defaults to HEAD.")
    parser.add_argument("--head-ref", default="working-tree", help="Head ref. Defaults to working-tree.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_path = capture_boundary(
            run_dir=Path(args.run_dir),
            lane_id=args.lane_id,
            repo_root=Path(args.repo_root) if args.repo_root else None,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
