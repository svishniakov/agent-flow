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


def confined_path(root: Path, value: str, *, relative=False, inputs=None) -> Path:
    if inputs is not None:
        key = "confined-path:" + json.dumps([str(root), value, relative])
        return Path(inputs.capture(key, lambda: str(confined_path(root, value, relative=relative))))
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
    if snapshot is not None:
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
        inputs = snapshot.external_inputs
        path = confined_path(root, name, relative=True, inputs=inputs)
        from validation_inputs import captured
        raw = captured(inputs, "result-file:" + str(path), lambda: path.read_bytes() if path.exists() else None)
        entries.append([name, sha256(raw) if raw is not None else "deleted"])
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
        verify_candidate(workspace, inputs=snapshot.external_inputs)
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
        sealed = verify_candidate(workspace, inputs=snapshot.external_inputs)
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
    from journal_lifecycle import capture_validation, freeze_validation
    from dataclasses import replace
    snapshot, captured = capture_validation(snapshot, session_source=source)
    summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
    lanes = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
    historical = None
    if registered_workspace(snapshot) is None and summary.get("verification", {}).get("task_kind") == "change":
        errors = validate_verification(run_dir, summary, lanes, "ship", captured, snapshot=snapshot)
        if not errors:
            historical = historical_identity(summary)
    freeze_validation(snapshot, captured)
    payload = {"result_contract_version": 2}
    identifier = operation_id(run_dir, "result-contract-transition", payload, identifier="result-contract-v2")
    def transition(current):
        current = replace(current, external_inputs=snapshot.external_inputs)
        require(current.revision == snapshot.revision, "journal changed during result-contract transition; retry")
        if historical is not None:
            require(not validate_verification(run_dir, summary, lanes, "ship", captured, snapshot=current),
                    "historical acceptance changed during transition")
        return {}, {"result_contract_version": 2, "historical_identity": historical}
    transact(run_dir, identifier, payload, transition)
    return JournalSnapshot.open(run_dir)


class CapturedSource:
    """Capture source reads before a write transaction; later new dependencies refuse."""
    def __init__(self, source, *, inputs=None):
        self.source, self.events, self.resolutions, self.frozen = source, {}, {}, False
        self.inputs = inputs

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


