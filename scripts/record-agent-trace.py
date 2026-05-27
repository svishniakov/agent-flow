#!/usr/bin/env python3
"""Record one subagent or role-lane event in Agent Flow traces."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def safe_path_segment(value: str) -> str:
    segment = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-")
    return segment[:80] or "agent"


def display_path(raw_path: str, run_dir: Path) -> str:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
        try:
            return resolved.relative_to(run_dir).as_posix()
        except ValueError:
            return resolved.as_posix()
    return path.as_posix()


def unique_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def load_artifact_index(path: Path) -> tuple[list[Any], dict[str, Any] | None]:
    if not path.exists():
        return [], None

    try:
        data = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"artifacts.json invalid JSON: {exc}") from exc

    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        artifacts = data.get("artifacts")
        if artifacts is None:
            artifacts = []
            data["artifacts"] = artifacts
        if not isinstance(artifacts, list):
            raise SystemExit("artifacts.json field 'artifacts' must be a JSON array")
        return artifacts, data

    raise SystemExit("artifacts.json must be a JSON array or an object with an artifacts array")


def write_artifact_index(path: Path, artifacts: list[Any], container: dict[str, Any] | None) -> None:
    output: list[Any] | dict[str, Any]
    if container is None:
        output = artifacts
    else:
        container["artifacts"] = artifacts
        output = container

    path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def upsert_artifacts(
    artifacts_path: Path,
    artifact_paths: list[str],
    *,
    role: str,
    execution_mode: str,
    stable_agent_name: str,
    stable_agent_slug: str,
    timestamp: str,
) -> int:
    if not artifact_paths:
        return 0

    artifacts, container = load_artifact_index(artifacts_path)
    indexed = 0

    for artifact_path in artifact_paths:
        entry = {
            "path": artifact_path,
            "role": role,
            "execution_mode": execution_mode,
            "stable_agent_name": stable_agent_name,
            "stable_agent_slug": stable_agent_slug,
            "source": "agent-trace",
            "timestamp": timestamp,
        }

        match_index = next(
            (
                index
                for index, item in enumerate(artifacts)
                if isinstance(item, dict)
                and item.get("role") == role
                and item.get("path") == artifact_path
            ),
            None,
        )

        if match_index is None:
            artifacts.append(entry)
        else:
            existing = artifacts[match_index]
            artifacts[match_index] = {**existing, **entry}
        indexed += 1

    write_artifact_index(artifacts_path, artifacts, container)
    return indexed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--next-step", default="")
    parser.add_argument("--stable-agent-name")
    parser.add_argument("--stable-agent-slug")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--execution-mode", choices=["subagent", "role-lane"], default="subagent")
    parser.add_argument("--codex-thread-id")
    parser.add_argument("--runtime-nickname")
    args = parser.parse_args()

    if args.execution_mode == "subagent" and args.stage == "spawned" and not args.codex_thread_id:
        raise SystemExit("spawned subagent events require --codex-thread-id")

    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    role_segment = safe_path_segment(args.role)
    stable_agent_name = args.stable_agent_name or args.role
    stable_agent_slug = args.stable_agent_slug or role_segment
    timestamp = datetime.now().astimezone().isoformat()

    timeline_path = run_dir / "timeline.jsonl"
    agent_dir = run_dir / "agents" / role_segment
    agent_trace_path = agent_dir / "trace.jsonl"
    agent_artifact_dir = run_dir / "artifacts" / "agents" / role_segment
    artifacts_path = run_dir / "artifacts.json"

    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_artifact_dir.mkdir(parents=True, exist_ok=True)

    artifact_paths = unique_paths([display_path(path, run_dir) for path in args.artifact])
    event = {
        "timestamp": timestamp,
        "stage": args.stage,
        "role": args.role,
        "stable_agent_name": stable_agent_name,
        "stable_agent_slug": stable_agent_slug,
        "status": args.status,
        "summary": args.summary,
        "artifacts": artifact_paths,
        "next_step": args.next_step,
        "execution_mode": args.execution_mode,
        "agent_trace": agent_trace_path.relative_to(run_dir).as_posix(),
        "agent_artifact_dir": agent_artifact_dir.relative_to(run_dir).as_posix(),
    }
    if args.codex_thread_id:
        event["codex_thread_id"] = args.codex_thread_id
    if args.runtime_nickname:
        event["runtime_nickname"] = args.runtime_nickname

    indexed_count = upsert_artifacts(
        artifacts_path,
        artifact_paths,
        role=args.role,
        execution_mode=args.execution_mode,
        stable_agent_name=stable_agent_name,
        stable_agent_slug=stable_agent_slug,
        timestamp=timestamp,
    )

    encoded_event = json.dumps(event, ensure_ascii=False)
    with timeline_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded_event + "\n")
    with agent_trace_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded_event + "\n")

    print(f"recorded timeline: {timeline_path}")
    print(f"recorded agent trace: {agent_trace_path}")
    print(f"agent artifact dir: {agent_artifact_dir}")
    print(f"indexed artifacts: {indexed_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
