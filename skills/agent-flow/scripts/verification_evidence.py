from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from agent_config import default_agents_dir, read_frontmatter, role_config, resolve_role_path
from journal_io import JournalError, event_time, source_time

UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
EvidenceError = JournalError

def require(condition, message):
    if not condition:
        raise EvidenceError(message)

def sha256(data):
    return hashlib.sha256(data).hexdigest()


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


class _SessionEvents(list):
    def __init__(self):
        super().__init__()
        self.metadata = {}


class CodexSessionSource:
    """Read the current native session selected by the Codex index, never exports."""

    def __init__(self):
        self._raw = {}
        self._views = {}
        self._own = {}
        self._native = {}
        self._validated = set()
        self._turn_index = {}

    def turn_index(self, thread):
        if thread not in self._turn_index:
            events = self.read(thread)
            own = tuple(_own_session_events(self, events, thread))
            groups, started = {}, []
            completed, active, last_started = set(), None, None
            duplicate_completions = set()
            for index, event in own:
                payload = event.get('payload', {})
                kind = payload.get('type') if event.get('type') == 'event_msg' else None
                if kind == 'task_started':
                    if active is not None and active != payload.get('turn_id'):
                        groups.setdefault(active, []).append((index, event))
                    active = payload.get('turn_id')
                    last_started = active
                    started.append(active)
                if active is not None:
                    groups.setdefault(active, []).append((index, event))
                if kind in {'task_complete', 'turn_aborted'}:
                    turn = payload.get('turn_id')
                    if turn != active:
                        groups.setdefault(turn, []).append((index, event))
                    if active != turn or turn in completed:
                        duplicate_completions.add(turn)
                    completed.add(turn)
                    active = None
            self._turn_index[thread] = {
                'groups': groups, 'started': started, 'counts': Counter(started),
                'positions': {turn: index for index, turn in enumerate(started)},
                'completed': completed, 'invalid_completions': duplicate_completions,
                'last_started': last_started, 'active': active,
            }
        return self._turn_index[thread]

    def native_index(self, thread):
        if thread not in self._native:
            initial = self.read(thread)
            events = self.read(thread, event_indices=range(1, len(initial) + 1))
            calls, outputs, items = {}, {}, {}
            active = None
            for _, event in _own_session_events(self, events, thread):
                payload = event.get('payload', {})
                if event.get('type') == 'event_msg':
                    if payload.get('type') == 'task_started':
                        active = payload.get('turn_id')
                    elif payload.get('type') in {'task_complete', 'turn_aborted'}:
                        active = None
                    elif payload.get('type') == 'item_completed':
                        item = payload.get('item', {})
                        items.setdefault(item.get('id'), []).append((active, payload))
                elif event.get('type') == 'response_item' and active is not None:
                    kind = payload.get('type')
                    target = calls if kind in {'function_call', 'custom_tool_call'} else outputs if kind in {'function_call_output', 'custom_tool_call_output'} else None
                    if target is not None:
                        target.setdefault((active, payload.get('call_id')), []).append(payload)
            self._native[thread] = (events, calls, outputs, items)
        return self._native[thread]

    def session_dir(self) -> Path:
        codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        return codex_dir / "sessions"

    def _index_rows(self, query, parameters):
        index = (self.session_dir().parent / 'state_5.sqlite').resolve()
        try:
            with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)) as connection:
                return connection.execute(query, parameters).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise EvidenceError(f'execution unconfirmed: cannot read Codex source index: {exc}') from exc

    def resolve_session(self, agent_path, root_id, role):
        require(isinstance(agent_path, str) and re.fullmatch(r'/root(?:/[a-z0-9_]+)+', agent_path),
                'agent_id must be an exact canonical task path')
        require(isinstance(root_id, str) and UUID.fullmatch(root_id), 'root agent ID must be a Codex UUID')
        matches = []
        candidates = self._index_rows(
            "SELECT id FROM threads WHERE agent_path = ? AND json_extract(source, '$.subagent.thread_spawn.parent_thread_id') = ?",
            (agent_path, root_id))
        for (thread_id,) in candidates:
            _, meta = own_metadata(self.read(thread_id), thread_id)
            records = (meta, child_spawn(meta))
            if any(record.get('agent_path') == agent_path for record in records) and any(record.get('parent_thread_id') == root_id for record in records):
                require(all(record.get('agent_path') == agent_path for record in records), 'execution unconfirmed: conflicting source agent_path metadata')
                require(all(record.get('parent_thread_id') == root_id for record in records), 'execution unconfirmed: conflicting source parent_thread_id metadata')
                require(all(record.get('agent_role') == role for record in records), 'execution unconfirmed: source agent_role mismatch')
                matches.append(thread_id)
        require(len(matches) == 1, 'execution unconfirmed: source task path missing or ambiguous')
        session_metadata(self.read(matches[0]), matches[0], root_id, role, source=self)
        return {'codex_thread_id': matches[0], 'root_thread_id': root_id, 'agent_path': agent_path}

    def read(self, thread_id: str, *, event_indices=()) -> list[dict]:
        require(isinstance(thread_id, str) and UUID.fullmatch(thread_id), "invalid Codex thread ID")
        selection = frozenset(event_indices)
        key = (thread_id, selection)
        if key in self._views:
            return self._views[key]
        rows = self._index_rows('SELECT rollout_path FROM threads WHERE id = ?', (thread_id,))
        require(len(rows) == 1, f"execution unconfirmed: source session {thread_id} missing or ambiguous in Codex index")
        require(isinstance(rows[0][0], str) and bool(rows[0][0]),
                f"execution unconfirmed: source session {thread_id} has no rollout_path")
        events = _SessionEvents()
        try:
            path = Path(rows[0][0])
            require(path.is_absolute() and path.resolve().is_relative_to(self.session_dir().resolve()),
                    f"execution unconfirmed: source session {thread_id} is outside native sessions")
            if thread_id not in self._raw:
                with path.open(encoding="utf-8", newline="") as handle:
                    self._raw[thread_id] = [json.loads(line) for line in handle]
            for index, event in enumerate(self._raw[thread_id], 1):
                    require(isinstance(event, dict), "source event must be an object")
                    payload = event.get("payload", {})
                    require(isinstance(payload, dict), "source payload must be an object")
                    kind = event.get("type")
                    if kind == "session_meta":
                        payload = {k: payload[k] for k in ("id", "session_id", "parent_thread_id", "agent_role", "agent_path", "source", "cwd") if k in payload}
                    elif kind == "turn_context":
                        payload = {k: payload[k] for k in ("turn_id", "model") if k in payload}
                    elif kind == "event_msg" and payload.get("type") in {"task_started", "task_complete", "turn_aborted"}:
                        payload = {k: payload[k] for k in ("type", "turn_id", "last_agent_message", "started_at", "completed_at") if k in payload}
                    elif kind == "event_msg" and payload.get("type") == "item_completed":
                        item = payload.get("item", {})
                        require(isinstance(item, dict), "native item must be an object")
                        payload = {k: payload[k] for k in ("type", "thread_id", "turn_id") if k in payload}
                        payload["item"] = {k: item[k] for k in ("type", "id", "status", "command", "cwd", "exit_code", "stdout", "stderr", "aggregated_output") if k in item}
                        if item.get("type") == "FileChange":
                            changes = item.get("changes")
                            payload["item"]["changes"] = {
                                path: {k: change[k] for k in ("type", "move_path", "unified_diff", "content") if k in change} if isinstance(change, dict) else change
                                for path, change in changes.items()
                            } if isinstance(changes, dict) else changes
                    elif kind == "response_item" and payload.get("type") == "message" and (
                        payload.get("role") == "assistant" and payload.get("phase") == "final_answer" or
                        index in event_indices and payload.get("role") in {"user", "assistant"}
                    ):
                        payload = {k: payload[k] for k in ("type", "role", "phase", "content", "id") if k in payload}
                        payload["content"], payload["content_unavailable"] = public_content(payload.get("content", []))
                    elif kind == "response_item" and payload.get("type") == "agent_message" and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "author", "recipient", "content") if k in payload}
                        payload["content"], payload["content_unavailable"] = public_content(payload.get("content", []))
                    elif kind == "response_item" and payload.get("type") in {"custom_tool_call", "function_call"} and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "name", "input", "arguments") if k in payload}
                    elif kind == "response_item" and payload.get("type") in {"custom_tool_call_output", "function_call_output"} and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "output", "output_unavailable") if k in payload}
                        if not isinstance(payload.get("output"), str):
                            payload["output"], unavailable = public_content(payload.get("output"))
                            payload["output_unavailable"] = bool(payload.get("output_unavailable") or unavailable)
                    else:
                        payload = {}
                    filtered = {"type": kind, "payload": payload}
                    if "timestamp" in event:
                        filtered["timestamp"] = event["timestamp"]
                    events.append(filtered)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(f"execution unconfirmed: unreadable source session {thread_id}") from exc
        own_metadata(events, thread_id)
        self._views[key] = events
        return events