def evaluate_obligations(snapshot, summary):
    """Resolve explicit replacements without changing any original outcome."""
    records = {r.get("lane_id"): r for kind in ("subagents", "role_lanes")
               for r in summary.get(kind, []) if isinstance(r, dict)}
    resolved, errors = set(), []
    visiting = set()
    if snapshot.archives:
        from journal_recovery import assignments
        originals = [snapshot.archive(k) for k in snapshot.archives if snapshot.archive(k).storage_version == 1]
        if originals:
            inventory = assignments(originals[0])
            classification = json.loads(snapshot.documents.get("artifacts/lifecycle/legacy-classification.json", b"{}"))
            corrections = classification.get("corrections", {})
            planned = classification.get("planned", {})
            for lane_id in inventory:
                if lane_id not in records and lane_id not in corrections:
                    errors.append("uncovered legacy assignment: " + lane_id)
                elif lane_id in planned and lane_id in records:
                    obligation = records[lane_id].get("obligation", {})
                    if obligation.get("id") != planned[lane_id]["obligation_id"] or obligation.get("required") is not True:
                        errors.append("planned legacy obligation mismatch: " + lane_id)
                elif lane_id in records and not records[lane_id].get("legacy"):
                    errors.append("unclassified legacy assignment: " + lane_id)
    def check(lane_id):
        if lane_id in visiting:
            raise EvidenceError("cyclic obligation replacement: " + str(lane_id))
        require(lane_id in records, "missing obligation replacement: " + str(lane_id))
        record = records[lane_id]
        obligation = record.get("obligation")
        require(isinstance(obligation, dict), "unresolved obligation: " + str(lane_id))
        require(isinstance(obligation.get("id"), str) and bool(obligation["id"].strip()),
                "obligation requires an id: " + str(lane_id))
        require(type(obligation.get("required")) is bool, "obligation requires explicit required flag")
        state = obligation.get("state")
        events = [json.loads(line) for line in snapshot.read_text(record["trace"]).splitlines() if line.strip()] if record.get("trace") else []
        events = [event for event in events if event.get("lane_id") == lane_id]
        terminal = events[-1] if events else {}
        if state == "current":
            if record.get("legacy"):
                legacy = record["legacy"]
                require(legacy.get("outcome") in {"pass", "pass-with-risks"},
                        "legacy assignment requires explicit current replacement: " + str(lane_id))
                latest = max(assignments(snapshot)[lane_id]["events"],
                             key=lambda e: (e["event"].get("timestamp", ""), e["index"]))
                require(latest["event"] == legacy["terminal"]["event"], "legacy assignment has newer unresolved work")
                return obligation["id"]
            require(record.get("status") in {"pass", "pass-with-risks"}, "current obligation not accepted: " + str(lane_id))
            require(terminal.get("stage") == "handoff" and terminal.get("status") == record["status"],
                    "current obligation terminal status mismatch: " + str(lane_id))
            require(record.get("handoff") in terminal.get("artifacts", []), "current obligation handoff missing")
            require(bool(snapshot.read_bytes(record["handoff"]).strip()), "current obligation handoff empty")
            return obligation["id"]
        require(state in {"resolved", "exempt"}, "unsupported obligation state: " + str(lane_id))
        resolution = obligation.get("resolution")
        require(isinstance(resolution, dict) and resolution.get("original_lane") == lane_id,
                "resolution must reference original lane")
        require(isinstance(resolution.get("reason"), str) and bool(resolution["reason"].strip()), "resolution reason missing")
        legacy = record.get("legacy")
        if legacy:
            original = snapshot if legacy.get("continued") else snapshot.archive(legacy["archive_id"])
            require(legacy["terminal"]["event"] in original.timeline() or
                    legacy["terminal"]["event"] in [json.loads(l) for l in original.read_text(legacy["terminal"]["path"]).splitlines()],
                    "historical terminal missing from immutable archive")
        require(bool(legacy) or terminal.get("status") == record.get("status") and terminal.get("stage") in {"handoff", "blocked", "fail"},
                "historical outcome missing or active: " + str(lane_id))
        refs = resolution.get("evidence")
        require(isinstance(refs, list) and bool(refs), "resolution evidence missing")
        for ref in refs:
            verify_reference(snapshot.artifact_root, ref, snapshot=snapshot)
        if state == "exempt":
            require(obligation["required"] is False and (record.get("purpose") == "consultation" or legacy and legacy["classification"].get("optional") is True),
                    "only an explicitly optional consultation may be exempt")
            require((legacy.get("outcome") if legacy else record.get("status")) in {"pass", "pass-with-risks"},
                    "negative consultation must resolve its findings")
        else:
            visiting.add(lane_id)
            replacement = resolution.get("replacement")
            require(check(replacement) == obligation["id"], "replacement covers a different obligation")
            require(any(ref.get("path") == records[replacement].get("handoff") for ref in refs),
                    "resolution must reference replacement handoff evidence")
            visiting.remove(lane_id)
        resolved.add(lane_id)
        return obligation["id"]
    for lane_id in records:
        try:
            check(lane_id)
        except (EvidenceError, KeyError, FileNotFoundError, TypeError) as exc:
            errors.append("delegation-summary.json: " + str(exc))
            visiting.clear()
    for key in ("qa", "reviewer"):
        selected = summary.get("verification", {}).get(key)
        if selected in resolved:
            errors.append("selected " + key + " must be a current obligation")
    return resolved, errors


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
            with paths[0].open(encoding="utf-8", newline="") as handle:
                for index, line in enumerate(handle, 1):
                    event = json.loads(line)
                    require(isinstance(event, dict), "source event must be an object")
                    # Preserve line positions for behavioral evidence, without retaining reasoning.
                    payload = event.get("payload", {})
                    require(isinstance(payload, dict), "source payload must be an object")
                    kind = event.get("type")
                    if kind == "session_meta":
                        payload = {k: payload[k] for k in ("id", "session_id", "parent_thread_id", "agent_role", "agent_path", "source", "cwd") if k in payload}
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
                    elif kind == "response_item" and payload.get("type") == "custom_tool_call" and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "name", "input") if k in payload}
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
                    if index in event_indices:
                        filtered["source_sha256"] = sha256(line.encode())
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
    from validation_inputs import captured
    inputs = getattr(source, "inputs", None)
    expected = captured(inputs, "role-model:" + role, lambda: role_config(
        read_frontmatter(resolve_role_path(default_agents_dir(), role), inputs=inputs), role)["model"])
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
                          confined_path=lambda root, value, **kw: confined_path(root, value, inputs=snapshot.external_inputs, **kw),
                          root=project_root(run_dir, snapshot=snapshot), snapshot=snapshot)


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
                confined_path(project_root(run_dir, snapshot=snapshot), name, relative=True, inputs=snapshot.external_inputs)
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
            reopens = [e for e in snapshot.timeline() if e.get("stage") == "reopen"]
            if reopens:
                latest = reopens[-1]
                archived = snapshot.archive(latest["archive_id"])
                previous = json.loads(archived.documents.get("delegation-summary.json", b"{}"))
                require(lane_id not in {r.get("lane_id") for r in previous.get("subagents", [])},
                        "reopened generation requires new QA/reviewer assignments")
                started = source.read(thread_id)[completion["task_started_event"] - 1]
                require(event_time(started, "started_at")[0] >= timestamp(latest["timestamp"]),
                        "reopened acceptance must start after reopen")
            answer = completion["answer"]
            if key == "reviewer":
                require(answer.get("qa_handoff_sha256") == accepted["qa"]["handoff_sha256"], "reviewer: source qa_handoff_sha256 mismatch")
                require(completion_follows(completion, accepted["qa"]), "reviewer acceptance must follow QA completion")
            accepted[key] = completion
        validate_behavioral_checks(run_dir, checks, source, records[verification["reviewer"]]["codex_thread_id"], snapshot=snapshot)
        return []
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"verification: {exc}"]

