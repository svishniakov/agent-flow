#!/usr/bin/env python3
"""Record one subagent or role-lane event in Agent Flow traces.

For a real subagent, record `stage=spawned` with `--codex-thread-id`, then a
terminal handoff/blocked/fail event. Successful subagent lanes need a terminal
handoff event that references the lane handoff artifact.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_config import AgentConfigError

from verification_evidence import (
    CodexSessionSource, EvidenceError, completed_turn, empty_verification,
    evidence_path, reference_bytes, result_hash, sha256, validate_verification,
    resolve_completion, completion_follows, validate_summary_shape,
)


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
    lane_id: str | None,
    wave: int | None,
    critical: bool,
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
        if lane_id:
            entry["lane_id"] = lane_id
        if wave is not None:
            entry["wave"] = wave
        if critical:
            entry["critical"] = critical

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


def prepare_summary(run_dir: Path, args, artifact_paths: list[str], source) -> tuple[dict | None, dict]:
    """Validate everything before writing any trace, directory or index."""
    if not (args.verification_json or args.completion_turn_id or args.lane_id or args.resolve_session):
        return None, {}
    path = run_dir / "delegation-summary.json"
    data = json.loads(path.read_text()) if path.exists() else {
        "version": 1, "subagents_used": False, "role_lanes_used": False,
        "subagents": [], "role_lanes": [], "notes": "Recorded agent assignments.",
        "verification": empty_verification(),
    }
    validate_summary_shape(data)
    if args.verification_json:
        raw = args.verification_json
        if not raw.lstrip().startswith("{"):
            raw = Path(raw).expanduser().read_text()
        verification = json.loads(raw)
        if not isinstance(verification, dict):
            raise EvidenceError("--verification-json must contain the verification object")
        data["verification"] = verification
    verification = data.get("verification")
    if not isinstance(verification, dict):
        raise EvidenceError("verification must be an object")
    for field in ("initial_snapshot", "task_scope"):
        ref = verification.get(field)
        if isinstance(ref, dict) and "sha256" not in ref:
            ref["sha256"] = sha256(reference_bytes(run_dir, ref))
    if verification.get("task_kind") == "change":
        verification["result_hash"] = result_hash(run_dir, verification)
        selected = [r for r in data["subagents"] if r.get("lane_id") in (verification.get("qa"), verification.get("reviewer"))]
        if any(r.get("reviewed_result_hash") != verification["result_hash"] for r in selected):
            verification["qa"] = verification["reviewer"] = None

    extra = {}
    if args.execution_mode == "subagent" and args.lane_id:
        existing = next((r for r in data["subagents"] if r.get("lane_id") == args.lane_id), None)
        record = dict(existing or {})
        thread_id = args.codex_thread_id or record.get("codex_thread_id")
        role = "reviewer" if args.role == "reviewer.qa" else args.role
        if args.resolve_session:
            extra.update(source.resolve_session(args.agent_path, verification.get("root_thread_id"), role,
                                                thread_id=thread_id))
            thread_id = extra["codex_thread_id"]
            args.codex_thread_id = thread_id
            record.update({k: extra[k] for k in ("agent_path", "observed_spawn_at", "session_meta_event", "task_started_event")})
        if not thread_id:
            raise EvidenceError("subagent assignment requires --codex-thread-id")
        if existing and (existing.get("codex_thread_id") != thread_id or existing.get("role") != args.role):
            raise EvidenceError("assignment identity changed; use a new lane_id")
        record.update(lane_id=args.lane_id, role=args.role, codex_thread_id=thread_id,
                      trace=f"agents/{safe_path_segment(args.role)}/trace.jsonl")
        if args.role == "qa-verifier" and args.stage in {"spawned", "handoff"}:
            verification["qa"] = verification["reviewer"] = None
        elif args.role in {"reviewer", "reviewer.qa"} and args.stage == "spawned":
            verification["reviewer"] = None
        if args.resolve_session and args.stage == "handoff" and not args.completion_turn_id:
            args.completion_turn_id = resolve_completion(source, thread_id, verification.get("root_thread_id"),
                                                         role, verification.get("result_hash"), artifact_paths)
        if args.completion_turn_id:
            if args.stage != "handoff":
                raise EvidenceError("--completion-turn-id requires stage=handoff")
            role = "reviewer" if args.role == "reviewer.qa" else args.role
            if role not in {"qa-verifier", "reviewer"}:
                raise EvidenceError("completion evidence is limited to QA and reviewer")
            completion = completed_turn(source, thread_id, args.completion_turn_id, verification.get("root_thread_id"), role)
            answer = completion["answer"]
            if not isinstance(answer.get("verdict"), str) or answer["verdict"] not in {"passed", "pass-with-risks"} or args.status not in {"pass", "pass-with-risks"}:
                raise EvidenceError("successful handoff requires accepted source verdict and status")
            handoff = answer.get("handoff")
            if handoff not in artifact_paths:
                raise EvidenceError("source handoff must be included in --artifact; pass its exact path relative to the run directory")
            if answer.get("reviewed_result_hash") != verification.get("result_hash"):
                raise EvidenceError("source reviewed_result_hash does not match current result; recompute with --verification-json and obtain acceptance of that current hash")
            if sha256(evidence_path(run_dir, handoff).read_bytes()) != answer.get("handoff_sha256"):
                raise EvidenceError("source handoff_sha256 mismatch")
            extra.update({k: answer[k] for k in ("reviewed_result_hash", "handoff", "handoff_sha256")})
            extra["completion_turn_id"] = args.completion_turn_id
            extra.update({k: completion[k] for k in ("session_meta_event", "task_started_event", "task_complete_event")})
            record.update(extra)
            record["evidence"] = [{"path": p, "sha256": sha256(reference_bytes(run_dir, {"path": p}))}
                                  for p in artifact_paths if p != handoff]
            if not record["evidence"]:
                raise EvidenceError("QA/reviewer handoff requires at least one --artifact evidence reference")
            if role == "reviewer":
                qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), {})
                if answer.get("qa_handoff_sha256") != qa.get("handoff_sha256") or not qa.get("handoff_sha256"):
                    raise EvidenceError("source qa_handoff_sha256 mismatch")
                qa_completion = completed_turn(source, qa.get("codex_thread_id"), qa.get("completion_turn_id"),
                                               verification.get("root_thread_id"), "qa-verifier")
                if not completion_follows(completion, qa_completion):
                    raise EvidenceError("reviewer acceptance must follow QA completion")
            verification["reviewer" if role == "reviewer" else "qa"] = args.lane_id
        elif args.stage == "handoff" and artifact_paths:
            record["handoff"] = artifact_paths[0]
        if existing:
            data["subagents"][data["subagents"].index(existing)] = record
        else:
            data["subagents"].append(record)
        data["subagents_used"] = True
    elif args.completion_turn_id:
        raise EvidenceError("--completion-turn-id requires subagent and --lane-id")
    errors = validate_verification(run_dir, data, {}, "pending", source)
    if errors:
        raise EvidenceError("; ".join(errors))
    return data, extra


def main(argv=None, *, session_source=None) -> int:
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
    parser.add_argument("--resolve-session", action="store_true", help="Resolve exact child session metadata and current handoff completion.")
    parser.add_argument("--agent-path", help="Exact canonical task path returned by the spawn tool.")
    parser.add_argument("--runtime-nickname")
    parser.add_argument("--lane-id")
    parser.add_argument("--wave", type=int)
    parser.add_argument("--critical", action="store_true")
    parser.add_argument("--completion-turn-id")
    parser.add_argument("--verification-json", help="Verification object as JSON or a local file path.")
    args = parser.parse_args(argv)

    if args.resolve_session:
        if not args.agent_path or args.execution_mode != "subagent" or not args.lane_id or args.stage not in {"spawned", "handoff"}:
            parser.error("--resolve-session requires --agent-path, --lane-id, subagent mode and stage spawned or handoff")
    elif args.agent_path:
        parser.error("--agent-path requires --resolve-session")
    if args.execution_mode == "subagent" and args.stage == "spawned" and not (args.codex_thread_id or args.resolve_session):
        raise SystemExit("spawned subagent events require --codex-thread-id or --resolve-session --agent-path")

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

    artifact_paths = unique_paths([display_path(path, run_dir) for path in args.artifact])
    try:
        summary_data, completion_fields = prepare_summary(run_dir, args, artifact_paths, session_source or CodexSessionSource())
        load_artifact_index(artifacts_path)
        if args.stage == "behavior-input-prepared":
            if len(artifact_paths) != 1:
                raise EvidenceError("behavior-input-prepared requires exactly one --artifact input")
            completion_fields["input_sha256"] = sha256(reference_bytes(run_dir, {"path": artifact_paths[0]}))
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_artifact_dir.mkdir(parents=True, exist_ok=True)
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
        **completion_fields,
    }
    if args.codex_thread_id:
        event["codex_thread_id"] = args.codex_thread_id
    if args.runtime_nickname:
        event["runtime_nickname"] = args.runtime_nickname
    if args.lane_id:
        event["lane_id"] = args.lane_id
    if args.wave is not None:
        event["wave"] = args.wave
    if args.critical:
        event["critical"] = args.critical

    indexed_count = upsert_artifacts(
        artifacts_path,
        artifact_paths,
        role=args.role,
        execution_mode=args.execution_mode,
        stable_agent_name=stable_agent_name,
        stable_agent_slug=stable_agent_slug,
        timestamp=timestamp,
        lane_id=args.lane_id,
        wave=args.wave,
        critical=args.critical,
    )

    encoded_event = json.dumps(event, ensure_ascii=False)
    with timeline_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded_event + "\n")
    with agent_trace_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded_event + "\n")

    if summary_data is not None:
        (run_dir / "delegation-summary.json").write_text(json.dumps(summary_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if summary_data["verification"].get("result_hash"):
            print(f"result_hash: {summary_data['verification']['result_hash']}")

    if args.resolve_session:
        fields = ("codex_thread_id", "root_thread_id", "agent_path", "observed_spawn_at", "session_meta_event",
                  "task_started_event", "completion_turn_id", "task_complete_event")
        print("resolver: " + json.dumps({k: completion_fields[k] for k in fields if k in completion_fields}, sort_keys=True))

    print(f"recorded timeline: {timeline_path}")
    print(f"recorded agent trace: {agent_trace_path}")
    print(f"agent artifact dir: {agent_artifact_dir}")
    print(f"indexed artifacts: {indexed_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
