from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import sqlite3
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


class CodexSessionSource:
    """Read the current native session selected by the Codex index, never exports."""

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
        rows = self._index_rows('SELECT rollout_path FROM threads WHERE id = ?', (thread_id,))
        require(len(rows) == 1, f"execution unconfirmed: source session {thread_id} missing or ambiguous in Codex index")
        require(isinstance(rows[0][0], str) and bool(rows[0][0]),
                f"execution unconfirmed: source session {thread_id} has no rollout_path")
        events = []
        try:
            path = Path(rows[0][0])
            require(path.is_absolute() and path.resolve().is_relative_to(self.session_dir().resolve()),
                    f"execution unconfirmed: source session {thread_id} is outside native sessions")
            with path.open(encoding="utf-8", newline="") as handle:
                for index, line in enumerate(handle, 1):
                    event = json.loads(line)
                    require(isinstance(event, dict), "source event must be an object")
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
                    elif kind == "response_item" and payload.get("type") in {"custom_tool_call", "function_call"} and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "name", "input", "arguments") if k in payload}
                    elif kind == "response_item" and payload.get("type") == "custom_tool_call_output" and index in event_indices:
                        payload = {k: payload[k] for k in ("type", "call_id", "output") if k in payload}
                        if not isinstance(payload.get("output"), str):
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
        own_metadata(events, thread_id)
        return events


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
    return matches[0]


def _own_session_events(source, events, thread_id):
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
    for _, event in _own_session_events(source, events, thread_id):
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
    require(not current or last_started == turn_id and active is None, "selected completion is stale or continuation is unfinished")
    return completions[0]



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
    return {'digest': sha256(json.dumps(criteria, ensure_ascii=False, sort_keys=True).encode()), 'criteria': criteria}


def result_fingerprint(snapshot):
    from task_workspace import workspace_fingerprint
    return workspace_fingerprint(snapshot.json('scope.json'), requirements(snapshot)['digest'], run_dir=snapshot.logical_root)


def assignments(snapshot):
    return {Path(name).stem: json.loads(value) for name, value in snapshot.documents.items()
            if name.startswith('assignments/') and name.endswith('.json')}


def reports(snapshot):
    return {Path(name).stem: json.loads(value) for name, value in snapshot.documents.items()
            if name.startswith('reports/') and name.endswith('.json')}


def native_tool(source, ref, *, root_id=None, role=None):
    require(isinstance(ref, dict), 'evidence reference must be an object')
    thread, turn, call_id = (ref.get(key) for key in ('thread_id', 'turn_id', 'call_id'))
    require(all(isinstance(value, str) and value for value in (thread, turn, call_id)), 'native evidence requires thread_id, turn_id and call_id')
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
    output = outputs[0].get('output')
    if isinstance(output, list):
        content, unavailable = public_content(output)
        require(not unavailable, 'native tool output unavailable')
        output = ''.join(part['text'] for part in content)
    require(isinstance(output, str) and bool(output.strip()), 'native tool output unavailable')
    return calls[0], output


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
    turn, completed, calls, outputs = None, False, [], {}
    for _, event in _own_session_events(source, events, thread):
        payload = event.get('payload', {})
        if event.get('type') == 'event_msg' and payload.get('type') == 'task_started':
            turn, completed, calls, outputs = payload.get('turn_id'), False, [], {}
        elif event.get('type') == 'event_msg' and payload.get('type') == 'task_complete' and payload.get('turn_id') == turn:
            completed = True
        elif turn is not None and not completed and event.get('type') == 'response_item':
            if payload.get('type') in {'custom_tool_call', 'function_call'}:
                calls.append(payload)
            elif payload.get('type') in {'custom_tool_call_output', 'function_call_output'}:
                outputs[payload.get('call_id')] = payload.get('output')
    require(isinstance(turn, str) and bool(turn), 'own source turn missing')
    if completed and thread != root:
        completed_turn(source, thread, turn, root, assignment['role'])
    observations = []
    for call in calls:
        if call.get('call_id') not in outputs:
            continue
        raw_output = outputs[call['call_id']]
        if isinstance(raw_output, str) and not raw_output.strip():
            continue
        ref = {'thread_id': thread, 'turn_id': turn, 'call_id': call['call_id']}
        observed_call, output = native_tool(source, ref, root_id=root, role=assignment['role'])
        try:
            paths = native_patch_paths(observed_call, output, metadata.get('cwd', snapshot.source_root))
        except JournalError:
            paths = []
        observations.append({'ref': ref, 'name': observed_call.get('name'),
                             'success': bool(paths) or native_succeeded(observed_call, output), 'authored_paths': paths})
    return {'assignment_id': assignment_id, 'thread_id': thread, 'turn_id': turn,
            'completed': completed, 'observations': observations}


