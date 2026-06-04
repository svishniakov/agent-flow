#!/usr/bin/env python3
"""Validate Agent Flow traceable run completeness before final handoff."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
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
AGENT_EXECUTION_MODES = {"subagent", "role-lane"}
IMPLEMENTATION_STAGES = {"implementation", "fix"}
SUCCESSFUL_CHECK_STAGES = {"verification", "checks"}
SUCCESS_STATUSES = {"pass", "ship", "pass-with-risks"}
COMMIT_HASH_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
COMMIT_DECLARATION_PATTERN = re.compile(
    r"(?i)(^\s*(?:[-*]\s*)?(?:product\s+)?commit\s*:|committed\s+as)"
)


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


def parse_event_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def event_key(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_execution_mode(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("_", "-")


def extract_declared_commit_hashes(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []

    hashes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not COMMIT_DECLARATION_PATTERN.search(line):
            continue
        hashes.extend(match.group(0).lower() for match in COMMIT_HASH_PATTERN.finditer(line))
    return list(dict.fromkeys(hashes))


def event_commit_hashes(event: dict) -> list[str]:
    values: list[str] = []
    commit_hash = event.get("commit_hash")
    if isinstance(commit_hash, str):
        values.append(commit_hash)
    for field in ["summary", "next_step"]:
        value = event.get(field)
        if isinstance(value, str):
            values.extend(match.group(0) for match in COMMIT_HASH_PATTERN.finditer(value))
    return [value.lower() for value in values]


def hashes_match(left: str, right: str) -> bool:
    return left.startswith(right) or right.startswith(left)


def validate_final_timeline_event(run_dir: Path, require_timeline: bool) -> list[str]:
    timeline_path = run_dir / "timeline.jsonl"
    if not timeline_path.exists():
        return ["missing timeline.jsonl for final timeline event"] if require_timeline else []

    timeline_events, timeline_load_errors = load_jsonl_events(timeline_path)
    if timeline_load_errors:
        return []
    if not timeline_events:
        return ["timeline.jsonl: no events"]

    errors: list[str] = []
    final_events = [event for event in timeline_events if event.get("stage") == "final"]
    if not final_events:
        errors.append("timeline.jsonl: missing final event")
    elif len(final_events) > 1:
        errors.append("timeline.jsonl: multiple final events")

    last_event = timeline_events[-1]
    if last_event.get("stage") != "final":
        errors.append("timeline.jsonl: last event must be stage=final")
    if last_event.get("role") != "orchestrator":
        errors.append("timeline.jsonl: final event must be owned by role=orchestrator")
    return errors


def validate_timeline_sequence(run_dir: Path) -> list[str]:
    timeline_path = run_dir / "timeline.jsonl"
    if not timeline_path.exists():
        return []

    timeline_events, timeline_load_errors = load_jsonl_events(timeline_path)
    if timeline_load_errors or not timeline_events:
        return []

    errors: list[str] = []
    previous_timestamp: datetime | None = None
    for index, event in enumerate(timeline_events, start=1):
        timestamp = parse_event_timestamp(event.get("timestamp"))
        if timestamp is None:
            errors.append(f"timeline.jsonl:{index}: invalid timestamp")
            continue
        if previous_timestamp is not None and timestamp < previous_timestamp:
            errors.append(f"timeline.jsonl:{index}: timestamp must be non-decreasing")
        previous_timestamp = timestamp

    last_implementation_index = max(
        (
            index
            for index, event in enumerate(timeline_events)
            if event.get("role") == "orchestrator" and event.get("stage") in IMPLEMENTATION_STAGES
        ),
        default=None,
    )
    last_successful_check_index = max(
        (
            index
            for index, event in enumerate(timeline_events)
            if event.get("role") == "orchestrator"
            and event.get("stage") in SUCCESSFUL_CHECK_STAGES
            and event.get("status") in SUCCESS_STATUSES
        ),
        default=None,
    )

    if (
        last_implementation_index is not None
        and last_successful_check_index is not None
        and last_successful_check_index < last_implementation_index
    ):
        errors.append(
            "timeline.jsonl: final successful verification/checks event must come after "
            "the last orchestrator implementation/fix event"
        )

    commit_indexes = [
        index
        for index, event in enumerate(timeline_events)
        if event.get("role") == "orchestrator" and event.get("stage") == "commit"
    ]
    final_indexes = [
        index
        for index, event in enumerate(timeline_events)
        if event.get("role") == "orchestrator" and event.get("stage") == "final"
    ]
    if commit_indexes:
        last_commit_index = max(commit_indexes)
        if last_successful_check_index is not None and last_commit_index < last_successful_check_index:
            errors.append(
                "timeline.jsonl: orchestrator commit event must come after the "
                "last successful verification/checks event"
            )
        if final_indexes and last_commit_index > max(final_indexes):
            errors.append("timeline.jsonl: orchestrator commit event must come before final event")

    declared_commit_hashes = extract_declared_commit_hashes(run_dir / "final.md")
    if declared_commit_hashes:
        commit_events = [
            event
            for event in timeline_events
            if event.get("role") == "orchestrator" and event.get("stage") == "commit"
        ]
        if not commit_events:
            errors.append("timeline.jsonl: final.md declares a commit but no orchestrator stage=commit event exists")
        else:
            commit_event_hashes = [
                commit_hash
                for event in commit_events
                for commit_hash in event_commit_hashes(event)
            ]
            for declared_hash in declared_commit_hashes:
                if not any(hashes_match(declared_hash, event_hash) for event_hash in commit_event_hashes):
                    errors.append(
                        f"timeline.jsonl: final.md declares commit {declared_hash} "
                        "but no matching stage=commit event records it"
                    )

    return errors


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
    else:
        errors.extend(f"agents require timeline.jsonl: {error}" for error in timeline_load_errors)

    for agent_dir in sorted(agents_dir.iterdir()):
        display_dir = agent_dir.relative_to(run_dir).as_posix()
        if not agent_dir.is_dir():
            errors.append(f"{display_dir} is not a directory")
            continue

        trace_path = agent_dir / "trace.jsonl"
        display_name = trace_path.relative_to(run_dir).as_posix()
        errors.extend(validate_jsonl(trace_path, display_name))
        trace_events, trace_load_errors = load_jsonl_events(trace_path)
        if trace_load_errors:
            continue

        execution_modes = {
            normalize_execution_mode(event.get("execution_mode"))
            for event in trace_events
            if event.get("execution_mode") is not None
        }
        invalid_modes = sorted(mode for mode in execution_modes if mode not in AGENT_EXECUTION_MODES)
        if invalid_modes:
            errors.append(f"{display_name}: invalid execution_mode values: {', '.join(invalid_modes)}")

        is_role_lane = execution_modes == {"role-lane"}
        if not is_role_lane:
            spawned_events = [event for event in trace_events if event.get("stage") == "spawned"]
            if not spawned_events:
                errors.append(
                    f"{display_name}: missing spawned event with codex_thread_id; "
                    "use execution_mode=role-lane for non-spawned role lanes"
                )
            elif not any(event.get("codex_thread_id") for event in spawned_events):
                errors.append(f"{display_name}: spawned event missing codex_thread_id")

        if timeline_load_errors:
            continue
        for index, event in enumerate(trace_events, start=1):
            if event_key(event) not in timeline_event_keys:
                errors.append(f"{display_name}:{index}: event missing from timeline.jsonl")
    return errors


def read_fields(path: Path, name: str) -> list[str]:
    prefix = f"{name}:"
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            values.append(line[len(prefix) :].strip())
    return values


def validate_verdict(path: Path) -> list[str]:
    errors: list[str] = []
    verdicts = read_fields(path, "Verdict")
    if not verdicts:
        errors.append(f"{path.name}: missing Verdict field")
    elif len(verdicts) > 1:
        errors.append(f"{path.name}: multiple Verdict fields")
    elif verdicts[0] not in FINAL_VERDICTS:
        verdict = verdicts[0]
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

    has_timeline = (run_dir / "timeline.jsonl").exists()
    has_agents = (run_dir / "agents").exists()
    if has_timeline or has_agents:
        errors.extend(validate_jsonl(run_dir / "timeline.jsonl"))
        errors.extend(validate_timeline_sequence(run_dir))
        errors.extend(validate_agent_traces(run_dir))
        if not allow_pending:
            errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))

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
    errors.extend(validate_timeline_sequence(run_dir))
    errors.extend(validate_agent_traces(run_dir))
    if not allow_pending:
        errors.extend(validate_final_timeline_event(run_dir, require_timeline=True))

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
