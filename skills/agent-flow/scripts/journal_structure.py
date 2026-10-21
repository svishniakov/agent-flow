from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from journal_io import JournalError, JournalSnapshot, capture_file, digest, encode_json, logical_path, require, transact

REQUEST_FIELDS = {
    "assignment": {"id", "role", "agent_id", "kind", "allowed_paths", "depends_on"},
    "scope": {"roots", "context"},
    "report": {"assignment_id", "completion_turn_id", "turn_id", "report"},
    "check": {"id", "assignment_id", "command", "cwd", "timeout"},
    "generation": {"assignment_id", "command", "cwd", "outputs", "timeout"},
}
ASSIGNMENT_KINDS = {"author", "qa", "reviewer", "analysis"}
REPORT_STATUSES = {"completed", "failed"}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}")


def encoded(value):
    return (encode_json(value) + "\n").encode()


def record_id(value):
    require(isinstance(value, str) and IDENTIFIER.fullmatch(value), "id must contain letters, digits, underscores or hyphens")
    return value


def request_contract():
    return {
        "records": {kind: sorted(fields) for kind, fields in REQUEST_FIELDS.items()},
        "assignment_kinds": sorted(ASSIGNMENT_KINDS),
        "report_statuses": sorted(REPORT_STATUSES),
        "report_required": ["status", "summary", "evidence"],
        "author_fields": ["authored_paths"],
        "qa_fields": ["fingerprint", "criteria"],
        "reviewer_fields": ["fingerprint", "qa"],
        "native_evidence": ["thread_id", "turn_id", "call_id"],
        "native_item_evidence": ["thread_id", "turn_id", "item_id"],
        "generation_evidence": ["path", "sha256", "native"],
        "artifact_evidence": ["path", "sha256"],
        "read": ["summary", "result", "requirements", "native/ID", "task.json", "scope.json",
                 "assignments/ID.json", "reports/ID.json", "checks/ID.json", "generations/OPERATION_ID.json", "final.json"],
        "check": "Executes command once; records actual cwd, exit code, output and unchanged result fingerprint. A missing receipt after interruption requires inspecting native execution before retrying.",
        "report": "Child: assignment_id + completion_turn_id. Root author: assignment_id + turn_id + report with native edit evidence.",
        "generation": "Executes once under BEGIN IMMEDIATE; only declared output files may change. Requires exclusive product editing. Replay returns executed:false and cannot prove authorship. Inspect execution before retrying without a receipt.",
        "generation_invocation": {"python": sys.executable, "journal": str(Path(__file__).with_name("journal.py").resolve()),
                                  "shell": "/bin/zsh", "login": True, "stdin_delimiter": "AF_GENERATION_REQUEST"},
    }


def validate_request(kind, request):
    require(kind in REQUEST_FIELDS, "unknown record kind: " + str(kind))
    require(isinstance(request, dict), "request must be an object")
    require(not set(request) - REQUEST_FIELDS[kind],
            "unknown request fields: " + ", ".join(sorted(set(request) - REQUEST_FIELDS[kind])))