_CACHED_SESSION_SOURCE = CodexSessionSource


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
    if isinstance(events, _SessionEvents) and thread_id in events.metadata:
        return events.metadata[thread_id]
    matches = []
    for index, event in enumerate(events):
        require(isinstance(event, dict), "source event must be an object")
        if event.get("type") != "session_meta":
            continue
        meta = event.get("payload", {})
        require(isinstance(meta, dict), "source session metadata must be an object")
        require(isinstance(meta.get("id"), str) and UUID.fullmatch(meta["id"]), "invalid source session id metadata")
        if meta["id"] == thread_id:
            matches.append((index, meta))
    require(len(matches) == 1, "execution unconfirmed: missing or conflicting session metadata")
    if isinstance(events, _SessionEvents):
        events.metadata[thread_id] = matches[0]
    return matches[0]


def _own_session_events(source, events, thread_id):
    if isinstance(source, _CACHED_SESSION_SOURCE):
        key = (thread_id, id(events))
        if key not in source._own:
            source._own[key] = tuple(_uncached_own_session_events(source, events, thread_id))
        return iter(source._own[key])
    return _uncached_own_session_events(source, events, thread_id)


def _uncached_own_session_events(source, events, thread_id):
    meta_index, meta = own_metadata(events, thread_id)
    inherited = [event["payload"] for event in events if event.get("type") == "session_meta"
                 and event["payload"]["id"] != thread_id]
    pending = {record["id"] for record in inherited}
    ancestor_turns = set()
    seen = {thread_id}
    while pending:
        parent = meta.get("parent_thread_id")
        spawn = child_spawn(meta)
        require(isinstance(parent, str) and UUID.fullmatch(parent), "inherited metadata is not a source ancestor")
        require(parent not in seen, "conflicting source ancestry cycle")
        require(spawn.get("parent_thread_id") == parent, "conflicting parent_thread_id in source ancestry")
        require(isinstance(meta.get("agent_role"), str) and spawn.get("agent_role") == meta["agent_role"],
                "conflicting agent_role in source ancestry")
        seen.add(parent)
        ancestor_events = source.read(parent)
        _, meta = own_metadata(ancestor_events, parent)
        for copied in inherited:
            if copied["id"] == parent:
                for field in ("id", "session_id", "parent_thread_id", "agent_role", "agent_path"):
                    require(copied.get(field) == meta.get(field), "conflicting inherited " + field + " metadata")
                for field in ("parent_thread_id", "agent_role", "agent_path"):
                    require(child_spawn(copied).get(field) == child_spawn(meta).get(field),
                            "conflicting inherited thread_spawn " + field + " metadata")
        if meta.get("parent_thread_id") is None:
            require(not child_spawn(meta), "conflicting source root ancestry metadata")
        else:
            require(isinstance(meta["parent_thread_id"], str) and UUID.fullmatch(meta["parent_thread_id"]),
                    "invalid parent_thread_id in source ancestry")
            require(meta["parent_thread_id"] not in seen, "conflicting source ancestry cycle")
            for field in ("parent_thread_id", "agent_role"):
                require(isinstance(meta.get(field), str) and child_spawn(meta).get(field) == meta[field],
                        "conflicting " + field + " in source ancestry")
        pending.discard(parent)
        for event in ancestor_events:
            payload = event.get("payload", {})
            if (event.get("type") == "turn_context" or event.get("type") == "event_msg"
                    and payload.get("type") in {"task_started", "task_complete"}):
                if isinstance(payload.get("turn_id"), str):
                    ancestor_turns.add(payload["turn_id"])
    inherited_active = False
    for index, event in enumerate(events[meta_index + 1:], meta_index + 2):
        payload = event.get("payload", {})
        kind = event.get("type")
        if kind == "event_msg" and payload.get("type") == "task_started":
            inherited_active = payload.get("turn_id") in ancestor_turns
        if not inherited_active and payload.get("turn_id") not in ancestor_turns:
            yield index, event
        if kind == "event_msg" and payload.get("type") == "task_complete":
            inherited_active = False


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


def session_metadata(events, thread_id: str, root_id: str, role: str, *, source) -> None:
    _, meta = own_metadata(events, thread_id)
    spawn = child_spawn(meta)
    require(isinstance(spawn, dict) and bool(spawn), "execution unconfirmed: not a child session")
    for record in (meta, spawn):
        require(record.get("parent_thread_id") == root_id, "source parent_thread_id mismatch")
        require(record.get("agent_role") == role, f"source agent_role must be {role}")
    if meta.get("session_id") is not None:
        if meta["session_id"] not in (thread_id, root_id):
            verify_conversation_ancestry(source, thread_id, root_id, meta["session_id"])