def trace_source_calls(text):
    """Decode the observed literal exec wrapper, never evaluate JavaScript."""
    decoder = json.JSONDecoder()
    calls = []
    while text.strip():
        text = text.lstrip()
        match = re.match(r'text\(await tools\.(exec_command|write_stdin)\(\{', text)
        require(match is not None, 'unsupported trace source wrapper')
        name = match[1]
        text = text[match.end():]
        values = {}
        while True:
            text = text.lstrip()
            if text.startswith('"'):
                key, end = decoder.raw_decode(text)
            else:
                key_match = re.match(r'(cmd|session_id|chars)\b', text)
                require(key_match is not None, 'unsupported trace source key')
                key, end = key_match[1], key_match.end()
            require(key not in values, 'duplicate trace source key')
            text = text[end:].lstrip()
            require(text.startswith(':'), 'trace source key separator missing')
            value, end = decoder.raw_decode(text[1:].lstrip())
            values[key] = value
            text = text[1:].lstrip()[end:].lstrip()
            if text.startswith('}));'):
                text = text[5:]
                break
            require(text.startswith(','), 'unsupported trace source expression')
            text = text[1:]
        allowed = {'yield_time_ms', 'max_output_tokens'} | ({'cmd', 'workdir'} if name == 'exec_command' else {'session_id', 'chars'})
        require(set(values) <= allowed, 'unsupported trace source option')
        require(all(type(values[k]) is int for k in ('yield_time_ms', 'max_output_tokens', 'session_id') if k in values),
                'trace source numeric option must be literal')
        if name == 'exec_command':
            require(isinstance(values.get('cmd'), str), 'literal source cmd required')
        else:
            require(type(values.get('session_id')) is int and values.get('chars') == '', 'only empty source polling supported')
        calls.append((name, values))
    require(bool(calls), 'empty trace source wrapper')
    return calls


