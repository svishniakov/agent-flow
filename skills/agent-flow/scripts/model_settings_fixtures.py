"""Synthetic Codex observations for validator tests; never runtime evidence."""

import json
import re
import shutil
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from model_settings import assignment_policy


def fixture_id(label: str) -> str:
    try:
        if UUID(label).version == 7:
            return label
    except ValueError:
        pass
    value = uuid5(NAMESPACE_URL, "synthetic-agent-flow/" + label).hex
    return str(UUID(value[:12] + "7" + value[13:16] + "8" + value[17:]))


ROOT_SESSION = fixture_id("root")


def stage_test_observations(run_dir: Path) -> Path:
    """Install synthetic client records into this test's isolated Codex home."""
    codex_home = run_dir / ".fixture-codex"
    sessions_dir = codex_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    settings_path = run_dir / "model-settings.json"
    if not settings_path.is_file():
        return codex_home
    data = {}
    try:
        data = json.loads(settings_path.read_text())
        assignments = data.get("assignments", [])
        root = next(item for item in assignments if item.get("role") == "root")
        root_source = root["sessions"][0]["session_evidence"]["path"]
        for assignment in assignments:
            if assignment.get("role") == "root":
                continue
            for session in assignment.get("sessions", []):
                launch = session.get("launch_evidence", {})
                if launch.get("kind") == "native":
                    continue
                source = run_dir / launch["path"]
                launch_record = json.loads(source.read_text().splitlines()[launch["line"] - 1])
                observed = launch_record["result"]
                sid = observed["thread"]["id"]
                parent = observed["thread"]["parentThreadId"]
                root_source = next(item["session_evidence"]["path"] for record in assignments for item in record.get("sessions", []) if item.get("session_id") == parent)
                child_source = session["session_evidence"]["path"]
                if any(not (run_dir / value).resolve().is_relative_to(run_dir.resolve()) for value in (root_source, child_source)):
                    raise ValueError("test fixture conversion cannot edit external client sources")
                child = [json.loads(line) for line in (run_dir / child_source).read_text().splitlines()]
                invocation = next(item for item in assignment["events"] if item.get("kind") == "invocation" and item.get("session_id") == sid)
                aid = assignment["assignment_id"]
                agent_path = "/root/" + aid
                child[0]["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_path": agent_path}}}
                child.append({"timestamp": invocation["at"], "type": "event_msg", "payload": {"type": "task_started", "turn_id": invocation["turn_id"]}})
                write_jsonl(run_dir / child_source, child)
                parent_records = [json.loads(line) for line in (run_dir / root_source).read_text().splitlines()]
                first = len(parent_records) + 1
                call_id = "fixture-native-" + sid
                parent_records.extend([
                    {"timestamp": launch_record["captured_at"], "type": "response_item", "payload": {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent", "call_id": call_id, "arguments": json.dumps({"task_name": aid, "model": observed["model"], "reasoning_effort": observed["reasoningEffort"]})}},
                    {"timestamp": launch_record["captured_at"], "type": "event_msg", "payload": {"type": "item_completed", "thread_id": parent, "item": {"type": "SubAgentActivity", "kind": "started", "id": call_id, "agent_thread_id": sid, "agent_path": agent_path}}},
                    {"timestamp": launch_record["captured_at"], "type": "response_item", "payload": {"type": "function_call_output", "call_id": call_id, "output": json.dumps({"task_name": agent_path})}},
                ])
                write_jsonl(run_dir / root_source, parent_records)
                session["launch_evidence"] = {"kind": "native", "call": {"path": root_source, "line": first}, "activity": {"path": root_source, "line": first + 1}, "result": {"path": root_source, "line": first + 2}, "session": session["session_evidence"], "context": invocation["evidence"]}
                invocation["request_evidence"] = {"kind": "native", "launch": session["launch_evidence"], "started": {"path": child_source, "line": len(child)}}
    except (ValueError, KeyError, TypeError, StopIteration, OSError):
        # Malformed negative fixtures remain malformed for the validator.
        pass
    paths = {}
    def visit(value):
        if isinstance(value, dict):
            source_path = value.get("path")
            if isinstance(source_path, str) and "line" in value:
                source = run_dir / source_path
                if source.is_file() and source_path not in paths:
                    try:
                        first = json.loads(source.read_text().splitlines()[0])
                        if first.get("type") == "session_meta":
                            destination = sessions_dir / f"rollout-fixture-{first['payload']['id']}.jsonl"
                            if source.resolve() != destination.resolve():
                                shutil.copyfile(source, destination)
                            paths[source_path] = str(destination)
                    except (ValueError, KeyError, IndexError):
                        pass
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(data)
    def rewrite(value):
        if isinstance(value, dict):
            if value.get("path") in paths:
                value["path"] = paths[value["path"]]
            for item in value.values():
                rewrite(item)
        elif isinstance(value, list):
            for item in value:
                rewrite(item)
    rewrite(data)
    for assignment in data.get("assignments", []):
        for session in assignment.get("sessions", []):
            snapshot_path = session.get("context_snapshot")
            if isinstance(snapshot_path, str) and (run_dir / snapshot_path).is_file():
                snapshot = json.loads((run_dir / snapshot_path).read_text())
                visit(snapshot)
                rewrite(snapshot)
                write_json(run_dir / snapshot_path, snapshot)
    write_json(settings_path, data)
    return codex_home


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: list) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def stamp(second: int) -> str:
    return f"2026-09-09T00:{second // 60:02d}:{second % 60:02d}+00:00"


def add_assignment(run_dir: Path, aid: str, role: str, sid: str, *, parent=ROOT_SESSION, second=0) -> dict:
    policy = assignment_policy(role)
    model = policy["model"]
    effort = policy["reasoning_effort"] if role != "root" else "high"
    prefix = f"model-evidence/{aid}"
    launch = {"captured_at": stamp(second), "method": "thread/start", "result": {
        "thread": {"id": sid, "parentThreadId": parent}, "model": model,
        "reasoningEffort": effort, "serviceTier": policy["service_tier"]}}
    turn_id = f"fixture-turn-{aid}"
    request = {"captured_at": stamp(second + 1), "method": "turn/start",
               "params": {"threadId": sid, "effort": effort}, "result": {"turn": {"id": turn_id}}}
    completion = {"captured_at": stamp(second + 3), "method": "turn/completed",
                  "params": {"threadId": sid, "turn": {"id": turn_id, "status": "completed"}}}
    rollout = [
        {"timestamp": stamp(second), "type": "session_meta", "payload": {"id": sid, "source": "vscode"}},
        {"timestamp": stamp(second + 2), "type": "turn_context", "payload": {"turn_id": turn_id, "model": model, "effort": effort}},
    ]
    (run_dir / "model-evidence").mkdir(exist_ok=True)
    write_jsonl(run_dir / f"{prefix}-tool.jsonl", [launch, request, completion])
    write_jsonl(run_dir / f"{prefix}-rollout.jsonl", rollout)
    return {"assignment_id": aid, "role": role, "model": model, "service_tier": policy["service_tier"],
            "baseline_effort": effort, "ceiling_effort": policy["escalation"]["reasoning_effort"],
            "scope": "Synthetic validator fixture", "sessions": [{"session_id": sid, "parent_session_id": parent,
                "launch_evidence": {"path": f"{prefix}-tool.jsonl", "line": 1},
                "session_evidence": {"path": f"{prefix}-rollout.jsonl", "line": 1}}],
            "events": [{"kind": "invocation", "session_id": sid, "turn_id": turn_id,
                "at": stamp(second + 1), "attempt": 1, "effort": effort,
                "evidence": {"path": f"{prefix}-rollout.jsonl", "line": 2},
                "request_evidence": {"path": f"{prefix}-tool.jsonl", "line": 2},
                "completion_evidence": {"path": f"{prefix}-tool.jsonl", "line": 3}}]}


def seed_model_settings(run_dir: Path) -> None:
    """Enrich existing synthetic traces without repairing their lifecycle defects."""
    assignments = [add_assignment(run_dir, "root", "root", ROOT_SESSION, parent=None)]
    timeline_path = run_dir / "timeline.jsonl"
    timeline = [json.loads(line) for line in timeline_path.read_text().splitlines() if line.strip()] if timeline_path.exists() else []
    seen = set()
    for event in timeline:
        if event.get("execution_mode") != "subagent":
            continue
        aid = event.get("assignment_id") or event.get("lane_id")
        if not aid:
            continue
        event["assignment_id"] = aid
        event["parent_thread_id"] = ROOT_SESSION
        if isinstance(event.get("codex_thread_id"), str):
            event["codex_thread_id"] = fixture_id(event["codex_thread_id"])
        if event.get("stage") == "spawned" and aid not in seen:
            seen.add(aid)
            assignments.append(add_assignment(run_dir, aid, event["role"], event["codex_thread_id"], second=len(seen) * 5))
    for path in (run_dir / "agents").glob("*/trace.jsonl"):
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for record in records:
            if record.get("execution_mode") == "subagent":
                record["assignment_id"] = record.get("assignment_id") or record.get("lane_id")
                record["parent_thread_id"] = ROOT_SESSION
                if isinstance(record.get("codex_thread_id"), str):
                    record["codex_thread_id"] = fixture_id(record["codex_thread_id"])
        write_jsonl(path, records)
    if timeline_path.exists():
        write_jsonl(timeline_path, timeline)
    summary_path = run_dir / "delegation-summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text())
        except ValueError:
            summary = None
        if isinstance(summary, dict):
            for record in summary.get("subagents", []):
                record["assignment_id"] = record.get("assignment_id") or record.get("lane_id")
                record["parent_thread_id"] = ROOT_SESSION
                if isinstance(record.get("codex_thread_id"), str):
                    record["codex_thread_id"] = fixture_id(record["codex_thread_id"])
                handoff = run_dir / record.get("handoff", "missing")
                if handoff.is_file():
                    content = re.sub(r"^(assignment_id|codex_thread_id|parent_thread_id):.*\n?", "", handoff.read_text(encoding="utf-8"), flags=re.MULTILINE).rstrip()
                    handoff.write_text(content + "\n", encoding="utf-8")
                    with handoff.open("a", encoding="utf-8") as stream:
                        stream.write("\n" + "\n".join(f"{key}: {record.get(key)}" for key in ("assignment_id", "codex_thread_id", "parent_thread_id")) + "\n")
            write_json(summary_path, summary)
    write_json(run_dir / "model-settings.json", {"schema_version": 1, "assignments": assignments})