def verify_evidence(snapshot, refs, source, *, root_id, role=None, success_required=False):
    require(isinstance(refs, list) and bool(refs), 'report evidence must be a nonempty array')
    successful = False
    for ref in refs:
        require(isinstance(ref, dict), 'evidence reference must be an object')
        if 'path' in ref:
            path = ref['path']
            require(isinstance(path, str) and path.startswith(('artifacts/', 'checks/')) and '..' not in Path(path).parts,
                    'evidence must name artifacts/ or checks/')
            require(snapshot.exists(path), 'artifact evidence missing: ' + path)
            require(ref.get('sha256') == sha256(snapshot.read_bytes(path)), 'artifact evidence bytes changed')
            if path.startswith('checks/'):
                check = json.loads(snapshot.read_bytes(path))
                require(check.get('fingerprint') == result_fingerprint(snapshot), 'referenced check is stale')
                require(type(check.get('exit_code')) is int and (not success_required or check['exit_code'] == 0), 'referenced check did not succeed')
                receipt = snapshot.operation_receipt(check.get('operation_id'))
                require(receipt is not None and receipt['result'].get('path') == path
                        and receipt['result'].get('sha256') == sha256(snapshot.read_bytes(path)), 'check operation receipt missing')
                verify_evidence(snapshot, [check.get('output')], source, root_id=root_id)
                successful = successful or check['exit_code'] == 0
        else:
            call, output = native_tool(source, ref, root_id=root_id, role=role)
            successful = successful or native_succeeded(call, output)
    require(not success_required or successful, 'passed criterion requires a successful check or native inspection')


