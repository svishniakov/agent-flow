"""Check assignment settings against Codex launch and per-turn source records.

Evidence pointers select one-based JSONL lines. The source files are observations
collected by the caller; JSON shape alone cannot authenticate their origin.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config


CONTEXT_FIELDS = (
    "goal", "acceptance_criteria", "accepted_decisions", "superseded_decisions",
    "unknowns", "scope", "completed_changes", "check_results", "forbidden_repeats", "evidence",
)
TERMINAL_STAGES = {"handoff", "blocked", "fail", "stopped"}


def require_session_id(value: object) -> None:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.version != 7:
        raise ValueError("Codex session ID must be a UUIDv7, in addition to source evidence")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_evidence(run_dir: Path, pointer: object) -> dict:
    if not isinstance(pointer, dict) or not isinstance(pointer.get("path"), str):
        raise ValueError("missing source evidence pointer")
    line = pointer.get("line")
    if type(line) is not int or line < 1:
        raise ValueError("source evidence line must be a positive integer")
    path = Path(pointer["path"])
    if not path.is_absolute():
        path = run_dir / path
    try:
        with path.open(encoding="utf-8") as stream:
            for index, raw in enumerate(stream, 1):
                if index == line:
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError("source evidence must be an object")
                    if value.get("type") in {"session_meta", "turn_context", "response_item", "event_msg"}:
                        sessions_root = (Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions").resolve()
                        original = path.resolve()
                        if not original.is_relative_to(sessions_root):
                            raise ValueError("client source must be an original rollout inside CODEX_HOME/sessions")
                        with original.open(encoding="utf-8") as source:
                            first = json.loads(source.readline())
                        sid = first.get("payload", {}).get("id")
                        require_session_id(sid)
                        if first.get("type") != "session_meta" or not original.name.startswith("rollout-") or not original.name.endswith(f"-{sid}.jsonl"):
                            raise ValueError("original rollout filename/session_meta identity mismatch")
                        if value.get("type") == "session_meta" and line != 1:
                            raise ValueError("session_meta evidence must reference first original rollout line")
                    return value
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"source evidence unavailable: {path}:{line}: {exc}") from exc
    raise ValueError(f"source evidence line not found: {path}:{line}")


def timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result


def observed_tier(value):
    return None if value in {None, "default"} else value


def launch_observation(run_dir: Path, pointer: object) -> tuple[dict, datetime]:
    if isinstance(pointer, dict) and pointer.get("kind") == "native":
        call = read_evidence(run_dir, pointer.get("call"))
        activity = read_evidence(run_dir, pointer.get("activity"))
        output = read_evidence(run_dir, pointer.get("result"))
        meta = read_evidence(run_dir, pointer.get("session"))
        context = read_evidence(run_dir, pointer.get("context"))
        payload = call.get("payload", {})
        if call.get("type") != "response_item" or payload.get("type") != "function_call" or payload.get("name") != "spawn_agent" or payload.get("namespace") != "collaboration":
            raise ValueError("native launch requires actual collaboration.spawn_agent call")
        if not payload.get("call_id") or not all(pointer[key]["path"] == pointer["call"]["path"] for key in ("activity", "result")):
            raise ValueError("native launch call/activity/result must share parent source and call ID")
        arguments = json.loads(payload.get("arguments", "null"))
        item = activity.get("payload", {}).get("item", {})
        result = output.get("payload", {})
        if activity.get("type") != "event_msg" or activity.get("payload", {}).get("type") != "item_completed" or item.get("type") != "SubAgentActivity" or item.get("kind") != "started" or item.get("id") != payload.get("call_id"):
            raise ValueError("native launch missing matching client SubAgentActivity")
        if output.get("type") != "response_item" or result.get("type") != "function_call_output" or result.get("call_id") != payload.get("call_id"):
            raise ValueError("native launch missing matching tool result")
        result = json.loads(result.get("output", "null"))
        if result.get("task_name") != item.get("agent_path") or not item.get("agent_path", "").endswith("/" + arguments.get("task_name", "")):
            raise ValueError("native launch task result does not match client activity")
        session = meta.get("payload", {})
        require_session_id(session.get("id"))
        parent = session.get("source", {}).get("subagent", {}).get("thread_spawn", {}).get("parent_thread_id")
        observed = context.get("payload", {})
        if meta.get("type") != "session_meta" or session.get("id") != item.get("agent_thread_id") or not parent or parent != activity.get("payload", {}).get("thread_id"):
            raise ValueError("native launch client session ID/parent mismatch")
        if context.get("type") != "turn_context" or pointer["session"]["path"] != pointer["context"]["path"]:
            raise ValueError("native launch requires same-session client turn context")
        if observed.get("model") != arguments.get("model") or observed.get("effort") != arguments.get("reasoning_effort"):
            raise ValueError("native launch settings differ from explicit tool arguments")
        return {"session_id": session["id"], "parent_session_id": parent, "task_name": arguments.get("task_name"), "model": observed.get("model"),
                "reasoningEffort": observed.get("effort"), "serviceTier": observed.get("service_tier")}, timestamp(call.get("timestamp"))
    source = read_evidence(run_dir, pointer)
    if source.get("method") != "thread/start":
        raise ValueError("launch evidence must be a thread/start tool result")
    result = source.get("result")
    thread = result.get("thread") if isinstance(result, dict) else None
    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"].strip():
        raise ValueError("launch evidence missing actual thread.id")
    require_session_id(thread["id"])
    if "parentThreadId" not in thread or "model" not in result or "reasoningEffort" not in result:
        raise ValueError("launch evidence missing parent or model settings")
    return {**result, "session_id": thread["id"], "parent_session_id": thread["parentThreadId"]}, timestamp(source.get("captured_at"))


def assignment_policy(role: str) -> dict:
    if role == "root":
        return {
            "model": "gpt-6-astra", "service_tier": None,
            "default": {"reasoning_effort": "high"},
            "escalation": {"reasoning_effort": "xhigh", "triggers": ["multi-lane", "architecture-risk", "broad-scope"]},
        }
    return role_config(read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)


def native_followup_time(run_dir: Path, pointer: dict, session: dict, turn_id: str) -> datetime:
    call = read_evidence(run_dir, pointer.get("call"))
    activity = read_evidence(run_dir, pointer.get("activity"))
    output = read_evidence(run_dir, pointer.get("result"))
    started = read_evidence(run_dir, pointer.get("started"))
    meta = read_evidence(run_dir, session.get("session_evidence"))
    source_path = pointer["call"]["path"]
    if any(pointer[key]["path"] != source_path for key in ("activity", "result")):
        raise ValueError("native followup call/activity/result must share parent source")
    parent_meta = read_evidence(run_dir, {"path": source_path, "line": 1})
    parent_id = session.get("parent_session_id")
    if parent_meta.get("type") != "session_meta" or parent_meta.get("payload", {}).get("id") != parent_id:
        raise ValueError("native followup source does not belong to recorded parent")
    payload = call.get("payload", {})
    if call.get("type") != "response_item" or payload.get("type") != "function_call" or payload.get("namespace") != "collaboration" or payload.get("name") != "followup_task" or not payload.get("call_id"):
        raise ValueError("native followup requires actual collaboration.followup_task call")
    arguments = json.loads(payload.get("arguments", "null"))
    if any(key in arguments for key in ("model", "effort", "reasoning_effort", "service_tier")):
        raise ValueError("native followup cannot request model settings changes")
    spawn = meta.get("payload", {}).get("source", {}).get("subagent", {}).get("thread_spawn", {})
    agent_path = meta.get("payload", {}).get("agent_path") or spawn.get("agent_path")
    parent_path = parent_meta.get("payload", {}).get("agent_path") or "/root"
    target = arguments.get("target")
    if not isinstance(target, str) or not target or any(part in {".", "..", ""} for part in target.lstrip("/").split("/")):
        raise ValueError("native followup target must be a relative or canonical task name")
    canonical_target = target if target.startswith("/") else parent_path.rstrip("/") + "/" + target
    if not agent_path or canonical_target != agent_path or spawn.get("parent_thread_id") != parent_id:
        raise ValueError("native followup target differs from child session identity")
    item = activity.get("payload", {}).get("item", {})
    if activity.get("type") != "event_msg" or activity.get("payload", {}).get("type") != "item_completed" or activity.get("payload", {}).get("thread_id") != parent_id or item.get("type") != "SubAgentActivity" or item.get("kind") != "interacted" or item.get("id") != payload["call_id"] or item.get("agent_thread_id") != session["session_id"] or item.get("agent_path") != agent_path:
        raise ValueError("native followup missing matching child interaction")
    result = output.get("payload", {})
    if output.get("type") != "response_item" or result.get("type") != "function_call_output" or result.get("call_id") != payload["call_id"] or result.get("output") != "":
        raise ValueError("native followup missing successful matching tool result")
    if pointer["started"]["path"] != session["session_evidence"]["path"] or started.get("type") != "event_msg" or started.get("payload", {}).get("type") != "task_started" or started.get("payload", {}).get("turn_id") != turn_id:
        raise ValueError("native followup missing matching child task_started")
    call_at = timestamp(call.get("timestamp"))
    interaction_at = timestamp(activity.get("timestamp"))
    started_at = timestamp(started.get("timestamp"))
    if not call_at <= interaction_at <= started_at or timestamp(output.get("timestamp")) < call_at:
        raise ValueError("native followup started before its source interaction")
    return started_at


def validate_model_settings(run_dir: Path, *, allow_pending: bool = False) -> list[str]:
    errors: list[str] = []
    path = run_dir / "model-settings.json"
    if not path.is_file():
        if allow_pending:
            evidence_paths = [run_dir / "timeline.jsonl", *sorted((run_dir / "agents").glob("*/trace.jsonl"))]
            try:
                started = any(
                    event.get("execution_mode") == "subagent" or event.get("stage") in {"spawn", "spawned", "invocation"} or event.get("codex_thread_id")
                    for source in evidence_paths if source.is_file()
                    for raw in source.read_text(encoding="utf-8").splitlines() if raw.strip()
                    for event in [json.loads(raw)]
                )
                if not started:
                    return []
            except (OSError, ValueError, AttributeError):
                pass
        return ["model-settings.json: missing observed model settings"]
    try:
        data = read_json(path)
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        assignments = data.get("assignments")
        if not isinstance(assignments, list) or not assignments:
            raise ValueError("assignments must be a non-empty array")
    except (ValueError, OSError) as exc:
        return [f"model-settings.json: {exc}"]

    known_sessions: dict[str, tuple[str, str, object]] = {}
    assignment_records: dict[str, dict] = {}
    for assignment in assignments:
        label = "model-settings.json assignment"
        try:
            if not isinstance(assignment, dict):
                raise ValueError("must be an object")
            aid = assignment.get("assignment_id")
            role = assignment.get("role")
            if not isinstance(aid, str) or not aid or not isinstance(role, str):
                raise ValueError("requires assignment_id and role")
            label += f" {aid}"
            if aid in assignment_records:
                raise ValueError("duplicate assignment_id")
            assignment_records[aid] = assignment
            policy = assignment_policy(role)
            baseline = policy["default"]["reasoning_effort"]
            ceiling = policy["escalation"]["reasoning_effort"]
            expected = {"model": policy["model"], "service_tier": policy["service_tier"],
                        "baseline_effort": baseline, "ceiling_effort": ceiling}
            for key, value in expected.items():
                if key not in assignment or assignment[key] != value:
                    raise ValueError(f"immutable {key} must match role policy: {value}")
            if (aid == "root") != (role == "root"):
                raise ValueError("root assignment must use role root")
            sessions = assignment.get("sessions")
            events = assignment.get("events")
            if not isinstance(sessions, list) or not sessions or not isinstance(events, list) or not events:
                raise ValueError("sessions and events must be non-empty arrays")
            session_records = {}
            for index, session in enumerate(sessions):
                if not isinstance(session, dict):
                    raise ValueError("session must be an object")
                sid = session.get("session_id")
                if not isinstance(sid, str) or not sid or sid in known_sessions:
                    raise ValueError("session_id missing or reused across assignments/sessions")
                observed, launched_at = launch_observation(run_dir, session.get("launch_evidence"))
                native_launch = session.get("launch_evidence", {}).get("kind") == "native"
                if (role == "root" and native_launch) or (role != "root" and not native_launch):
                    raise ValueError("root requires thread/start; child requires source-bound native launch")
                parent = session.get("parent_session_id")
                if "parent_session_id" not in session or sid != observed["session_id"] or parent != observed["parent_session_id"]:
                    raise ValueError("session ID or parent differs from launch source")
                if (role == "root" and parent is not None) or (role != "root" and not isinstance(parent, str)):
                    raise ValueError("incorrect parent: root requires null; child requires parent ID")
                if observed["model"] != expected["model"] or observed_tier(observed.get("serviceTier")) != expected["service_tier"]:
                    raise ValueError("launch model or service tier differs from immutable policy")
                if "task_name" in observed and observed["task_name"] != aid:
                    raise ValueError("assignment_id must match actual native spawn task_name")
                meta = read_evidence(run_dir, session.get("session_evidence"))
                if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != sid or not meta.get("payload", {}).get("source"):
                    raise ValueError("session evidence must identify the launched client session")
                client_source = meta["payload"]["source"]
                if role == "root":
                    if not isinstance(client_source, str) or client_source == "subagent":
                        raise ValueError("root requires a non-subagent client session source")
                else:
                    subagent = client_source.get("subagent") if isinstance(client_source, dict) else None
                    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                    if not isinstance(spawn, dict) or spawn.get("parent_thread_id") != parent:
                        raise ValueError("child parent must match authoritative client session source")
                if index == 0:
                    if session.get("predecessor_session_id") is not None:
                        raise ValueError("initial session cannot have predecessor")
                else:
                    predecessor = sessions[index - 1]["session_id"]
                    if session.get("predecessor_session_id") != predecessor:
                        raise ValueError("successor must explicitly link previous session")
                    snapshot = read_json(run_dir / session["context_snapshot"])
                    if not isinstance(snapshot, dict) or any(key not in snapshot for key in CONTEXT_FIELDS):
                        raise ValueError("successor requires complete context snapshot")
                    for key in CONTEXT_FIELDS:
                        value = snapshot[key]
                        if not isinstance(value, (str, list)) or (key in {"goal", "acceptance_criteria", "scope", "evidence"} and not value):
                            raise ValueError(f"invalid context snapshot {key}")
                    if not isinstance(snapshot["evidence"], list):
                        raise ValueError("context evidence must be an array of source pointers")
                    if snapshot.get("assignment_id") != aid or snapshot.get("predecessor_session_id") != predecessor:
                        raise ValueError("context snapshot assignment/predecessor mismatch")
                    stop = read_evidence(run_dir, session.get("stop_evidence"))
                    if stop.get("type") == "event_msg":
                        pointer = session["stop_evidence"]
                        previous_source = sessions[index - 1]["session_evidence"]["path"]
                        terminal = stop.get("payload", {})
                        if pointer["path"] != previous_source or terminal.get("type") not in {"task_complete", "turn_aborted"} or not any(event.get("kind") == "invocation" and event.get("session_id") == predecessor and event.get("turn_id") == terminal.get("turn_id") and event.get("completion_evidence") == pointer for event in events):
                            raise ValueError("successor stop must match predecessor terminal invocation")
                        with (run_dir / previous_source).open(encoding="utf-8") as source:
                            for line, raw in enumerate(source, 1):
                                later = json.loads(raw)
                                if line > pointer["line"] and (later.get("type") == "turn_context" or later.get("payload", {}).get("type") == "task_started"):
                                    raise ValueError("predecessor has execution after terminal stop evidence")
                        stopped_at = timestamp(stop.get("timestamp"))
                    else:
                        if stop.get("method") != "thread/archive" or stop.get("params", {}).get("threadId") != predecessor or "result" not in stop:
                            raise ValueError("successor requires prior execution stopped evidence")
                        stopped_at = timestamp(stop.get("captured_at"))
                    if stopped_at >= launched_at:
                        raise ValueError("previous execution must stop before successor launch")
                    for pointer in snapshot["evidence"]:
                        read_evidence(run_dir, pointer)
                known_sessions[sid] = (aid, role, parent)
                session_records[sid] = (session, observed, launched_at)

            current = baseline
            attempt = 1
            previous_at = None
            last_session_index = -1
            invocations = set()
            turn_ids = set()
            pending_escalation_at = None
            last_completed = None
            last_status = None
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError("event must be an object")
                at = timestamp(event.get("at"))
                if previous_at is not None and at <= previous_at:
                    raise ValueError("events must be strictly ordered; backdated reason forbidden")
                previous_at = at
                sid = event.get("session_id")
                if sid not in session_records:
                    raise ValueError("event references unknown session")
                if type(event.get("attempt")) is not int or event["attempt"] != attempt:
                    raise ValueError("attempt reset/change without recovery event")
                kind = event.get("kind")
                if kind == "escalation":
                    if event.get("from_effort") != current or event.get("to_effort") != ceiling or current == ceiling:
                        raise ValueError("illegal escalation or ceiling already reached")
                    if event.get("trigger") not in policy["escalation"]["triggers"] or not event.get("reason"):
                        raise ValueError("escalation requires permitted trigger and reason")
                    source = read_evidence(run_dir, event.get("evidence"))
                    keys = ("attempt", "from_effort", "to_effort", "trigger", "reason")
                    if source.get("method") != "reasoning/escalation" or source.get("params", {}).get("assignment_id") != aid or any(source.get("params", {}).get(key) != event.get(key) for key in keys):
                        raise ValueError("escalation differs from preliminary decision source")
                    source_session = source.get("params", {}).get("session_id")
                    linked_session = session_records[sid][0]
                    if source_session != sid:
                        predecessor = linked_session.get("predecessor_session_id")
                        if sid in invocations or at >= session_records[sid][2] or source_session != predecessor or source.get("params", {}).get("parent_session_id") != linked_session.get("parent_session_id"):
                            raise ValueError("prelaunch escalation must identify assignment and parent/predecessor")
                    if timestamp(source.get("captured_at")) != at or (last_completed and at <= last_completed):
                        raise ValueError("escalation reason is backdated or execution still active")
                    current = ceiling
                    pending_escalation_at = at
                    continue
                if kind == "recovery":
                    if attempt >= 3 or event.get("next_attempt") != attempt + 1:
                        raise ValueError("recovery exceeds existing three-attempt limit")
                    recovery = read_json(run_dir / "risk-resolutions.json")
                    records = recovery.get("resolutions", [])
                    matching = [record for record in records if record.get("risk_id") == event.get("risk_id")]
                    if not matching:
                        raise ValueError("recovery must reference existing risk resolution")
                    attempts = matching[0].get("attempts", [])
                    if not any(item.get("attempt") == attempt and item.get("status") == "blocked" for item in attempts) or not any(item.get("attempt") == attempt + 1 and item.get("owner_lane") == aid for item in attempts):
                        raise ValueError("recovery lacks blocked attempt and linked successor attempt")
                    recovery_reviews = matching[0].get("blocked_recovery", {})
                    required_reviews = ("senior_qa_test_design_review", "architect_review") if attempt == 1 else ("supervising_architect_review",)
                    if any(not isinstance(recovery_reviews.get(key), dict) or not recovery_reviews[key].get("lane") for key in required_reviews):
                        raise ValueError("recovery lacks existing required review chain")
                    if last_completed and at <= last_completed:
                        raise ValueError("recovery cannot overlap previous invocation")
                    attempt += 1
                    continue
                if kind != "invocation":
                    raise ValueError("unknown model-settings event kind")
                session, observed, launched_at = session_records[sid]
                session_index = next(i for i, item in enumerate(sessions) if item["session_id"] == sid)
                if session_index < last_session_index:
                    raise ValueError("invocation returned to stopped predecessor")
                if last_completed and at <= last_completed:
                    raise ValueError("overlapping invocations in assignment")
                if sid not in invocations:
                    if observed["reasoningEffort"] != current:
                        raise ValueError("launch effort differs from authorized effort")
                    if pending_escalation_at and launched_at <= pending_escalation_at:
                        raise ValueError("initial elevated launch predates escalation reason")
                    if session_index > 0:
                        snapshot = read_json(run_dir / session["context_snapshot"])
                        if snapshot.get("attempt") != attempt or snapshot.get("scope") != assignment.get("scope"):
                            raise ValueError("successor reset attempt or changed scope")
                last_session_index = session_index
                invocations.add(sid)
                turn_id = event.get("turn_id")
                if not isinstance(turn_id, str) or not turn_id or turn_id in turn_ids:
                    raise ValueError("missing or reused turn_id")
                turn_ids.add(turn_id)
                source = read_evidence(run_dir, event.get("evidence"))
                payload = source.get("payload", {})
                if source.get("type") != "turn_context" or payload.get("turn_id") != turn_id:
                    raise ValueError("invocation must reference client turn_context")
                # The client context must belong to the same rollout as session_meta.
                if event["evidence"]["path"] != session["session_evidence"]["path"]:
                    raise ValueError("turn context and session metadata sources differ")
                if payload.get("model") != expected["model"] or observed_tier(payload.get("service_tier")) != expected["service_tier"]:
                    raise ValueError("observed model or service tier changed")
                if event.get("effort") != current or payload.get("effort") != current:
                    raise ValueError("observed effort differs from authorized effort; downshift forbidden")
                request_pointer = event.get("request_evidence")
                if isinstance(request_pointer, dict) and request_pointer.get("kind") == "native-followup":
                    if pending_escalation_at:
                        raise ValueError("native followup cannot apply a pending escalation")
                    if not any(e.get("kind") == "invocation" and e.get("session_id") == sid for e in events[:events.index(event)]):
                        raise ValueError("native followup requires prior invocation in same session")
                    if any(e.get("request_evidence", {}).get("kind") == "native-followup" and e["request_evidence"].get("call") == request_pointer.get("call") for e in events[:events.index(event)]):
                        raise ValueError("native followup call cannot authorize multiple invocations")
                    request_at = native_followup_time(run_dir, request_pointer, session, turn_id)
                elif isinstance(request_pointer, dict) and request_pointer.get("kind") == "native":
                    native, _ = launch_observation(run_dir, request_pointer.get("launch"))
                    started = read_evidence(run_dir, request_pointer.get("started"))
                    if native["session_id"] != sid or native["reasoningEffort"] != current or request_pointer["started"]["path"] != session["session_evidence"]["path"] or started.get("type") != "event_msg" or started.get("payload", {}).get("type") != "task_started" or started.get("payload", {}).get("turn_id") != turn_id:
                        raise ValueError("native invocation does not match launch/client task_started")
                    if request_pointer.get("launch") != session.get("launch_evidence") or len([e for e in events if e.get("kind") == "invocation" and e.get("session_id") == sid and e.get("request_evidence", {}).get("kind") == "native"]) != 1:
                        raise ValueError("native launch authorizes only the initial invocation")
                    request_at = timestamp(started.get("timestamp"))
                else:
                    request = read_evidence(run_dir, request_pointer)
                    params = request.get("params", {})
                    explicit_effort = params.get("effort")
                    if request.get("method") != "turn/start" or params.get("threadId") != sid or (explicit_effort is not None and explicit_effort != current) or request.get("result", {}).get("turn", {}).get("id") != turn_id:
                        raise ValueError("turn/start request/result differs from invocation")
                    if pending_escalation_at and len([e for e in events[:events.index(event)] if e.get("kind") == "invocation" and e.get("session_id") == sid]) and explicit_effort != current:
                        raise ValueError("same-session escalation requires explicit effort request")
                    request_at = timestamp(request.get("captured_at"))
                if request_at != at or timestamp(source.get("timestamp")) < at or at < launched_at:
                    raise ValueError("invocation/source timestamps out of order")
                if pending_escalation_at and at <= pending_escalation_at:
                    raise ValueError("invocation predates escalation reason")
                completion = read_evidence(run_dir, event.get("completion_evidence"))
                params = completion.get("params", {})
                turn = params.get("turn", {})
                if completion.get("type") == "event_msg":
                    completion_kind = completion.get("payload", {}).get("type")
                    if event["completion_evidence"]["path"] != session["session_evidence"]["path"] or completion_kind not in {"task_complete", "turn_aborted"} or completion.get("payload", {}).get("turn_id") != turn_id:
                        raise ValueError("invocation missing matching client task_complete evidence")
                    last_status = "completed" if completion_kind == "task_complete" else "interrupted"
                    last_completed = timestamp(completion.get("timestamp"))
                else:
                    if completion.get("method") != "turn/completed" or params.get("threadId") != sid or turn.get("id") != turn_id or turn.get("status") not in {"completed", "failed", "interrupted"}:
                        raise ValueError("invocation missing terminal turn/completed evidence")
                    last_status = turn["status"]
                    last_completed = timestamp(completion.get("captured_at"))
                if last_completed < timestamp(source.get("timestamp")):
                    raise ValueError("completion predates client invocation")
                pending_escalation_at = None
            if invocations != set(session_records) or pending_escalation_at:
                raise ValueError("each session/escalation requires an observed invocation")
            final_path = run_dir / "final.md"
            failed_run = final_path.is_file() and re.search(r"^Verdict:\s*(blocked|fail)\s*$", final_path.read_text(encoding="utf-8"), re.MULTILINE)
            if last_status != "completed" and not failed_run:
                raise ValueError("assignment must finish with successful observed invocation")
            observed_turns = set()
            invocation_by_turn = {event["turn_id"]: event for event in events if event.get("kind") == "invocation"}
            for session, _observed, _launched_at in session_records.values():
                source_path = run_dir / session["session_evidence"]["path"]
                with source_path.open(encoding="utf-8") as stream:
                    for raw in stream:
                        record = json.loads(raw)
                        if record.get("type") == "turn_context":
                            context = record.get("payload", {})
                            observed_turns.add(context.get("turn_id"))
                            invocation = invocation_by_turn.get(context.get("turn_id"))
                            if invocation and (context.get("model") != expected["model"] or context.get("effort") != invocation["effort"] or observed_tier(context.get("service_tier")) != expected["service_tier"]):
                                raise ValueError("client context changed settings within recorded turn")
            if observed_turns != turn_ids:
                raise ValueError("client rollout contains unrecorded or mismatched invocations")
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            errors.append(f"{label}: {exc}")
    if "root" not in assignment_records:
        errors.append("model-settings.json: root assignment missing")
    for sid, (aid, role, parent) in known_sessions.items():
        if role != "root" and (parent not in known_sessions or parent == sid):
            errors.append(f"model-settings.json assignment {aid}: parent session is not recorded")
        ancestors = {sid}
        while parent in known_sessions:
            if parent in ancestors:
                errors.append(f"model-settings.json assignment {aid}: parent cycle")
                break
            ancestors.add(parent)
            parent = known_sessions[parent][2]
    for assignment in assignment_records.values():
        for session in assignment.get("sessions", []):
            try:
                launch = session.get("launch_evidence", {})
                if launch.get("kind") == "native":
                    parent = session.get("parent_session_id")
                    parent_sources = [item["session_evidence"]["path"] for record in assignment_records.values() for item in record.get("sessions", []) if item.get("session_id") == parent]
                    if launch["call"]["path"] not in parent_sources or launch["session"] != session.get("session_evidence"):
                        errors.append("model-settings.json: native launch must point to recorded parent and child rollouts")
                for event in assignment.get("events", []):
                    request = event.get("request_evidence", {})
                    if event.get("session_id") == session.get("session_id") and request.get("kind") == "native-followup":
                        parent_sources = [item["session_evidence"]["path"] for record in assignment_records.values() for item in record.get("sessions", []) if item.get("session_id") == session.get("parent_session_id")]
                        if request["call"]["path"] not in parent_sources:
                            errors.append("model-settings.json: native followup must point to recorded parent rollout")
                with (run_dir / session["session_evidence"]["path"]).open(encoding="utf-8") as stream:
                    for raw in stream:
                        record = json.loads(raw)
                        item = record.get("payload", {}).get("item", {})
                        if item.get("type") == "SubAgentActivity" and item.get("kind") == "started" and item.get("agent_thread_id") not in known_sessions:
                            errors.append("model-settings.json: native launched child assignment omitted")
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                # Missing/malformed evidence is reported with its assignment above.
                continue
    errors.extend(validate_assignment_traces(run_dir, assignment_records, known_sessions))
    return errors


def validate_assignment_traces(run_dir: Path, assignments: dict, sessions: dict) -> list[str]:
    errors = []
    try:
        timeline_path = run_dir / "timeline.jsonl"
        timeline = [json.loads(line) for line in timeline_path.read_text(encoding="utf-8").splitlines() if line.strip()] if timeline_path.exists() else []
        traces = []
        for path in sorted((run_dir / "agents").glob("*/trace.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    event = json.loads(line)
                    if event not in timeline:
                        errors.append(f"{path.relative_to(run_dir)}: event differs from timeline.jsonl")
                    traces.append(event)
        for event in timeline + traces:
            if event.get("stage") == "spawn":
                errors.append("agent trace: stage=spawn is invalid; use spawned")
        events_by_session = {}
        for event in timeline:
            if event.get("execution_mode") != "subagent":
                continue
            aid = event.get("assignment_id") or event.get("lane_id")
            sid = event.get("codex_thread_id")
            expected = sessions.get(sid)
            if not expected or expected[0] != aid or expected[1] != event.get("role"):
                errors.append("agent trace: assignment/session ID differs from observed launch")
                continue
            if "parent_thread_id" not in event or event.get("parent_thread_id") != expected[2]:
                errors.append(f"agent trace {aid}: parent differs from launch")
            if event not in traces:
                errors.append(f"agent trace {aid}: timeline event missing from role trace")
            events_by_session.setdefault(sid, []).append(event)
        summary_path = run_dir / "delegation-summary.json"
        summary = read_json(summary_path) if summary_path.is_file() else {}
        records = summary.get("subagents", [])
        final_path = run_dir / "final.md"
        failed_run = final_path.is_file() and re.search(r"^Verdict:\s*(blocked|fail)\s*$", final_path.read_text(encoding="utf-8"), re.MULTILINE)
        for aid, assignment in assignments.items():
            if aid == "root":
                continue
            for session in assignment.get("sessions", []):
                sid = session.get("session_id")
                events = events_by_session.get(sid, [])
                if not events or events[0].get("stage") != "spawned" or sum(event.get("stage") == "spawned" for event in events) != 1:
                    errors.append(f"agent trace {aid}: each session requires exactly one initial spawned event")
                if not events or events[-1].get("stage") not in TERMINAL_STAGES:
                    errors.append(f"agent trace {aid}: missing terminal handoff trace event")
                if events and session is assignment["sessions"][-1] and not failed_run and (events[-1].get("stage") != "handoff" or events[-1].get("status") not in {"pass", "pass-with-risks"}):
                    errors.append(f"agent trace {aid}: positive run requires successful terminal handoff")
                if any(event.get("stage") in TERMINAL_STAGES for event in events[:-1]):
                    errors.append(f"agent trace {aid}: events after terminal stage")
                matching = [record for record in records if (record.get("assignment_id") or record.get("lane_id")) == aid and record.get("codex_thread_id") == sid]
                if len(matching) != 1 or matching[0].get("role") != assignment.get("role") or matching[0].get("parent_thread_id") != session.get("parent_session_id"):
                    errors.append(f"delegation-summary.json: missing/mismatched assignment session {aid}/{sid}")
                    continue
                handoff = matching[0].get("handoff")
                if not isinstance(handoff, str):
                    errors.append(f"agent trace {aid}: missing handoff path")
                    continue
                content = (run_dir / handoff).read_text(encoding="utf-8")
                for key, value in (("assignment_id", aid), ("codex_thread_id", sid), ("parent_thread_id", session.get("parent_session_id"))):
                    found = re.findall(rf"^{key}:\s*(.*?)\s*$", content, flags=re.MULTILINE)
                    if found != [str(value)]:
                        errors.append(f"handoff {aid}: missing/mismatched {key}")
                if events and handoff not in events[-1].get("artifacts", []):
                    errors.append(f"agent trace {aid}: terminal handoff trace event missing handoff artifact")
        for record in records:
            sid = record.get("codex_thread_id")
            if sid not in sessions or sessions[sid][0] != (record.get("assignment_id") or record.get("lane_id")):
                errors.append("delegation-summary.json: unobserved or swapped session ID")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append(f"assignment trace: {exc}")
    return errors
