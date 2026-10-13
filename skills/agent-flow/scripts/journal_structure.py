"""Pure assignment checks shared by journal publication and run diagnostics."""
import json
import re
import hashlib
import html
from functools import lru_cache


MODES = {"subagent", "role-lane"}
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
TERMINAL = {"pass", "pass-with-risks", "blocked", "fail"}
STATUSES = {"planned", "spawned", "active", "timed-out", "replaced", *TERMINAL}

# Caller vocabulary is deliberately independent from archived domain states.
STATUS_MEANINGS = {
    "planned": "Assignment is declared; execution has not started.",
    "active": "Execution is in progress; spawned is a stage with this status.",
    "pass": "The stated work completed successfully; verification requires actual evidence.",
    "pass-with-risks": "Work completed with explicitly reported remaining risks.",
    "blocked": "Work cannot proceed; this is a valid substantive outcome.",
    "fail": "Work failed; this is a valid substantive outcome.",
    "timed-out": "Execution exceeded its allowed time; obligation remains unresolved.",
    "replaced": "Another assignment replaces this execution; responsibility requires explicit resolution.",
}
CALLER_STATUSES = frozenset(STATUS_MEANINGS)
EVENT_STATUS_RULES = {"spawned": {"active"}, "handoff": {"pass", "pass-with-risks"}, "blocked": {"blocked"}, "fail": {"fail"}}
RESERVED_EVENT_COMMANDS = {"final": "journal finalize", "reopen": "journal reopen", "upgrade": "journal upgrade", "spawn": "stage spawned"}
RESERVED_EVENT_STAGES = frozenset(RESERVED_EVENT_COMMANDS)
TERMINAL_KINDS = {"handoff", "final", "check"}
DOCUMENT_PATHS = {"task": "run.md", "plan": "plan.md", "progress": "progress.md", "final": "final.md", "lesson": "lesson.md"}
REQUEST_FIELDS = {
    "event": {"summary": "string", "status": "status", "stage": "string", "role": "string",
              "lane_id": "string?", "evidence": "references?", "next_step": "string?"},
    "handoff": {"summary": "string", "status": "status", "lane_id": "string", "evidence": "references?"},
    **{kind: {"summary": "string", "status": "status", "path": "string?", "evidence": "references?"}
       for kind in ("task", "plan", "progress", "final", "lesson")},
    "check": {"summary": "string", "status": "status", "command": "strings", "cwd": "string"},
}

