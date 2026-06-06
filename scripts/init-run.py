#!/usr/bin/env python3
"""Create an Agent Flow traceable run skeleton."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


RUN_FILES = {
    "manifest.md": "# Manifest\n\nStatus: active\nVerdict: pending\n\n",
    "context.md": "# Context\n\n## Initial Worktree Snapshot\n\n",
    "route.md": "# Route\n\n",
    "plan.md": "# Plan\n\n",
    "definition-of-done.md": "# Definition Of Done\n\n",
    "decisions.md": "# Decisions\n\n",
    "final.md": "# Final\n\nVerdict: pending\n\n## Worktree Hygiene\n\n",
}
LANE_MAP = {
    "schema_version": 1,
    "lanes": [],
}
COVERAGE_MATRIX = """# Coverage Matrix

Use this file as the human-readable coverage summary for Lane Sharding runs.
The machine-readable source of truth is `lane-map.json`.

| Lane | Acceptance Area | Evidence | Status | Notes |
| --- | --- | --- | --- | --- |
"""


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "agent-flow-run"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Project repo path.")
    parser.add_argument("--slug", required=True, help="Short task slug.")
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to local date.")
    parser.add_argument("--reuse", action="store_true", help="Reuse an existing run directory.")
    parser.add_argument("--with-lanes", action="store_true", help="Create Lane Sharding skeleton artifacts.")
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    date = args.date or datetime.now().astimezone().strftime("%Y-%m-%d")
    run_dir = repo / ".agent-work" / "runs" / f"{date}-{slugify(args.slug)}"

    if run_dir.exists() and not args.reuse:
        raise SystemExit(f"run dir already exists: {run_dir} (use --reuse or choose another slug/date)")

    (run_dir / "handoffs").mkdir(parents=True, exist_ok=True)
    (run_dir / "checks").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    for name, content in RUN_FILES.items():
        path = run_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")

    artifacts = run_dir / "artifacts.json"
    if not artifacts.exists():
        artifacts.write_text("[]\n", encoding="utf-8")

    if args.with_lanes:
        lane_map = run_dir / "lane-map.json"
        if not lane_map.exists():
            lane_map.write_text(json.dumps(LANE_MAP, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        coverage_matrix = run_dir / "checks" / "coverage-matrix.md"
        if not coverage_matrix.exists():
            coverage_matrix.write_text(COVERAGE_MATRIX, encoding="utf-8")

    timeline = run_dir / "timeline.jsonl"
    if not timeline.exists():
        event = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "stage": "intake",
            "role": "orchestrator",
            "stable_agent_name": "orchestrator",
            "stable_agent_slug": "orchestrator",
            "status": "active",
            "summary": "Traceable run initialized.",
            "artifacts": [],
            "next_step": "route",
        }
        timeline.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")

    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