def trace_recorder_argv(command, cwd):
    """Recognize the two recorded Python forms without executing their contents."""
    import ast
    import posixpath
    import shlex
    require(isinstance(cwd, str) and cwd.startswith('/'), 'absolute source cwd required')
    lines = command.splitlines()
    require(lines and lines[0] == "python3 -B - <<'PY'", 'unsupported source heredoc')
    ends = [i for i, line in enumerate(lines) if line == 'PY']
    require(len(ends) == 1, 'ambiguous source heredoc boundary')
    end = ends[0]
    shell_argv = None
    if end != len(lines) - 1:
        require(end == len(lines) - 2, 'unsupported source shell remainder')
        line = lines[-1]
        quote = None
        for character in line:
            if quote == "'":
                if character == "'":
                    quote = None
            elif character in "'\"":
                quote = None if quote == character else character
            else:
                require(character not in '`$\\' and (quote is not None or character not in '|;&<>'),
                        'dynamic source shell arguments')
        require(quote is None, 'unterminated source shell quote')
        argv = shlex.split(line)
        require(argv[:2] == ['python3', '-B'], 'source executable must be python3 -B')
        shell_argv = argv
    module = ast.parse('\n'.join(lines[1:end]))
    allowed_imports = {'sys', 'json', 'subprocess'}
    allowed_from = {'pathlib': {'Path'}, 'journal_io': {'JournalSnapshot'}, 'verification_evidence': {'evaluate_obligations'}}
    allowed_calls = {'Path', 'str', 'print', 'next', 'len', 'evaluate_obligations', 'sys.path.insert',
                     'JournalSnapshot.open', 'json.loads', 'json.dumps', 's.read_text', 'v.pop', 'v.get', 'subprocess.run'}
    def dotted(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return dotted(node.value) + '.' + node.attr
        return ''
    for node in ast.walk(module):
        require(not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
                                      ast.With, ast.AsyncWith, ast.Try, ast.While, ast.If, ast.Delete, ast.AugAssign)),
                'unsupported source preparation control')
        if isinstance(node, ast.Import):
            require(all(a.name in allowed_imports and a.asname is None for a in node.names), 'unsupported source preparation import')
        if isinstance(node, ast.ImportFrom):
            require(node.level == 0 and node.module in allowed_from
                    and all(a.name in allowed_from[node.module] and a.asname is None for a in node.names),
                    'unsupported source preparation import')
        if isinstance(node, ast.Call):
            name = dotted(node.func)
            path_call = isinstance(node.func, ast.Attribute) and node.func.attr in {'resolve', 'write_text'}
            require(name in allowed_calls or path_call, 'unsupported source preparation call')
            if path_call:
                require(isinstance(node.func.value, (ast.Name, ast.Call, ast.BinOp)), 'unsupported source path receiver')
                if isinstance(node.func.value, ast.Name):
                    require(node.func.value.id in {'p', 'r', 'o', 'f'}, 'unsupported source path receiver')
    protected = {'sys', 'subprocess', 'Path', 'json', 'JournalSnapshot', 'evaluate_obligations',
                 'str', 'print', 'next', 'len', 'command', 'p', 'r', 'o', 'f'}
    assignments = {}
    command_statement = None
    run_statement = None
    for statement in module.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            require(all(alias.asname is None for alias in statement.names), 'source import alias forbidden')
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in protected:
                require(isinstance(statement, ast.Assign) and len(statement.targets) == 1
                        and isinstance(statement.targets[0], ast.Name) and node is statement.targets[0],
                        'indirect source binding forbidden')
                require(node.id not in assignments and node.id in {'command', 'p', 'r', 'o', 'f'},
                        'source binding reassigned')
                assignments[node.id] = statement.value
                if node.id == 'command':
                    command_statement = statement
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
                require(isinstance(node.value, ast.Name) and node.value.id == 'c'
                        and isinstance(node.slice, ast.Constant) and node.slice.value == 'verifier_thread_id',
                        'source subscript mutation forbidden')
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == 'command':
                require(isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Attribute) and dotted(statement.value.func) == 'subprocess.run'
                        and node in statement.value.args or
                        isinstance(statement, ast.Assign) and len(statement.targets) == 1
                        and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == 'res' and run_statement is not None
                        and isinstance(statement.value, ast.Dict) and any(isinstance(key, ast.Constant)
                            and key.value == 'command' and value is node for key, value in zip(statement.value.keys, statement.value.values)),
                        'source command alias forbidden')
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                require(not isinstance(node.value, ast.Name) or node.value.id not in protected,
                        'source attribute mutation forbidden')
            if isinstance(node, ast.Call):
                require(not isinstance(node.func, ast.Name) or node.func.id not in {'eval', 'exec', 'compile', '__import__'},
                        'dynamic source code forbidden')
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    owner = node.func.value.id
                    require(owner != 'command', 'source command mutation forbidden')
                    if owner == 'subprocess':
                        require(node.func.attr == 'run' and isinstance(statement, ast.Assign)
                                and statement.value is node and command_statement is not None and run_statement is None,
                                'source recorder call must be direct and unique')
                        require(len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == 'command'
                                and {k.arg: getattr(k.value, 'value', None) for k in node.keywords} == {'capture_output': True, 'text': True}
                                and len(node.keywords) == 2, 'unsupported source subprocess invocation')
                        run_statement = statement
    def path_value(node, visiting=()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            require(node.id in {'p', 'r', 'o', 'f'} and node.id in assignments and node.id not in visiting,
                    'unknown source path binding')
            definition = next(s for s in module.body if isinstance(s, ast.Assign) and s.value is assignments[node.id])
            require(command_statement is None or module.body.index(definition) < module.body.index(command_statement), 'source path binding must precede command')
            return path_value(assignments[node.id], (*visiting, node.id))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return posixpath.join(path_value(node.left, visiting), node.right.value)
        if isinstance(node, ast.Call) and not node.keywords:
            if isinstance(node.func, ast.Name) and node.func.id in {'str', 'Path'} and len(node.args) == 1:
                return path_value(node.args[0], visiting)
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'resolve' and not node.args:
                return posixpath.normpath(posixpath.join(cwd, path_value(node.func.value, visiting)))
        raise EvidenceError('unsupported source path expression')
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'write_text':
            target = posixpath.normpath(posixpath.join(cwd, path_value(node.func.value)))
            require(target.startswith(cwd.rstrip('/') + '/') and target.endswith('.json'),
                    'source preparation may only write JSON captures within cwd')
    def check_import_path(argv):
        script_directory = posixpath.dirname(posixpath.normpath(posixpath.join(cwd, argv[2])))
        for index, statement in enumerate(module.body):
            for node in ast.walk(statement):
                if isinstance(node, ast.Call) and dotted(node.func) == 'sys.path.insert':
                    require(len(node.args) == 2 and not node.keywords and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value == 0 and posixpath.normpath(posixpath.join(cwd, path_value(node.args[1]))) == script_directory,
                            'source import path must match recorder directory')
                    if shell_argv is None:
                        require(any(isinstance(prior, ast.Import) and any(a.name == 'subprocess' for a in prior.names)
                                    for prior in module.body[:index]), 'subprocess must be imported before local import path')
    if shell_argv is not None:
        require(command_statement is None and run_statement is None, 'multiple source recorder invocations')
        check_import_path(shell_argv)
        return shell_argv
    require(command_statement is not None and run_statement is not None, 'source recorder invocation missing')
    require(isinstance(command_statement.value, ast.List), 'source argv must be a literal list')
    argv = []
    path_slots = {2}
    for index, node in enumerate(command_statement.value.elts):
        if index == 0:
            require(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == 'sys' and node.attr == 'executable', 'source executable must be sys.executable')
            argv.append('python3')
        elif index in path_slots or (argv and argv[-1] in {'--run-dir', '--verification-json'}):
            argv.append(path_value(node))
        else:
            require(isinstance(node, ast.Constant) and isinstance(node.value, str), 'source argv argument must be literal')
            argv.append(node.value)
    require(argv[:2] == ['python3', '-B'], 'source executable requires -B')
    check_import_path(argv)
    return argv

def verify_trace_mode_correction(snapshot, correction, source):
    """Return exact trace addresses justified by root calls and original receipts."""
    from journal_io import encode_json, digest
    require(set(correction) == {'run_uuid', 'generation', 'revision', 'root_thread_id', 'operation_id',
                                'reason', 'timeline', 'trace', 'targets'}, 'unsupported mode correction fields')
    require(correction['run_uuid'] == snapshot.run_uuid and isinstance(correction['reason'], str)
            and bool(correction['reason'].strip()), 'mode correction run identity or reason invalid')
    summary = json.loads(snapshot.read_text('delegation-summary.json'))
    root = summary['verification']['root_thread_id']
    require(root == correction['root_thread_id'], 'mode correction root identity changed')
    require(type(correction['generation']) is int and 1 <= correction['generation'] <= snapshot.generation
            and type(correction['revision']) is int and correction['revision'] <= snapshot.revision,
            'mode correction version invalid')
    documents = []
    for key, path in [('timeline', 'timeline.jsonl'), ('trace', 'agents/orchestrator/trace.jsonl')]:
        ref = correction[key]
        require(set(ref) == {'path', 'size', 'sha256'} and ref['path'] == path and type(ref['size']) is int and ref['size'] > 0,
                'mode correction document reference invalid')
        raw = snapshot.read_bytes(path)
        prefix = raw[:ref['size']]
        require(len(prefix) == ref['size'] and prefix.endswith(b'\n') and sha256(prefix) == ref['sha256'],
                'mode correction prefix hash changed')
        documents.append(prefix.splitlines(keepends=True))
    targets = correction['targets']
    require(isinstance(targets, list) and bool(targets), 'mode correction targets required')
    seen = set()
    for target in targets:
        require(set(target) == {'timeline_index', 'trace_index', 'sha256', 'old', 'new', 'request', 'source'},
                'unsupported mode correction target fields')
        require(target['old'] == {'execution_mode': 'subagent'} and target['new'] == {'execution_mode': 'role-lane'},
                'only subagent to role-lane correction permitted')
        indices = (target['timeline_index'], target['trace_index'])
        require(all(type(i) is int and 1 <= i <= len(lines) for i, lines in zip(indices, documents)),
                'mode correction target index invalid')
        require(indices[1] not in seen, 'duplicate mode correction target')
        seen.add(indices[1])
        left, right = [lines[i-1] for i, lines in zip(indices, documents)]
        require(left == right and sha256(left) == target['sha256'], 'mode correction raw line hash mismatch')
        event = json.loads(left)
        allowed = {'timestamp', 'stage', 'role', 'stable_agent_name', 'stable_agent_slug', 'status', 'summary',
                   'artifacts', 'next_step', 'execution_mode', 'agent_trace', 'agent_artifact_dir'}
        require(set(event) <= allowed and event.get('role') == 'orchestrator'
                and event.get('execution_mode') == 'subagent' and event.get('artifacts') == []
                and (event.get('stage'), event.get('status')) in {('verification-prepared', 'active'), ('verification-ready', 'pass')}
                and event.get('agent_trace') == 'agents/orchestrator/trace.jsonl', 'target is not root verification metadata')
        request = target['request']
        require(set(request) == {'run_uuid', 'operation_id', 'command', 'payload'}
                and request['run_uuid'] == snapshot.run_uuid and request['command'] == 'record-agent-trace',
                'original mode request envelope invalid')
        receipt = snapshot.operation_receipt(request['operation_id'])
        require(receipt is not None and receipt['payload_sha256'] == digest(encode_json(request['payload']).encode())
                and receipt['revision'] <= correction['revision'], 'original mode receipt mismatch')
        payload = request['payload']
        require(set(payload) <= {'agent_path', 'artifact', 'codex_thread_id', 'completion_turn_id', 'critical',
            'execution_mode', 'lane_id', 'next_step', 'obligation_id', 'prepare_conclusion', 'render_final',
            'resolve_session', 'role', 'runtime_nickname', 'stable_agent_name', 'stable_agent_slug', 'stage', 'status',
            'summary', 'verification_json', 'wave', 'captured_references'}, 'unsupported original mode payload fields')
        require(not payload.get('prepare_conclusion') and not payload.get('render_final'), 'original request is not metadata')
        require(payload.get('next_step', '') == event.get('next_step', '') and
                (payload.get('stable_agent_name') or 'orchestrator') == event.get('stable_agent_name') and
                (payload.get('stable_agent_slug') or 'orchestrator') == event.get('stable_agent_slug'),
                'original metadata fields differ from target')
        identity_fields = ('role', 'stage', 'status', 'summary')
        signature = tuple(event.get(key) for key in identity_fields)
        require(all(sum(tuple(json.loads(raw).get(key) for key in identity_fields) == signature
                        for raw in lines) == 1 for lines in documents), 'ambiguous original metadata event')
        for field in ('role', 'stage', 'status', 'summary', 'execution_mode'):
            require(payload.get(field) == event.get(field), 'original mode payload differs from target')
        require(payload.get('artifact') == [] and payload.get('execution_mode') == 'subagent'
                and all(not payload.get(k) for k in ('codex_thread_id', 'lane_id', 'agent_path', 'completion_turn_id',
                    'resolve_session', 'runtime_nickname', 'wave', 'critical', 'obligation_id')),
                'original mode request has assignment identity')
        require(isinstance(payload.get('verification_json'), str), 'original verification payload missing')
        verification = json.loads(payload['verification_json'])
        require(verification.get('root_thread_id') == root, 'original verification root differs')
        steps = target['source']
        require(isinstance(steps, list) and bool(steps), 'mode source chain required')
        refs = [ref for step in steps for ref in step.values()]
        require(all(set(step) == {'call', 'output'} for step in steps)
                and all(set(ref) == {'index', 'sha256'} and type(ref['index']) is int and ref['index'] > 0 for ref in refs),
                'mode source references invalid')
        selected = [r['index'] for r in refs]
        require(selected == sorted(set(selected)), 'mode source chain order invalid')
        events = source.read(root, event_indices=selected)
        _, metadata = own_metadata(events, root)
        require(not child_spawn(metadata) and not metadata.get('parent_thread_id'), 'mode correction actor is not root')
        session_id = None
        result = None
        for step_number, step in enumerate(steps):
            values = []
            for ref in (step['call'], step['output']):
                require(ref['index'] <= len(events), 'mode source event missing')
                observed = events[ref['index']-1]
                require(observed.get('source_sha256') == ref['sha256'], 'mode source raw hash mismatch')
                require(observed.get('type') == 'response_item', 'mode source item type invalid')
                values.append(observed['payload'])
            call, output = values
            require(call.get('type') == 'custom_tool_call' and call.get('name') == 'exec'
                    and output.get('type') == 'custom_tool_call_output' and call.get('call_id')
                    and output.get('call_id') == call['call_id'] and not output.get('output_unavailable'),
                    'mode source call/output identity mismatch')
            calls = trace_source_calls(call['input'])
            chunks = []
            for part in output['output']:
                text = part.get('text', '')
                if text.startswith('Script completed\n'):
                    continue
                chunks.append(json.loads(text))
            require(len(chunks) == len(calls), 'ambiguous mode source output position')
            if step_number == 0:
                positions = [i for i, (name, _) in enumerate(calls) if name == 'exec_command']
                require(len(positions) == 1, 'ambiguous source recorder invocation')
                position = positions[0]
                options = calls[position][1]
                cwd = options.get('workdir', metadata.get('cwd'))
                argv = trace_recorder_argv(options['cmd'], cwd)
                import posixpath
                absolute = lambda value: posixpath.normpath(posixpath.join(cwd, value))
                script = Path(absolute(argv[2]))
                require(script.name == 'record-agent-trace.py' and script.parent.name == 'scripts'
                        and script.parent.parent.name == 'agent-flow', 'source script is not canonical recorder')
                require(script.is_relative_to(snapshot.source_root), 'source recorder outside project root')
                require(len(argv[3:]) % 2 == 0, 'unsupported recorder flags')
                flags = dict(zip(argv[3::2], argv[4::2]))
                require(len(flags)*2 == len(argv[3:]) and set(flags) == {'--run-dir', '--operation-id', '--role',
                    '--stage', '--status', '--verification-json', '--summary'}, 'source recorder flags must be explicit metadata only')
                # logical_root preserves the original run identity in a disposable copy.
                require(absolute(flags['--run-dir']) == str(snapshot.logical_root), 'source recorder run path mismatch')
                require(flags['--operation-id'] == request['operation_id'], 'source recorder operation mismatch')
                for field in ('role', 'stage', 'status', 'summary'):
                    require(flags['--'+field] == payload[field], 'source recorder argv differs from saved payload')
            else:
                positions = [i for i, (name, options) in enumerate(calls)
                             if name == 'write_stdin' and options['session_id'] == session_id]
                require(len(positions) == 1, 'source async session mismatch')
                position = positions[0]
            result = chunks[position]
            if step_number < len(steps)-1:
                require('exit_code' not in result and type(result.get('session_id')) is int, 'source async continuation missing')
                session_id = result['session_id']
        require(result.get('exit_code') == 0, 'source recorder did not complete successfully')
        receipts = [json.loads(line[len('receipt: '):]) for line in result.get('output', '').splitlines()
                    if line.startswith('receipt: ')]
        require(receipts == [{'operation_id': request['operation_id'], **receipt['result']}],
                'source recorder result differs from canonical receipt')
    return seen


def effective_trace_modes(snapshot, source):
    """Keep raw history intact; expose only verified corrections by trace index."""
    from journal_io import encode_json, digest
    corrected = set()
    timeline = snapshot.read_bytes('timeline.jsonl').splitlines(keepends=True)
    trace_path = 'agents/orchestrator/trace.jsonl'
    for index, raw in enumerate(timeline, 1):
        event = json.loads(raw)
        if event.get('stage') != 'trace-mode-corrected':
            continue
        proof = event.get('mode_correction')
        require(isinstance(proof, dict), 'mode correction proof missing')
        receipt = snapshot.operation_receipt(proof.get('operation_id'))
        require(receipt is not None and receipt['payload_sha256'] == digest(encode_json({'command': 'trace-mode-correction', 'correction': proof}).encode()),
                'mode correction receipt missing or conflicting')
        require(set(event) == {'timestamp', 'stage', 'role', 'stable_agent_name', 'stable_agent_slug', 'status',
            'summary', 'artifacts', 'next_step', 'execution_mode', 'generation', 'mode_correction', 'agent_trace'}
                and event.get('summary') == proof['reason'] and event.get('artifacts') == []
                and event.get('agent_trace') == trace_path and event.get('next_step') == ''
                and event.get('stable_agent_name') == event.get('stable_agent_slug') == 'orchestrator', 'mode correction audit fields invalid')
        require(receipt['revision'] == proof['revision'] + 1 and receipt['result'] == {
            'corrected': len(proof['targets']), 'generation': proof['generation'], 'revision': receipt['revision']},
            'mode correction receipt result invalid')
        require(event.get('execution_mode') == 'role-lane' and event.get('role') == 'orchestrator'
                and event.get('status') == 'done' and event.get('generation') == proof['generation'],
                'mode correction audit invalid')
        require(timeline[:index-1] and b''.join(timeline[:index-1]) == snapshot.read_bytes('timeline.jsonl')[:proof['timeline']['size']],
                'mode correction audit must immediately follow captured prefix')
        traces = snapshot.read_bytes(trace_path).splitlines(keepends=True)
        require(b''.join(traces)[:proof['trace']['size']] + raw == b''.join(traces)[:proof['trace']['size']+len(raw)],
                'mode correction trace audit mismatch')
        addresses = verify_trace_mode_correction(snapshot, proof, source)
        require(not corrected.intersection(addresses), 'target already has a mode correction')
        corrected.update(addresses)
    return {(trace_path, index): 'role-lane' for index in corrected}