DOCUMENT_PATHS.update({"route": "route.md", "decision": "decisions.md", "dod": "definition-of-done.md", "checks": "checks.md", "coverage": "checks/coverage-matrix.md", "simplicity-scope": "checks/engineering-simplicity-scope.md"})
REQUEST_FIELDS.update({kind: {"summary": "string", "status": "status"} for kind in ("route", "decision", "dod", "checks", "coverage")})
SERVICE_FIELDS = {
    "claim": {"id": "string", "subjects": "strings", "owner_lane": "nullable-string?", "reviewed_by": "nullable-string?", "section": "string?", "evidence": "marker-evidence?"},
    "acceptance": {"id": "string", "source": "string", "subjects": "strings", "contract_types": "strings", "surface_expectations": "expectations", "evidence": "surface-evidence?", "negative_fixture_evidence": "surface-evidence?"},
    "risk": {"id": "string", "category": "string", "impact": "string", "affected_scope": "string", "owner_lane": "string", "detected_by": "string", "evidence": "references"},
    "resolution": {"risk_id": "string", "resolution_type": "string", "effect": "string", "verification": "string", "owner_lane": "string", "verified_by": "nullable-string?", "reviewed_by": "nullable-string?", "evidence": "references?"},
    "resolution-attempt": {"risk_id": "string", "id": "string", "resolution_type": "string", "effect": "string", "verification": "string", "owner_lane": "string", "verified_by": "nullable-string?", "reviewed_by": "nullable-string?", "evidence": "references?", "blocked_reason": "string?", "rollback": "rollback?", "lesson": "lesson-observation?"},
    "senior-qa-recovery": {"risk_id": "string", "lane_id": "string", "decision": "string", "recheck": "string", **{key: "string-list" for key in ("covered_acceptance_criteria", "missing_acceptance_criteria", "ambiguous_acceptance_criteria", "added_acceptance_criteria", "external_blockers", "test_cases", "edge_cases", "negative_cases", "regression_cases", "integration_cases", "data_state_cases", "environment_cases")}, "evidence": "references"},
    **{kind: {"risk_id": "string", "lane_id": "string", "decision": "string", "instruction": "string", "forbidden_repeat": "strings", "evidence": "references"} for kind in ("architect-recovery", "supervising-recovery")},
    "readiness-attempt": {"id": "string", "lane_id": "string", "blockers": "string-list", "approval_requests": "string-list?", "evidence": "references?"},
    "readiness-gate": {"attempt_id": "string", "axis": "string", "facet": "string", "check": "string", "evidence": "references", "approval_request": "string?"},
    "readiness-approval": {"id": "string", "affected_gates": "strings", "commands": "commands", "manual_instruction": "string", "decision": "string?", "source_event": "integer?", "source_quote": "string?"},
    "readiness-execution": {"request_id": "string", "check_id": "string"},
    "continuation": {"checkpoint": "checkpoint", "readiness_lane": "string", "qa_recheck_lane": "nullable-string?", "reviewer_recheck_lane": "nullable-string?", "historical_worker_lanes": "string-list", "new_worker_lanes": "string-list", "revalidated_lanes": "string-list"},
    "continuation-blocker": {"id": "string", "evidence": "references"},
    "harness-finding": {"id": "string", "finding_type": "string", "problem_class": "string", "architecture_context": "string-list", "architecture_capabilities": "string-list", "outcome": "string", "lesson": "string", "reuse_when": "string", "do_not_reuse_when": "string", "evidence": "references"},
    "harness-proposal": {"id": "string", "finding_id": "string", "proposal_type": "string", "evidence": "references"},
    "harness-evaluation": {"evidence": "references", "reviewed_by": "nullable-string?"},
    "lane": {"id": "string", "lane_type": "string", "role": "string", "execution_mode": "string", "wave": "integer", "critical": "boolean", "allowed_paths": "strings?", "forbidden_paths": "string-list?"},
    "lane-compliance": {"lane_id": "string", "sections": "strings", "matrix_facets": "string-list", "needs_recheck": "boolean", "recheck_lane": "nullable-string?"},
    "lane-simplicity": {"lane_id": "string", "checked_checks": "strings", "action": "string?", "evidence": "references?"},
    "lane-scope": {"lane_id": "string", "primary_surfaces": "string-list", "secondary_surfaces": "string-list", "evidence": "references"},
    "simplicity-scope": {"primary_surfaces": "string-list", "secondary_surfaces": "string-list", "evidence": "references"},
    "lane-gate": {"lane_id": "string", "axis": "string", "facet": "string", "check": "string", "evidence": "references"},
    "lane-report": {"lane_id": "string", "sections": "sections?", "evidence": "references?"},
}
REQUEST_FIELDS.update({kind: {"summary": "string", "status": "status", **fields} for kind, fields in SERVICE_FIELDS.items()})
REQUEST_FIELDS["handoff"]["sections"] = "sections?"
SERVICE_KINDS = frozenset(SERVICE_FIELDS) - {"lane-report", "simplicity-scope"}
SERVICE_STATUS_RULES = {"lane": {"planned"}, "readiness-approval": {"planned", "active", "pass", "blocked"},
    "continuation-blocker": {"pass", "pass-with-risks"},
    "readiness-execution": {"pass", "fail"}, "harness-evaluation": {"active", "pass", "pass-with-risks", "blocked", "fail"},
    "continuation": {"planned", "active", "pass", "pass-with-risks", "blocked", "fail"},
    **{kind: {"pass", "pass-with-risks", "blocked", "fail"} for kind in ("senior-qa-recovery", "architect-recovery", "supervising-recovery")}}
RECOVERY_NEGATIVE_DECISIONS = {
    "senior-qa-recovery": {"criteria-blocked"},
    "architect-recovery": {"blocked"},
    "supervising-recovery": {"confirmed-final-block", "external-blocker", "fail"},
}


def recovery_decisions(kind, status):
    choices = set(semantic_enums()[kind]["decision"])
    negative = RECOVERY_NEGATIVE_DECISIONS[kind]
    return choices - negative if status in {"pass", "pass-with-risks"} else choices & negative