def completed_turn(source, thread_id: str, turn_id: str, root_id: str, role: str, *, current=True) -> dict:
    events = source.read(thread_id)
    session_metadata(events, thread_id, root_id, role, source=source)
    expected = role_config(
        read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)["model"]
    require(isinstance(turn_id, str) and bool(turn_id), "completion_turn_id missing")
    active = None
    contexts = []
    finals = []
    completions = []
    last_started = None
    started_at = None
    indexed = source.turn_index(thread_id) if isinstance(source, _CACHED_SESSION_SOURCE) else None
    selected_events = indexed['groups'].get(turn_id, ()) if indexed is not None else _own_session_events(source, events, thread_id)
    for _, event in selected_events:
        payload = event.get("payload", {})
        kind = event.get("type")
        if kind == "event_msg" and payload.get("type") == "task_started":
            require(active != turn_id, "selected turn restarted before completion")
            active = payload.get("turn_id")
            last_started = active
            if active == turn_id:
                contexts, finals = [], []
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
                text = payload.get("last_agent_message")
                structured = role in {"qa-verifier", "reviewer"}
                require(isinstance(text, str) and bool(text.strip()), "completion final answer missing")
                answer = final_json(text) if structured else text
                if finals:
                    require((final_json(finals[-1]) if structured else finals[-1]) == answer,
                            "completion and final answer disagree")
                observed = source_time(event, "completed_at")
                completed_at, completed_before = observed.event_at, observed.event_before
                require(completed_before > started_at if completed_before is not None else completed_at >= started_at,
                        "source completion is before its own task_started")
                completions.append({"answer": answer})
            active = None
    require(len(completions) == 1, "execution unconfirmed: own completed turn missing or ambiguous")
    if indexed is not None:
        last_started, active = indexed['last_started'], indexed['active']
    require(not current or last_started == turn_id and active is None, "selected completion is stale or continuation is unfinished")
    return completions[0]


def author_turn_ids(source, assignment, *, turn=None, events=None):
    thread = assignment['agent_id']
    events = source.read(thread) if events is None else events
    if isinstance(source, _CACHED_SESSION_SOURCE):
        index = source.turn_index(thread)
        started, counts, positions = index['started'], index['counts'], index['positions']
        require(bool(started), 'author source turn missing')
        start = assignment.get('start_turn_id')
        turn = started[-1] if turn is None else turn
        require(isinstance(start, str) and bool(start) and counts[start] == 1, 'author assignment start turn missing or ambiguous')
        require(isinstance(turn, str) and bool(turn) and counts[turn] == 1, 'author report turn missing or ambiguous')
        first, last = positions[start], positions[turn]
        require(first <= last, 'author report precedes assignment start turn')
        selected = started[first:last + 1]
        require(all(isinstance(value, str) and value and counts[value] == 1 for value in selected), 'author source turn missing or ambiguous')
        require(not set(selected) & index['invalid_completions'], 'author completion missing or ambiguous')
        require(all(value in index['completed'] for value in selected[:-1]), 'previous author turn is unfinished')
        return selected
    own_events = [event for _, event in _own_session_events(source, events, thread)]
    started = [event['payload'].get('turn_id') for event in own_events
               if event.get('type') == 'event_msg' and event.get('payload', {}).get('type') == 'task_started']
    require(bool(started), 'author source turn missing')
    start = assignment.get('start_turn_id')
    turn = started[-1] if turn is None else turn
    require(isinstance(start, str) and bool(start) and started.count(start) == 1,
            'author assignment start turn missing or ambiguous')
    require(isinstance(turn, str) and bool(turn) and started.count(turn) == 1,
            'author report turn missing or ambiguous')
    first, last = started.index(start), started.index(turn)
    require(first <= last, 'author report precedes assignment start turn')
    selected = started[first:last + 1]
    require(all(isinstance(value, str) and value and started.count(value) == 1 for value in selected),
            'author source turn missing or ambiguous')
    completed, active = set(), None
    for event in own_events:
        payload = event.get('payload', {})
        if event.get('type') != 'event_msg':
            continue
        if payload.get('type') == 'task_started':
            active = payload.get('turn_id')
        elif payload.get('type') == 'task_complete':
            value = payload.get('turn_id')
            if value in selected:
                require(active == value and value not in completed, 'author completion missing or ambiguous')
                completed.add(value)
            active = None
    require(all(value in completed for value in selected[:-1]), 'previous author turn is unfinished')
    return selected



def requirements(snapshot):
    task = snapshot.json('task.json')
    ref = task.get('requirements', {})
    path = Path(ref.get('path', ''))
    require(path.is_absolute() and path.is_file(), 'requirements path must name an existing absolute file')
    text = path.read_text(encoding='utf-8')
    def section(value, heading):
        require(isinstance(heading, str) and heading.strip(), 'requirements heading required')
        lines = value.splitlines()
        matches = [(i, len(m.group(1))) for i, line in enumerate(lines)
                   if (m := re.match(r'^(#{1,6})\s+(.+?)\s*#*$', line)) and m.group(2) == heading]
        require(len(matches) == 1, 'requirements heading missing or ambiguous: ' + heading)
        start, depth = matches[0]
        end = next((i for i in range(start + 1, len(lines))
                    if (m := re.match(r'^(#{1,6})\s+', lines[i])) and len(m.group(1)) <= depth), len(lines))
        return '\n'.join(lines[start + 1:end])
    selected = section(section(text, ref.get('task_heading')), ref.get('criteria_heading'))
    criteria = []
    for line in selected.splitlines():
        if not line.strip():
            continue
        match = re.match(r'^[-*+]\s+(?:\[[ xX]\]\s*)?(.+)$', line)
        require(match is not None, 'acceptance criteria must be a flat Markdown list')
        criteria.append({'id': 'ac-' + str(len(criteria) + 1), 'text': match.group(1).strip()})
    require(bool(criteria), 'acceptance criteria missing')
    return {'criteria': criteria}


def assignments(snapshot):
    return {Path(name).stem: json.loads(value) for name, value in snapshot.documents.items()
            if name.startswith('assignments/') and name.endswith('.json')}


def reports(snapshot):
    return {Path(name).stem: json.loads(value) for name, value in snapshot.documents.items()
            if name.startswith('reports/') and name.endswith('.json')}


