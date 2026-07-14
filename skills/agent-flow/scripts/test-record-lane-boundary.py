#!/usr/bin/env python3
"""Fixture tests for record-lane-boundary.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record-lane-boundary.py"


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(args)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-lane-boundary-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()

        run(["git", "init"], cwd=repo)
        run(["git", "config", "user.email", "fixture@example.com"], cwd=repo)
        run(["git", "config", "user.name", "Fixture"], cwd=repo)

        tracked_path = repo / "apps/api-service/src/routes/settings.ts"
        tracked_path.parent.mkdir(parents=True)
        tracked_path.write_text("export const value = 1;\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".agent-work/\n", encoding="utf-8")
        run(["git", "add", "."], cwd=repo)
        run(["git", "commit", "-m", "base"], cwd=repo)

        tracked_path.write_text("export const value = 2;\n", encoding="utf-8")
        untracked_path = repo / "apps/shared/src/new.ts"
        untracked_path.parent.mkdir(parents=True)
        untracked_path.write_text("export const created = true;\n", encoding="utf-8")
        ignored_path = repo / ".agent-work/ignored.txt"
        ignored_path.parent.mkdir(parents=True)
        ignored_path.write_text("ignored\n", encoding="utf-8")

        run_dir = repo / ".agent-work/runs/lane-boundary-fixture"
        run_dir.mkdir(parents=True)

        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(run_dir),
                "--lane-id",
                "worker-a",
                "--base-ref",
                "HEAD",
            ],
            cwd=repo,
        )
        artifact_path = run_dir / "checks/lane-boundary-worker-a.json"
        if str(artifact_path) not in result.stdout:
            raise AssertionError("recorder stdout must include output artifact path")
        if not artifact_path.exists():
            raise AssertionError("recorder must write lane boundary artifact")

        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        expected_tracked = ["apps/api-service/src/routes/settings.ts"]
        expected_untracked = ["apps/shared/src/new.ts"]
        if data.get("version") != 1:
            raise AssertionError("artifact version must be 1")
        if data.get("lane_id") != "worker-a":
            raise AssertionError("artifact lane_id must match requested lane")
        if data.get("status") != "captured":
            raise AssertionError("artifact status must be captured")
        if data.get("base_ref") != "HEAD":
            raise AssertionError("artifact base_ref must record HEAD")
        if data.get("head_ref") != "working-tree":
            raise AssertionError("artifact head_ref must default to working-tree")
        if data.get("tracked_changed_paths") != expected_tracked:
            raise AssertionError(
                f"tracked paths mismatch: {data.get('tracked_changed_paths')!r}"
            )
        if data.get("untracked_paths") != expected_untracked:
            raise AssertionError(
                f"untracked paths mismatch: {data.get('untracked_paths')!r}"
            )
        if data.get("changed_paths") != [*expected_tracked, *expected_untracked]:
            raise AssertionError(f"changed paths mismatch: {data.get('changed_paths')!r}")
        if any(".agent-work" in path for path in data.get("changed_paths", [])):
            raise AssertionError("ignored agent work files must not be captured")
        if "git diff --name-only HEAD" not in data.get("command", ""):
            raise AssertionError("artifact command must record tracked diff command")
        if data.get("notes") != "Boundary evidence for worker-a.":
            raise AssertionError("artifact notes must identify lane")

    print("PASS record-lane-boundary fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