NESTED_FIELDS = {
    "marker-evidence": {"path": "string", "markers": "strings"},
    "surface-evidence": {"path": "string", "markers": "strings", "surface": "string", "polarity": "string", "proof_kind": "string"},
    "expectations": {"surface": "string", "polarity": "string", "proof_kinds": "strings"},
    "sections": {"section": "string", "summary": "string"},
    "commands": {"cwd": "string", "command": "string", "source": "string"},
    "rollback": {"disposition": "string", "summary": "string", "evidence": "references?"},
    "lesson-observation": {"disposition": "string", "classification": "string", "summary": "string", "forbidden_repeat": "strings"},
    "checkpoint": {"lane_id": "string", "snapshot": "string"},
}


@lru_cache(maxsize=1)
def domain_validator():
    """Reuse the existing validator and its enums; no parallel schema registry."""
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    spec = spec_from_file_location("journal_domain_validator", Path(__file__).with_name("validate-run.py"))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semantic_enums():
    validator = domain_validator()
    return {"risk": {"category": sorted(validator.RISK_MITIGATION_CATEGORIES)},
            "resolution": {"resolution_type": sorted(validator.RISK_RESOLUTION_TYPES), "effect": ["fix", "mitigate", "contain"]},
            "resolution-attempt": {"resolution_type": sorted(validator.RISK_RESOLUTION_TYPES), "effect": ["fix", "mitigate", "contain"], "blocked_reason": sorted(validator.BLOCKED_RESOLUTION_REASONS)},
            "senior-qa-recovery": {"decision": sorted(validator.SENIOR_QA_REVIEW_STATUSES), "recheck": sorted(validator.SENIOR_QA_RECHECK_RESULTS)},
            "architect-recovery": {"decision": sorted(validator.ARCHITECT_REVIEW_DECISIONS)},
            "supervising-recovery": {"decision": sorted(validator.SUPERVISING_ARCHITECT_REVIEW_DECISIONS)},
            "harness-finding": {"finding_type": sorted(validator.HARNESS_FINDING_TYPES), "outcome": sorted(validator.HARNESS_OUTCOMES)},
            "harness-proposal": {"proposal_type": sorted(validator.HARNESS_PROPOSAL_TYPES)},
            "readiness-approval": {"decision": ["approve", "decline"]},
            "lane": {"lane_type": sorted(validator.LANE_TYPES), "execution_mode": sorted(validator.AGENT_EXECUTION_MODES)},
            "lane-simplicity": {"checked_checks": validator.ENGINEERING_SIMPLICITY_REQUIRED_CHECKS},
            "lane-compliance": {"sections": validator.ARCHITECTURE_CONTRACT_SECTIONS},
            "acceptance": {"contract_types": sorted(validator.CONTRACT_NEGATIVE_FIXTURE_TYPES)}}


def nested_choices():
    validator = domain_validator()
    surfaces = {"surface": sorted(validator.ACCEPTANCE_TRACEABILITY_SURFACES), "polarity": sorted(validator.ACCEPTANCE_TRACEABILITY_POLARITIES)}
    sections = {value for key, value in vars(validator).items() if key.endswith("_SECTION") and type(value) is str}
    sections.update(validator.ARCHITECTURE_CONTRACT_SECTIONS)
    sections.update({"Architecture Invariants", "Boundary Evidence", "Engineering Simplicity", "Architecture Compliance", "DoD", "Risks", "Commands", "Files Changed"})
    return {
        "surface-evidence": {**surfaces, "proof_kind": sorted(validator.ACCEPTANCE_TRACEABILITY_PROOF_KINDS)},
        "expectations": {**surfaces, "proof_kinds": sorted(validator.ACCEPTANCE_TRACEABILITY_PROOF_KINDS)},
        "rollback": {"disposition": sorted(validator.ROLLBACK_STATUSES)},
        "lesson-observation": {"disposition": sorted(validator.BLOCKED_LESSON_STATUSES), "classification": sorted(validator.BLOCKED_RESOLUTION_REASONS)},
        "sections": {"section": sorted(sections)},
    }