def replay(snapshot, identifier, payload):
    prior = snapshot.operation_receipt(identifier)
    if prior is not None:
        require(prior["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
    return prior


def assignments(snapshot):
    return {Path(name).stem: json.loads(data) for name, data in snapshot.documents.items()
            if name.startswith("assignments/") and name.endswith(".json")}


def overlap(left, right):
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def assignment_record(snapshot, request, source=None):
    from agent_config import default_agents_dir, read_frontmatter, resolve_role_path
    from task_workspace import path_allowed
    from verification_evidence import CodexSessionSource, own_metadata, session_metadata
    identifier = record_id(request.get("id"))
    kind = request.get("kind")
    require(kind in ASSIGNMENT_KINDS, "invalid assignment kind")
    role = request.get("role")
    require(isinstance(role, str), "role must be a string")
    read_frontmatter(resolve_role_path(default_agents_dir(), role))
    require(kind != "qa" or role == "qa-verifier", "QA assignment requires qa-verifier")
    require(kind != "reviewer" or role == "reviewer", "reviewer assignment requires reviewer")
    agent_id = request.get("agent_id")
    source = source or CodexSessionSource()
    root_id = snapshot.identities["root_agent_id"]
    if agent_id == "/root":
        agent_id = root_id
    elif isinstance(agent_id, str) and agent_id.startswith("/root/"):
        agent_id = source.resolve_session(agent_id, root_id, role)["codex_thread_id"]
    events = source.read(agent_id)
    if agent_id == root_id:
        own_metadata(events, root_id)
        require(kind in {"author", "analysis"}, "QA and reviewer must be independent child agents")
    else:
        session_metadata(events, agent_id, root_id, role, source=source)
    paths = request.get("allowed_paths", [])
    require(isinstance(paths, list) and all(isinstance(p, str) and Path(p).is_absolute() for p in paths),
            "allowed_paths must be absolute paths")
    require(kind != "author" or bool(paths), "author assignment requires allowed_paths")
    for path in paths:
        require(path_allowed(snapshot.json("scope.json"), path), "assignment path outside product scope: " + path)
    depends = request.get("depends_on", [])
    require(isinstance(depends, list) and all(isinstance(item, str) for item in depends), "depends_on must be assignment IDs")
    current = assignments(snapshot)
    require(identifier not in depends and set(depends) <= current.keys(), "assignment dependencies must already exist")
    record = {"id": identifier, "kind": kind, "role": role, "agent_id": agent_id,
              "allowed_paths": sorted(set(paths)), "depends_on": sorted(set(depends))}
    require(identifier not in current or current[identifier] == record,
            "assignment already exists; declare a new assignment for a different responsibility")
    if kind == "author":
        for other_id, other in current.items():
            if other_id == identifier or other["kind"] != "author":
                continue
            if any(overlap(Path(a), Path(b)) for a in paths for b in other["allowed_paths"]):
                require(other_id in depends and snapshot.exists("reports/" + other_id + ".json"),
                        "overlapping author paths require a predecessor report and explicit dependency")
    return record


def execute_check(snapshot, request):
    from task_workspace import path_allowed
    from verification_evidence import result_fingerprint
    identifier = record_id(request.get("id"))
    assignment_id = record_id(request.get("assignment_id"))
    assignment = snapshot.json("assignments/" + assignment_id + ".json")
    command = request.get("command")
    require(isinstance(command, list) and command and all(isinstance(value, str) and value for value in command),
            "command must be a nonempty argv array")
    raw_cwd = request.get("cwd")
    require(isinstance(raw_cwd, str) and Path(raw_cwd).is_absolute(), "cwd must be an absolute directory")
    cwd = Path(raw_cwd)
    require(cwd.is_dir() and path_allowed(snapshot.json("scope.json"), raw_cwd)
            and path_allowed(snapshot.json("scope.json"), str(cwd.resolve())), "check cwd outside product scope")
    timeout = request.get("timeout", 300)
    require(type(timeout) is int and timeout > 0, "timeout must be a positive integer")
    path = "checks/" + identifier + ".json"
    require(not snapshot.exists(path), "check ID already exists; use a new ID for a new execution")
    before = result_fingerprint(snapshot)
    try:
        result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise JournalError("check timed out; inspect native execution before any retry") from exc
    require(result_fingerprint(snapshot) == before, "check changed product inputs; inspect changes before recording a new check")
    output = "artifacts/checks/" + identifier + ".log"
    return path, output, result.stdout, {
        "assignment_id": assignment_id, "command": command, "cwd": str(cwd),
        "exit_code": result.returncode, "fingerprint": before,
        "output": {"path": output, "sha256": digest(result.stdout)},
    }, assignment


def generation_invocation(run_dir, identifier, request, python=None):
    command = [python or sys.executable, "-B", str(Path(__file__).with_name("journal.py").resolve()),
               "--run-dir", str(Path(run_dir).resolve()), "record", "--kind", "generation",
               "--request-file", "-", "--operation-id", identifier]
    return shlex.join(command) + " <<'AF_GENERATION_REQUEST'\n" + encode_json(request) + "\nAF_GENERATION_REQUEST"


def generation_state(snapshot):
    from task_workspace import current_inventory, root_inventory
    from verification_evidence import requirements
    scope = snapshot.json("scope.json")
    inventory = {str(Path(entry["root"]) / entry["path"]): entry
                 for entry in current_inventory(scope, snapshot.logical_root)}
    roots = [{"path": path} for path in scope["context"]]
    context = [entry for root in roots for entry in root_inventory(root, roots, None, scope.get("memory", []))]
    return inventory, digest(encode_json({"context": context, "requirements": requirements(snapshot)["digest"]}).encode())


def execute_generation(snapshot, request, identifier):
    from workspace_scope import canonical_path, path_allowed
    identifier = record_id(identifier)
    assignment_id = record_id(request.get("assignment_id"))
    assignment = snapshot.json("assignments/" + assignment_id + ".json")
    require(assignment.get("kind") == "author", "generation requires an author assignment")
    command, outputs = request.get("command"), request.get("outputs")
    require(isinstance(command, list) and command and all(isinstance(value, str) and value for value in command),
            "command must be a nonempty argv array")
    require(isinstance(outputs, list) and outputs and all(isinstance(value, str) for value in outputs)
            and len(outputs) == len(set(outputs)), "generation outputs must be a nonempty unique path list")
    scope = snapshot.json("scope.json")
    for value in outputs:
        path = Path(value)
        require(path.is_absolute() and str(canonical_path(path)) == value and path_allowed(scope, value),
                "generation output outside product scope: " + value)
        require(any(path == Path(allowed) or path.is_relative_to(allowed) for allowed in assignment["allowed_paths"]),
                "generation output outside assignment: " + value)
        require(not path.is_symlink() and (not path.exists() or path.is_file()), "generation output must be a regular file: " + value)
    cwd = request.get("cwd")
    require(isinstance(cwd, str) and Path(cwd).is_absolute() and Path(cwd).is_dir()
            and path_allowed(scope, cwd) and path_allowed(scope, str(Path(cwd).resolve())), "generation cwd outside product scope")
    timeout = request.get("timeout", 300)
    require(type(timeout) is int and timeout > 0, "timeout must be a positive integer")
    before, context = generation_state(snapshot)
    try:
        result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise JournalError("generation timed out; file changes remain; inspect native execution before any retry") from exc
    after, current_context = generation_state(snapshot)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    require(context == current_context, "generation changed context or requirements; inspect file changes before any retry")
    require(set(changed) <= set(outputs), "generation changed undeclared product paths: " + ", ".join(set(changed) - set(outputs)))
    for path in outputs:
        require(not Path(path).is_symlink() and (not Path(path).exists() or Path(path).is_file()),
                "generation output must remain a regular file: " + path)
        require(not Path(path).exists() or path in after, "generation output is not inventoried: " + path)
    path = "generations/" + identifier + ".json"
    output = "artifacts/generations/" + identifier + ".log"
    body = {"operation_id": identifier, "request": request, "python": sys.executable, "exit_code": result.returncode,
            "changes": [{"path": value, "before": before.get(value), "after": after.get(value)} for value in changed],
            "output": {"path": output, "sha256": digest(result.stdout)}}
    data = encoded(body)
    return {path: data, output: result.stdout}, {"path": path, "sha256": digest(data), "exit_code": result.returncode}


def write_entry(run_dir, kind, request, *, identifier, source=None):
    from task_workspace import expand_workspace
    from verification_evidence import accept_report, result_fingerprint
    validate_request(kind, request)
    require(isinstance(identifier, str) and bool(identifier.strip()), "operation-id is required")
    payload = {"kind": kind, "request": request}
    snapshot = JournalSnapshot.open(run_dir)
    prior = replay(snapshot, identifier, payload)
    if prior is not None:
        return {"executed": False, "receipt": prior} if kind == "generation" else prior
    require(not snapshot.exists("final.json"), "task is already finalized")
    prepared_check = execute_check(snapshot, request) if kind == "check" else None
    prepared_report = None
    if kind == "report":
        assignment_id = record_id(request.get("assignment_id"))
        prepared_report = accept_report(snapshot, snapshot.json("assignments/" + assignment_id + ".json"), request, source=source)
    if kind == "assignment":
        assignment_record(snapshot, request, source)
    executed = False

    def publish(current):
        nonlocal executed
        if kind == "generation":
            updates, result = execute_generation(current, request, identifier)
            executed = True
            return updates, result
        if kind == "assignment":
            body = assignment_record(current, request, source)
            path = "assignments/" + body["id"] + ".json"
        elif kind == "scope":
            body = expand_workspace(current.json("scope.json"), request.get("roots"), request.get("context"),
                                    run_dir=current.logical_root)
            path = "scope.json"
        elif kind == "report":
            assignment_id = record_id(request.get("assignment_id"))
            assignment = current.json("assignments/" + assignment_id + ".json")
            require(assignment == snapshot.json("assignments/" + assignment_id + ".json"), "assignment changed before publication")
            body = accept_report(current, assignment, request, source=source)
            require(body == prepared_report, "report inputs changed before publication")
            path = "reports/" + assignment_id + ".json"
        else:
            path, output, output_bytes, body, assignment = prepared_check
            require(current.json("assignments/" + body["assignment_id"] + ".json") == assignment,
                    "check assignment changed before publication")
            require(result_fingerprint(current) == body["fingerprint"], "check result is stale before publication")
            require(not current.exists(path), "check ID already exists; use a new ID for a new execution")
            body = {**body, "operation_id": identifier}
            data = encoded(body)
            return {path: data, output: output_bytes}, {"path": path, "sha256": digest(data), "exit_code": body["exit_code"]}
        data = encoded(body)
        return {path: data}, {"path": path, "sha256": digest(data)}

    receipt = transact(run_dir, identifier, payload, publish)
    return {"executed": executed, "receipt": receipt} if kind == "generation" else receipt


def publish_artifact(run_dir, name, source_path, *, identifier):
    logical_path(name)
    require(name.startswith("artifacts/") and not name.startswith(("artifacts/checks/", "artifacts/generations/")),
            "publish requires an artifact path outside writer-owned checks and generations")
    snapshot = JournalSnapshot.open(run_dir)
    payload = {"kind": "artifact", "path": name, "source": str(Path(source_path).absolute())}
    prior = replay(snapshot, identifier, payload)
    if prior is not None:
        return prior
    data = capture_file(Path(source_path))

    def publish(current):
        require(not current.exists(name) or current.read_bytes(name) == data,
                "artifact already exists with different bytes; use a new name")
        return {name: data}, {"path": name, "sha256": digest(data)}

    return transact(run_dir, identifier, payload, publish)


def read_record(snapshot, name):
    from task_workspace import changed_paths
    from verification_evidence import requirements, result_fingerprint
    if name.startswith("native/"):
        from verification_evidence import native_references
        return native_references(snapshot, record_id(name.removeprefix("native/")))
    if name == "requirements":
        return requirements(snapshot)
    if name == "result":
        return {"fingerprint": result_fingerprint(snapshot),
                "changed_paths": changed_paths(snapshot.json("scope.json"), run_dir=snapshot.logical_root)}
    if name == "summary":
        return {"task": snapshot.json("task.json"), "assignments": list(assignments(snapshot).values()),
                "reports": [{"path": key, "sha256": digest(data), "status": json.loads(data)["status"],
                             "summary": json.loads(data)["summary"]}
                            for key, data in sorted(snapshot.documents.items()) if key.startswith("reports/")],
                "final": snapshot.json("final.json") if snapshot.exists("final.json") else None}
    return snapshot.json(name)


def markdown_view(value):
    if isinstance(value, dict) and "status" in value and "summary" in value:
        lines = ["# " + value["status"], "", value["summary"]]
        if value.get("reason"):
            lines.extend(["", value["reason"]])
        for evidence in value.get("evidence", []):
            lines.append("\n- " + encode_json(evidence))
        return "\n".join(lines) + "\n"
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"
