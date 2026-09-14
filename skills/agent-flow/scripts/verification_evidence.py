"""Read local execution evidence and bind acceptance to the current result.

Session-source injection is an internal testing seam. Public commands always
use CodexSessionSource; run artifacts cannot select a replacement source.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, role_config, resolve_role_path
from journal_io import JournalError, timestamp, event_time, source_time, declared_paths, JournalSnapshot, operation_id, transact
from task_workspace import registered_workspace, verify_candidate, manifest_digest

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
SHA256 = re.compile(r"[0-9a-f]{64}")
POSITIVE = {"ship", "pass-with-risks"}
ACCEPTED = {"passed", "pass-with-risks"}


EvidenceError = JournalError


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def project_root(run_dir: Path, *, snapshot=None) -> Path:
    if snapshot is not None:
        return snapshot.source_root
    for parent in run_dir.parents:
        if parent.name == ".agent-work":
            return parent.parent.resolve()
        if (parent / ".git").exists():
            return parent.resolve()
    return run_dir.parent.resolve()


def confined_path(root: Path, value: str, *, relative=False) -> Path:
    require(isinstance(value, str) and bool(value.strip()), "path must be a non-empty string")
    path = Path(value)
    require(".." not in path.parts and "\\" not in value, f"unsafe path: {value}")
    if relative:
        require(not path.is_absolute() and path.as_posix() == value and value != ".",
                f"result path must be canonical and project-relative: {value}")
    path = root / path
    require(path.resolve().is_relative_to(root.resolve()), f"path escapes project: {value}")
    if relative:
        require(not {".git", ".agent-work"}.intersection((*Path(value).parts, *path.resolve().relative_to(root.resolve()).parts)),
                f"result_files cannot contain working memory: {value}")
        require(not path.is_dir(), f"result_files must list files, not directories: {value}")
    return path


def evidence_path(run_dir: Path, value: str, *, snapshot=None) -> Path:
    require(isinstance(value, str) and bool(value), "evidence path must be a string")
    path = Path(value)
    require(".." not in path.parts, f"unsafe evidence path: {value}")
    if not path.is_absolute():
        path = run_dir / path
    if snapshot is not None and (path.is_relative_to(snapshot.artifact_root) or path.is_relative_to(snapshot.logical_root)):
        snapshot.key(path)
        return path
    return confined_path(project_root(run_dir, snapshot=snapshot), str(path))


def reference_bytes(run_dir: Path, ref: dict, *, snapshot=None) -> bytes:
    require(isinstance(ref, dict), "evidence reference must be an object")
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    path = evidence_path(run_dir, ref.get("path"), snapshot=snapshot)
    data = snapshot.read_bytes(path)
    section = ref.get("section")
    if section is not None:
        require(isinstance(section, str) and bool(section), "evidence section must be a string")
        text = data.decode("utf-8")
        matches = list(re.finditer(r"(?im)^#{1,6} +" + re.escape(section) + r"[^\S\n]*\n", text))
        require(len(matches) == 1, f"evidence section missing or ambiguous: {section}")
        match = matches[0]
        end = re.search(r"(?m)^#{1,6} +", text[match.end():])
        data = text[match.end():match.end() + end.start() if end else len(text)].encode("utf-8")
    require(bool(data.strip()), f"empty evidence: {ref.get('path')}")
    return data


def verify_reference(run_dir: Path, ref: dict, *, snapshot=None) -> str:
    actual = sha256(reference_bytes(run_dir, ref, snapshot=snapshot))
    require(ref.get("sha256") == actual, f"evidence sha256 mismatch: {ref.get('path')}")
    return actual


def historical_result_hash(run_dir: Path, verification: dict, *, snapshot=None) -> str:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    files = verification.get("result_files")
    require(isinstance(files, list) and bool(files), "change requires non-empty result_files")
    require(all(isinstance(p, str) for p in files), "result_files must contain paths")
    require(len(files) == len(set(files)), "result_files contains duplicates")
    root = project_root(run_dir, snapshot=snapshot)
    entries = []
    for name in sorted(files):
        path = confined_path(root, name, relative=True)
        entries.append([name, sha256(path.read_bytes()) if path.exists() else "deleted"])
    initial = verification.get("initial_snapshot")
    require(isinstance(initial, dict) and isinstance(initial.get("section"), str) and initial["section"].lower() == "initial worktree snapshot",
            "initial_snapshot must reference Initial Worktree Snapshot")
    require(isinstance(initial.get("path"), str) and Path(initial["path"]).name in {"context.md", "route.md"},
            "initial_snapshot must use context.md or route.md")
    refs = {}
    for field in ("initial_snapshot", "task_scope"):
        ref = verification.get(field)
        digest = verify_reference(run_dir, ref, snapshot=snapshot)
        refs[field] = {"path": ref["path"], "section": ref.get("section"), "sha256": digest}
    encoded = json.dumps({"files": entries, **refs}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8"))


def result_contract(snapshot):
    transition = snapshot.operation_receipt("result-contract-v2")
    if transition is not None:
        return transition["result"]
    initialized = snapshot.operation_receipt("initialize")
    return initialized["result"] if initialized else {"result_contract_version": 1}


def historical_identity(summary):
    verification = summary.get("verification", {})
    records = {record.get("lane_id"): record for record in summary.get("subagents", [])}
    selected = []
    for key in ("qa", "reviewer"):
        record = records.get(verification.get(key))
        if record is None:
            return None
        fields = {name: record.get(name) for name in ("role", "lane_id", "codex_thread_id", "completion_turn_id",
                  "reviewed_result_hash", "handoff", "handoff_sha256", "qa_handoff_sha256")}
        fields["evidence"] = sorted(record.get("evidence", []), key=lambda value: json.dumps(value, sort_keys=True))
        selected.append(fields)
    payload = {name: verification.get(name) for name in ("result_hash", "initial_snapshot", "task_scope", "root_thread_id", "task_kind")}
    payload["result_hash"] = verification.get("result_hash") or selected[0]["reviewed_result_hash"]
    payload["result_files"] = sorted(verification.get("result_files", []))
    payload["author_thread_ids"] = sorted(verification.get("author_thread_ids", []))
    payload["behavioral_checks"] = verification.get("behavioral_checks", [])
    payload["accepted"] = selected
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())


def require_workspace_or_historical(snapshot, summary):
    workspace = registered_workspace(snapshot)
    if workspace is not None:
        verify_candidate(workspace)
        return workspace
    contract = result_contract(snapshot)
    if contract.get("result_contract_version", 1) < 2:
        return None
    expected = contract.get("historical_identity")
    require(expected is not None and historical_identity(summary) == expected,
            "fresh change acceptance requires registered and sealed workspace")
    return None


def result_hash(run_dir: Path, verification: dict, *, snapshot=None, require_ready=True):
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    workspace = registered_workspace(snapshot)
    if workspace is not None:
        if "seal" not in workspace and not require_ready:
            return None
        sealed = verify_candidate(workspace)
        files = verification.get("result_files")
        require(isinstance(files, list) and bool(files), "change requires non-empty result_files")
        require(all(isinstance(name, str) for name in files) and len(files) == len(set(files)),
                "result_files must contain distinct paths")
        require(set(files) == set(sealed["changed_paths"]),
                "result_files must equal the full workspace delta")
        initial = verification.get("initial_snapshot")
        require(isinstance(initial, dict) and isinstance(initial.get("section"), str) and
                initial["section"].lower() == "initial worktree snapshot",
                "initial_snapshot must reference Initial Worktree Snapshot")
        require(isinstance(initial.get("path"), str) and Path(initial["path"]).name in {"context.md", "route.md"},
                "initial_snapshot must use context.md or route.md")
        refs = {}
        for name in ("initial_snapshot", "task_scope"):
            reference = verification.get(name)
            require(isinstance(reference, dict), f"missing source reference: {name}")
            refs[name] = {"path": reference.get("path"), "section": reference.get("section"),
                          "sha256": verify_reference(run_dir, reference, snapshot=snapshot)}
        payload = {"hash_version": 2, "workspace_id": workspace["workspace_id"],
                   "candidate_id": sealed["candidate_id"], "baseline_manifest_digest": workspace["baseline_digest"],
                   "candidate_manifest_digest": sealed["candidate_digest"], "delta": sealed["delta"],
                   "metadata_scope": workspace.get("metadata_scope"), **refs}
        return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
    contract = result_contract(snapshot)
    if contract.get("result_contract_version", 1) >= 2:
        summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
        summary["verification"] = verification
        if not require_ready and (contract.get("historical_identity") is None or historical_identity(summary) != contract["historical_identity"]):
            return None
        require_workspace_or_historical(snapshot, summary)
    return historical_result_hash(run_dir, verification, snapshot=snapshot)


def ensure_result_contract(run_dir, source=None):
    """One transition before caller inputs; only a currently eligible old pair is retained."""
    snapshot = JournalSnapshot.open(run_dir)
    if result_contract(snapshot).get("result_contract_version", 1) >= 2:
        return snapshot
    require(snapshot.durable, "import journal before result-contract transition")
    captured = CapturedSource(source or CodexSessionSource())
    summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
    lanes = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
    historical = None
    if registered_workspace(snapshot) is None and summary.get("verification", {}).get("task_kind") == "change":
        errors = validate_verification(run_dir, summary, lanes, "ship", captured, snapshot=snapshot)
        if not errors:
            historical = historical_identity(summary)
    captured.frozen = True
    payload = {"result_contract_version": 2}
    identifier = operation_id(run_dir, "result-contract-transition", payload, identifier="result-contract-v2")
    def transition(current):
        require(current.revision == snapshot.revision, "journal changed during result-contract transition; retry")
        if historical is not None:
            require(not validate_verification(run_dir, summary, lanes, "ship", captured, snapshot=current),
                    "historical acceptance changed during transition")
        return {}, {"result_contract_version": 2, "historical_identity": historical}
    transact(run_dir, identifier, payload, transition)
    return JournalSnapshot.open(run_dir)


class CapturedSource:
    """Capture source reads before a write transaction; later new dependencies refuse."""
    def __init__(self, source):
        self.source, self.events, self.resolutions, self.frozen = source, {}, {}, False

    def read(self, thread_id, *, event_indices=()):
        key = (thread_id, tuple(event_indices))
        if key not in self.events:
            require(not self.frozen, "journal preconditions changed; capture source again")
            self.events[key] = copy.deepcopy(self.source.read(thread_id, event_indices=event_indices))
        return copy.deepcopy(self.events[key])

    def resolve_session(self, agent_path, root_id, role, *, thread_id=None):
        key = (agent_path, root_id, role, thread_id)
        if key not in self.resolutions:
            require(not self.frozen, "journal preconditions changed; resolve source again")
            result = self.source.resolve_session(agent_path, root_id, role, thread_id=thread_id)
            self.resolutions[key] = result
            self.resolutions[(agent_path, root_id, role, result["codex_thread_id"])] = result
        return copy.deepcopy(self.resolutions[key])


def empty_verification() -> dict:
    return {"task_kind": None, "root_thread_id": None, "author_thread_ids": [],
            "result_files": [], "initial_snapshot": None, "task_scope": None,
            "qa": None, "reviewer": None, "behavioral_checks": []}


def validate_summary_shape(data: dict) -> None:
    require(isinstance(data, dict) and type(data.get("version")) is int and data["version"] == 1,
            "delegation-summary.json must be a version 1 object")
    for field in ("subagents_used", "role_lanes_used"):
        require(isinstance(data.get(field), bool), f"delegation-summary.json: {field} must be a boolean")
    require(isinstance(data.get("notes"), str) and bool(data["notes"].strip()),
            "delegation-summary.json: notes must be a non-empty string")
    require(all(isinstance(data.get(k), list) and all(isinstance(r, dict) for r in data[k])
                for k in ("subagents", "role_lanes")), "delegation-summary records must be object arrays")
    ids = [r.get("lane_id") for k in ("subagents", "role_lanes") for r in data[k]]
    require(all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids)),
            "delegation-summary assignment IDs must be non-empty and unique")
    require(isinstance(data.get("verification"), dict), "verification must be an object")


def public_content(content) -> tuple[list[dict], bool]:
    if not isinstance(content, list):
        return [], True
    visible = []
    unavailable = False
    for part in content:
        if (isinstance(part, dict) and part.get("type") in {"input_text", "output_text"}
                and isinstance(part.get("text"), str)):
            visible.append({"type": part["type"], "text": part["text"]})
        else:
            unavailable = True
    return visible, unavailable


class CodexSessionSource:
    """Read only files whose names contain the requested UUID, never exports."""

    def session_dir(self) -> Path:
        codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        return codex_dir / "sessions"

    def resolve_session(self, agent_path: str, root_id: str, role: str, *, thread_id=None) -> dict:
        require(isinstance(agent_path, str) and re.fullmatch(r"/root(?:/[a-z0-9_]+)+", agent_path),
                "--agent-path must be an exact canonical task path")
        require(isinstance(root_id, str) and UUID.fullmatch(root_id),
                "verification.root_thread_id must be registered before --resolve-session")
        matches = []
        try:
            for path in self.session_dir().glob("**/*.jsonl"):
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        event = json.loads(line)
                        require(isinstance(event, dict), "source metadata event must be an object")
                        if event.get("type") != "session_meta":
                            continue
                        meta = event.get("payload", {})
                        require(isinstance(meta, dict), "source metadata payload must be an object")
                        spawn = child_spawn(meta)
                        records = (meta, spawn)
                        if (any(r.get("agent_path") == agent_path for r in records)
                                and any(r.get("parent_thread_id") == root_id for r in records)):
                            require(all(r.get("agent_path") == agent_path for r in records),
                                    "conflicting source agent_path metadata")
                            require(all(r.get("parent_thread_id") == root_id for r in records),
                                    "conflicting source parent_thread_id metadata")
                            require(all(r.get("agent_role") == role for r in records),
                                    "conflicting source agent_role metadata")
                            matches.append(meta.get("id"))
                        break
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("execution unconfirmed: unreadable session metadata during resolution") from exc
        require(bool(matches), "execution unconfirmed: no matching source session; check exact path, root UUID and role")
        require(len(matches) == 1, "execution unconfirmed: multiple source sessions; use a distinct explicit UUID")
        resolved = matches[0]
        require(thread_id is None or thread_id == resolved, "explicit Codex UUID conflicts with resolved source session")
        events = self.read(resolved)
        index, _ = session_metadata(events, resolved, root_id, role, source=self)
        started = next(((i, e) for i, e in enumerate(events[index + 1:], index + 2)
                        if e.get("type") == "event_msg" and e.get("payload", {}).get("type") == "task_started"), None)
        require(started is not None, "execution unconfirmed: own task_started missing")
        observed, _ = event_time(started[1], "started_at")
        return {"codex_thread_id": resolved, "root_thread_id": root_id, "agent_path": agent_path,
                "session_meta_event": index + 1, "task_started_event": started[0],
                "observed_spawn_at": observed.isoformat()}

    def read(self, thread_id: str, *, event_indices=()) -> list[dict]:
        require(isinstance(thread_id, str) and UUID.fullmatch(thread_id), "invalid Codex thread ID")
        paths = list(self.session_dir().glob(f"**/*{thread_id}.jsonl"))
        require(len(paths) == 1, f"execution unconfirmed: source session {thread_id} missing or ambiguous")
        events = []
        try:
            with paths[0].open(encoding="utf-8") as handle:
                for index, line in enumerate(handle, 1):
                    event = json.loads(line)
                    require(isinstance(event, dict), "source event must be an object")
                    # Preserve line positions for behavioral evidence, without retaining reasoning.
                    payload = event.get("payload", {})
                    require(isinstance(payload, dict), "source payload must be an object")
                    kind = event.get("type")
                    if kind == "session_meta":
                        payload = {k: payload[k] for k in ("id", "session_id", "parent_thread_id", "agent_role", "agent_path", "source") if k in payload}
                    elif kind == "turn_context":
                        payload = {k: payload[k] for k in ("turn_id", "model") if k in payload}
                    elif kind == "event_msg" and payload.get("type") in {"task_started", "task_complete"}:
                        payload = {k: payload[k] for k in ("type", "turn_id", "last_agent_message", "started_at", "completed_at") if k in payload}
                    elif kind == "response_item" and payload.get("type") == "message" and (
                        payload.get("role") == "assistant" and payload.get("phase") == "final_answer" or
                        index in event_indices and payload.get("role") in {"user", "assistant"}
                    ):
                        payload = {k: payload[k] for k in ("type", "role", "phase", "content", "id") if k in payload}
                        payload["content"], payload["content_unavailable"] = public_content(payload.get("content", []))
                    elif kind == "response_item" and payload.get("type") == "agent_message" and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "author", "recipient", "content") if k in payload}
                        payload["content"], payload["content_unavailable"] = public_content(payload.get("content", []))
                    elif kind == "response_item" and payload.get("type") == "custom_tool_call_output" and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "output") if k in payload}
                        payload["output"], payload["output_unavailable"] = public_content(payload.get("output"))
                    elif kind == "response_item" and index in event_indices and payload.get("type") == "function_call_output":
                        payload = {k: payload[k] for k in ("type", "call_id", "output") if k in payload}
                    else:
                        payload = {}
                    filtered = {"type": kind, "payload": payload}
                    if "timestamp" in event:
                        filtered["timestamp"] = event["timestamp"]
                    events.append(filtered)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(f"execution unconfirmed: unreadable source session {thread_id}") from exc
        return events


def completion_follows(later: dict, earlier: dict) -> bool:
    if later.get("published_at") is not None and earlier.get("published_at") is not None:
        return later["published_at"] > earlier["published_at"]
    end = earlier.get("completed_before")
    return later["completed_at"] >= end if end is not None else later["completed_at"] > earlier["completed_at"]


def final_json(text) -> dict:
    require(isinstance(text, str), "completion has no structured final answer")
    fenced = re.search(r"```(?:json)?\s*\n(.*?)\n```\s*$", text, re.S)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError as exc:
        raise EvidenceError("completion final answer must end in a JSON object; continue the same assignment with a complete JSON answer, then register its new completion_turn_id") from exc
    require(isinstance(data, dict), "completion final answer must be an object")
    return data


def child_spawn(meta: dict) -> dict:
    spawn = meta.get("source", {})
    spawn = spawn.get("subagent", {}) if isinstance(spawn, dict) else {}
    spawn = spawn.get("thread_spawn", {}) if isinstance(spawn, dict) else {}
    return spawn if isinstance(spawn, dict) else {}


def own_metadata(events, thread_id: str):
    require(isinstance(events, list), "unsupported source events")
    metadata = [e.get("payload", {}) for e in events if e.get("type") == "session_meta"]
    require(len(metadata) == 1, "execution unconfirmed: missing or conflicting session metadata")
    meta = metadata[0]
    require(isinstance(meta, dict), "source session metadata must be an object")
    require(meta.get("id") == thread_id, "source session id mismatch")
    return next(i for i, event in enumerate(events) if event.get("type") == "session_meta"), meta


def verify_conversation_ancestry(source, thread_id: str, root_id: str, conversation_id: str) -> None:
    require(isinstance(conversation_id, str) and UUID.fullmatch(conversation_id), "invalid conversation session_id metadata")
    seen = {thread_id}
    ancestor_id = root_id
    while True:
        require(ancestor_id not in seen, "conflicting session_id metadata: ancestry cycle")
        seen.add(ancestor_id)
        _, meta = own_metadata(source.read(ancestor_id), ancestor_id)
        require(meta.get("session_id") == conversation_id, "conflicting session_id metadata in source ancestry")
        spawn = child_spawn(meta)
        parent = meta.get("parent_thread_id")
        if ancestor_id == conversation_id:
            require(parent is None and not spawn, "conflicting session_id metadata: conversation ancestor is not a root")
            return
        require(isinstance(parent, str) and UUID.fullmatch(parent), "conversation ancestry link unavailable")
        require(spawn.get("parent_thread_id") == parent, "conflicting parent_thread_id in source ancestry")
        require(isinstance(meta.get("agent_role"), str) and spawn.get("agent_role") == meta["agent_role"],
                "conflicting agent_role in source ancestry")
        ancestor_id = parent


def session_metadata(events, thread_id: str, root_id: str, role: str, *, source):
    index, meta = own_metadata(events, thread_id)
    spawn = child_spawn(meta)
    require(isinstance(spawn, dict) and bool(spawn), "execution unconfirmed: not a child session")
    for record in (meta, spawn):
        require(record.get("parent_thread_id") == root_id, "source parent_thread_id mismatch")
        require(record.get("agent_role") == role, f"source agent_role must be {role}")
    if meta.get("session_id") is not None:
        if meta["session_id"] not in (thread_id, root_id):
            verify_conversation_ancestry(source, thread_id, root_id, meta["session_id"])
    return index, meta


def completed_turn(source, thread_id: str, turn_id: str, root_id: str, role: str) -> dict:
    events = source.read(thread_id)
    meta_index, _ = session_metadata(events, thread_id, root_id, role, source=source)
    expected = role_config(read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)["model"]
    require(isinstance(turn_id, str) and bool(turn_id), "completion_turn_id missing")
    active = None
    contexts = []
    finals = []
    completions = []
    last_started = None
    started_event = None
    started_at = None
    for event_index, event in enumerate(events[meta_index + 1:], meta_index + 2):
        payload = event.get("payload", {})
        kind = event.get("type")
        if kind == "event_msg" and payload.get("type") == "task_started":
            require(active != turn_id, "selected turn restarted before completion")
            active = payload.get("turn_id")
            last_started = active
            if active == turn_id:
                contexts, finals = [], []
                started_event = event_index
                started_at, _ = event_time(event, "started_at")
        elif active == turn_id and kind == "turn_context":
            require(payload.get("turn_id") == turn_id, "foreign turn_context in selected turn")
            contexts.append(payload.get("model"))
        elif active == turn_id and kind == "response_item" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
            finals.append("".join(c.get("text", "") for c in payload.get("content", []) if c.get("type") == "output_text"))
        elif kind == "event_msg" and payload.get("type") == "task_complete":
            if payload.get("turn_id") == turn_id:
                require(active == turn_id and bool(contexts), "completion requires own task_started and turn_context")
                require(all(model == expected for model in contexts), f"source model must be {expected}")
                answer = final_json(payload.get("last_agent_message"))
                if finals:
                    require(final_json(finals[-1]) == answer, "completion and final answer disagree")
                observed = source_time(event, "completed_at")
                completed_at, completed_before = observed.event_at, observed.event_before
                require(completed_before > started_at if completed_before is not None else completed_at >= started_at,
                        "source completion is before its own task_started")
                completions.append({"answer": answer, "completed_at": completed_at,
                                    "published_at": observed.observed_at, "time_origin": observed.event_origin,
                                    "completed_before": completed_before, "task_started_event": started_event,
                                    "task_complete_event": event_index, "session_meta_event": meta_index + 1})
            active = None
    require(len(completions) == 1, "execution unconfirmed: own completed turn missing or ambiguous")
    require(last_started == turn_id and active is None, "selected completion is stale or continuation is unfinished")
    return completions[0]


def resolve_completion(source, thread_id: str, root_id: str, role: str, digest: str, handoffs: list[str]) -> str:
    events = source.read(thread_id)
    meta_index, _ = session_metadata(events, thread_id, root_id, role, source=source)
    candidates = []
    for event in events[meta_index + 1:]:
        payload = event.get("payload", {})
        if event.get("type") != "event_msg" or payload.get("type") != "task_complete":
            continue
        try:
            answer = final_json(payload.get("last_agent_message"))
        except EvidenceError:
            continue
        if answer.get("reviewed_result_hash") == digest and answer.get("handoff") in handoffs:
            candidates.append(payload.get("turn_id"))
    require(len(candidates) == 1, "completion resolution missing or ambiguous; supply --completion-turn-id for the current handoff/result hash")
    completed_turn(source, thread_id, candidates[0], root_id, role)
    return candidates[0]


def changed_paths(run_dir: Path, lane_map: dict, verification=None, *, snapshot=None) -> set[str]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    workspace = registered_workspace(snapshot)
    if workspace is not None:
        return set(workspace.get("seal", {}).get("changed_paths", []))
    return declared_paths(run_dir, lane_map, verification or {}, evidence_path=evidence_path,
                          confined_path=confined_path, root=project_root(run_dir, snapshot=snapshot), snapshot=snapshot)


def validate_behavioral_checks(run_dir: Path, checks: list, source, reviewer_id: str, *, snapshot=None):
    """Check captured bytes and observable order; QA judges their meaning."""
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    require(isinstance(checks, list), "behavioral_checks must be an array")
    seen = set()
    for check in checks:
        require(isinstance(check, dict), "behavioral check must be an object")
        criterion = check.get("criterion_id")
        require(isinstance(criterion, str) and criterion and criterion not in seen, "behavioral criterion_id missing or duplicate")
        seen.add(criterion)
        require(check.get("verifier_thread_id") == reviewer_id, "behavioral check requires independent reviewer")
        verify_reference(run_dir, check.get("handoff"), snapshot=snapshot)
        require(isinstance(check.get("strict_inputs"), bool), "behavioral strict_inputs must be boolean")
        inputs, outputs = check.get("inputs"), check.get("outputs")
        require(isinstance(inputs, list) and inputs and isinstance(outputs, list) and outputs,
                "behavioral checks require inputs and outputs")
        require(all(isinstance(ref, dict) for ref in [*inputs, *outputs]), "behavioral references must be objects")
        events = source.read(check.get("session_thread_id"), event_indices=[ref.get("source_event") for ref in [*inputs, *outputs]])
        metadata = [e.get("payload", {}) for e in events if e.get("type") == "session_meta"]
        require(len(metadata) == 1 and metadata[0].get("id") == check.get("session_thread_id"), "behavioral source session id mismatch")
        own_events = set()
        active = False
        for index, event in enumerate(events, 1):
            payload = event.get("payload", {})
            if event.get("type") == "session_meta":
                active = False
                own_events.clear()
            elif event.get("type") == "event_msg" and payload.get("type") == "task_started":
                active = True
            elif event.get("type") == "event_msg" and payload.get("type") == "task_complete":
                active = False
            elif active:
                own_events.add(index)
        positions = []
        for ref in [*inputs, *outputs]:
            verify_reference(run_dir, ref, snapshot=snapshot)
            index = ref.get("source_event")
            require(type(index) is int and 1 <= index <= len(events), "behavioral source_event unavailable")
            require(index in own_events, "behavioral evidence must belong to own session turn")
            event = events[index - 1]
            payload = event.get("payload", {})
            require(event.get("type") == "response_item" and payload.get("type") in {"message", "agent_message", "function_call_output", "custom_tool_call_output"}, "behavioral source must be an observed message or tool output")
            content, unavailable = public_content(payload.get("content", []))
            unavailable = unavailable or payload.get("content_unavailable", False)
            observed = "".join(c["text"] for c in content)
            if ref in inputs:
                if payload.get("type") == "agent_message":
                    meta = metadata[0]
                    parent_id = meta.get("parent_thread_id")
                    session_metadata(events, check["session_thread_id"], parent_id, meta.get("agent_role"), source=source)
                    recipient = meta.get("agent_path")
                    require(isinstance(recipient, str) and child_spawn(meta).get("agent_path") == recipient
                            and payload.get("recipient") == recipient, "behavioral agent_message recipient mismatch")
                    _, parent_meta = own_metadata(source.read(parent_id), parent_id)
                    parent_spawn = child_spawn(parent_meta)
                    parent_path = parent_meta.get("agent_path")
                    if parent_meta.get("parent_thread_id") is None and not parent_spawn:
                        parent_path = "/root"
                    else:
                        require(parent_spawn.get("agent_path") == parent_path, "behavioral parent agent_path metadata conflict")
                    require(isinstance(parent_path, str) and payload.get("author") == parent_path,
                            "behavioral agent_message author must be its source parent")
                else:
                    require(payload.get("type") == "message" and payload.get("role") == "user", "behavioral input source must be a user message")
                prepared = ref.get("prepared_event")
                timeline = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
                require(type(prepared) is int and 1 <= prepared <= len(timeline), "behavioral preparation event missing")
                capture = timeline[prepared - 1]
                require(capture.get("stage") == "behavior-input-prepared", "behavioral preparation requires a behavior-input-prepared event")
                require(capture.get("input_sha256") == ref["sha256"] and ref["path"] in capture.get("artifacts", []), "behavioral preparation does not bind input")
                require(timestamp(capture.get("timestamp")) < timestamp(event.get("timestamp")), "behavioral input was prepared after invocation")
                if unavailable or not observed:
                    require(not check["strict_inputs"], "strict behavioral inputs unconfirmed: source input encrypted or unavailable")
                    continue
            else:
                if payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
                    require(isinstance(ref.get("source_call_id"), str) and ref["source_call_id"] == payload.get("call_id"), "behavioral tool call_id mismatch")
                    if payload.get("type") == "custom_tool_call_output":
                        parts, unavailable = public_content(payload.get("output"))
                        require(not unavailable and not payload.get("output_unavailable", False), "behavioral tool output unavailable")
                        observed = "".join(c["text"] for c in parts)
                    else:
                        observed = payload.get("output")
                    require(isinstance(observed, str), "behavioral tool output unavailable")
                else:
                    require(payload.get("type") == "message" and payload.get("role") == "assistant", "behavioral output source must be assistant message")
                    require(not unavailable, "behavioral assistant output unavailable")
                positions.append(index)
            require(sha256(observed.encode()) == ref["sha256"], "behavioral source bytes mismatch")
        require(positions == sorted(set(positions)), "behavioral outputs must preserve source order")
        require(min(positions) > min(ref["source_event"] for ref in inputs), "behavioral output precedes initial input")
        require(isinstance(check.get("required_order", []), list), "behavioral required_order must be an array")
        for pair in check.get("required_order", []):
            require(isinstance(pair, list) and len(pair) == 2 and all(type(i) is int for i in pair), "behavioral required_order must contain event pairs")
            require(pair[0] in positions and pair[1] in positions and pair[0] < pair[1], "behavioral required source order violated")


def require_own_reviewer_handoff(run_dir, handoff, qa_handoff, *, snapshot=None):
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    reviewer_path = evidence_path(run_dir, handoff, snapshot=snapshot)
    qa_path = evidence_path(run_dir, qa_handoff, snapshot=snapshot)
    reviewer_key, qa_key = snapshot.key(reviewer_path), snapshot.key(qa_path)
    reviewer_identity, qa_identity = snapshot.identities.get(reviewer_key), snapshot.identities.get(qa_key)
    require(reviewer_key != qa_key and not (reviewer_identity is not None and reviewer_identity == qa_identity),
            "reviewer must use its own handoff file, distinct from accepted QA handoff")


def accepted_record(run_dir, record, verification, role, source, *, qa_handoff=None, snapshot=None):
    """Revalidate one source-bound acceptance, including current evidence bytes."""
    thread = record.get("codex_thread_id")
    require(thread not in [verification.get("root_thread_id"), *verification.get("author_thread_ids", [])],
            "QA, reviewer, root and authors must be distinct")
    require(record.get("role") in ({"reviewer", "reviewer.qa"} if role == "reviewer" else {role}), "incorrect canonical role")
    if role == "reviewer":
        require_own_reviewer_handoff(run_dir, record.get("handoff"), qa_handoff, snapshot=snapshot)
    handoff_hash = verify_reference(run_dir, {"path": record.get("handoff"), "sha256": record.get("handoff_sha256")}, snapshot=snapshot)
    text = reference_bytes(run_dir, {"path": record["handoff"]}, snapshot=snapshot).decode()
    refs = record.get("evidence")
    require(isinstance(refs, list) and bool(refs), "evidence references required")
    for ref in refs:
        verify_reference(run_dir, ref, snapshot=snapshot)
        require(ref["path"] in text and ref["sha256"] in text, "evidence reference and sha256 must appear in source-bound handoff")
    digest = verification.get("result_hash") or result_hash(run_dir, verification, snapshot=snapshot)
    require(record.get("reviewed_result_hash") == digest, "reviewed_result_hash is stale")
    completion = completed_turn(source, thread, record.get("completion_turn_id"), verification.get("root_thread_id"), role)
    answer = completion["answer"]
    require(isinstance(answer.get("verdict"), str) and answer["verdict"] in ACCEPTED, "source verdict does not accept result")
    for field, value in (("reviewed_result_hash", digest), ("handoff", record.get("handoff")), ("handoff_sha256", handoff_hash)):
        require(answer.get(field) == value, f"source {field} mismatch")
    return {**completion, "handoff_sha256": handoff_hash}


def validate_verification(run_dir: Path, summary: dict, lane_map: dict, verdict: str | None, source=None, *, snapshot=None) -> list[str]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    try:
        require(isinstance(summary.get("subagents"), list) and all(isinstance(r, dict) for r in summary["subagents"]),
                "subagents must be an object array")
        verification = summary.get("verification")
        require(isinstance(verification, dict), "verification must be an object")
        positive = verdict in POSITIVE
        kind = verification.get("task_kind")
        require(isinstance(kind, str) and kind in {"change", "analysis"} or kind is None and not positive, "verification.task_kind must be change or analysis")
        files = verification.get("result_files")
        require(isinstance(files, list) and all(isinstance(p, str) for p in files), "verification.result_files must be a path array")
        require(len(files) == len(set(files)), "verification.result_files contains duplicates")
        workspace = registered_workspace(snapshot)
        if workspace is None:
            for name in files:
                confined_path(project_root(run_dir, snapshot=snapshot), name, relative=True)
        else:
            require(set(files) == set(workspace.get("seal", {}).get("changed_paths", [])),
                    "result_files must equal the full workspace delta")
        owned = changed_paths(run_dir, lane_map, verification, snapshot=snapshot)
        require(owned <= set(files), "result_files omits run-owned paths: " + ", ".join(sorted(owned - set(files))))
        workers = any(isinstance(lane, dict) and lane.get("type") in {"implementation", "integration"} for lane in lane_map.get("lanes", []))
        require(kind != "analysis" or not (files or owned or workers), "analysis contradicts product changes or worker lanes")
        root_id = verification.get("root_thread_id")
        authors = verification.get("author_thread_ids")
        require(isinstance(authors, list) and all(isinstance(i, str) and UUID.fullmatch(i) for i in authors), "author_thread_ids must contain Codex UUIDs")
        require(len(authors) == len(set(authors)), "duplicate author_thread_ids")
        require(root_id is None and not positive or isinstance(root_id, str) and UUID.fullmatch(root_id), "root_thread_id must be a Codex UUID")
        checks = verification.get("behavioral_checks", [])
        require(isinstance(checks, list), "behavioral_checks must be an array")
        records = {r["lane_id"]: r for r in summary.get("subagents", []) if isinstance(r, dict) and isinstance(r.get("lane_id"), str)}
        for role in ("qa", "reviewer"):
            lane_id = verification.get(role)
            require(lane_id is None or isinstance(lane_id, str) and lane_id in records, f"verification.{role} must reference a subagent record")
        for field in ("initial_snapshot", "task_scope"):
            ref = verification.get(field)
            if ref is not None:
                verify_reference(run_dir, ref, snapshot=snapshot)
        for record in records.values():
            for field in ("reviewed_result_hash", "handoff_sha256"):
                value = record.get(field)
                require(value is None or isinstance(value, str) and SHA256.fullmatch(value), f"subagent {field} must be SHA-256")
            turn = record.get("completion_turn_id")
            require(turn is None or isinstance(turn, str) and bool(turn), "completion_turn_id must be a non-empty string")
            refs = record.get("evidence", [])
            require(isinstance(refs, list), "subagent evidence must be an array")
            for ref in refs:
                require(isinstance(ref, dict) and isinstance(ref.get("sha256"), str) and SHA256.fullmatch(ref["sha256"]), "subagent evidence reference must contain SHA-256")
                evidence_path(run_dir, ref.get("path"), snapshot=snapshot)
                if record["lane_id"] in {verification.get("qa"), verification.get("reviewer")}:
                    verify_reference(run_dir, ref, snapshot=snapshot)
        if not positive:
            if verdict in {"blocked", "fail"}:
                require(isinstance(verification.get("blocker"), str) and verification["blocker"].strip(), "blocked/fail verification requires blocker reason")
            return []
        if kind == "analysis":
            require(not checks, "analysis without product result cannot claim behavioral acceptance")
            return []
        require(bool(authors), "change requires author_thread_ids")
        qa_record = records.get(verification.get("qa"))
        reviewer_record = records.get(verification.get("reviewer"))
        if qa_record and reviewer_record:
            require_own_reviewer_handoff(run_dir, reviewer_record.get("handoff"), qa_record.get("handoff"), snapshot=snapshot)
        require_workspace_or_historical(snapshot, summary)
        worker_lanes = [lane for lane in lane_map.get("lanes", []) if lane.get("type") in {"implementation", "integration"}]
        if not worker_lanes or any(lane.get("execution_mode") == "role-lane" for lane in worker_lanes):
            require(root_id in authors, "root-owned changes require root in author_thread_ids")
        for lane in worker_lanes:
            author = records.get(lane.get("id"), {}).get("codex_thread_id")
            if author:
                require(author in authors, "worker author missing from author_thread_ids")
        require(owned <= set(files), f"result_files omits run-owned paths: {', '.join(sorted(owned - set(files)))}")
        digest = result_hash(run_dir, verification, snapshot=snapshot)
        require(verification.get("result_hash", digest) == digest, "verification.result_hash is stale")
        source = source or CodexSessionSource()
        accepted = {}
        ids = set(authors) | {root_id}
        for key, role in (("qa", "qa-verifier"), ("reviewer", "reviewer")):
            lane_id = verification.get(key)
            require(lane_id in records, f"verification.{key} requires separate {role} subagent")
            record = records[lane_id]
            lane = next((l for l in lane_map.get("lanes", []) if l.get("id") == lane_id), None)
            if lane is not None:
                require(lane.get("status") in {"pass", "pass-with-risks"} and lane.get("execution_mode") == "subagent", f"{key}: lane must be a successful subagent")
            thread_id = record.get("codex_thread_id")
            require(isinstance(thread_id, str) and UUID.fullmatch(thread_id), f"{key}: invalid child thread ID")
            require(thread_id not in ids, f"{key}: QA, reviewer, root and authors must be distinct")
            ids.add(thread_id)
            require(isinstance(record.get("role"), str) and record["role"] in ({"reviewer", "reviewer.qa"} if key == "reviewer" else {"qa-verifier"}), f"{key}: incorrect canonical role")
            completion = accepted_record(run_dir, record, verification, role, source,
                                         qa_handoff=accepted.get("qa", {}).get("answer", {}).get("handoff"), snapshot=snapshot)
            answer = completion["answer"]
            if key == "reviewer":
                require(answer.get("qa_handoff_sha256") == accepted["qa"]["handoff_sha256"], "reviewer: source qa_handoff_sha256 mismatch")
                require(completion_follows(completion, accepted["qa"]), "reviewer acceptance must follow QA completion")
            accepted[key] = completion
        validate_behavioral_checks(run_dir, checks, source, records[verification["reviewer"]]["codex_thread_id"], snapshot=snapshot)
        return []
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"verification: {exc}"]