def validate_nested(field_type, value, name):
    """Only the finite intent shapes declared by this package are accepted."""
    if field_type == "nullable-string":
        if value is not None and (type(value) is not str or not value.strip()):
            raise ValueError(name + " must be a string or null")
    elif field_type == "integer":
        if type(value) is not int or value < 0:
            raise ValueError(name + " must be a non-negative integer")
    elif field_type == "boolean":
        if type(value) is not bool:
            raise ValueError(name + " must be a boolean")
    elif field_type == "string-list":
        if type(value) is not list or any(type(item) is not str or not item.strip() for item in value):
            raise ValueError(name + " must contain only strings")
    else:
        shapes = NESTED_FIELDS
        if field_type not in shapes:
            raise ValueError("unknown field type: " + field_type)
        rows = value if field_type in {"marker-evidence", "surface-evidence", "expectations", "sections", "commands"} else [value]
        if type(rows) is not list:
            raise ValueError(name + " must be an array")
        for row in rows:
            validate_fields(row, shapes[field_type])
            for field, allowed in nested_choices().get(field_type, {}).items():
                values = row[field] if type(row[field]) is list else [row[field]]
                if any(choice not in allowed for choice in values):
                    raise ValueError(name + "." + field + " has an unknown semantic choice")


def validate_fields(request, fields):
    if type(request) is not dict:
        raise ValueError("request must be an object")
    unknown = set(request) - set(fields)
    if unknown:
        raise ValueError("unknown request fields: " + ", ".join(sorted(unknown)))
    for name, field_type in fields.items():
        if name not in request:
            if not field_type.endswith("?"):
                raise ValueError(name + " is required")
            continue
        value = request[name]
        base = field_type.rstrip("?")
        if base in {"string", "status"}:
            if type(value) is not str or (name != "next_step" and not value.strip()):
                raise ValueError(name + " must be a non-empty string")
            if base == "status" and value not in CALLER_STATUSES:
                raise ValueError("unknown caller status: " + value)
        elif base == "strings":
            if type(value) is not list or not value or any(type(item) is not str or not item.strip() for item in value):
                raise ValueError(name + " must contain non-empty strings")
        elif base == "references":
            if type(value) is not list or any(type(ref) is not dict or set(ref) != {"path"} or
                    type(ref["path"]) is not str or not ref["path"].strip() for ref in value):
                raise ValueError("evidence must contain objects with only a string path")
        else:
            validate_nested(base, value, name)


def validate_request(kind, request):
    """Pure public input validation; called before request allocation or writes."""
    if type(kind) is not str or kind not in REQUEST_FIELDS:
        raise ValueError("unknown journal request kind")
    validate_fields(request, REQUEST_FIELDS[kind])
    if kind == "lane":
        from agent_config import default_agents_dir, resolve_role_path, read_frontmatter, role_config
        path = resolve_role_path(default_agents_dir(), request["role"])
        if not path.is_file():
            raise ValueError("unknown registered role: " + request["role"])
        role_config(read_frontmatter(path), request["role"])
    if kind == "check" and ("\x00" in request["cwd"] or any("\x00" in value for value in request["command"])):
        raise ValueError("check command/cwd cannot contain NUL")
    if kind == "readiness-approval":
        supplied = {key for key in ("decision", "source_event", "source_quote") if key in request}
        if supplied and supplied != {"decision", "source_event", "source_quote"} or request["status"] in {"pass", "blocked"} and not supplied:
            raise ValueError("approval observation requires decision, source_event and source_quote together")
        if supplied and (request["source_event"] < 1 or request["decision"] != ("approve" if request["status"] == "pass" else "decline") or request["status"] not in {"pass", "blocked"}):
            raise ValueError("approval decision/status/source_event mismatch")
    if kind in SERVICE_FIELDS:
        if request["status"] not in SERVICE_STATUS_RULES.get(kind, CALLER_STATUSES):
            raise ValueError(kind + " has an invalid caller status")
        if kind in RECOVERY_NEGATIVE_DECISIONS and request["decision"] not in recovery_decisions(kind, request["status"]):
            raise ValueError("recovery decision differs from caller status")
        for name, allowed in semantic_enums().get(kind, {}).items():
            if name in request:
                values = request[name] if type(request[name]) is list else [request[name]]
                if any(value not in allowed for value in values):
                    raise ValueError(name + " has an unknown semantic choice")
        for name in ("id", "attempt_id", "risk_id", "lane_id"):
            if name in request and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", request[name]):
                raise ValueError(name + " must be kebab-case")
        if "axis" in request and request["axis"] not in {"risk_gates", "verification_gates"}:
            raise ValueError("unknown gate axis")
    if kind == "event":
        if request["stage"] == "blocked-checkpoint" and (not request.get("lane_id") or request["role"] != "orchestrator" or request["status"] != "blocked"):
            raise ValueError("blocked-checkpoint requires an orchestrator lane and blocked status")
        if request["stage"] in RESERVED_EVENT_STAGES:
            raise ValueError("stage " + request["stage"] + " requires " + RESERVED_EVENT_COMMANDS[request["stage"]])
        statuses = EVENT_STATUS_RULES
        if request["stage"] in statuses and request["status"] not in statuses[request["stage"]]:
            raise ValueError("stage/status mismatch")
    if kind in TERMINAL_KINDS and request["status"] not in TERMINAL:
        raise ValueError(kind + " requires a terminal caller status")
    if "path" in request and request["path"] != DOCUMENT_PATHS.get(kind):
        raise ValueError("document target is writer-owned")
    return dict(request)


