#!/usr/bin/env python3
"""Analyze Agent Flow Evidence Records from implementation notes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evidence_records import analyze_file


DEFAULT_NOTES = Path(".agent-work/tasks/implementation-notes.md")


def print_human(report: dict) -> None:
    summary = report.get("summary", {})
    print(f"Evidence Records: {summary.get('records', 0)} records, {summary.get('groups', 0)} groups")
    for error in report.get("errors", []):
        print(f"ERROR {error}")
    for group in report.get("groups", []):
        print(
            f"- {group['state']}: {group['problem_class']} / {group['approach']} "
            f"(success={group['success']}, failure={group['failure']}, "
            f"regression={group['regression']}, rejected={group['rejected']}, unknown={group['unknown']})"
        )
        blockers = group.get("auto_gate", {}).get("blockers", [])
        if blockers:
            print(f"  auto-gate blocked: {', '.join(blockers)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", type=Path, default=DEFAULT_NOTES)
    parser.add_argument("--json", action="store_true", help="Print machine-readable report.")
    parser.add_argument("--fail-on-invalid", action="store_true", help="Fail on missing file or invalid records.")
    parser.add_argument("--min-successes", type=int, default=3, help="Success count needed for Local Best Practice.")
    args = parser.parse_args()

    if args.min_successes < 1:
        print("--min-successes must be positive", file=sys.stderr)
        return 1

    report, status = analyze_file(
        args.notes.expanduser(),
        min_successes=args.min_successes,
        fail_on_invalid=args.fail_on_invalid,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)

    if status and args.fail_on_invalid:
        for error in report.get("errors", []):
            print(error, file=sys.stderr)
    return status if args.fail_on_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
