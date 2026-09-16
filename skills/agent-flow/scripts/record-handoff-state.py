#!/usr/bin/env python3
"""Record Handoff State Gate lifecycle state in lane-map.json.

record-handoff-state.py supports lane maps with handoff_state_required=true.
It records queued, accepted, completed, blocked, or failed handoff_state for
one lane. It does not write timeline events.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from journal_io import now_iso, JournalError, operation_id, transact, encode_json
from journal_io import HANDOFF_STATE_MODES, HANDOFF_STATE_STATUSES, update_handoff_state


def load_lane_map(path: Path, *, snapshot=None) -> dict[str, Any]:
    try:
        data = json.loads(snapshot.read_text(path) if snapshot else path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"lane-map.json not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lane-map.json is invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("lane-map.json must be a JSON object")
    lanes = data.get("lanes")
    if not isinstance(lanes, list):
        raise RuntimeError("lane-map.json must contain a lanes array")
    return data


def find_lane(lanes: list[Any], lane_id: str) -> dict[str, Any]:
    for lane in lanes:
        if isinstance(lane, dict) and lane.get("id") == lane_id:
            return lane
    raise RuntimeError(f"unknown lane id: {lane_id}")


def record_handoff_state(
    *,
    run_dir: Path,
    lane_id: str,
    status: str,
    mode: str | None,
    from_lane: str | None,
    to_lane: str | None,
    task: str | None,
    handoff: str | None,
    batch_id: str | None,
    batch_items: list[str],
    identifier: str | None = None,
) -> Path:
    lane_map_path = run_dir.resolve() / "lane-map.json"
    payload = dict(lane_id=lane_id, status=status, mode=mode, from_lane=from_lane,
                   to_lane=to_lane, task=task, handoff=handoff, batch_id=batch_id, batch_items=batch_items)
    identifier = operation_id(run_dir, "handoff-state", payload, identifier=identifier)
    def mutation(snapshot):
        if snapshot.exists("timeline.jsonl"):
            events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines() if line.strip()]
            if snapshot.closed:
                raise JournalError("timeline already has final event")
        data = load_lane_map(lane_map_path, snapshot=snapshot)
        lane = find_lane(data["lanes"], lane_id)
        expected = {"pass": "completed", "pass-with-risks": "completed", "fail": "failed", "blocked": "blocked"}.get(lane.get("status"))
        if expected is not None and status != expected:
            raise JournalError(f"terminal lane status {lane['status']} requires handoff state {expected}")
        lane["handoff_state"] = update_handoff_state(lane=lane, **payload)
        return {"lane-map.json": encode_json(data, pretty=True) + "\n"}, {}
    transact(run_dir, identifier, payload, mutation)
    return lane_map_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Traceable run directory.")
    parser.add_argument("--lane-id", required=True, help="Lane id in lane-map.json.")
    parser.add_argument("--operation-id", help="Saved machine request ID for retry.")
    parser.add_argument("--status", required=True, choices=sorted(HANDOFF_STATE_STATUSES))
    parser.add_argument("--mode", choices=sorted(HANDOFF_STATE_MODES), help="Defaults to task.")
    parser.add_argument("--from", dest="from_lane", help="Source lane or role id.")
    parser.add_argument("--to", dest="to_lane", help="Target lane or role id. Defaults to lane id.")
    parser.add_argument("--task", help="Task identifier carried by this handoff.")
    parser.add_argument("--handoff", help="Handoff markdown path. Must match lane handoff.")
    parser.add_argument("--batch-id", help="Batch id for mode=batch.")
    parser.add_argument(
        "--batch-item",
        dest="batch_items",
        action="append",
        default=[],
        help="Referenced lane id for mode=batch. May be repeated.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_path = record_handoff_state(
            run_dir=Path(args.run_dir),
            lane_id=args.lane_id,
            status=args.status,
            mode=args.mode,
            from_lane=args.from_lane,
            to_lane=args.to_lane,
            task=args.task,
            handoff=args.handoff,
            batch_id=args.batch_id,
            batch_items=args.batch_items,
            identifier=args.operation_id,
        )
    except (RuntimeError, JournalError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"updated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