def native_tool_record(source, ref, *, root_id=None, role=None):
    require(isinstance(ref, dict), 'evidence reference must be an object')
    require('item_id' not in ref, 'native reference must select either call_id or item_id')
    thread, turn, call_id = (ref.get(key) for key in ('thread_id', 'turn_id', 'call_id'))
    require(all(isinstance(value, str) and value for value in (thread, turn, call_id)), 'native evidence requires thread_id, turn_id and call_id')
    if isinstance(source, _CACHED_SESSION_SOURCE):
        events, indexed_calls, indexed_outputs, _ = source.native_index(thread)
        _validate_native_source(source, events, thread, root_id, role)
        calls = indexed_calls.get((turn, call_id), [])
        outputs = indexed_outputs.get((turn, call_id), [])
        require(len(calls) == 1 and len(outputs) == 1, 'native tool call or output missing or ambiguous')
        return calls[0], outputs[0]
    events = source.read(thread)
    indices = range(1, len(events) + 1)
    events = source.read(thread, event_indices=indices)
    _, meta = own_metadata(events, thread)
    if root_id and thread != root_id:
        session_metadata(events, thread, root_id, role or meta.get('agent_role'), source=source)
    active = None
    calls, outputs = [], []
    for _, event in _own_session_events(source, events, thread):
        payload = event.get('payload', {})
        if event.get('type') == 'event_msg' and payload.get('type') == 'task_started':
            active = payload.get('turn_id')
        elif event.get('type') == 'event_msg' and payload.get('type') == 'task_complete':
            active = None
        elif active == turn and event.get('type') == 'response_item' and payload.get('call_id') == call_id:
            if payload.get('type') in {'function_call', 'custom_tool_call'}:
                calls.append(payload)
            elif payload.get('type') in {'function_call_output', 'custom_tool_call_output'}:
                outputs.append(payload)
    require(len(calls) == 1 and len(outputs) == 1, 'native tool call or output missing or ambiguous')
    return calls[0], outputs[0]


def native_output_text(record):
    if record.get('output_unavailable'):
        return None
    output = record.get('output')
    if isinstance(output, list):
        content, unavailable = public_content(output)
        if unavailable:
            return None
        output = ''.join(part['text'] for part in content)
    return output if isinstance(output, str) and output.strip() else None


def native_tool(source, ref, *, root_id=None, role=None):
    call, record = native_tool_record(source, ref, root_id=root_id, role=role)
    output = native_output_text(record)
    require(output is not None, 'native tool output unavailable')
    return call, output


def native_item(source, ref, *, root_id=None, role=None):
    require(isinstance(ref, dict), 'evidence reference must be an object')
    require('call_id' not in ref, 'native reference must select either call_id or item_id')
    thread, turn, item_id = (ref.get(key) for key in ('thread_id', 'turn_id', 'item_id'))
    require(all(isinstance(value, str) and value for value in (thread, turn, item_id)),
            'native evidence requires thread_id, turn_id and item_id')
    if isinstance(source, _CACHED_SESSION_SOURCE):
        events, _, _, items = source.native_index(thread)
        _validate_native_source(source, events, thread, root_id, role)
        matches = []
        for active, payload in items.get(item_id, []):
            require(active == turn and payload.get('turn_id') == turn and payload.get('thread_id') == thread,
                    'native item thread or turn mismatch')
            matches.append(payload['item'])
        require(len(matches) == 1, 'native item missing or ambiguous')
        require(matches[0].get('type') in {'FileChange', 'CommandExecution'}, 'unsupported native item type')
        return matches[0]
    events = source.read(thread)
    _, meta = own_metadata(events, thread)
    if root_id and thread != root_id:
        session_metadata(events, thread, root_id, role or meta.get('agent_role'), source=source)
    elif root_id:
        require(not meta.get('parent_thread_id') and not child_spawn(meta), 'root identity must be the actual source root')
    active, matches = None, []
    for _, event in _own_session_events(source, events, thread):
        payload = event.get('payload', {})
        if event.get('type') != 'event_msg':
            continue
        if payload.get('type') == 'task_started':
            active = payload.get('turn_id')
        elif payload.get('type') == 'task_complete':
            active = None
        elif payload.get('type') == 'item_completed' and payload.get('item', {}).get('id') == item_id:
            require(active == turn and payload.get('turn_id') == turn and payload.get('thread_id') == thread,
                    'native item thread or turn mismatch')
            matches.append(payload['item'])
    require(len(matches) == 1, 'native item missing or ambiguous')
    require(matches[0].get('type') in {'FileChange', 'CommandExecution'}, 'unsupported native item type')
    return matches[0]


def _validate_native_source(source, events, thread, root_id, role):
    key = (thread, root_id, role)
    if key in source._validated:
        return
    _, meta = own_metadata(events, thread)
    if root_id and thread != root_id:
        session_metadata(events, thread, root_id, role or meta.get('agent_role'), source=source)
    elif root_id:
        require(not meta.get('parent_thread_id') and not child_spawn(meta), 'root identity must be the actual source root')
    source._validated.add(key)


def native_file_change_paths(item):
    require(item.get('type') == 'FileChange', 'native edit requires FileChange')
    require(item.get('status') == 'completed', 'native edit did not succeed')
    changes = item.get('changes')
    require(isinstance(changes, dict) and bool(changes), 'native file changes missing')
    paths = set()
    for name, change in changes.items():
        require(isinstance(name, str) and Path(name).is_absolute(), 'native file change path must be absolute')
        require(isinstance(change, dict) and change.get('type') in {'add', 'update', 'delete'}, 'unsupported native file change')
        paths.add(str(Path(name).absolute()))
        target = change.get('move_path')
        if target is not None:
            require(isinstance(target, str) and Path(target).is_absolute(), 'native move path must be absolute')
            paths.add(str(Path(target).absolute()))
    return sorted(paths)


def native_assignment_stopped(source, assignment, *, before_turn_id=None):
    thread = assignment['agent_id']
    events = source.read(thread)
    own_metadata(events, thread)
    own = tuple(_own_session_events(source, events, thread))
    boundary = None
    if before_turn_id is not None:
        starts = [(index, event) for index, event in own if event.get('type') == 'event_msg'
                  and event.get('payload', {}).get('type') == 'task_started']
        require(starts and starts[-1][1]['payload'].get('turn_id') == before_turn_id, 'transfer destination turn is not current')
        boundary = starts[-1][0]
    active, stopped = None, None
    for index, event in own:
        if boundary is not None and index >= boundary:
            break
        payload = event.get('payload', {})
        if event.get('type') != 'event_msg':
            continue
        if payload.get('type') == 'task_started':
            active, stopped = payload.get('turn_id'), None
        elif payload.get('type') in {'task_complete', 'turn_aborted'}:
            require(active is not None and payload.get('turn_id') == active, 'native stop turn mismatch')
            stopped = {'thread_id': thread, 'turn_id': active, 'event_index': index,
                       'event_type': payload['type']}
            active = None
    require(active is None and stopped is not None, 'previous assignment executor is still active or stop unconfirmed')
    if boundary is not None:
        require(not any(index > boundary and event.get('payload', {}).get('turn_id') == stopped['turn_id']
                        for index, event in own), 'previous executor has events after transfer destination start')
    return stopped


def trusted_patch_executable(executable):
    installed = shutil.which('apply_patch')
    if installed is None:
        return False
    candidate = installed if executable == 'apply_patch' else executable
    return Path(candidate).is_absolute() and Path(candidate).resolve() == Path(installed).resolve()


