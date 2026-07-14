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
from datetime import datetime
from pathlib import Path
from typing import Any


HANDOFF_STATE_MODES = {"task", "batch"}
HANDOFF_STATE_STATUSES = {"queued", "accepted", "completed", "blocked", "failed"}
STATUS_TIMESTAMP_FIELDS = {
    "queued": "queued_at",
    "accepted": "accepted_at",
    "completed": "completed_at",
    "blocked": "completed_at",
    "failed": "completed_at",
}


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def load_lane_map(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def validate_mode(mode: str) -> None:
    if mode not in HANDOFF_STATE_MODES:
        allowed = ", ".join(sorted(HANDOFF_STATE_MODES))
        raise RuntimeError(f"invalid handoff_state mode {mode!r}; expected one of: {allowed}")


def validate_status(status: str) -> None:
    if status not in HANDOFF_STATE_STATUSES:
        allowed = ", ".join(sorted(HANDOFF_STATE_STATUSES))
        raise RuntimeError(f"invalid handoff_state status {status!r}; expected one of: {allowed}")


def normalize_existing_state(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError("existing handoff_state must be a JSON object")
    return dict(value)


def update_handoff_state(
    *,
    lane: dict[str, Any],
    lane_id: str,
    status: str,
    mode: str | None,
    from_lane: str | None,
    to_lane: str | None,
    task: str | None,
    handoff: str | None,
    batch_id: str | None,
    batch_items: list[str],
) -> dict[str, Any]:
    validate_status(status)

    lane_handoff = lane.get("handoff")
    if handoff and isinstance(lane_handoff, str) and lane_handoff and handoff != lane_handoff:
        raise RuntimeError(
            f"handoff must match lane handoff: expected {lane_handoff!r}, got {handoff!r}"
        )

    state = normalize_existing_state(lane.get("handoff_state"))
    selected_mode = mode or state.get("mode") or ("batch" if batch_id or batch_items else "task")
    if not isinstance(selected_mode, str):
        raise RuntimeError("handoff_state.mode must be a string")
    validate_mode(selected_mode)

    if selected_mode == "batch":
        existing_batch = state.get("batch") if isinstance(state.get("batch"), dict) else {}
        selected_batch_id = batch_id or existing_batch.get("id")
        existing_items = existing_batch.get("items", [])
        if not isinstance(existing_items, list):
            raise RuntimeError("existing handoff_state.batch.items must be an array")
        selected_items = [*existing_items, *batch_items]
        if not all(isinstance(item, str) and item for item in selected_items):
            raise RuntimeError("handoff_state.batch.items must contain only non-empty strings")
        selected_items = list(dict.fromkeys(selected_items))
        if not isinstance(selected_batch_id, str) or not selected_batch_id:
            raise RuntimeError("batch mode requires --batch-id or existing handoff_state.batch.id")
        if not selected_items:
            raise RuntimeError("batch mode requires at least one --batch-item or existing batch item")
        state["batch"] = {"id": selected_batch_id, "items": selected_items}
    elif batch_id or batch_items:
        raise RuntimeError("--batch-id and --batch-item require --mode batch")

    selected_handoff = handoff
    if selected_handoff is None and isinstance(lane_handoff, str):
        selected_handoff = lane_handoff
    if selected_handoff is None:
        selected_handoff = state.get("handoff")
    if not isinstance(selected_handoff, str) or not selected_handoff:
        raise RuntimeError("handoff_state.handoff is required; set lane handoff or pass --handoff")
    if isinstance(lane_handoff, str) and lane_handoff and selected_handoff != lane_handoff:
        raise RuntimeError(
            f"handoff_state.handoff must match lane handoff: expected {lane_handoff!r}, got {selected_handoff!r}"
        )

    state["version"] = 1
    state["mode"] = selected_mode
    state["status"] = status
    state["to"] = to_lane or state.get("to") or lane_id
    state["handoff"] = selected_handoff

    if from_lane:
        state["from"] = from_lane
    if task:
        state["task"] = task

    timestamp_field = STATUS_TIMESTAMP_FIELDS[status]
    if not state.get(timestamp_field):
        state[timestamp_field] = now_iso()

    return state


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
) -> Path:
    lane_map_path = run_dir.resolve() / "lane-map.json"
    data = load_lane_map(lane_map_path)
    lane = find_lane(data["lanes"], lane_id)
    lane["handoff_state"] = update_handoff_state(
        lane=lane,
        lane_id=lane_id,
        status=status,
        mode=mode,
        from_lane=from_lane,
        to_lane=to_lane,
        task=task,
        handoff=handoff,
        batch_id=batch_id,
        batch_items=batch_items,
    )
    lane_map_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lane_map_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Traceable run directory.")
    parser.add_argument("--lane-id", required=True, help="Lane id in lane-map.json.")
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
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"updated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
