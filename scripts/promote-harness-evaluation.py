#!/usr/bin/env python3
"""Promote validated Harness Evaluation findings into project Evidence Records."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness_promotion import PromotionError, promote_harness_evaluation


DEFAULT_NOTES = Path(".agent-work/tasks/implementation-notes.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Traceable run directory.")
    parser.add_argument("--notes", type=Path, default=DEFAULT_NOTES, help="Project implementation notes file.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report records without writing notes.")
    args = parser.parse_args()

    try:
        result = promote_harness_evaluation(args.run_dir, args.notes, dry_run=args.dry_run)
    except PromotionError as error:
        print(str(error), file=sys.stderr)
        return 1

    action = "would promote" if result.dry_run else "promoted"
    print(f"{action} {result.promoted} Evidence Records; skipped {result.skipped}")
    for record_id in result.record_ids:
        print(f"- {record_id}")
    for group in result.preview_groups:
        gate = group.get("auto_gate", {})
        blockers = gate.get("blockers", [])
        print(
            "preview "
            f"{group.get('state')}: "
            f"{group.get('problem_class')} / {group.get('approach')} "
            f"(helpful={group.get('helpful', 0)}, harmful={group.get('harmful', 0)}, "
            f"neutral={group.get('neutral', 0)}, auto_gate={gate.get('allowed', False)})"
        )
        if blockers:
            print(f"  blockers: {', '.join(blockers)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