def native_command_paths(item):
    require(item.get('type') == 'CommandExecution', 'native command execution required')
    require(item.get('status') == 'completed' and type(item.get('exit_code')) is int
            and item['exit_code'] == 0, 'native edit command did not succeed')
    command, cwd = item.get('command'), item.get('cwd')
    require(isinstance(command, list) and all(isinstance(x, str) for x in command), 'native command argv required')
    require(isinstance(cwd, str) and Path(cwd).is_absolute(), 'native command cwd required')
    script = command[2] if len(command) == 3 and command[:2] in (['/bin/zsh', '-lc'], ['/bin/bash', '-lc']) else None
    output = item.get('aggregated_output', item.get('stdout', ''))
    require(isinstance(output, str), 'native command output required')
    patch = None
    if script is not None:
        lines = script.strip().splitlines()
        match = re.fullmatch(r"((?:/[A-Za-z0-9_./-]+/)?apply_patch)\s+<<\s*'([A-Za-z_][A-Za-z0-9_]*)'\s*", lines[0]) if lines else None
        if match and match.group(2) in lines[1:]:
            if not trusted_patch_executable(match.group(1)):
                return []
            end = lines.index(match.group(2), 1)
            require(not any(line.strip() for line in lines[end + 1:]), 'standalone patch must end at first heredoc delimiter')
            patch = '\n'.join(lines[1:end])
        else:
            try:
                argv = shlex.split(script)
            except ValueError:
                return []
            if argv and argv[0] in {'rm', '/bin/rm', '/usr/bin/rm'} and script.strip() == shlex.join(argv):
                return native_command_paths({**item, 'command': argv})
            return []
    elif len(command) == 2 and trusted_patch_executable(command[0]):
        patch = command[1]
    elif command and command[0] in {'rm', '/bin/rm', '/usr/bin/rm'}:
        args = command[1:]
        if not args or args[0] != '-v':
            return []
        args = args[1:]
        if args and args[0] == '--':
            args = args[1:]
        require(args and all(Path(x).is_absolute() and '..' not in Path(x).parts for x in args), 'literal absolute removal paths required')
        lines = set(output.splitlines())
        require(all(x in lines or "removed '" + x + "'" in lines for x in args), 'native removal result does not confirm paths')
        return sorted(set(args))
    else:
        return []
    require(patch.startswith('*** Begin Patch\n') and patch.endswith('\n*** End Patch'), 'complete standalone patch required')
    require(patch.splitlines().count('*** Begin Patch') == 1 and patch.splitlines().count('*** End Patch') == 1,
            'patch must contain exactly one begin and end marker')
    require(all(not line.startswith('*** ') or re.fullmatch(r'\*\*\* (?:Begin Patch|End Patch|End of File|(?:Add|Update|Delete) File: .+|Move to: .+)', line)
                for line in patch.splitlines()), 'unsupported patch control line')
    require(not re.search(r'(?im)^.*(?:error:|verification failed|failed to)', output), 'native patch output reports failure')
    requested = []
    operations = {'Add': 'A', 'Update': 'M', 'Delete': 'D'}
    for operation, name in re.findall(r'^\*\*\* (Add|Update|Delete) File: ([^\n]+)$', patch, re.M):
        requested.append((operations[operation], name.strip()))
    for name in re.findall(r'^\*\*\* Move to: ([^\n]+)$', patch, re.M):
        requested.append(('M', name.strip()))
    require(len({name for _, name in requested}) == len(requested), 'duplicate patch paths are ambiguous')
    require(requested and ('Success. Updated' in output or 'Done!' in output), 'native patch success missing')
    paths = set()
    actual = {name.strip() for name in re.findall(r'^[AMD] ([^\n]+)$', output, re.M)}
    moved = dict(re.findall(r'^\*\*\* Update File: ([^\n]+)\n\*\*\* Move to: ([^\n]+)', patch, re.M))
    for _, name in requested:
        path = Path(name)
        absolute = str((path if path.is_absolute() else Path(cwd) / path).absolute())
        require('..' not in path.parts, 'native patch path must not traverse parents')
        confirmed = moved.get(name, name)
        confirmed_path = Path(confirmed)
        confirmed_absolute = str((confirmed_path if confirmed_path.is_absolute() else Path(cwd) / confirmed_path).absolute())
        require(confirmed in actual or confirmed_absolute in actual, 'native patch result does not confirm path: ' + name)
        paths.add(absolute)
    return sorted(paths)


def native_succeeded(call, output):
    raw = str(call.get('input', call.get('arguments', '')))
    if call.get('name', '').endswith('exec_command') or 'tools.exec_command(' in raw:
        codes = re.findall(r'"exit_code"\s*:\s*(-?\d+)|Process exited with code\s+(-?\d+)|Exit code:\s*(-?\d+)', output)
        return bool(codes) and all(next(value for value in values if value) == '0' for values in codes)
    return False


def native_patch_paths(call, output, cwd):
    raw = call.get('input', call.get('arguments', ''))
    if not isinstance(raw, str):
        raw = json.dumps(raw)
    patch_text = None
    if call.get('name', '').endswith('apply_patch'):
        require('Success. Updated' in output or 'Done!' in output, 'native edit did not succeed')
        patch_text = raw
    elif call.get('name', '').endswith('exec'):
        match = re.fullmatch(r'\s*(?:text\(\s*)?await\s+tools\.apply_patch\(("(?:\\.|[^"\\])*")\)\s*\)?\s*;?\s*', raw)
        if match:
            failed = re.search(r'Script (?:error|failed)|apply_patch verification failed|"isError"\s*:\s*true|(?im:^Error:)', output)
            succeeded = re.search(r'(?m)^\s*\{\s*\}\s*$|Success\. Updated|Done!', output)
            require('Script completed' in output and succeeded and not failed, 'native edit did not succeed')
            patch_text = json.loads(match.group(1))
    paths = set()
    if patch_text is not None:
        for name in re.findall(r'\*\*\* (?:(?:Add|Update|Delete) File|Move to): ([^\n]+)', patch_text):
            path = Path(name.strip())
            paths.add(str((path if path.is_absolute() else Path(cwd) / path).absolute()))
    return sorted(paths)