def authored_evidence(snapshot, assignment, report, source):
    from workspace_scope import path_allowed
    paths = report.get('authored_paths')
    require(isinstance(paths, list) and len(paths) == len(set(paths)), 'author report requires unique authored_paths')
    scope = snapshot.json('scope.json')
    allowed = assignment.get('allowed_paths', [])
    root_id = snapshot.identities['root_agent_id']
    observed = set()
    for ref in report.get('evidence', []):
        if 'path' in ref or ref.get('thread_id') != assignment['agent_id']:
            continue
        call, output = native_tool(source, ref, root_id=root_id, role=assignment['role'])
        _, metadata = own_metadata(source.read(assignment['agent_id']), assignment['agent_id'])
        observed.update(native_patch_paths(call, output, metadata.get('cwd', snapshot.source_root)))
    for value in paths:
        require(isinstance(value, str) and Path(value).is_absolute(), 'authored_paths must be absolute')
        path = Path(value).absolute()
        require(path_allowed(scope, str(path)), 'author path outside product scope: ' + value)
        require(any(path == Path(item) or path.is_relative_to(Path(item)) for item in allowed), 'author path outside assignment: ' + value)
        require(str(path) in observed, 'authored path has no native edit evidence: ' + value)


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
    require(report.get('status') in {'completed', 'failed'}, 'report status must be completed or failed')
    require(isinstance(report.get('summary'), str) and bool(report['summary'].strip()), 'report summary required')
    if report['status'] == 'failed':
        require(isinstance(report.get('reason'), str) and bool(report['reason'].strip()), 'failed report reason required')
    verify_evidence(snapshot, report.get('evidence'), source, root_id=root_id)
    kind = assignment['kind']
    if kind == 'author':
        authored_evidence(snapshot, assignment, report, source)
    if kind == 'analysis' and thread == root_id:
        require(any('path' not in ref and ref.get('thread_id') == thread
                    and ref.get('turn_id') == turn
                    and native_succeeded(*native_tool(source, ref, root_id=root_id))
                    for ref in report['evidence']), 'root analysis requires its own successful native inspection')
    if kind in {'qa', 'reviewer'}:
        require(thread != root_id, 'acceptance requires an independent child')
        require(report.get('fingerprint') == result_fingerprint(snapshot), 'report fingerprint is stale')
        known = assignments(snapshot)
        author_ids = {item['agent_id'] for item in known.values() if item.get('kind') == 'author'}
        require(thread not in author_ids, 'QA/reviewer cannot be a product author')
        require(any('path' not in ref and ref.get('thread_id') == thread
                    and ref.get('turn_id') == turn
                    and (report['status'] == 'failed' or native_succeeded(*native_tool(source, ref, root_id=root_id, role=assignment['role'])))
                    for ref in report['evidence']), 'QA/reviewer requires its own successful native inspection')
    if kind == 'qa':
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
        if report['status'] == 'completed':
            require(all(item['status'] == 'passed' for item in criteria.values()), 'completed QA requires passed criteria')
    if kind == 'reviewer':
        qa = report.get('qa', {})
        key = qa.get('assignment_id')
        require(isinstance(key, str) and key in assignments(snapshot), 'reviewer QA assignment missing')
        qa_assignment = assignments(snapshot)[key]
        require(qa_assignment.get('kind') == 'qa' and qa_assignment['agent_id'] != thread, 'reviewer must be independent from QA')
        path = 'reports/' + key + '.json'
        require(snapshot.exists(path) and qa.get('sha256') == sha256(snapshot.read_bytes(path)), 'reviewer QA report changed')
        accepted = json.loads(snapshot.read_bytes(path))
        require(accepted.get('status') == 'completed' and accepted.get('fingerprint') == report['fingerprint'], 'reviewer requires current completed QA')
        revalidate_report(snapshot, qa_assignment, accepted, source)
    report['source'] = {'thread_id': thread, 'turn_id': turn, 'root_author': thread == root_id}
    if kind == 'qa':
        report['source']['authors'] = {key: sha256(snapshot.read_bytes('reports/' + key + '.json'))
                                      for key, item in assignments(snapshot).items() if item.get('kind') == 'author'
                                      and snapshot.exists('reports/' + key + '.json')}
    refs = [*report['evidence'], *(ref for outcome in report.get('criteria', {}).values() for ref in outcome.get('evidence', []))]
    report['source']['evidence_sha256'] = [sha256(json.dumps([ref, *native_tool(source, ref, root_id=root_id)], sort_keys=True).encode())
                                         for ref in refs if 'path' not in ref]
    return report


def revalidate_report(snapshot, assignment, report, source):
    binding = report.get('source', {})
    request = {'turn_id': binding.get('turn_id'), 'report': {k: v for k, v in report.items() if k != 'source'}} if binding.get('root_author') else {'completion_turn_id': binding.get('turn_id')}
    current = accept_report(snapshot, assignment, request, source, current=False)
    require(current == report, 'accepted native report or author binding is stale')


def validate_acceptance(snapshot, source=None):
    source = source or CodexSessionSource()
    errors = []
    try:
        result_fingerprint(snapshot)
        known, accepted = assignments(snapshot), reports(snapshot)
        require(bool(known), 'assignments missing')
        for key, assignment in known.items():
            require(key in accepted, 'assignment report missing: ' + key)
            report = accepted[key]
            require(report.get('status') == 'completed', 'assignment failed: ' + key)
            revalidate_report(snapshot, assignment, report, source)
            for dependency in assignment.get('depends_on', []):
                require(dependency in accepted and accepted[dependency].get('status') == 'completed', 'assignment dependency incomplete')
        authors = [item for key, item in known.items() if item.get('kind') == 'author']
        from task_workspace import changed_paths
        delta = set(changed_paths(snapshot.json('scope.json'), run_dir=snapshot.logical_root))
        covered = {path for key, report in accepted.items() if known[key].get('kind') == 'author' for path in report.get('authored_paths', [])}
        require(delta <= covered, 'current product changes lack native author coverage: ' + ', '.join(sorted(delta - covered)))
        if authors or delta:
            require(any(item.get('kind') == 'qa' for item in known.values()), 'independent QA missing')
            require(any(item.get('kind') == 'reviewer' for item in known.values()), 'independent reviewer missing')
    except (JournalError, OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return errors
