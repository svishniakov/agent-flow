#!/usr/bin/env python3
"""Validate Agent Flow traceable run completeness before final handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


FULL_REQUIRED_FILES = [
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

FULL_REQUIRED_DIRS = ["handoffs", "checks", "artifacts"]
COMPACT_REQUIRED_FILES = ["run.md", "checks.md", "final.md"]
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


def detect_mode(run_dir: Path, requested_mode: str) -> str:
    if requested_mode != "auto":
        return requested_mode

    if (run_dir / "run.md").exists() and not (run_dir / "manifest.md").exists():
        return "compact"
    return "full"


def is_empty_file(path: Path) -> bool:
    return not path.read_text(encoding="utf-8").strip()


def validate_artifacts_index(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return errors

    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError as exc:
        return [f"artifacts.json invalid JSON: {exc}"]

    if isinstance(data, list):
        return errors
    if isinstance(data, dict):
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append("artifacts.json field 'artifacts' must be a JSON array")
        return errors

    errors.append("artifacts.json must be a JSON array or an object with an artifacts array")
    return errors


def validate_jsonl(path: Path, display_name: str | None = None) -> list[str]:
    errors: list[str] = []
    label = display_name or path.name
    if not path.exists():
        return [f"missing {label}"]
    has_event = False
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        has_event = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{label}:{index}: invalid JSON: {exc}")
            continue

        if not isinstance(event, dict):
            errors.append(f"{label}:{index}: event must be a JSON object")
            continue

        missing = REQUIRED_TIMELINE_KEYS - event.keys()
        if missing:
            errors.append(f"{label}:{index}: missing keys: {', '.join(sorted(missing))}")
        if "artifacts" in event and not isinstance(event["artifacts"], list):
            errors.append(f"{label}:{index}: artifacts must be a JSON array")
    if not has_event:
        errors.append(f"{label}: no events")
    return errors


def load_jsonl_events(path: Path) -> tuple[list[dict], list[str]]:
    events: list[dict] = []
    errors: list[str] = []
    if not path.exists():
        return events, [f"missing {path.name}"]

    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{index}: invalid JSON: {exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"{path.name}:{index}: event must be a JSON object")
            continue
        events.append(event)
    return events, errors


def event_key(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_agent_traces(run_dir: Path) -> list[str]:
    errors: list[str] = []
    agents_dir = run_dir / "agents"
    if not agents_dir.exists():
        return errors
    if not agents_dir.is_dir():
        return ["agents exists but is not a directory"]

    timeline_events, timeline_load_errors = load_jsonl_events(run_dir / "timeline.jsonl")
    timeline_event_keys = set()
    if not timeline_load_errors:
        timeline_event_keys = {event_key(event) for event in timeline_events}

    for agent_dir in sorted(agents_dir.iterdir()):
        display_dir = agent_dir.relative_to(run_dir).as_posix()
        if not agent_dir.is_dir():
            errors.append(f"{display_dir} is not a directory")
            continue

        trace_path = agent_dir / "trace.jsonl"
        display_name = trace_path.relative_to(run_dir).as_posix()
        errors.extend(validate_jsonl(trace_path, display_name))
        trace_events, _trace_load_errors = load_jsonl_events(trace_path)
        if timeline_load_errors:
            continue
        for index, event in enumerate(trace_events, start=1):
            if event_key(event) not in timeline_event_keys:
                errors.append(f"{display_name}:{index}: event missing from timeline.jsonl")
    return errors


def read_field(path: Path, name: str) -> str | None:
    prefix = f"{name}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def validate_verdict(path: Path) -> list[str]:
    errors: list[str] = []
    verdict = read_field(path, "Verdict")
    if verdict is None:
        errors.append(f"{path.name}: missing Verdict field")
    elif verdict not in FINAL_VERDICTS:
        allowed = ", ".join(sorted(FINAL_VERDICTS))
        errors.append(f"{path.name}: invalid Verdict '{verdict}' (expected one of: {allowed})")
    return errors


def validate_compact_run(run_dir: Path, allow_no_check: bool, allow_pending: bool) -> list[str]:
    errors: list[str] = []

    for name in COMPACT_REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")
            continue
        if not path.is_file():
            errors.append(f"{name} exists but is not a file")
            continue
        if is_empty_file(path) and not (name == "checks.md" and allow_no_check):
            errors.append(f"{name}: empty file")

    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.exists() and not artifacts_dir.is_dir():
        errors.append("artifacts exists but is not a directory")

    final_path = run_dir / "final.md"
    if not allow_pending and final_path.exists() and final_path.is_file():
        errors.extend(validate_verdict(final_path))

    return errors


def validate_full_run(
    run_dir: Path,
    require_handoff: bool,
    allow_no_check: bool,
    allow_pending: bool,
) -> list[str]:
    errors: list[str] = []

    for name in FULL_REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing file: {name}")

    for name in FULL_REQUIRED_DIRS:
        path = run_dir / name
        if not path.is_dir():
            errors.append(f"missing dir: {name}")

    errors.extend(validate_artifacts_index(run_dir / "artifacts.json"))

    errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))
    errors.extend(validate_agent_traces(run_dir))

    if require_handoff and not list((run_dir / "handoffs").glob("*.md")):
        errors.append("no handoff markdown files")

    if not allow_no_check and not list((run_dir / "checks").glob("*.md")):
        errors.append("no check markdown files")

    if not allow_pending:
        for name in ["manifest.md", "final.md"]:
            path = run_dir / name
            if not path.exists():
                continue
            errors.extend(validate_verdict(path))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--mode", choices=["auto", "compact", "full"], default="auto")
    parser.add_argument("--require-handoff", action="store_true")
    parser.add_argument("--allow-no-check", action="store_true")
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()

    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    mode = detect_mode(run_dir, args.mode)
    if mode == "compact":
        errors = validate_compact_run(run_dir, args.allow_no_check, args.allow_pending)
    else:
        errors = validate_full_run(
            run_dir,
            args.require_handoff,
            args.allow_no_check,
            args.allow_pending,
        )

    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1

    print(f"PASS {run_dir} ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