def generation_evidence(snapshot, ref, source, *, assignment=None, item=None):
    from journal_io import encode_json
    from journal_structure import generation_invocation, record_id
    from workspace_scope import path_allowed
    path = ref.get('path')
    require(isinstance(path, str) and path.startswith('generations/') and path.endswith('.json'), 'generation evidence path required')
    identifier = record_id(path.removeprefix('generations/').removesuffix('.json'))
    require(snapshot.exists(path), 'generation evidence missing')
    body = snapshot.json(path)
    request = body.get('request', {})
    known = snapshot.json('assignments/' + record_id(request.get('assignment_id')) + '.json')
    require(known.get('kind') == 'author' and (assignment is None or known == assignment), 'generation belongs to another assignment')
    receipt = snapshot.operation_receipt(identifier)
    require(receipt is not None and receipt['result'].get('path') == path
            and receipt['payload_sha256'] == sha256(encode_json({'kind': 'generation', 'request': request}).encode())
            and body.get('operation_id') == identifier, 'generation operation receipt missing or changed')
    require(type(body.get('exit_code')) is int and body['exit_code'] == 0, 'generation command did not succeed')
    native = ref.get('native')
    require(isinstance(native, dict) and native.get('thread_id') == known['agent_id'], 'generation requires its own author native execution')
    observed = item if item is not None else native_item(source, native, root_id=snapshot.identities['root_agent_id'], role=known['role'])
    require(observed.get('type') == 'CommandExecution' and observed.get('status') == 'completed'
            and type(observed.get('exit_code')) is int and observed['exit_code'] == 0, 'generation native execution did not succeed')
    command = observed.get('command')
    require(isinstance(command, list) and len(command) == 3 and command[:2] == ['/bin/zsh', '-lc']
            and isinstance(command[2], str), 'generation requires the canonical standalone writer invocation')
    python = body.get('python')
    require(isinstance(python, str) and Path(python).is_absolute(), 'generation interpreter missing')
    expected = generation_invocation(snapshot.logical_root, identifier, request, python)
    prefix = expected.split('\n', 1)[0] + '\n'
    suffix = '\nAF_GENERATION_REQUEST'
    script = command[2].rstrip('\n')
    require(script.startswith(prefix) and script.endswith(suffix), 'generation requires the canonical standalone writer invocation')
    try:
        supplied = json.loads(script[len(prefix):-len(suffix)])
        envelope = json.loads(observed.get('aggregated_output', ''))
    except (ValueError, TypeError) as exc:
        raise JournalError('generation native request or receipt unavailable') from exc
    require(encode_json(supplied) == encode_json(request), 'generation native request changed')
    require(isinstance(envelope, dict) and set(envelope) == {'executed', 'receipt'}
            and envelope['executed'] is True and encode_json(envelope['receipt']) == encode_json(receipt),
            'generation requires actual execution, not a replay or another receipt')
    output = body.get('output', {})
    require(output.get('path') == 'artifacts/generations/' + identifier + '.log', 'generation output path changed')
    verify_evidence(snapshot, [output], source, root_id=snapshot.identities['root_agent_id'])
    outputs = request.get('outputs')
    require(isinstance(outputs, list) and outputs, 'generation output paths missing')
    scope = snapshot.json('scope.json')
    paths, seen = [], set()
    for value in outputs:
        require(isinstance(value, str) and Path(value).is_absolute() and path_allowed(scope, value), 'generation path outside product scope')
        require(any(Path(value) == Path(allowed) or Path(value).is_relative_to(allowed) for allowed in known['allowed_paths']),
                'generation path outside assignment')
        require(value not in seen, 'generation output paths must be unique')
        seen.add(value)
        paths.append(value)
    return paths


def native_references(snapshot, assignment_id, source=None):
    source = source or CodexSessionSource()
    assignment = snapshot.json('assignments/' + assignment_id + '.json')
    thread, root = assignment['agent_id'], snapshot.identities['root_agent_id']
    initial = source.read(thread)
    events = source.read(thread, event_indices=range(1, len(initial) + 1))
    _, metadata = own_metadata(events, thread)
    if thread == root:
        require(not metadata.get('parent_thread_id') and not child_spawn(metadata), 'root identity must be the actual source root')
    else:
        session_metadata(events, thread, root, assignment['role'], source=source)
    author_turns = set(author_turn_ids(source, assignment, events=events)) if assignment['kind'] == 'author' else None
    turn, completed, calls, outputs, items = None, False, [], {}, []
    started_turns = set()
    for _, event in _own_session_events(source, events, thread):
        payload = event.get('payload', {})
        if event.get('type') == 'event_msg' and payload.get('type') == 'task_started':
            turn, completed = payload.get('turn_id'), False
            if isinstance(turn, str) and turn:
                started_turns.add(turn)
            if author_turns is None:
                calls, outputs, items = [], {}, []
        elif event.get('type') == 'event_msg' and payload.get('type') == 'task_complete' and payload.get('turn_id') == turn:
            completed = True
        elif author_turns is not None and turn not in author_turns:
            continue
        elif turn is not None and not completed and event.get('type') == 'event_msg' and payload.get('type') == 'item_completed':
            if payload.get('item', {}).get('type') in {'FileChange', 'CommandExecution'}:
                if payload['item']['type'] == 'CommandExecution':
                    item_turn, item_id = payload.get('turn_id'), payload['item'].get('id')
                    require(all(isinstance(value, str) and value for value in (payload.get('thread_id'), item_turn, item_id)),
                            'native evidence requires thread_id, turn_id and item_id')
                    require(payload['thread_id'] == thread and item_turn in started_turns,
                            'native item thread or turn mismatch')
                    if item_turn != turn:
                        continue
                items.append((turn, payload['item']))
        elif turn is not None and not completed and event.get('type') == 'response_item':
            if payload.get('type') in {'custom_tool_call', 'function_call'}:
                calls.append((turn, payload))
            elif payload.get('type') in {'custom_tool_call_output', 'function_call_output'}:
                outputs[turn, payload.get('call_id')] = payload
    require(isinstance(turn, str) and bool(turn), 'own source turn missing')
    if completed and thread != root:
        completed_turn(source, thread, turn, root, assignment['role'])
    observations = []
    for call_turn, call in calls:
        if (call_turn, call.get('call_id')) not in outputs:
            continue
        raw_output = outputs[call_turn, call['call_id']].get('output')
        if isinstance(raw_output, str) and not raw_output.strip():
            continue
        ref = {'thread_id': thread, 'turn_id': call_turn, 'call_id': call['call_id']}
        observed_call, record = native_tool_record(source, ref, root_id=root, role=assignment['role'])
        output = native_output_text(record)
        if output is None:
            observations.append({'ref': ref, 'name': observed_call.get('name'), 'success': False,
                                 'authored_paths': [], 'reason': 'native tool output unavailable for text verification'})
            continue
        try:
            paths = native_patch_paths(observed_call, output, metadata.get('cwd', snapshot.source_root))
        except JournalError:
            paths = []
        observations.append({'ref': ref, 'name': observed_call.get('name'),
                             'success': bool(paths) or native_succeeded(observed_call, output), 'authored_paths': paths})
    for item_turn, item in items:
        ref = {'thread_id': thread, 'turn_id': item_turn, 'item_id': item.get('id')}
        observed = native_item(source, ref, root_id=root, role=assignment['role'])
        if observed.get('type') == 'CommandExecution':
            try:
                paths = native_command_paths(observed)
            except JournalError:
                paths = []
            if paths:
                observations.append({'ref': ref, 'name': 'CommandExecution', 'success': True, 'authored_paths': paths})
                continue
            try:
                envelope = json.loads(observed.get('aggregated_output', ''))
            except (ValueError, TypeError):
                continue
            if not isinstance(envelope, dict) or not isinstance(envelope.get('receipt'), dict):
                continue
            result = envelope['receipt'].get('result', {})
            if not isinstance(result, dict) or not isinstance(result.get('path'), str) or not result['path'].startswith('generations/'):
                continue
            reference = {'path': result['path'], 'native': ref}
            try:
                paths = generation_evidence(snapshot, reference, source, assignment=assignment, item=observed)
            except JournalError as exc:
                observations.append({'ref': reference, 'name': 'generation', 'success': False, 'authored_paths': [], 'reason': str(exc)})
            else:
                observation = {'ref': reference, 'name': 'generation', 'success': bool(paths), 'authored_paths': paths}
                observations.append(observation)
            continue
        if observed.get('status') == 'completed':
            paths = native_file_change_paths(observed)
            observations.append({'ref': ref, 'name': 'FileChange', 'success': True, 'authored_paths': paths})
        else:
            observations.append({'ref': ref, 'name': 'FileChange', 'success': False,
                                 'authored_paths': [], 'reason': 'native edit did not succeed'})
    return {'assignment_id': assignment_id, 'thread_id': thread, 'turn_id': turn,
            'completed': completed, 'observations': observations}


