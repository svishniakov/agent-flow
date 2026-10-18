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
import stat
import fnmatch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, role_config, resolve_role_path
from journal_io import JournalError, timestamp, event_time, source_time, declared_paths, JournalSnapshot, operation_id, transact, UNSUPPORTED_STORAGE_VERSION
from task_workspace import registered_workspace, verify_candidate, manifest_digest, checked_document, manifest_delta

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
    if len(data) == 0 and section is None:
        key = snapshot.key(path)
        if key.startswith("checks/command-") and key.endswith(".output"):
            from journal_io import require_command_evidence
            require_command_evidence(snapshot, [key[:-7] + ".json"], successful=False)
            return data
    require(bool(data.strip()), f"empty evidence: {ref.get('path')}")
    return data


def verify_reference(run_dir: Path, ref: dict, *, snapshot=None) -> str:
    actual = sha256(reference_bytes(run_dir, ref, snapshot=snapshot))
    require(ref.get("sha256") == actual, f"evidence sha256 mismatch: {ref.get('path')}")
    return actual


def evidence_assignment(record):
    """Immutable source/assignment identity, excluding duty resolution metadata."""
    return {key: record.get(key) for key in ("lane_id", "role", "codex_thread_id", "completion_turn_id",
        "source_completion_sha256", "handoff", "handoff_sha256", "status") if record.get(key) is not None}


def historical_evidence_bytes(snapshot, record, ref):
    """Resolve preserved input only through its native mutation receipt binding."""
    from dataclasses import replace
    from types import MappingProxyType
    binding = evidence_assignment(record)
    matches = [row for raw in snapshot.receipts.values()
        for row in json.loads(raw).get("result", {}).get("preserved_evidence", [])
        if type(row.get("assignment")) is dict
        and {"lane_id", "role", "handoff", "status"} <= row["assignment"].keys()
        and row["assignment"].keys() <= binding.keys()
        and all(binding.get(key) == value for key, value in row["assignment"].items())
        and row.get("reference") == ref]
    if not matches:
        if review_subject_kind(record) == "requirements-plan":
            pin = review_input_receipt(snapshot, record, current=False)
            captures = [capture for capture in pin["captures"] if capture["key"] == "journal:" + ref["path"]]
            matches = [{"lane_id": record["lane_id"], "capture": {"path": capture["path"],
                       "sha256": sha256(snapshot.read_bytes(capture["path"]))}} for capture in captures]
    if not matches:
        verify_reference(snapshot.logical_root, ref, snapshot=snapshot)
        return reference_bytes(snapshot.logical_root, ref, snapshot=snapshot)
    raw_bytes = None
    for row in matches:
        capture = row["capture"]
        require(row.get("lane_id") == record.get("lane_id"), "preserved evidence lane differs")
        require(isinstance(capture.get("path"), str) and snapshot.exists(capture["path"]),
                "preserved evidence capture missing: " + str(capture.get("path")))
        raw = snapshot.read_bytes(capture["path"])
        require(capture["path"] == "artifacts/source-captures/" + sha256(raw) + ".bin" and
                capture.get("sha256") == sha256(raw), "preserved evidence capture mismatch")
        require(raw_bytes is None or raw_bytes == raw, "preserved evidence binding conflict")
        raw_bytes = raw
    preserved = replace(snapshot, documents=MappingProxyType({**snapshot.documents, ref["path"]: raw_bytes}))
    verify_reference(snapshot.logical_root, ref, snapshot=preserved)
    return reference_bytes(snapshot.logical_root, ref, snapshot=preserved)


def require_current_architecture_inputs(snapshot):
    """Historical proof survives; current architect duties require current inputs."""
    from dataclasses import replace
    from types import MappingProxyType
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    records = [row for group in ("subagents", "role_lanes") for row in summary.get(group, []) if row.get("role") == "architect"
               and row.get("status") in {"pass", "pass-with-risks"}]
    resolved = [row["lane_id"] for row in records if row.get("obligation", {}).get("state") in {"resolved", "exempt"}]
    if resolved:
        _, errors = evaluate_obligations(snapshot, summary, selected_lane_ids=set(resolved))
        require(not errors, "; ".join(errors))
    for record in records:
        if record["lane_id"] in resolved:
            continue
        for ref in record.get("evidence", []):
            historical = historical_evidence_bytes(snapshot, record, ref)
            section = ref.get("section") or {"run.md": "Task Scope", "plan.md": "Execution Plan"}.get(ref["path"])
            if section and not ref.get("section"):
                # Reuse the same strict section reader for both versions.
                previous = replace(snapshot, documents=MappingProxyType({**snapshot.documents, ref["path"]: historical}))
                comparison = {"path": ref["path"], "section": section}
                historical = reference_bytes(snapshot.logical_root, comparison, snapshot=previous)
                current = reference_bytes(snapshot.logical_root, comparison, snapshot=snapshot)
            else:
                current = reference_bytes(snapshot.logical_root, ref, snapshot=snapshot)
            require((current.rstrip(b"\n") == historical.rstrip(b"\n")) if section and not ref.get("section") else current == historical,
                    "architecture reviewed input is stale: " + ref["path"] + "; obtain current same-duty review")


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
        if record is None or review_subject_kind(record) != "product-result":
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


def semantic_result_payload(snapshot, workspace, sealed, scope, *, require_complete=True):
    """The closed v5 product contract; presentation and OS observations stay out."""
    task = workspace.get("task_scope", {})
    def fields(item, names):
        return {name: item.get(name) for name in names}
    def ordered(items):
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    def link(item):
        return {**fields(item, ("source", "original_target")),
                "mode": item.get("mode", stat.S_IMODE(item["descriptor"][2]) if "descriptor" in item else None)}
    products = task.get("product_roots", [{"source": workspace["source"], "relative_path": ".",
                                           "source_metadata": workspace["source_metadata"]}])
    binding = workspace.get("metadata_scope")
    metadata = None if binding is None else {
        **fields(binding["request"], ("namespace", "authorized_source")),
        "excluded_relative_path": Path(binding["metadata_root"]).relative_to(binding["source_root"]).as_posix()}
    lanes = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
    boundaries = []
    entries = resolve_author_boundaries(snapshot, workspace, require_complete=require_complete)
    if entries is None:
        return None
    if require_complete:
        require_current_contributions(snapshot, workspace, entries)
    for entry in entries:
        boundary = entry["boundary"]
        data = entry["data"]
        boundaries.append({"lane_id": entry["lane_id"], "assignment": fields(entry["assignment"], ("codex_thread_id", "role")),
                           "allowed_paths": sorted(boundary.get("allowed_paths", [])) if boundary is not None else None,
                           "forbidden_paths": sorted(boundary.get("forbidden_paths", [])) if boundary is not None else None,
                           "authored_paths": sorted(data.get("authored_paths", [])),
                           "coauthors": {path: sorted(peers) for path, peers in sorted(data.get("coauthors", {}).items())}})
    external = scope["external_followup"]["gate_refs"] if scope else []
    gates = [{"axis": axis, "facet": facet}
             for axis in ("verification_gates", "risk_gates")
             for facet in lanes.get("architecture_context", {}).get(axis, [])
             if {"axis": axis, "facet": facet} not in external]
    return {"hash_version": 5, "baseline_manifest_digest": workspace["baseline_digest"],
            "candidate_manifest_digest": sealed["candidate_digest"], "delta": sealed["delta"],
            "product_roots": ordered([{**fields(item, ("source", "relative_path")),
                                       "kind": item.get("source_metadata", {}).get("kind", "directory")} for item in products]),
            "context_roots": ordered([fields(item, ("canonical_path", "kind", "manifest", "digest"))
                                      for item in task.get("context_roots", [])]),
            "context_exclusions": ordered([{**fields(item, ("source", "relative_path", "original_target", "canonical_path")),
                                            "mode": link(item)["mode"],
                                            "resolution_chain": [link(part) for part in item.get("resolution_chain", [])]}
                                           for item in task.get("context_exclusions", [])]),
            "link_provenance": ordered([fields(item, ("source", "original_target", "target", "canonical_path"))
                                        for item in task.get("link_provenance", [])]),
            "metadata_exclusion": metadata, "delivery_scope": scope,
            "author_boundaries": ordered(boundaries), "required_local_gates": ordered(gates)}


