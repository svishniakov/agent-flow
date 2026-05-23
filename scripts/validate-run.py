#!/usr/bin/env python3
"""Validate Agent Flow traceable run completeness before final handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_FILES = [
    "manifest.md",
    "context.md",
    "route.md",
    "plan.md",
    "definition-of-done.md",
    "decisions.md",
    "artifacts.json",
    "timeline.jsonl",
    "final.md",
]

REQUIRED_DIRS = ["handoffs", "checks", "artifacts"]
REQUIRED_TIMELINE_KEYS = {
    "timestamp",
    "stage",
    "role",
    "stable_agent_name",
    "stable_agent_slug",
    "status",
    "summary",
    "artifacts",
    "next_step",
}
FINAL_VERDICTS = {"ship", "pass-with-risks", "blocked", "fail"}


def validate_jsonl(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing {path.name}"]
    has_event = False
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        has_event = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index}: invalid JSON: {exc}")
            continue

        if not isinstance(event, dict):
            errors.append(f"{path.name}:{index}: event must be a JSON object")
            continue

        missing = REQUIRED_TIMELINE_KEYS - event.keys()
        if missing:
            errors.append(f"{path.name}:{index}: missing keys: {', '.join(sorted(missing))}")
        if "artifacts" in event and not isinstance(event["artifacts"], list):
            errors.append(f"{path.name}:{index}: artifacts must be a JSON array")
    if not has_event:
        errors.append(f"{path.name}: no events")
    return errors


def read_field(path: Path, name: str) -> str | None:
    prefix = f"{name}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--require-handoff", action="store_true")
    parser.add_argument("--allow-no-check", action="store_true")
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    errors: list[str] = []

    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    for name in REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")

    for name in REQUIRED_DIRS:
        path = run_dir / name
        if not path.is_dir():
            errors.append(f"missing dir: {name}")

    artifacts = run_dir / "artifacts.json"
    if artifacts.exists():
        try:
            data = json.loads(artifacts.read_text(encoding="utf-8") or "[]")
            if not isinstance(data, list):
                errors.append("artifacts.json must be a JSON array")
        except json.JSONDecodeError as exc:
            errors.append(f"artifacts.json invalid JSON: {exc}")

    errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))

    if args.require_handoff and not list((run_dir / "handoffs").glob("*.md")):
        errors.append("no handoff markdown files")

    if not args.allow_no_check and not list((run_dir / "checks").glob("*.md")):
        errors.append("no check markdown files")

    if not args.allow_pending:
        for name in ["manifest.md", "final.md"]:
            path = run_dir / name
            if not path.exists():
                continue
            verdict = read_field(path, "Verdict")
            if verdict is None:
                errors.append(f"{name}: missing Verdict field")
            elif verdict not in FINAL_VERDICTS:
                allowed = ", ".join(sorted(FINAL_VERDICTS))
                errors.append(f"{name}: invalid Verdict '{verdict}' (expected one of: {allowed})")

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1

    print(f"PASS {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
