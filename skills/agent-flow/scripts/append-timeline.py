#!/usr/bin/env python3
"""Append one JSONL event to an Agent Flow timeline."""

from __future__ import annotations

import argparse
from pathlib import Path
from journal_io import now_iso, append_event, JournalError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--next-step", default="")
    parser.add_argument("--stable-agent-name")
    parser.add_argument("--stable-agent-slug")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--commit-hash")
    parser.add_argument("--operation-id", help="Saved machine request ID for retry.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    timeline = run_dir / "timeline.jsonl"
    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    event = {
        "timestamp": now_iso(),
        "stage": args.stage,
        "role": args.role,
        "stable_agent_name": args.stable_agent_name or args.role,
        "stable_agent_slug": args.stable_agent_slug or args.role,
        "status": args.status,
        "summary": args.summary,
        "artifacts": args.artifact,
        "next_step": args.next_step,
    }
    if args.commit_hash:
        event["commit_hash"] = args.commit_hash

    try:
        receipt = append_event(timeline, event, identifier=args.operation_id, assign_timestamp=True)
    except (JournalError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    print(f"appended: {timeline} (revision {receipt['revision']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