def result_hash(run_dir: Path, verification: dict, *, snapshot=None, require_ready=True):
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    workspace = registered_workspace(snapshot)
    if workspace is not None:
        if "seal" not in workspace and not require_ready:
            return None
        sealed = verify_candidate(workspace, inputs=snapshot.external_inputs)
        binding = workspace.get("metadata_scope")
        if binding is not None:
            for field in ("scope_ref", "qa_proof_ref", "reviewer_proof_ref"):
                verify_reference(run_dir, binding["request"][field], snapshot=snapshot)
        files = verification.get("result_files")
        require(isinstance(files, list) and (bool(files) or not require_ready), "change requires non-empty result_files")
        require(all(isinstance(name, str) for name in files) and len(files) == len(set(files)),
                "result_files must contain distinct paths")
        require(set(files) == set(sealed["changed_paths"]),
                "result_files must equal the full workspace delta")
        marker = sealed.get("result_hash_version")
        require(type(marker) is int and marker == 5, "unsupported result_hash_version")
        refs = {}
        for name in ("initial_snapshot", "task_scope"):
            reference = verification.get(name)
            if reference is None and (not require_ready or name == "initial_snapshot"):
                refs[name] = None
                continue
            require(isinstance(reference, dict), f"missing source reference: {name}")
            refs[name] = {"path": reference.get("path"), "section": reference.get("section"),
                          "sha256": verify_reference(run_dir, reference, snapshot=snapshot)}
        scope = delivery_scope(snapshot, verification)
        if scope is not None and require_ready:
            validate_delivery_scope_history(snapshot, scope, verification)
        payload = semantic_result_payload(snapshot, workspace, sealed, scope, require_complete=require_ready)
        if payload is None:
            require(verification.get("result_hash") is None, "required author proof missing for existing result_hash")
            return None
        payload["requirements_sha256"] = refs["task_scope"]["sha256"] if refs["task_scope"] else None
        return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
    if verification.get("delivery_scope") is not None:
        delivery_scope(snapshot, verification)
        require(not require_ready, "delivery_scope requires retained local workspace candidate")
        return None
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
    require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
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


def delivery_scope(snapshot, verification=None, *, summary=None):
    """Validate explicit external references once; absence retains strict policy."""
    if summary is None:
        summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
    verification = verification if verification is not None else summary.get("verification", {})
    scope = verification.get("delivery_scope")
    if scope is None:
        return None
    require(verification.get("task_kind") == "change", "delivery_scope requires an independent local change result")
    require(isinstance(scope, dict) and set(scope) == {"version", "completion_target", "external_followup"}, "delivery_scope fields invalid")
    require(type(scope["version"]) is int and scope["version"] == 1 and scope["completion_target"] == "local-deliverable", "delivery_scope version or completion_target invalid")
    followup = scope["external_followup"]
    fields = {"acceptance_ids", "claim_ids", "lane_ids", "gate_refs", "risk_ids"}
    require(isinstance(followup, dict) and set(followup) == fields, "delivery_scope external_followup fields invalid")
    normalized = {}
    for name in fields:
        values = followup[name]
        require(isinstance(values, list), "delivery_scope " + name + " must be an array")
        if name == "gate_refs":
            require(all(isinstance(v, dict) and set(v) == {"axis", "facet"} and all(isinstance(v[k], str) and v[k] for k in v) for v in values), "delivery_scope gate_refs invalid")
        else:
            require(all(isinstance(v, str) and v for v in values), "delivery_scope " + name + " invalid")
        keys = [json.dumps(v, sort_keys=True) for v in values]
        require(len(keys) == len(set(keys)), "delivery_scope " + name + " duplicates")
        normalized[name] = sorted(values, key=lambda v: json.dumps(v, sort_keys=True))
    def document(path):
        return json.loads(snapshot.read_text(path)) if snapshot.exists(path) else {}
    lanes = document("lane-map.json")
    records = {r.get("lane_id"): r for kind in ("subagents", "role_lanes") for r in summary.get(kind, [])}
    records.update({r.get("id"): r for r in lanes.get("lanes", [])})
    inventories = {
        "acceptance_ids": {r.get("id") for r in document("acceptance-traceability.json").get("acceptance", [])},
        "claim_ids": {r.get("id") for r in document("claim-evidence.json").get("claims", [])},
        "lane_ids": set(records),
        "risk_ids": {r.get("id") for r in document("risk-mitigations.json").get("risks", [])},
    }
    for name, inventory in inventories.items():
        require(set(normalized[name]) <= inventory, "delivery_scope unknown " + name)
    for risk in document("risk-mitigations.json").get("risks", []):
        if risk.get("id") in normalized["risk_ids"]:
            require(risk.get("category") not in {"architecture-drift", "incomplete-implementation", "test-gap", "security-risk", "data-risk", "ux-risk"}, "delivery_scope cannot exclude known product defect")
    protected = {verification.get("qa"), verification.get("reviewer")}
    for lane_id in normalized["lane_ids"]:
        lane = records[lane_id]
        require(lane_id not in protected and lane.get("role") not in {"python-worker", "frontend-worker", "backend-worker", "typescript-worker", "golang-worker", "ios-worker", "worker"} and lane.get("type") not in {"implementation", "integration", "worker", "frontend", "backend"}
                and not lane.get("boundary") and lane.get("codex_thread_id") not in verification.get("author_thread_ids", []), "delivery_scope cannot exclude author or selected verifier: " + lane_id)
    context = lanes.get("architecture_context", {})
    for ref in normalized["gate_refs"]:
        require(ref["axis"] in {"verification_gates", "risk_gates"} and ref["facet"] in context.get(ref["axis"], []), "delivery_scope unknown gate_ref")
        require(ref["facet"] not in {"evidence-source-integrity", "local-private-files"}, "delivery_scope cannot exclude integrity gate")
    source = verification.get("task_scope")
    verify_reference(snapshot.artifact_root, source, snapshot=snapshot)
    source_text = reference_bytes(snapshot.artifact_root, source, snapshot=snapshot).decode()
    for name, values in normalized.items():
        for value in values:
            tokens = value.values() if isinstance(value, dict) else [value]
            require(all(token in source_text for token in tokens), "delivery_scope references must be fixed in task_scope")
    return {"version": 1, "completion_target": "local-deliverable", "external_followup": normalized}


def external_followup(snapshot, field, verification=None, *, summary=None):
    if snapshot is None:
        return []
    scope = delivery_scope(snapshot, verification, summary=summary)
    return scope["external_followup"][field] if scope else []