def request_contract():
    return {"statuses": sorted(CALLER_STATUSES), "status_meanings": STATUS_MEANINGS,
            "constraints": {kind: {"statuses": sorted(SERVICE_STATUS_RULES.get(kind, TERMINAL if kind in TERMINAL_KINDS else CALLER_STATUSES)),
                                    "path": DOCUMENT_PATHS.get(kind),
                                    "route": "source-checked finalize" if kind == "final" else "semantic writer",
                                    **({"decision_by_status": {status: sorted(recovery_decisions(kind, status)) for status in SERVICE_STATUS_RULES[kind]}} if kind in RECOVERY_NEGATIVE_DECISIONS else {}),
                                    **({"stage_status": {stage: sorted(values) for stage, values in EVENT_STATUS_RULES.items()}, "reserved_stages": sorted(RESERVED_EVENT_STAGES)} if kind == "event" else {})}
                            for kind in REQUEST_FIELDS},
            "fields": REQUEST_FIELDS,
            "nested_fields": NESTED_FIELDS, "nested_choices": nested_choices(), "semantic_choices": semantic_enums(),
            "examples": {kind: request_example(kind) for kind in REQUEST_FIELDS}}


def request_example(kind):
    def value(name, field_type):
        if field_type in NESTED_FIELDS:
            row = {key: value(key, type_name) for key, type_name in NESTED_FIELDS[field_type].items() if not type_name.endswith("?")}
            return [row] if field_type in {"marker-evidence", "surface-evidence", "expectations", "sections", "commands"} else row
        if field_type == "status":
            allowed = SERVICE_STATUS_RULES.get(kind, TERMINAL if kind in TERMINAL_KINDS else CALLER_STATUSES)
            return "active" if "active" in allowed else "planned" if "planned" in allowed else "pass"
        if field_type == "boolean":
            return False
        if field_type == "integer":
            return 1
        if field_type == "nullable-string":
            return None
        if field_type == "references":
            return [{"path": "artifacts/logs/command.stdout"}]
        if field_type in {"strings", "string-list"}:
            return ["unit-test"] if name == "proof_kinds" else [] if field_type == "string-list" else ["example"]
        return {"id": "record-example", "risk_id": "risk-example", "lane_id": "qa-example", "attempt_id": "readiness-example",
                "cwd": "/private/tmp", "stage": "progress", "role": "orchestrator", "section": "Architecture Invariants",
                "surface": "service", "polarity": "positive", "proof_kind": "unit-test", "axis": "verification_gates"}.get(name, "Meaningful text")
    result = {name: value(name, field_type) for name, field_type in REQUEST_FIELDS[kind].items() if not field_type.endswith("?")}
    for name, allowed in semantic_enums().get(kind, {}).items():
        if name in result:
            result[name] = [allowed[0]] if type(result[name]) is list else allowed[0]
    if kind == "check":
        result["command"] = ["python3", "-V"]
    if kind in RECOVERY_NEGATIVE_DECISIONS:
        result["decision"] = sorted(recovery_decisions(kind, result["status"]))[0]
    return result


def literal_content(text):
    """Keep narrative inside a Markdown quote, outside machine structure."""
    lines = html.escape(text, quote=False).splitlines()
    return "\n".join("> " + line for line in lines) or "> "


def literal_label(text):
    """Represent exact metadata on one line without active Markdown/HTML."""
    label = json.dumps(text, ensure_ascii=False)[1:-1]
    label = "".join(json.dumps(char, ensure_ascii=True)[1:-1] if char.splitlines() != [char] else char for char in label)
    return html.escape(label, quote=False).replace("`", "&#96;")


