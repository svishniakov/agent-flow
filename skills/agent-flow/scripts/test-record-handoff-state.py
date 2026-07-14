#!/usr/bin/env python3
"""Fixture tests for record-handoff-state.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record-handoff-state.py"


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


def write_lane_map(run_dir: Path, lane_handoff: str = "handoffs/worker-a.md") -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "lane-map.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "budget": "standard",
                "unknown_root": {"preserve": True},
                "lanes": [
                    {
                        "id": "worker-a",
                        "type": "implementation",
                        "role": "python-worker",
                        "wave": 1,
                        "critical": True,
                        "execution_mode": "role-lane",
                        "status": "planned",
                        "handoff": lane_handoff,
                        "unknown_lane": ["keep"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def read_lane_map(run_dir: Path) -> dict:
    return json.loads((run_dir / "lane-map.json").read_text(encoding="utf-8"))


def assert_state(run_dir: Path, expected_status: str) -> dict:
    data = read_lane_map(run_dir)
    if data.get("unknown_root") != {"preserve": True}:
        raise AssertionError("recorder must preserve unknown root fields")
    lane = data["lanes"][0]
    if lane.get("unknown_lane") != ["keep"]:
        raise AssertionError("recorder must preserve unknown lane fields")
    state = lane.get("handoff_state")
    if not isinstance(state, dict):
        raise AssertionError("recorder must create handoff_state object")
    if state.get("version") != 1:
        raise AssertionError("handoff_state.version must be 1")
    if state.get("mode") != "task":
        raise AssertionError("handoff_state.mode must default to task")
    if state.get("status") != expected_status:
        raise AssertionError(f"handoff_state.status mismatch: {state.get('status')!r}")
    if state.get("handoff") != "handoffs/worker-a.md":
        raise AssertionError("handoff_state.handoff must match lane handoff")
    return state


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-handoff-state-") as temp_dir:
        root = Path(temp_dir)

        create_run = root / "create"
        write_lane_map(create_run)
        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(create_run),
                "--lane-id",
                "worker-a",
                "--status",
                "queued",
                "--from",
                "architecture-contract",
                "--task",
                "worker-a-implementation",
                "--handoff",
                "handoffs/worker-a.md",
            ],
            cwd=root,
        )
        if "updated" not in result.stdout:
            raise AssertionError("recorder stdout must confirm update")
        state = assert_state(create_run, "queued")
        if state.get("from") != "architecture-contract":
            raise AssertionError("recorder must set from field")
        if state.get("to") != "worker-a":
            raise AssertionError("recorder must default to field to lane id")
        if state.get("task") != "worker-a-implementation":
            raise AssertionError("recorder must set task field")
        if not state.get("queued_at"):
            raise AssertionError("queued status must set queued_at")

        progress_run = root / "progress"
        write_lane_map(progress_run)
        for status in ["queued", "accepted", "completed"]:
            run(
                [
                    sys.executable,
                    str(RECORDER),
                    "--run-dir",
                    str(progress_run),
                    "--lane-id",
                    "worker-a",
                    "--status",
                    status,
                ],
                cwd=root,
            )
        state = assert_state(progress_run, "completed")
        for field in ["queued_at", "accepted_at", "completed_at"]:
            if not state.get(field):
                raise AssertionError(f"{field} must be set after status progression")

        unknown_lane_run = root / "unknown-lane"
        write_lane_map(unknown_lane_run)
        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(unknown_lane_run),
                "--lane-id",
                "missing",
                "--status",
                "queued",
            ],
            cwd=root,
            check=False,
        )
        if result.returncode == 0 or "unknown lane id: missing" not in result.stderr:
            raise AssertionError("recorder must reject unknown lane ids")

        mismatch_run = root / "handoff-mismatch"
        write_lane_map(mismatch_run)
        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(mismatch_run),
                "--lane-id",
                "worker-a",
                "--status",
                "queued",
                "--handoff",
                "handoffs/other.md",
            ],
            cwd=root,
            check=False,
        )
        if result.returncode == 0 or "handoff must match lane handoff" not in result.stderr:
            raise AssertionError("recorder must reject mismatched handoff paths")

        invalid_status_run = root / "invalid-status"
        write_lane_map(invalid_status_run)
        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(invalid_status_run),
                "--lane-id",
                "worker-a",
                "--status",
                "done",
            ],
            cwd=root,
            check=False,
        )
        if result.returncode == 0 or "invalid choice" not in result.stderr:
            raise AssertionError("recorder must reject invalid statuses")

        invalid_mode_run = root / "invalid-mode"
        write_lane_map(invalid_mode_run)
        result = run(
            [
                sys.executable,
                str(RECORDER),
                "--run-dir",
                str(invalid_mode_run),
                "--lane-id",
                "worker-a",
                "--status",
                "queued",
                "--mode",
                "single",
            ],
            cwd=root,
            check=False,
        )
        if result.returncode == 0 or "invalid choice" not in result.stderr:
            raise AssertionError("recorder must reject invalid modes")

    print("PASS record-handoff-state fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