def verify_evidence(snapshot, refs, source, *, root_id, role=None, success_required=False):
    require(isinstance(refs, list) and bool(refs), 'report evidence must be a nonempty array')
    successful = False
    for ref in refs:
        require(isinstance(ref, dict), 'evidence reference must be an object')
        if isinstance(ref.get('path'), str) and ref['path'].startswith('generations/'):
            generation_evidence(snapshot, ref, source)
        elif 'path' in ref:
            path = ref['path']
            require(isinstance(path, str) and path.startswith(('artifacts/', 'checks/')) and '..' not in Path(path).parts,
                    'evidence must name artifacts/ or checks/')
            require(snapshot.exists(path), 'artifact evidence missing: ' + path)
            if path.startswith('checks/'):
                check = json.loads(snapshot.read_bytes(path))
                require(type(check.get('exit_code')) is int and (not success_required or check['exit_code'] == 0), 'referenced check did not succeed')
                receipt = snapshot.operation_receipt(check.get('operation_id'))
                require(receipt is not None and receipt['result'].get('path') == path, 'check operation receipt missing')
                verify_evidence(snapshot, [check.get('output')], source, root_id=root_id)
                successful = successful or check['exit_code'] == 0
        elif 'item_id' in ref:
            item = native_item(source, ref, root_id=root_id, role=role)
            if item.get('type') == 'CommandExecution':
                require(bool(native_command_paths(item)), 'native command edit unconfirmed')
                successful = True
            else:
                native_file_change_paths(item)
        else:
            call, output = native_tool(source, ref, root_id=root_id, role=role)
            successful = successful or native_succeeded(call, output)
    require(not success_required or successful, 'passed criterion requires a successful check or native inspection')


def observed_author_paths(snapshot, assignment, report, source, *, turn):
    root_id = snapshot.identities['root_agent_id']
    allowed_turns = author_turn_ids(source, assignment, turn=turn)
    observed = set()
    for ref in report.get('evidence', []):
        if isinstance(ref.get('path'), str) and ref['path'].startswith('generations/'):
            require(ref.get('native', {}).get('turn_id') in allowed_turns, 'generation author turn outside assignment')
            observed.update(generation_evidence(snapshot, ref, source, assignment=assignment))
            continue
        if 'path' in ref or ref.get('thread_id') != assignment['agent_id']:
            continue
        require(ref.get('turn_id') in allowed_turns, 'native edit author turn outside assignment')
        if 'item_id' in ref:
            item = native_item(source, ref, root_id=root_id, role=assignment['role'])
            observed.update(native_command_paths(item) if item.get('type') == 'CommandExecution' else native_file_change_paths(item))
            continue
        call, output = native_tool(source, ref, root_id=root_id, role=assignment['role'])
        _, metadata = own_metadata(source.read(assignment['agent_id']), assignment['agent_id'])
        observed.update(native_patch_paths(call, output, metadata.get('cwd', snapshot.source_root)))
    return observed


def authored_evidence(snapshot, assignment, report, source, *, turn):
    from workspace_scope import path_allowed
    paths = report.get('authored_paths')
    require(isinstance(paths, list) and len(paths) == len(set(paths)), 'author report requires unique authored_paths')
    scope = snapshot.json('scope.json')
    allowed = assignment.get('allowed_paths', [])
    observed = observed_author_paths(snapshot, assignment, report, source, turn=turn)
    for value in paths:
        require(isinstance(value, str) and Path(value).is_absolute(), 'authored_paths must be absolute')
        path = Path(value).absolute()
        require(path_allowed(scope, str(path)), 'author path outside product scope: ' + value)
        require(any(path == Path(item) or path.is_relative_to(Path(item)) for item in allowed), 'author path outside assignment: ' + value)
        require(str(path) in observed, 'authored path has no native edit evidence: ' + value)


def accepted_review(snapshot, key, source, *, qa_agent_id):
    known = assignments(snapshot)
    require(qa_agent_id not in {item['agent_id'] for item in known.values() if item['kind'] == 'author'},
            'QA cannot be a product author')
    require(isinstance(key, str) and key in known and known[key]['kind'] == 'reviewer',
            'QA reviewer assignment missing')
    assignment = known[key]
    require(assignment['agent_id'] != qa_agent_id, 'QA must be independent from reviewer')
    path = 'reports/' + key + '.json'
    require(snapshot.exists(path), 'QA requires current completed review')
    report = snapshot.json(path)
    require(report.get('status') == 'completed' and report.get('outcome') == 'passed', 'QA requires current passed review')
    revalidate_report(snapshot, assignment, report, source)
    return report