def render_document(kind, summary, status, metadata=None):
    validate_request("progress", {"summary": summary, "status": status})
    metadata = metadata or {}
    summary = literal_content(summary)
    verdict = "ship" if kind == "final" and status == "pass" else status
    text = "# " + kind.capitalize() + "\n\n" + ("Verdict: " if kind == "final" else "Status: ") + verdict + "\n\n" + summary + "\n"
    if kind == "task":
        text = "# Run\n\nStatus: " + status + "\n\n## Task Scope\n\n" + summary + "\n"
    elif kind == "plan":
        text = "# Plan\n\nStatus: " + status + "\n\n## Execution Plan\n\n" + summary + "\n"
    if metadata.get("evidence"):
        text += "\n## Evidence\n\n" + "".join("- " + literal_label(ref["path"]) + " sha256=" + ref["sha256"] + "\n" for ref in metadata["evidence"])
    for section in metadata.get("sections", []):
        text += "\n## " + section["section"] + "\n\n" + literal_content(section["summary"]) + "\n"
    return text


SERVICE_DOCUMENTS = {"claim-evidence.json", "acceptance-traceability.json", "risk-mitigations.json", "risk-resolutions.json", "verification-readiness.json", "continuation-summary.json", "harness-evaluation.json", "lane-map.json"}


def writer_owned_path(path, *, lane_ids=()):
    return path in {*DOCUMENT_PATHS.values(), *SERVICE_DOCUMENTS, "timeline.jsonl", "delegation-summary.json", "final.md", "task.md", "context.md", "manifest.md", "artifacts.json", "artifacts/source-references.json"} or (
        path.startswith("agents/") and path.endswith("/trace.jsonl")) or path.startswith(("handoffs/", "checks/command-", "checks/lane-boundary-", "artifacts/memory/", "artifacts/workspaces/", "artifacts/lifecycle/", "artifacts/checkpoints/")) or path in {
            "checks/" + lane_id + ".md" for lane_id in lane_ids} or path in {"checks/" + name.replace(".json", ".md") for name in SERVICE_DOCUMENTS}


def normalized(value):
    return value.replace("_", "-") if isinstance(value, str) else None


def canonical_role(value):
    return "reviewer" if value == "reviewer.qa" else value


def archived_assignment_facts(snapshot):
    """Read immutable legacy inventories, never trust a record's legacy flag."""
    from journal_recovery import assignments
    facts = {}
    for identifier in snapshot.archives:
        archived = snapshot.archive(identifier)
        if archived.storage_version != 1:
            continue
        summary = json.loads(archived.documents.get("delegation-summary.json", b"{}"))
        modes = {row["lane_id"]: mode for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane"))
                 for row in summary.get(group, [])}
        for lane_id, original in assignments(archived).items():
            lane = original["lane"] or {}
            first = original["events"][0]["event"] if original["events"] else {}
            facts[lane_id] = {**original, "mode": modes.get(lane_id, lane.get("execution_mode", first.get("execution_mode")))}
    return facts