def validate_delivery_scope_history(snapshot, scope, verification):
    binding = {"delivery_scope": scope, "task_scope": verification.get("task_scope")}
    events = snapshot.timeline()
    indices = [index for index, event in enumerate(events) if event.get("delivery_scope_binding") == binding]
    require(bool(indices), "delivery_scope must be recorded before corresponding checks")
    for index in indices:
        bound_digest = sha256(json.dumps(binding, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
        require(any((receipt := snapshot.operation_receipt(identifier)).get("result", {}).get("delivery_scope_binding_sha256") == bound_digest
                    and receipt["result"].get("timeline_index") == index + 1 for identifier in snapshot.receipts), "delivery_scope binding lacks native recorder receipt")
    before = min(indices)
    lanes = {verification.get("qa"), verification.get("reviewer")}
    previous = any(event.get("delivery_scope_binding") is not None and index < before for index, event in enumerate(events))
    if not previous:
        lanes.update(scope["external_followup"]["lane_ids"])
        if snapshot.exists("verification-readiness.json"):
            readiness = json.loads(snapshot.read_text("verification-readiness.json"))
            lanes.update(attempt.get("lane") for attempt in readiness.get("attempts", []))
    checked = [index for index, event in enumerate(events) if event.get("lane_id") in lanes and event.get("stage") in {"handoff", "blocked", "fail"}]
    require(all(before < index for index in checked), "delivery_scope classification recorded after corresponding check")


def verify_author_declaration(snapshot, declaration, *, lane_id, presentation_required=True):
    require(isinstance(declaration, dict) and set(declaration) == {"lane_id", "authored_paths", "handoff", "edit_evidence", "coauthors"}, "author declaration fields invalid")
    require(declaration["lane_id"] == lane_id, "author declaration lane_id mismatch")
    paths = declaration["authored_paths"]
    require(isinstance(paths, list) and all(isinstance(p, str) and p and not Path(p).is_absolute() and all(part not in {"", ".", ".."} for part in p.split("/")) for p in paths)
            and len(paths) == len(set(paths)), "author declaration authored_paths invalid")
    handoff = declaration["handoff"]
    require(isinstance(handoff, dict) and set(handoff) == {"path", "sha256"}, "author declaration handoff invalid")
    verify_reference(snapshot.artifact_root, handoff, snapshot=snapshot)
    evidence = declaration["edit_evidence"]
    require(isinstance(evidence, list) and bool(evidence), "author declaration edit evidence missing")
    for ref in evidence:
        require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "author edit evidence reference invalid")
        verify_reference(snapshot.artifact_root, ref, snapshot=snapshot)
    coauthors = declaration["coauthors"]
    require(isinstance(coauthors, dict) and set(coauthors) <= set(paths), "author coauthors paths invalid")
    for path, peers in coauthors.items():
        require(isinstance(peers, list) and bool(peers) and all(isinstance(peer, str) and peer and peer != lane_id for peer in peers)
                and len(peers) == len(set(peers)), "author coauthors invalid")
    if presentation_required:
        from journal_io import handoff_receipt_evidence
        recorded = handoff_receipt_evidence(snapshot, handoff["path"], boundary_path="checks/lane-boundary-" + lane_id + ".json",
                                            handoff_sha256=handoff["sha256"])
        if recorded is not None:
            declared = {(ref["path"], ref["sha256"]) for ref in evidence}
            require(declared <= recorded, "author declaration edit evidence differs from the conclusion receipt")
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    known = {r.get("lane_id") for kind in ("subagents", "role_lanes") for r in summary.get(kind, [])}
    require(all(peer in known for peers in coauthors.values() for peer in peers), "author coauthorship mismatch: unknown coauthor")
    records = [r for kind in ("subagents", "role_lanes") for r in summary.get(kind, []) if r.get("lane_id") == lane_id]
    require(len(records) == 1, "author declaration assignment missing")
    record = records[0]
    require(record.get("handoff") in {None, handoff["path"]}, "author declaration own handoff mismatch")
    role_lane = record in summary.get("role_lanes", [])
    thread = summary.get("verification", {}).get("root_thread_id") if role_lane else record.get("codex_thread_id")
    require(isinstance(thread, str) and UUID.fullmatch(thread) and record.get("trace") and snapshot.exists(record["trace"]), "author declaration trace identity missing")
    events = [json.loads(line) for line in snapshot.read_text(record["trace"]).splitlines() if line.strip()]
    own = [e for e in events if e.get("lane_id") == lane_id]
    require(bool(own) and all((e.get("execution_mode") == "role-lane" if role_lane else e.get("codex_thread_id") == thread) and e.get("role") == record.get("role") for e in own), "author declaration trace identity mismatch")
    return {"codex_thread_id": thread, "role": record.get("role"), "trace": record["trace"]}


def author_seals(snapshot, workspace):
    """Retained native seals in publication order; no new author registry."""
    seals = []
    for name in snapshot.receipts:
        if name.startswith("workspace-seal-"):
            receipt = snapshot.operation_receipt(name)
            sealed = checked_document(snapshot, receipt["result"])
            if (sealed.get("workspace_id") == workspace["workspace_id"] and
                    sealed.get("workspace_revision", 0) == workspace.get("workspace_revision", 0)):
                seals.append((receipt["revision"], sealed))
    return sorted(seals, key=lambda item: item[0])


def author_boundary_seal(snapshot, workspace, data):
    seals = author_seals(snapshot, workspace)
    matches = [(index, revision, sealed) for index, (revision, sealed) in enumerate(seals)
               if sealed["candidate_id"] == data.get("candidate_id")]
    require(len(matches) == 1, "author boundary retained seal missing")
    index, revision, sealed = matches[0]
    verify_candidate({**workspace, "seal": sealed}, inputs=snapshot.external_inputs)
    expected = {"workspace_id": workspace["workspace_id"], "baseline_digest": workspace["baseline_digest"],
                "candidate_digest": sealed["candidate_digest"], "changed_paths": sealed["changed_paths"],
                "tracked_changed_paths": [], "untracked_paths": []}
    require(all(data.get(key) == value for key, value in expected.items()), "author boundary must match full registered workspace delta")
    permitted = set(sealed["changed_paths"])
    for _, previous in seals[:index]:
        permitted.update(manifest_delta(previous["candidate_manifest"], sealed["candidate_manifest"]))
    require(set(data.get("authored_paths", [])) <= permitted, "authored_paths outside workspace delta")
    return revision, sealed


def latest_contributions(snapshot, workspace, entries):
    latest = {}
    for entry in entries:
        revision, sealed = author_boundary_seal(snapshot, workspace, entry["data"])
        for path in entry["data"]["authored_paths"]:
            if path not in latest or revision >= latest[path][0]:
                latest[path] = (revision, sealed["candidate_manifest"]["entries"].get(path), sealed, entry.get("boundary"))
    return latest


def require_current_contributions(snapshot, workspace, entries):
    """The latest recorded contribution must confirm the current state of its path."""
    latest = latest_contributions(snapshot, workspace, entries)
    final = workspace["seal"]
    covered = {path for path, (_, state, _, _) in latest.items()
               if state == final["candidate_manifest"]["entries"].get(path)}
    require(set(latest) <= covered, "author declarations must match full registered workspace delta; current state differs from latest contribution")
    return latest, covered


def author_coverage(snapshot, workspace, entries):
    """Each final state needs the latest recorded contribution for that path."""
    latest, covered = require_current_contributions(snapshot, workspace, entries)
    final = workspace["seal"]
    for path, change in final["delta"].items():
        before, after = change["before"], change["after"]
        directory = after if before is None else before if after is None else None
        if directory is not None and directory.get("type") == "directory":
            # Only ordinary creation/removal induced by owned descendants. An
            # empty directory, chmod or type replacement remains explicit work.
            descendants = [name for name, item in final["delta"].items()
                           if name.startswith(path + "/") and name in covered and name in latest and
                           latest[name][2]["candidate_manifest"]["entries"].get(path) == after and
                           (item["before"] is None if before is None else item["after"] is None)]
            for name in descendants:
                boundary = latest[name][3] or {}
                require(not any(fnmatch.fnmatchcase(path, pattern) for pattern in boundary.get("forbidden_paths", [])),
                        "author parent directory matches forbidden_paths: " + path)
            if descendants:
                covered.add(path)
    require(set(final["changed_paths"]) <= covered, "author declarations union must equal full registered workspace delta; current state lacks author proof")


def resolve_author_boundaries(snapshot, workspace, *, presentation_required=False, require_complete=True):
    """Resolve existing native author proofs from assignments, with an optional map."""
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    lane_map = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
    require(isinstance(summary, dict), "author inventory requires assignment summary object")
    require(isinstance(lane_map, dict) and isinstance(lane_map.get("lanes", []), list), "author inventory lanes must be an array")
    lanes = {}
    for lane in lane_map.get("lanes", []):
        require(isinstance(lane, dict) and isinstance(lane.get("id"), str) and lane["id"] and lane["id"] not in lanes,
                "author inventory lane missing or duplicate")
        lanes[lane["id"]] = lane
    assignments = {}
    for kind in ("subagents", "role_lanes"):
        require(isinstance(summary.get(kind, []), list), "author inventory assignments must be arrays")
        for record in summary.get(kind, []):
            require(isinstance(record, dict), "author inventory assignment must be an object")
            lane_id = record.get("lane_id")
            require(isinstance(lane_id, str) and lane_id and lane_id not in assignments,
                    "author inventory assignment missing or duplicate")
            assignments[lane_id] = (record, kind == "role_lanes")
    entries = []
    incomplete = False
    sealed = workspace["seal"]
    for lane_id in sorted(set(assignments) | set(lanes)):
        lane = lanes.get(lane_id)
        boundary = lane.get("boundary") if lane is not None else None
        require(boundary is None or isinstance(boundary, dict), "author boundary must be an object")
        explicit_declared = isinstance(boundary, dict) and "changed_paths_artifact" in boundary
        explicit = boundary.get("changed_paths_artifact") if isinstance(boundary, dict) else None
        require(not explicit_declared or isinstance(explicit, str) and explicit, "author boundary artifact path invalid")
        required = boundary is not None or lane is not None and lane.get("type") in {"implementation", "integration"}
        name = explicit if explicit is not None else f"checks/lane-boundary-{lane_id}.json"
        if not snapshot.exists(name):
            require(not required or not require_complete, "author boundary artifact missing: " + str(name))
            incomplete = incomplete or required
            continue
        require(lane_id in assignments, "author declaration assignment missing: " + lane_id)
        data = json.loads(snapshot.read_bytes(name))
        require(isinstance(data, dict) and data.get("version") == 2 and data.get("status") == "captured",
                "native author boundary requires captured version 2: " + name)
        author_boundary_seal(snapshot, workspace, data)
        declaration = {key: data.get(key) for key in ("lane_id", "authored_paths", "handoff", "edit_evidence", "coauthors")}
        assignment = verify_author_declaration(snapshot, declaration, lane_id=lane_id,
                                               presentation_required=presentation_required)
        require(data.get("assignment") == assignment, "author assignment mismatch: " + lane_id)
        record, role_lane = assignments[lane_id]
        entries.append({"lane_id": lane_id, "artifact": name, "boundary": boundary, "lane": lane,
                        "record": record, "role_lane": role_lane, "data": data, "assignment": assignment})
    return None if incomplete else entries


def evaluate_obligations(snapshot, summary, *, require_current=True, selected_lane_ids=None):
    """Resolve explicit replacements without changing any original outcome."""
    records = {r.get("lane_id"): r for kind in ("subagents", "role_lanes")
               for r in summary.get(kind, []) if isinstance(r, dict)}
    resolved, errors = set(), []
    visiting = set()
    external = set(external_followup(snapshot, "lane_ids", summary=summary)) if require_current else set()
    def check(lane_id, *, replacement=False):
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
            if (not require_current or lane_id in external) and not replacement:
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
        require(terminal.get("status") == record.get("status") and terminal.get("stage") in {"handoff", "blocked", "fail"},
                "historical outcome missing or active: " + str(lane_id))
        refs = resolution.get("evidence")
        require(isinstance(refs, list) and bool(refs), "resolution evidence missing")
        for ref in refs:
            verify_reference(snapshot.artifact_root, ref, snapshot=snapshot)
        if state == "exempt":
            require(obligation["required"] is False and record.get("purpose") == "consultation",
                    "only an explicitly optional consultation may be exempt")
            require(record.get("status") in {"pass", "pass-with-risks"},
                    "negative consultation must resolve its findings")
        else:
            visiting.add(lane_id)
            replacement = resolution.get("replacement")
            require(check(replacement, replacement=True) == obligation["id"], "replacement covers a different obligation")
            require(any(ref.get("path") == records[replacement].get("handoff") for ref in refs),
                    "resolution must reference replacement handoff evidence")
            visiting.remove(lane_id)
        resolved.add(lane_id)
        return obligation["id"]
    for lane_id in records:
        if selected_lane_ids is not None and lane_id not in selected_lane_ids:
            continue
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
                file_id = path.stem[-36:]
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        event = json.loads(line)
                        require(isinstance(event, dict), "source metadata event must be an object")
                        if event.get("type") != "session_meta":
                            continue
                        meta = event.get("payload", {})
                        require(isinstance(meta, dict), "source metadata payload must be an object")
                        if not UUID.fullmatch(file_id) or meta.get("id") != file_id:
                            continue
                        records = (meta, child_spawn(meta))
                        if (any(r.get("agent_path") == agent_path for r in records)
                                and any(r.get("parent_thread_id") == root_id for r in records)):
                            require(all(r.get("agent_path") == agent_path for r in records),
                                    "conflicting source agent_path metadata")
                            require(all(r.get("parent_thread_id") == root_id for r in records),
                                    "conflicting source parent_thread_id metadata")
                            require(all(r.get("agent_role") == role for r in records),
                                    "conflicting source agent_role metadata")
                            matches.append(file_id)
                        break
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("execution unconfirmed: unreadable session metadata during resolution") from exc
        require(bool(matches), "execution unconfirmed: no matching source session; check exact path, root UUID and role")
        require(len(matches) == 1, "execution unconfirmed: multiple source sessions; use a distinct explicit UUID")
        resolved = matches[0]
        require(thread_id is None or thread_id == resolved, "explicit Codex UUID conflicts with resolved source session")
        events = self.read(resolved)
        index, _ = session_metadata(events, resolved, root_id, role, source=self)
        started = next(((i, e) for i, e in _own_session_events(self, events, resolved)
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
    """Keep original positions while excluding turns proven to belong to ancestors."""
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


def completed_turn(source, thread_id: str, turn_id: str, root_id: str, role: str, *, current=True) -> dict:
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
    for event_index, event in _own_session_events(source, events, thread_id):
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
                completions.append({"answer": answer, "completed_at": completed_at,
                                    "published_at": observed.observed_at, "time_origin": observed.event_origin,
                                    "completed_before": completed_before, "task_started_event": started_event,
                                    "task_complete_event": event_index, "session_meta_event": meta_index + 1})
            active = None
    require(len(completions) == 1, "execution unconfirmed: own completed turn missing or ambiguous")
    require(not current or last_started == turn_id and active is None, "selected completion is stale or continuation is unfinished")
    return completions[0]


def resolve_completion(source, thread_id: str, root_id: str, role: str, digest: str, handoffs: list[str], *, review_subject=None, review_input_operation_id=None) -> str:
    events = source.read(thread_id)
    meta_index, _ = session_metadata(events, thread_id, root_id, role, source=source)
    candidates = []
    if role not in {"qa-verifier", "reviewer"}:
        candidates = [event["payload"].get("turn_id") for _, event in _own_session_events(source, events, thread_id)
                      if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "task_complete"]
        require(bool(candidates), "execution unconfirmed: own completed turn missing")
        completed_turn(source, thread_id, candidates[-1], root_id, role)
        return candidates[-1]
    for _, event in _own_session_events(source, events, thread_id):
        payload = event.get("payload", {})
        if event.get("type") != "event_msg" or payload.get("type") != "task_complete":
            continue
        try:
            answer = final_json(payload.get("last_agent_message"))
        except EvidenceError:
            continue
        matches_subject = (review_subject_kind(answer) == "product-result" if review_subject is None else
                           answer.get("review_subject") == review_subject and answer.get("review_input_operation_id") == review_input_operation_id)
        if matches_subject and answer.get("reviewed_result_hash") == digest and answer.get("handoff") in handoffs:
            candidates.append(payload.get("turn_id"))
    require(len(candidates) == 1, "completion resolution missing or ambiguous; supply --completion-turn-id for the current handoff/result hash")
    completed_turn(source, thread_id, candidates[0], root_id, role)
    return candidates[0]


def changed_paths(run_dir: Path, lane_map: dict, verification=None, *, snapshot=None) -> set[str]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    workspace = registered_workspace(snapshot)
    if workspace is not None:
        if "seal" in workspace:
            return set(workspace["seal"]["changed_paths"])
        from task_workspace import observed_tree, manifest_delta
        working = Path(workspace["working_root"])
        manifest = observed_tree(working, workspace["baseline_manifest"], workspace["working_identity"],
                                 inputs=snapshot.external_inputs, workspace=workspace)
        return set(manifest_delta(workspace["baseline_manifest"], manifest))
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
        _, metadata = own_metadata(events, check.get("session_thread_id"))
        own_events = set()
        active = False
        for index, event in _own_session_events(source, events, check.get("session_thread_id")):
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
                    meta = metadata
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


def review_subject_kind(record):
    """Closed domain classifier shared by product and preliminary consumers."""
    if "review_subject" not in record:
        return "product-result"
    subject = record["review_subject"]
    require(type(subject) is dict and subject.get("kind") in {"requirements-plan", "product-result"}, "unknown or malformed review subject")
    kind = subject["kind"]
    fields = {"kind", "digest", "sources"} if kind == "requirements-plan" else {"kind"}
    require(set(subject) == fields, "malformed review subject fields")
    if kind == "requirements-plan":
        require(type(subject.get("digest")) is str and SHA256.fullmatch(subject["digest"]), "malformed requirements digest")
        rows = subject["sources"]
        require(type(rows) is list and bool(rows) and all(type(row) is dict and set(row) == {"key", "sha256"}
            and type(row["key"]) is str and row["key"].startswith(("journal:", "source:", "context:"))
            and type(row["sha256"]) is str and SHA256.fullmatch(row["sha256"]) for row in rows), "malformed requirements sources")
        require(rows == sorted(rows, key=lambda row: row["key"]) and len({row["key"] for row in rows}) == len(rows), "review sources must be canonical and unique")
        require(subject["digest"] == requirements_digest(rows), "review subject digest mismatch")
        from journal_io import logical_path
        for row in rows:
            logical_path(row["key"].split(":", 1)[1])
            if row["key"].startswith("context:"):
                context_source_name(row["key"].split(":", 1)[1])
    return kind


def requirements_digest(rows):
    return sha256(("agentflow:requirements-plan:v1\n" + json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode())


def context_source_name(name):
    from journal_io import logical_path
    name = logical_path(name)
    parts = name.split("/")
    require(len(parts) >= 2 and re.fullmatch(r"0|[1-9][0-9]*", parts[0]), "malformed context source slot/name")
    return name


def review_context_sources(snapshot):
    """Derive keys from the existing declared manifests; no external scan or registry."""
    from journal_io import logical_path
    from workspace_scope import previous_roots
    workspace = registered_workspace(snapshot)
    if workspace is None:
        return {}
    products = previous_roots(workspace)
    rows = {}
    for slot, root in enumerate(workspace.get("task_scope", {}).get("context_roots", [])):
        path = Path(root["canonical_path"])
        require(path.is_absolute() and path.name not in {"", ".", ".."}, "invalid declared context root")
        manifest = root.get("manifest")
        require(type(manifest) is dict, "declared context manifest missing")
        if root.get("kind") == "file":
            require(manifest.get("type") == "file", "declared context root kind mismatch")
            members = {"": manifest}
        else:
            require(root.get("kind") == "directory" and type(manifest.get("entries")) is dict, "declared context root kind mismatch")
            require(all(type(entry) is dict for entry in manifest["entries"].values()), "malformed declared context member")
            members = {name: entry for name, entry in manifest["entries"].items() if entry.get("type") == "file"}
        for member, entry in members.items():
            if member:
                logical_path(member)
            require(type(entry.get("sha256")) is str and SHA256.fullmatch(entry["sha256"]), "declared context file hash missing")
            target = path / member if member else path
            key = None
            for journal in (snapshot.artifact_root, snapshot.logical_root):
                if target.is_relative_to(journal):
                    key = "journal:" + snapshot.key(target)
                    break
            if key is None:
                selected = [row for row in products if target.is_relative_to(Path(row["source"]))]
                require(len(selected) <= 1, "ambiguous review source scope")
                if selected:
                    key = "source:" + logical_path((Path(selected[0]["relative_path"]) / target.relative_to(selected[0]["source"])).as_posix())
                elif target.is_relative_to(snapshot.source_root):
                    key = "source:" + logical_path(target.relative_to(snapshot.source_root).as_posix())
                else:
                    key = "context:" + context_source_name(str(slot) + "/" + path.name + ("/" + member if member else ""))
            value = {"path": target, "sha256": entry["sha256"]}
            require(key not in rows or rows[key] == value, "ambiguous logical context source")
            rows[key] = value
    return rows


def review_source_bytes(snapshot, name, *, capture=True, context_sources=None):
    """Resolve declared scope; capture location never participates in identity."""
    from journal_io import capture_file, logical_path, validate_directory
    contexts = review_context_sources(snapshot) if context_sources is None else context_sources
    if name.startswith("context:"):
        key = "context:" + context_source_name(name.split(":", 1)[1])
        raw = current_review_source(snapshot, key, capture=capture, context_sources=contexts)
        return key, raw if capture else None
    path = Path(name).expanduser()
    if not path.is_absolute():
        logical_path(name)
        index = json.loads(snapshot.documents.get("artifacts/source-references.json", b"{}"))
        originals = [original for original, locator in index.items() if locator == name]
        require(len(originals) <= 1, "ambiguous captured review source")
        if originals:
            path = Path(originals[0])
        elif name in snapshot.documents:
            return "journal:" + snapshot.key(name), snapshot.read_bytes(name) if capture else None
        else:
            path = snapshot.source_root / name
    require(not path.is_symlink(), "review source must not be a symlink")
    validate_directory(path.parent)
    path = path.resolve()
    for root in (snapshot.artifact_root, snapshot.logical_root):
        if path.is_relative_to(root):
            key = snapshot.key(path)
            return "journal:" + key, snapshot.read_bytes(key) if capture else None
    workspace = registered_workspace(snapshot)
    if workspace:
        from workspace_scope import previous_roots
        for field in ("working_root", "baseline_root", "candidate_root"):
            if workspace.get(field) and path.is_relative_to(Path(workspace[field])):
                relative = path.relative_to(Path(workspace[field])).as_posix()
                key = "source:" + logical_path(relative)
                current_review_source(snapshot, key, capture=False, context_sources=contexts)
                return key, capture_file(path) if capture else None
        roots = previous_roots(workspace)
        matches = [row for row in roots if path.is_relative_to(Path(row["source"]))]
        require(len(matches) <= 1, "ambiguous review source scope")
        if matches:
            row = matches[0]
            relative = (Path(row["relative_path"]) / path.relative_to(Path(row["source"]))).as_posix()
            return "source:" + logical_path(relative), capture_file(path) if capture else None
    if not path.is_relative_to(snapshot.source_root):
        matches = [key for key, row in contexts.items() if row["path"] == path]
        require(len(matches) <= 1, "ambiguous declared context source; use its explicit context key")
        require(bool(matches), "review source outside approved source context")
        key = matches[0]
        raw = current_review_source(snapshot, key, capture=capture, context_sources=contexts)
        return key, raw if capture else None
    key = "source:" + logical_path(path.relative_to(snapshot.source_root).as_posix())
    current_review_source(snapshot, key, capture=False, context_sources=contexts)
    if key in contexts:
        return key, current_review_source(snapshot, key, capture=capture, context_sources=contexts) if capture else None
    return key, capture_file(path) if capture else None


def current_review_source(snapshot, key, *, capture=True, context_sources=None):
    """Relocated current scope resolves identity; old capture locators are inert."""
    require(type(key) is str and ":" in key, "malformed review source key")
    kind, name = key.split(":", 1)
    require(kind in {"journal", "source", "context"}, "unknown review source key")
    from journal_io import capture_file, logical_path, validate_directory
    name = logical_path(name)
    if kind == "journal":
        return snapshot.read_bytes(name) if capture else snapshot.logical_root / name
    contexts = review_context_sources(snapshot) if context_sources is None else context_sources
    if kind == "context":
        context_source_name(name)
        require(key in contexts, "review source outside declared product/context scope: " + key)
        path = contexts[key]["path"]
    else:
        workspace = registered_workspace(snapshot)
        path = snapshot.source_root / name
        if workspace:
            from workspace_scope import previous_roots
            selected = any(row["relative_path"] == "." or name == row["relative_path"] or
                           Path(name).is_relative_to(row["relative_path"]) for row in previous_roots(workspace))
            if selected:
                path = Path(workspace["working_root"]) / name
            else:
                require(key in contexts and contexts[key]["path"] == path, "review source outside declared product/context scope")
    if key in contexts and contexts[key]["path"] == path:
        require(not path.is_symlink(), "review source must not be a symlink")
        validate_directory(path.parent)
        if not capture:
            return path
        try:
            raw = snapshot.external_inputs.file(path) if snapshot.external_inputs is not None else capture_file(path)
        except OSError as exc:
            raise EvidenceError("declared context source unavailable: " + key) from exc
        require(sha256(raw) == contexts[key]["sha256"], "declared context bytes changed: " + key)
        return raw
    return capture_file(path) if capture else path


def declared_review_sources(snapshot, *, context_sources=None):
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    verification = summary.get("verification", {})
    sources = [ref["path"] for field in ("initial_snapshot", "task_scope")
               if isinstance(ref := verification.get(field), dict) and isinstance(ref.get("path"), str)]
    if snapshot.exists("run.md"):
        sources.append("run.md")
    if snapshot.exists("plan.md"):
        sources.append("plan.md")
    lanes = json.loads(snapshot.documents.get("lane-map.json", b"{}"))
    sources.extend(lane[field] for lane in lanes.get("lanes", []) if lane.get("type") == "architecture"
                   for field in ("handoff", "architecture_design_brief") if lane.get(field) and snapshot.exists(lane[field]))
    contexts = review_context_sources(snapshot) if context_sources is None else context_sources
    return {review_source_bytes(snapshot, name, capture=False, context_sources=contexts)[0] for name in sources} | contexts.keys()


def review_input_receipt(snapshot, record, *, current=False):
    require(review_subject_kind(record) == "requirements-plan", "requirements review subject required")
    identifier = record.get("review_input_operation_id")
    require(type(identifier) is str and bool(identifier), "review input operation required")
    saved = snapshot.operation_receipt(identifier)
    require(saved is not None, "native review input receipt missing")
    result = saved["result"]
    require(result.get("command") == "prepare-review-input" and result.get("run_uuid") == snapshot.run_uuid
        and (result.get("generation") == snapshot.generation if current else type(result.get("generation")) is int and result["generation"] <= snapshot.generation) and result.get("lane_id") == record.get("lane_id")
        and result.get("review_subject") == record["review_subject"], "review input receipt binding mismatch")
    captures = result.get("captures")
    require(type(captures) is list and len(captures) == len(record["review_subject"]["sources"]), "review captures missing")
    rows = []
    contexts = review_context_sources(snapshot) if current else None
    for capture in captures:
        raw = snapshot.read_bytes(capture["path"])
        require(capture["path"] == "artifacts/source-captures/" + sha256(raw) + ".bin", "review capture content address mismatch")
        rows.append({"key": capture["key"], "sha256": sha256(raw)})
        if current:
            require(sha256(current_review_source(snapshot, capture["key"], context_sources=contexts)) == sha256(raw), "current review input drift")
    require(rows == record["review_subject"]["sources"], "review pinned bytes mismatch")
    if current:
        from task_workspace import current_scope_digest
        workspace = registered_workspace(snapshot)
        if workspace is not None:
            current_scope_digest(workspace, inputs=snapshot.external_inputs)
        missing = declared_review_sources(snapshot, context_sources=contexts) - {row["key"] for row in rows}
        require(not missing, "review omits declared inputs: " + ", ".join(sorted(missing)))
    from journal_io import timestamp
    timestamp(result.get("frozen_at"))
    return result


def readiness_obligation_endpoints(snapshot, summary, lane_ids, *, require_current=True):
    """Configured duties select only endpoints of already validated resolutions."""
    validate_summary_shape(summary)
    records = {row["lane_id"]: row for row in summary["subagents"] + summary["role_lanes"]}
    require(all(lane_id in records for lane_id in lane_ids), "readiness configured assignment missing")
    _, errors = evaluate_obligations(snapshot, summary, require_current=require_current, selected_lane_ids=lane_ids)
    require(not errors, "; ".join(errors))
    endpoints = set()
    for lane_id in lane_ids:
        while records[lane_id].get("obligation", {}).get("state") == "resolved":
            lane_id = records[lane_id]["obligation"]["resolution"]["replacement"]
        obligation = records[lane_id].get("obligation", {})
        require(obligation.get("state") == "current" and obligation.get("required") is True,
                "readiness requires current mandatory obligation endpoint")
        endpoints.add(lane_id)
    return endpoints


def readiness_tool_evidence(snapshot, record, ref, source, *, completion=None, capture_required=True, current=True):
    """Exact observable own tool text, with a finite MCP result shape."""
    fields = {"path", "sha256", "source_event", "source_call_id", "source_part"}
    require(type(ref) is dict and set(ref) <= fields and {"path", "source_event"} <= set(ref), "unknown readiness tool evidence fields")
    from journal_io import logical_path, timestamp, source_time
    path = logical_path(ref["path"])
    require(path.startswith("artifacts/"), "readiness tool output requires attachment path")
    index = ref["source_event"]
    require(type(index) is int and index > 0, "readiness source_event must be a positive integer")
    thread = record.get("codex_thread_id")
    events = source.read(thread, event_indices=(index,))
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    session_metadata(events, thread, summary["verification"].get("root_thread_id"), record["role"], source=source)
    own = dict(_own_session_events(source, events, thread))
    require(index in own, "readiness tool output must belong to own session")
    starts = [i for i, event in own.items() if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "task_started"]
    start = completion["task_started_event"] if completion else max(starts, default=0)
    end = completion["task_complete_event"] if completion else min((i for i, event in own.items() if i > start and event.get("type") == "event_msg" and event.get("payload", {}).get("type") in {"task_complete", "task_started"}), default=len(events) + 1)
    require(start in own and start < index < end, "readiness tool output must belong to reviewing turn")
    pin = review_input_receipt(snapshot, record, current=current)
    require(timestamp(pin["frozen_at"]) < source_time(own[start], "started_at").event_at, "reviewing turn must start strictly after input freeze")
    payload = own[index].get("payload", {})
    require(own[index].get("type") == "response_item" and payload.get("type") in {"function_call_output", "custom_tool_call_output"}, "readiness proof requires observed tool output")
    call_id = payload.get("call_id")
    require(type(call_id) is str and bool(call_id) and ("source_call_id" not in ref or ref["source_call_id"] == call_id), "readiness source_call_id mismatch")
    if payload["type"] == "custom_tool_call_output":
        parts, unavailable = public_content(payload.get("output"))
        require(bool(parts) and not unavailable and not payload.get("output_unavailable", False), "readiness tool output unavailable")
        if "source_part" in ref:
            part = ref["source_part"]
            require(type(part) is int and 0 <= part < len(parts), "readiness source_part unavailable")
            text = parts[part]["text"]
        else:
            text = "".join(part["text"] for part in parts)
    else:
        require("source_part" not in ref, "readiness source_part requires public text blocks")
        text = payload.get("output")
        require(type(text) is str and not payload.get("output_unavailable", False), "readiness tool output unavailable")
    try:
        result = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("readiness tool output must be one MCP CallToolResult JSON object") from exc
    require(type(result) is dict and {"content"} <= set(result) <= {"content", "isError", "structuredContent", "_meta"}, "unsupported readiness tool result")
    require("isError" not in result or type(result["isError"]) is bool and not result["isError"], "readiness tool result reports error")
    require(all(type(result[key]) is dict for key in ("structuredContent", "_meta") if key in result), "malformed readiness tool metadata")
    content = result["content"]
    require(type(content) is list and bool(content) and all(type(part) is dict and set(part) == {"type", "text"} and part["type"] == "text" and type(part["text"]) is str and bool(part["text"].encode()) for part in content), "readiness tool result requires nonempty text content")
    raw = text.encode()
    require("sha256" not in ref or ref["sha256"] == sha256(raw), "readiness tool hash assertion mismatch")
    if capture_required:
        require(snapshot.read_bytes(path) == raw, "readiness tool source bytes mismatch")
    derived = {**ref, "path": path, "sha256": sha256(raw), "source_call_id": call_id}
    return raw, derived


def require_review_completion(snapshot, record, completion, source, *, current):
    """Native pin plus the exact own reviewing turn, without prompt parsing."""
    pin = review_input_receipt(snapshot, record, current=current)
    answer = completion["answer"]
    require(answer.get("review_subject") == record["review_subject"] and
            answer.get("review_input_operation_id") == record["review_input_operation_id"], "source review subject or operation mismatch")
    require(answer.get("reviewed_result_hash") is None and record.get("reviewed_result_hash") is None, "preliminary review cannot accept product hash")
    from journal_io import timestamp, source_time
    started = source.read(record["codex_thread_id"])[completion["task_started_event"] - 1]
    require(timestamp(pin["frozen_at"]) < source_time(started, "started_at").event_at, "reviewing turn must start strictly after input freeze")
    return pin


def source_bound_record(run_dir, record, verification, role, source, *, qa_handoff=None, snapshot=None, negative=False,
                        current=True, presentation_required=True):
    """Revalidate one source-bound acceptance, including current evidence bytes."""
    preliminary = review_subject_kind(record) == "requirements-plan"
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    thread = record.get("codex_thread_id")
    require(thread not in [verification.get("root_thread_id"), *verification.get("author_thread_ids", [])],
            "QA, reviewer, root and authors must be distinct")
    require(record.get("role") in ({"reviewer", "reviewer.qa"} if role == "reviewer" else {role}), "incorrect canonical role")
    if role == "reviewer" and not negative and current and not preliminary:
        require_own_reviewer_handoff(run_dir, record.get("handoff"), qa_handoff, snapshot=snapshot)
    handoff_hash = verify_reference(run_dir, {"path": record.get("handoff"), "sha256": record.get("handoff_sha256")}, snapshot=snapshot)
    text = reference_bytes(run_dir, {"path": record["handoff"]}, snapshot=snapshot).decode()
    refs = record.get("evidence")
    require(isinstance(refs, list) and bool(refs), "evidence references required")
    for ref in refs:
        if current:
            verify_reference(run_dir, ref, snapshot=snapshot)
        else:
            historical_evidence_bytes(snapshot, record, ref)
    if presentation_required:
        from journal_io import handoff_receipt_evidence
        recorded = handoff_receipt_evidence(snapshot, record["handoff"], handoff_sha256=handoff_hash)
        if recorded is not None:
            require({(ref["path"], ref["sha256"]) for ref in refs} <= recorded,
                    "evidence reference set differs from the conclusion receipt")
        else:
            # Legacy handoffs without a conclusion receipt bind evidence markers inline.
            require(all(ref["path"] in text and ref["sha256"] in text for ref in refs),
                    "evidence reference and sha256 must appear in source-bound handoff")
    digest = None if preliminary else record.get("reviewed_result_hash") if negative or not current else verification.get("result_hash") or result_hash(run_dir, verification, snapshot=snapshot)
    require(record.get("reviewed_result_hash") == digest, "reviewed_result_hash is stale")
    completion = completed_turn(source, thread, record.get("completion_turn_id"), verification.get("root_thread_id"), role, current=current)
    answer = completion["answer"]
    require(review_subject_kind(answer) == review_subject_kind(record) and
            answer.get("review_subject") == record.get("review_subject"), "source review subject differs from assignment")
    if preliminary:
        pin = require_review_completion(snapshot, record, completion, source, current=current)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        reviewed_keys = {row["key"] for row in pin["review_subject"]["sources"]}
        known_authors = {verification.get("root_thread_id"), *verification.get("author_thread_ids", [])}
        for assignment in summary.get("subagents", []) + summary.get("role_lanes", []):
            if "journal:" + str(assignment.get("handoff")) in reviewed_keys:
                known_authors.add(assignment.get("codex_thread_id") or verification.get("root_thread_id"))
        require(thread not in known_authors, "preliminary QA must be independent from known reviewed source authors")
        require(record.get("lane_id") not in {verification.get("qa"), verification.get("reviewer")}, "preliminary review cannot be selected as product acceptance")
        require(record.get("readiness_gates", []) == answer.get("readiness_gates", []), "source readiness gates mismatch")
        from journal_io import validate_preliminary_gates
        validate_preliminary_gates(snapshot, answer.get("readiness_gates", []), record=record, source=source, completion=completion, execution_required=current, current=current)
        require(all(any(item.get("path") == ref.get("path") and item.get("sha256") == ref.get("sha256") for item in refs) for gate in answer.get("readiness_gates", []) for ref in gate["evidence"]), "readiness evidence must be accepted source evidence")
    if negative:
        require(answer.get("verdict") in {"blocked", "fail"} and record.get("status") == answer["verdict"],
                "negative source verdict/status mismatch")
        require(isinstance(answer.get("reason"), str) and answer["reason"].strip(), "negative source reason required")
        require(isinstance(answer.get("unverified"), list) and bool(answer["unverified"]) and
                all(isinstance(item, str) and item.strip() for item in answer["unverified"]),
                "negative source unverified must contain unperformed checks")
        for field in ("reason", "unverified"):
            require(record.get(field) == answer[field], f"negative source {field} mismatch")
        require(record.get("lane_id") not in {verification.get("qa"), verification.get("reviewer")},
                "negative completion cannot be selected as accepted QA/reviewer")
    else:
        require(preliminary or isinstance(digest, str) and SHA256.fullmatch(digest), "accepted source result hash required")
        require(isinstance(answer.get("verdict"), str) and answer["verdict"] in ACCEPTED, "source verdict does not accept result")
    for field, value in (("reviewed_result_hash", digest), ("handoff", record.get("handoff")), ("handoff_sha256", handoff_hash)):
        require(answer.get(field) == value, f"source {field} mismatch")
    for field in ("session_meta_event", "task_started_event", "task_complete_event"):
        if field in record:
            require(record[field] == completion[field], f"source {field} mismatch")
    return {**completion, "handoff_sha256": handoff_hash}


def accepted_record(run_dir, record, verification, role, source, *, qa_handoff=None, snapshot=None, presentation_required=True):
    require(review_subject_kind(record) == "product-result", "preliminary review cannot be product acceptance")
    return source_bound_record(run_dir, record, verification, role, source,
                               qa_handoff=qa_handoff, snapshot=snapshot, presentation_required=presentation_required)


def archived_acceptance(snapshot, record):
    """A previously finalized acceptance remains bound to its immutable record."""
    if review_subject_kind(record) != "product-result":
        return False
    for identifier in snapshot.archives:
        archived = snapshot.archive(identifier)
        if not archived.closed:
            continue
        summary = json.loads(archived.documents.get("delegation-summary.json", b"{}"))
        verification = summary.get("verification", {})
        if record.get("lane_id") not in {verification.get("qa"), verification.get("reviewer")}:
            continue
        if not re.search(r"^Verdict:\s*(ship|pass-with-risks)\s*$", archived.documents.get("final.md", b"").decode(), re.MULTILINE):
            continue
        original = next((row for row in summary.get("subagents", []) if row.get("lane_id") == record.get("lane_id")), None)
        if original and {key: value for key, value in original.items() if key != "obligation"} == {
                key: value for key, value in record.items() if key != "obligation"}:
            return True
    return False


def validate_context_classification(run_dir, workspace, records, completions, source, *, snapshot, presentation_required=True):
    scope = workspace.get("task_scope", {})
    exclusions = scope.get("context_exclusions", [])
    if workspace.get("version", 1) < 2 or not exclusions:
        return
    expected = {item["relative_path"] for item in exclusions}
    digest = workspace["seal"]["task_scope_digest"]
    for key in ("qa", "reviewer"):
        classification = completions[key]["answer"].get("context_classification")
        require(isinstance(classification, dict), f"{key}: context_classification required")
        require(classification.get("task_scope_digest") == digest, f"{key}: context task_scope_digest stale")
        covered = classification.get("context_exclusions")
        require(isinstance(covered, list) and all(isinstance(item, str) for item in covered) and
                len(covered) == len(set(covered)) and set(covered) == expected,
                f"{key}: context_classification must cover exactly current context_exclusions")
    qa = records["qa"]
    completion = completions["qa"]
    refs = completion["answer"]["context_classification"].get("workflow_evidence")
    require(isinstance(refs, list) and bool(refs), "qa: context workflow_evidence required")
    require(all(isinstance(ref, dict) for ref in refs), "qa: workflow_evidence must contain references")
    require(all(type(ref.get("source_event")) is int and isinstance(ref.get("source_call_id"), str) and
                bool(ref["source_call_id"]) for ref in refs), "qa: context workflow source_event/call_id required")
    events = source.read(qa["codex_thread_id"], event_indices=[ref.get("source_event") for ref in refs])
    own = {index for index, _ in _own_session_events(source, events, qa["codex_thread_id"])}
    for ref in refs:
        verify_reference(run_dir, ref, snapshot=snapshot)
        require(any(item.get("path") == ref.get("path") and item.get("sha256") == ref.get("sha256")
                    for item in qa["evidence"]), "qa: context workflow output must be accepted QA evidence")
        if presentation_required:
            from journal_io import digest, handoff_receipt_evidence
            reviewer_handoff = records["reviewer"]["handoff"]
            recorded = handoff_receipt_evidence(snapshot, reviewer_handoff,
                                                handoff_sha256=digest(snapshot.read_bytes(reviewer_handoff)))
            if recorded is not None:
                # The receipt of the current handoff bytes is decisive; text is not consulted.
                require((ref.get("path"), ref.get("sha256")) in recorded,
                        "reviewer: context workflow evidence must be in the completeness assessment receipt")
            elif snapshot.durable:
                # No receipt for the current bytes: the literal current handoff text must carry the reference.
                text = snapshot.read_text(reviewer_handoff)
                require(ref["path"] in text and ref["sha256"] in text,
                        "reviewer: no receipt or current handoff bytes declare this evidence")
        index = ref.get("source_event")
        require(type(index) is int and index in own and
                completion["task_started_event"] < index < completion["task_complete_event"],
                "qa: context workflow output must belong to accepted QA turn")
        payload = events[index - 1].get("payload", {})
        require(events[index - 1].get("type") == "response_item" and
                payload.get("type") in {"function_call_output", "custom_tool_call_output"},
                "qa: context workflow proof requires observed tool output")
        require(isinstance(ref.get("source_call_id"), str) and ref["source_call_id"] == payload.get("call_id"),
                "qa: context workflow call_id mismatch")
        if payload["type"] == "custom_tool_call_output":
            parts, unavailable = public_content(payload.get("output"))
            require(not unavailable and not payload.get("output_unavailable", False), "qa: context output unavailable")
            observed = "".join(part["text"] for part in parts)
        else:
            observed = payload.get("output")
            require(not payload.get("output_unavailable", False), "qa: context output unavailable")
        require(isinstance(observed, str) and sha256(observed.encode()) == ref["sha256"],
                "qa: context workflow source bytes mismatch")


def validate_verification(run_dir: Path, summary: dict, lane_map: dict, verdict: str | None, source=None, *, snapshot=None,
                          selected_lane_ids=None, presentation_required=True) -> list[str]:
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    try:
        require(isinstance(summary.get("subagents"), list) and all(isinstance(r, dict) for r in summary["subagents"]),
                "subagents must be an object array")
        verification = summary.get("verification")
        require(isinstance(verification, dict), "verification must be an object")
        delivery_scope(snapshot, verification, summary=summary)
        positive = verdict in POSITIVE
        kind = verification.get("task_kind")
        require(isinstance(kind, str) and kind in {"change", "analysis"} or kind is None and not positive, "verification.task_kind must be change or analysis")
        files = verification.get("result_files")
        require(isinstance(files, list) and all(isinstance(p, str) for p in files), "verification.result_files must be a path array")
        require(len(files) == len(set(files)), "verification.result_files contains duplicates")
        workspace = registered_workspace(snapshot)
        sealed = workspace is not None and "seal" in workspace
        if workspace is None:
            for name in files:
                confined_path(project_root(run_dir, snapshot=snapshot), name, relative=True, inputs=snapshot.external_inputs)
        elif sealed:
            require(set(files) == set(workspace.get("seal", {}).get("changed_paths", [])),
                    "result_files must equal the full workspace delta")
            require(verification.get("result_hash") == result_hash(run_dir, verification, snapshot=snapshot, require_ready=positive),
                    "verification.result_hash is stale")
        else:
            require(not files and verification.get("result_hash") is None,
                    "unsealed workspace cannot claim accepted result_files or result_hash")
        if verdict in {"blocked", "fail"} and workspace is None and result_contract(snapshot).get("result_contract_version", 1) >= 2:
            require(verification.get("result_hash") == result_hash(run_dir, verification, snapshot=snapshot, require_ready=False),
                    "verification.result_hash is stale")
        owned = set() if not positive and (workspace is not None and not sealed and kind == "change" or kind is None and not files) else changed_paths(run_dir, lane_map, verification, snapshot=snapshot)
        if positive or sealed or workspace is None and verdict not in {"blocked", "fail"}:
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
        if positive and isinstance(verification.get("qa"), str) and isinstance(verification.get("reviewer"), str) and verification["qa"] in records and verification["reviewer"] in records:
            require(records[verification["qa"]].get("codex_thread_id") !=
                    records[verification["reviewer"]].get("codex_thread_id"),
                    "QA and reviewer must be distinct")
        for role in ("qa", "reviewer"):
            lane_id = verification.get(role)
            require(lane_id is None or isinstance(lane_id, str) and lane_id in records, f"verification.{role} must reference a subagent record")
        for field in ("initial_snapshot", "task_scope"):
            ref = verification.get(field)
            if ref is not None:
                verify_reference(run_dir, ref, snapshot=snapshot)
        for record in records.values():
            if selected_lane_ids is not None and record.get("lane_id") not in selected_lane_ids:
                continue
            subject_kind = review_subject_kind(record)
            if subject_kind == "requirements-plan":
                review_input_receipt(snapshot, record, current=False)
                require(record.get("lane_id") not in {verification.get("qa"), verification.get("reviewer")}, "preliminary review cannot be product selector")
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
                if record.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}:
                    historical_evidence_bytes(snapshot, record, ref)
                else:
                    verify_reference(run_dir, ref, snapshot=snapshot)
            if turn and subject_kind == "requirements-plan":
                source_bound_record(run_dir, record, verification, "reviewer" if record.get("role") == "reviewer.qa" else record.get("role"),
                    source or CodexSessionSource(), snapshot=snapshot, negative=record.get("status") in {"blocked", "fail"}, current=False, presentation_required=presentation_required)
            elif turn and record.get("role") not in {"qa-verifier", "reviewer", "reviewer.qa"}:
                require(record.get("lane_id") not in {verification.get("qa"), verification.get("reviewer")},
                        "worker completion cannot be selected as QA/reviewer")
                require(record.get("status") in {"pass", "pass-with-risks", "blocked", "fail"},
                        "source completion requires a terminal reported status")
                completion = completed_turn(source or CodexSessionSource(), record.get("codex_thread_id"), turn,
                                            verification.get("root_thread_id"), record.get("role"), current=False)
                require(record.get("source_completion_sha256") == sha256(completion["answer"].encode()),
                        "source completion text hash mismatch")
                verify_reference(run_dir, {"path": record.get("handoff"), "sha256": record.get("handoff_sha256")},
                                 snapshot=snapshot)
                require(record.get("completion_task_started_event") == completion["task_started_event"],
                        "source completion_task_started_event mismatch")
                for field in ("session_meta_event", "task_complete_event"):
                    require(record.get(field) == completion[field], "source " + field + " mismatch")
                require(record.get("reviewed_result_hash") is None, "worker completion cannot accept a result")
            elif record.get("completion_turn_id") and record.get("status") in {"blocked", "fail"}:
                role = "reviewer" if record.get("role") == "reviewer.qa" else record.get("role")
                require(role in {"qa-verifier", "reviewer"}, "negative completion must be QA/reviewer")
                source_bound_record(run_dir, record, verification, role, source or CodexSessionSource(),
                                    snapshot=snapshot, negative=True, current=False, presentation_required=presentation_required)
            elif verdict in {"blocked", "fail"} and record.get("completion_turn_id") and record.get("role") in {"qa-verifier", "reviewer", "reviewer.qa"} and not archived_acceptance(snapshot, record):
                source_bound_record(run_dir, record, verification,
                                    "reviewer" if record["role"] == "reviewer.qa" else record["role"],
                                    source or CodexSessionSource(), snapshot=snapshot, current=False,
                                    presentation_required=presentation_required)
        if not positive:
            for check in checks:
                require(isinstance(check, dict), "behavioral check must be an object")
                verifier = check.get("verifier_thread_id")
                require(any(record.get("codex_thread_id") == verifier and
                            record.get("role") in {"reviewer", "reviewer.qa"} for record in records.values()),
                        "behavioral check requires registered reviewer")
                validate_behavioral_checks(run_dir, [check], source or CodexSessionSource(), verifier, snapshot=snapshot)
            if verdict in {"blocked", "fail"}:
                require(isinstance(verification.get("blocker"), str) and verification["blocker"].strip(), "blocked/fail verification requires blocker reason")
                unverified = verification.get("unverified")
                require(unverified is None and result_contract(snapshot).get("result_contract_version", 1) < 2 or
                        isinstance(unverified, list) and bool(unverified) and
                        all(isinstance(item, str) and item.strip() for item in unverified),
                        "blocked/fail verification requires unverified checks")
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
        if sealed and workspace["seal"].get("result_hash_version") == 5:
            actual_authors = {entry["assignment"]["codex_thread_id"]
                              for entry in resolve_author_boundaries(snapshot, workspace, presentation_required=presentation_required)
                              if entry["data"]["authored_paths"]}
            require(actual_authors == set(authors), "author_thread_ids must equal proven native author assignments")
        else:
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
                                         qa_handoff=accepted.get("qa", {}).get("answer", {}).get("handoff"), snapshot=snapshot,
                                         presentation_required=presentation_required)
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
        for record in records.values():
            if selected_lane_ids is not None and record.get("lane_id") not in selected_lane_ids:
                continue
            if record.get("lane_id") not in {verification["qa"], verification["reviewer"]} and record.get("completion_turn_id") and record.get("status") not in {"blocked", "fail"} and record.get("role") in {"qa-verifier", "reviewer", "reviewer.qa"} and not archived_acceptance(snapshot, record):
                source_bound_record(run_dir, record, verification,
                                    "reviewer" if record["role"] == "reviewer.qa" else record["role"],
                                    source, snapshot=snapshot, current=False, presentation_required=presentation_required)
        if workspace is not None:
            validate_context_classification(run_dir, workspace,
                                            {key: records[verification[key]] for key in ("qa", "reviewer")},
                                            accepted, source, snapshot=snapshot, presentation_required=presentation_required)
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
            'summary', 'verification_json', 'wave', 'captured_references',
            'batch_id', 'batch_items', 'from_lane', 'to_lane', 'task'}, 'unsupported original mode payload fields')
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