def accept_report(snapshot, assignment, request, source=None, *, current=True):
    source = source or CodexSessionSource()
    root_id = snapshot.identities['root_agent_id']
    thread = assignment['agent_id']
    require(assignment.get('kind') in {'author', 'qa', 'reviewer', 'analysis'}, 'unsupported assignment kind')
    if thread == root_id:
        require(assignment['kind'] in {'author', 'analysis'}, 'QA and reviewer must be independent child agents')
        turn = request.get('turn_id')
        require(isinstance(turn, str) and bool(turn), 'root author turn_id required')
        events = source.read(thread)
        _, meta = own_metadata(events, thread)
        require(not meta.get('parent_thread_id'), 'root identity must be the actual source root')
        started = [event['payload'].get('turn_id') for _, event in _own_session_events(source, events, thread)
                   if event.get('type') == 'event_msg' and event.get('payload', {}).get('type') == 'task_started']
        require(turn in started and (not current or started[-1] == turn), 'root author turn is stale')
        report = copy.deepcopy(request.get('report'))
    else:
        turn = request.get('completion_turn_id')
        completion = completed_turn(source, thread, turn, root_id, assignment['role'], current=current)
        report = final_json(completion['answer']) if isinstance(completion['answer'], str) else copy.deepcopy(completion['answer'])
    require(isinstance(report, dict), 'report must be an object')
    require('source' not in report, 'report source binding is writer owned')
    report.pop('fingerprint', None)
    if isinstance(report.get('reviewer'), dict):
        report['reviewer'].pop('sha256', None)
    require(report.get('status') in {'completed', 'interrupted'}, 'report status must be completed or interrupted')
    require(report.get('outcome') in {'passed', 'failed', 'unverified'}, 'report outcome must be passed, failed or unverified')
    require(report['status'] == 'completed' or report['outcome'] != 'passed', 'interrupted report cannot pass')
    require(isinstance(report.get('summary'), str) and bool(report['summary'].strip()), 'report summary required')
    if report['outcome'] != 'passed':
        require(isinstance(report.get('reason'), str) and bool(report['reason'].strip()), 'nonpass report reason required')
    verify_evidence(snapshot, report.get('evidence'), source, root_id=root_id)
    kind = assignment['kind']
    if kind == 'author':
        authored_evidence(snapshot, assignment, report, source, turn=turn)
    if kind == 'analysis' and thread == root_id:
        require(any('path' not in ref and ref.get('thread_id') == thread
                    and ref.get('turn_id') == turn
                    and 'item_id' not in ref
                    and native_succeeded(*native_tool(source, ref, root_id=root_id))
                    for ref in report['evidence']), 'root analysis requires its own successful native inspection')
    if kind in {'qa', 'reviewer'}:
        require(thread != root_id, 'acceptance requires an independent child')
        known = assignments(snapshot)
        author_ids = {item['agent_id'] for item in known.values() if item.get('kind') == 'author'}
        require(thread not in author_ids, 'QA/reviewer cannot be a product author')
        require(any('path' not in ref and ref.get('thread_id') == thread
                    and ref.get('turn_id') == turn
                    and (report['outcome'] != 'passed' or 'item_id' not in ref and native_succeeded(*native_tool(source, ref, root_id=root_id, role=assignment['role'])))
                    for ref in report['evidence']), 'QA/reviewer requires its own successful native inspection')
    if kind == 'qa':
        reviewer = report.get('reviewer', {})
        require(isinstance(reviewer, dict), 'QA reviewer reference required')
        key = reviewer.get('assignment_id')
        require(isinstance(key, str) and key in assignment.get('depends_on', []), 'QA requires a reviewer dependency')
        accepted_review(snapshot, key, source, qa_agent_id=thread)
        expected = {item['id'] for item in requirements(snapshot)['criteria']}
        criteria = report.get('criteria')
        require(isinstance(criteria, dict) and set(criteria) == expected, 'QA must report every acceptance criterion')
        for criterion, outcome in criteria.items():
            try:
                require(isinstance(outcome, dict) and outcome.get('status') in {'passed', 'failed', 'unverified'}, 'invalid criterion outcome')
                if outcome['status'] == 'unverified':
                    require(isinstance(outcome.get('reason'), str) and bool(outcome['reason'].strip()), 'unverified criterion reason required')
                    refs = outcome.get('evidence', [])
                    require(isinstance(refs, list), 'criterion evidence must be an array')
                    if not refs:
                        continue
                verify_evidence(snapshot, outcome.get('evidence'), source, root_id=root_id,
                                success_required=outcome['status'] == 'passed')
            except JournalError as exc:
                raise JournalError(f'criterion {criterion}: {exc}') from exc
        if report['outcome'] == 'passed':
            require(all(item['status'] == 'passed' for item in criteria.values()), 'passed QA requires passed criteria')
        resolutions = report.get('resolves', {})
        require(isinstance(resolutions, dict), 'QA resolves must map assignment IDs to evidence')
        for prior, refs in resolutions.items():
            require(prior in known and known[prior]['kind'] in {'qa', 'reviewer'}
                    and snapshot.exists('reports/' + prior + '.json'), 'resolution must reference a historical review or QA report')
            require(snapshot.json('reports/' + prior + '.json')['outcome'] != 'passed', 'resolution must reference a negative result')
            verify_evidence(snapshot, refs, source, root_id=root_id, success_required=True)
    if kind == 'reviewer':
        require('qa' not in report, 'reviewer report must not depend on QA')
        verification_only = report.get('verification_only', [])
        require(isinstance(verification_only, list) and all(isinstance(key, str) for key in verification_only)
                and len(verification_only) == len(set(verification_only)),
                'verification_only must be unique author assignment IDs')
        require(all(key in known and known[key]['kind'] == 'author' for key in verification_only),
                'verification_only must reference existing author assignments')
        for key in assignment.get('depends_on', []):
            author = known[key]
            if author['kind'] != 'author':
                continue
            path = 'reports/' + key + '.json'
            require(snapshot.exists(path), 'reviewer author report missing: ' + key)
            accepted = snapshot.json(path)
            require(accepted.get('status') == 'completed', 'reviewer requires completed author report: ' + key)
            revalidate_report(snapshot, author, accepted, source)
    report['source'] = {'thread_id': thread, 'turn_id': turn, 'root_author': thread == root_id}
    return report


def revalidate_report(snapshot, assignment, report, source):
    report = copy.deepcopy(report)
    report.pop('fingerprint', None)
    if isinstance(report.get('reviewer'), dict):
        report['reviewer'].pop('sha256', None)
    binding = report.get('source', {})
    binding.pop('authors', None)
    binding.pop('evidence_sha256', None)
    request = {'turn_id': binding.get('turn_id'), 'report': {k: v for k, v in report.items() if k != 'source'}} if binding.get('root_author') else {'completion_turn_id': binding.get('turn_id')}
    current = accept_report(snapshot, assignment, request, source, current=False)
    require(current == report, 'accepted report does not match its native response')


def validate_acceptance(snapshot, source=None, *, report_id=None):
    source = source or CodexSessionSource()
    errors = []
    try:
        known, accepted = assignments(snapshot), reports(snapshot)
        require(report_id in known and known[report_id]['kind'] == 'qa', 'final report must select independent QA')
        require(report_id in accepted, 'final QA report missing')
        report = accepted[report_id]
        require(report.get('status') == 'completed' and report.get('outcome') == 'passed', 'final QA did not pass')
        revalidate_report(snapshot, known[report_id], report, source)
        review_id = report['reviewer']['assignment_id']
        covered = set(known[review_id].get('depends_on', []))
        superseded = {item['previous_assignment_id'] for item in known.values() if item.get('previous_assignment_id')}
        from journal_transfers import transferred_assignments
        superseded.update(transferred_assignments(snapshot))
        authors = {key for key, item in known.items() if item['kind'] == 'author' and key not in superseded}
        require(authors <= covered, 'final review does not cover current author assignments: ' + ', '.join(sorted(authors - covered)))
        negatives = {key for key, value in accepted.items() if key != report_id
                     and known[key]['kind'] in {'qa', 'reviewer'} and value.get('outcome') != 'passed'}
        require(negatives <= set(report.get('resolves', {})), 'final QA has unresolved findings: ' + ', '.join(sorted(negatives - set(report.get('resolves', {})))))
    except (JournalError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return errors