def assignment_errors(documents, *, history=None):
    """Check intermediate state using only supplied journal bytes."""
    errors = []
    history = history or {}

    def historical_identity(lane_id, identity, mode, *, supplied_only=False):
        fact = history.get(lane_id)
        if fact is None or normalized(mode) != normalized(fact["mode"]):
            return False
        original = fact["summary"] or fact["lane"] or (fact["events"][0]["event"] if fact["events"] else {})
        return all(identity.get(key) == original.get(key) for key in ("role", "codex_thread_id", "trace")
                   if key in original and (not supplied_only or key in identity))

    def historical_status(lane_id, value):
        fact = history.get(lane_id, {})
        return any(item.get("status") == value for item in [fact.get("summary") or {}, fact.get("lane") or {},
                   *[entry["event"] for entry in fact.get("events", [])]])

    def historical_thread(lane_id, value):
        fact = history.get(lane_id, {})
        original = fact.get("summary") or fact.get("lane") or (fact["events"][0]["event"] if fact.get("events") else {})
        return original.get("codex_thread_id") == value

    def document(path, default):
        try:
            return json.loads(documents[path]) if documents.get(path) is not None else default
        except (ValueError, TypeError) as exc:
            errors.append(f"{path}: invalid JSON: {exc}")
            return default

    lane_map = document("lane-map.json", {})
    summary = document("delegation-summary.json", {})
    if not isinstance(lane_map, dict) or not isinstance(summary, dict):
        return errors + ["lane-map.json and delegation-summary.json must be objects"]
    lanes = {}
    raw_lanes = lane_map.get("lanes", [])
    if not isinstance(raw_lanes, list):
        return errors + ["lane-map.json.lanes must be an array"]
    for lane in raw_lanes:
        if not isinstance(lane, dict):
            errors.append("lane-map.json.lanes entry must be an object")
            continue
        lane_id = lane.get("id")
        if not isinstance(lane_id, str) or not lane_id.strip():
            errors.append(f"lane-map.json lane .id invalid: {lane_id!r}")
            continue
        if lane_id in lanes:
            errors.append(f"lane-map.json duplicate lane id: {lane_id}")
        lanes[lane_id] = lane
        old_lane = historical_identity(lane_id, lane, lane.get("execution_mode"), supplied_only=True)
        if "status" in lane and normalized(lane["status"]) not in STATUSES and not (old_lane and historical_status(lane_id, lane["status"])):
            errors.append(f"lane {lane_id}.status invalid: {lane['status']!r}")
        declared = normalized(lane.get("status")) in STATUSES
        if ("execution_mode" in lane or declared) and normalized(lane.get("execution_mode")) not in MODES:
            errors.append(f"lane {lane_id}.execution_mode invalid: {lane.get('execution_mode')!r}")
        if ("role" in lane or declared) and (not isinstance(lane.get("role"), str) or
                                           not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", lane["role"])):
            errors.append(f"lane {lane_id}.role invalid: {lane.get('role')!r}")
    records = {}
    for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane")):
        entries = summary.get(group, [])
        if not isinstance(entries, list):
            errors.append(f"delegation-summary.json.{group} must be an array")
            continue
        for record in entries:
            if not isinstance(record, dict):
                errors.append(f"delegation-summary.json.{group} entry must be an object")
                continue
            lane_id = record.get("lane_id")
            if not isinstance(lane_id, str) or not lane_id.strip():
                errors.append(f"delegation-summary.json.{group}.lane_id invalid: {lane_id!r}")
                continue
            if lane_id in records:
                errors.append(f"delegation-summary.json duplicate lane_id: {lane_id}")
            records[lane_id] = (record, mode)
            old_record = historical_identity(lane_id, record, mode)
            if "status" in record and normalized(record["status"]) not in STATUSES and not (old_record and historical_status(lane_id, record["status"])):
                errors.append(f"assignment {lane_id}.status invalid: {record['status']!r}")
            if not isinstance(record.get("role"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", record["role"]):
                errors.append(f"assignment {lane_id}.role invalid: {record.get('role')!r}")
            lane = lanes.get(lane_id)
            if "lane-map.json" in documents and lane is None and not old_record:
                errors.append(f"assignment {lane_id}: unknown lane "
                              f"(role={record.get('role')!r}, mode={mode!r}, "
                              f"thread={record.get('codex_thread_id')!r}, status={record.get('status')!r})")
            if lane is not None:
                if canonical_role(lane.get("role")) != canonical_role(record.get("role")):
                    errors.append(f"assignment {lane_id}.role {record.get('role')!r} differs from lane {lane.get('role')!r}")
                if normalized(lane.get("execution_mode")) != mode:
                    errors.append(f"assignment {lane_id}.execution_mode {mode!r} differs from lane {lane.get('execution_mode')!r}")
                if record.get("status") is not None and lane.get("status") is not None and normalized(lane["status"]) not in {"timed-out", "replaced"} and normalized(record["status"]) != normalized(lane["status"]):
                    errors.append(f"assignment {lane_id}.status {record['status']!r} differs from lane {lane['status']!r}")
                if lane.get("codex_thread_id") is not None and lane["codex_thread_id"] != record.get("codex_thread_id"):
                    errors.append(f"assignment {lane_id}.codex_thread_id {record.get('codex_thread_id')!r} differs from lane {lane['codex_thread_id']!r}")
                if lane.get("trace") is not None and lane["trace"] != record.get("trace"):
                    errors.append(f"assignment {lane_id}.trace {record.get('trace')!r} differs from lane {lane['trace']!r}")
            planned = record.get("status") == "planned" or lane is not None and lane.get("status") == "planned"
            if mode == "subagent" and not planned and not (old_record and historical_thread(lane_id, record.get("codex_thread_id"))) and not (isinstance(record.get("codex_thread_id"), str) and
                                         UUID.fullmatch(record["codex_thread_id"])):
                errors.append(f"assignment {lane_id}.codex_thread_id invalid: {record.get('codex_thread_id')!r}")
    for lane_id, lane in lanes.items():
        if normalized(lane.get("status")) in {"active", "spawned", *TERMINAL} and lane_id not in records and not historical_identity(lane_id, lane, lane.get("execution_mode"), supplied_only=True):
            errors.append(f"lane {lane_id}.status {lane['status']!r} requires assignment registration")
    timeline = []
    trace_events = {}
    for path, raw in documents.items():
        if path != "timeline.jsonl" and not (path.startswith("agents/") and path.endswith("/trace.jsonl")):
            continue
        if raw is None:
            continue
        for number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                errors.append(f"{path}:{number}: invalid event JSON")
                continue
            if not isinstance(event, dict):
                errors.append(f"{path}:{number}: event must be an object")
                continue
            trace_events.setdefault(path, []).append(event)
            lane_id = event.get("lane_id")
            if lane_id is None or lane_id == "":
                continue
            if not isinstance(lane_id, str):
                errors.append(f"{path}:{number}: lane_id invalid: {lane_id!r}")
                continue
            registration, registered_mode = records.get(lane_id, ({}, None))
            if (not registration or historical_identity(lane_id, registration, registered_mode)) and any(
                    entry["path"] == path and entry["index"] == number and entry["event"] == event
                    for entry in history.get(lane_id, {}).get("events", [])):
                if path == "timeline.jsonl":
                    timeline.append(event)
                continue
            record, mode = records.get(lane_id, ({}, None))
            if mode is None:
                errors.append(f"{path}:{number}: assignment {lane_id} is unregistered")
                continue
            for field, expected in (("role", record.get("role")), ("execution_mode", mode)):
                normalize = canonical_role if field == "role" else normalized
                if normalize(event.get(field)) != normalize(expected):
                    errors.append(f"{path}:{number}: assignment {lane_id}.{field} {event.get(field)!r} differs from registration {expected!r}")
            if mode == "subagent" and event.get("codex_thread_id") != record.get("codex_thread_id"):
                errors.append(f"{path}:{number}: assignment {lane_id}.codex_thread_id {event.get('codex_thread_id')!r} differs from registration {record.get('codex_thread_id')!r}")
            if path == "timeline.jsonl":
                timeline.append(event)
    for event in timeline:
        path = event.get("agent_trace")
        if path:
            if not isinstance(path, str):
                errors.append(f"assignment {event['lane_id']}.agent_trace invalid: {path!r}")
                continue
            if event not in trace_events.get(path, []):
                errors.append(f"assignment {event['lane_id']}: timeline event missing from {path}")
    for lane_id, (record, mode) in records.items():
        path = record.get("trace")
        planned = record.get("status") == "planned" or lanes.get(lane_id, {}).get("status") == "planned"
        old_record = historical_identity(lane_id, record, mode)
        if not path:
            if not planned and not old_record:
                errors.append(f"assignment {lane_id}.trace required "
                              f"(role={record.get('role')!r}, thread={record.get('codex_thread_id')!r})")
            continue
        if not isinstance(path, str):
            errors.append(f"assignment {lane_id}.trace invalid: {path!r}")
            continue
        events = [event for event in trace_events.get(path, []) if event.get("lane_id") == lane_id]
        if mode == "subagent" and not planned and not old_record and not any(event.get("stage") == "spawned" for event in events):
            errors.append(f"assignment {lane_id}: registered assignment requires spawned trace in {path} "
                          f"(role={record.get('role')!r}, thread={record.get('codex_thread_id')!r})")
        if events and record.get("status") is not None and normalized(record["status"]) not in {"timed-out", "replaced"} and normalized(record["status"]) != normalized(events[-1].get("status")):
            errors.append(f"assignment {lane_id}.status {record['status']!r} differs from latest trace {events[-1].get('status')!r}")
        if path.startswith("agents/") and path.endswith("/trace.jsonl"):
            timeline_events = [event for event in timeline if event.get("lane_id") == lane_id]
            if events != timeline_events:
                fingerprint = lambda values: hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
                errors.append(f"assignment {lane_id}: ordered timeline/trace events differ: "
                              f"timeline count={len(timeline_events)} sha256={fingerprint(timeline_events)}, "
                              f"trace={path} count={len(events)} sha256={fingerprint(events)}")
    return errors
