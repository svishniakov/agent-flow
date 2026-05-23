#!/usr/bin/env python3
"""Append one JSONL event to an Agent Flow timeline."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


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
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    timeline = run_dir / "timeline.jsonl"
    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    event = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "stage": args.stage,
        "role": args.role,
        "stable_agent_name": args.stable_agent_name or args.role,
        "stable_agent_slug": args.stable_agent_slug or args.role,
        "status": args.status,
        "summary": args.summary,
        "artifacts": args.artifact,
        "next_step": args.next_step,
    }

    with timeline.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    print(f"appended: {timeline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
