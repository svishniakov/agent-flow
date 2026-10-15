"""Explicit recovery of the same journal and result, with immutable SQLite history."""
from dataclasses import replace
import json
from pathlib import Path
import re

from journal_io import (JournalSnapshot, JournalError, require, digest, encode_json, encode_json_line,
                        capture_archive, _transact, now_iso, validate_event, operation_id,
                        UNSUPPORTED_STORAGE_VERSION)
from journal_lifecycle import capture_validation, freeze_validation
from validation_inputs import captured
from task_workspace import registered_workspace, verify_candidate, read_owned
from verification_evidence import result_hash


def canonical_identity(snapshot):
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    verification = summary.get("verification", {})
    return {"run_uuid": snapshot.run_uuid, "logical_root": str(snapshot.logical_root),
            "source_root": str(snapshot.source_root),
            "verification": {k: verification.get(k) for k in
                             ("root_thread_id", "task_kind", "result_files", "task_scope", "initial_snapshot")},
            "behavioral_contract": [{k: v for k, v in check.items() if k not in {"verifier_thread_id", "handoff"}}
                                    for check in verification.get("behavioral_checks", [])],
            "workspace": registered_workspace(snapshot)}


def recovery_identity(snapshot):
    identity = canonical_identity(snapshot)
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    verification = summary.get("verification", {})
    identity["result_hash"] = result_hash(snapshot.artifact_root, verification, snapshot=snapshot) if verification.get("task_kind") == "change" else None
    return identity


def assignments(snapshot):
    """Inventory every lane-bearing source, including absent summary entries."""
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    records = {r["lane_id"]: {"summary": r, "events": [], "lane": None}
               for kind in ("subagents", "role_lanes") for r in summary.get(kind, [])}
    lane_map = json.loads(snapshot.documents.get("lane-map.json", b"{}"))
    for lane in lane_map.get("lanes", []):
        records.setdefault(lane["id"], {"summary": None, "events": [], "lane": None})["lane"] = lane
    for index, event in enumerate(snapshot.timeline(), 1):
        lane_id = event.get("lane_id")
        if lane_id:
            records.setdefault(lane_id, {"summary": None, "events": [], "lane": None})["events"].append(
                {"path": "timeline.jsonl", "index": index, "event": event})
    for name, raw in snapshot.documents.items():
        if raw is None or not name.startswith("agents/") or not name.endswith("/trace.jsonl"):
            continue
        for index, line in enumerate(raw.splitlines(), 1):
            event = json.loads(line)
            lane_id = event.get("lane_id")
            if lane_id:
                records.setdefault(lane_id, {"summary": None, "events": [], "lane": None})["events"].append(
                    {"path": name, "index": index, "event": event})
    return records


def delivery_history(snapshot):
    successful = []
    artifacts = []
    for identifier, raw in snapshot.receipts.items():
        result = json.loads(raw).get("result")
        require(isinstance(result, dict), "unreadable operation history")
        if {"candidate_root", "result_hash", "workspace_id", "candidate_id"} <= result.keys():
            successful.append(identifier)
    for name, raw in snapshot.documents.items():
        if "/delivery/" in name and name.endswith(".json") and raw is not None:
            require(isinstance(json.loads(raw), dict), "invalid delivery artifact")
            artifacts.append(name)
    require(not successful, "successful structured delivery receipt forbids recovery")
    require(not artifacts, "delivery artifact without a consistent successful receipt")
    workspace = registered_workspace(snapshot)
    if workspace and workspace.get("seal"):
        verify_candidate(workspace, inputs=snapshot.external_inputs)
        seal = workspace["seal"]
        container = Path(seal["candidate_root"]).parent
        def external():
            path = container / "delivery.md"
            return read_owned(container, "delivery.md", expected=seal["container_identity"]) if path.exists() else None
        description = captured(snapshot.external_inputs, "recovery-delivery:" + str(container), external)
        require(description is None, "external delivery description without committed receipt is ambiguous")
    return {"successful_receipts": successful, "artifacts": artifacts, "external_description": None}


def diagnose_recovery(run_dir):
    snapshot, _ = capture_validation(JournalSnapshot.open(run_dir))
    report = {"storage_version": snapshot.storage_version, "revision": snapshot.revision,
              "generation": snapshot.generation, "closed": snapshot.closed, "durable": snapshot.durable,
              "identity": recovery_identity(snapshot), "assignments": assignments(snapshot),
              "archives": list(snapshot.archives)}
    finals = [(i, e) for i, e in enumerate(snapshot.timeline(), 1) if e.get("stage") == "final"]
    report["final"] = {"index": finals[-1][0], "event": finals[-1][1],
                       "sha256": digest(snapshot.read_bytes("final.md"))} if finals and snapshot.closed else None
    try:
        report["delivery"] = delivery_history(snapshot)
    except (JournalError, ValueError, OSError) as exc:
        report["blockers"] = [str(exc)]
    return report


def recover(run_dir, *, expected_run_uuid, expected_revision,
            expected_generation, identifier, reason, identity, final_index,
            final_sha256, barrier=None):
    require(bool(identifier) and isinstance(reason, str) and bool(reason.strip()), "saved operation ID and reason required")
    current = JournalSnapshot.open(run_dir)
    require(current.durable, UNSUPPORTED_STORAGE_VERSION)
    payload = {"command": "reopen",
               "run_uuid": expected_run_uuid, "revision": expected_revision, "generation": expected_generation,
               "reason": reason, "identity": identity, "final_index": final_index,
               "final_sha256": final_sha256}
    identifier = operation_id(run_dir, payload["command"], payload, identifier=identifier)
    receipt = current.operation_receipt(identifier)
    if receipt:
        require(receipt["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return receipt["result"]
    snapshot, source = capture_validation(current)
    def verify(snapshot):
        require((snapshot.run_uuid, snapshot.revision, snapshot.generation) ==
                (expected_run_uuid, expected_revision, expected_generation), "recovery preconditions changed")
        require(recovery_identity(snapshot) == identity, "recovery identity changed")
        delivery_history(snapshot)
        require(snapshot.closed, "reopen requires closed generation")
        finals = [(i, e) for i, e in enumerate(snapshot.timeline(), 1) if e.get("stage") == "final"]
        require(len([e for e in snapshot.current_events() if e.get("stage") == "final"]) == 1,
                "ambiguous final history")
        index, event = finals[-1]
        require(index == final_index and digest(snapshot.read_bytes("final.md")) == final_sha256,
                "reopen final reference mismatch")
        verdicts = re.findall(r"^Verdict:\s*(.*?)\s*$", snapshot.read_text("final.md"), re.MULTILINE)
        require(len(verdicts) == 1 and verdicts[0] in {"blocked", "fail"}, "reopen requires negative current final")
        require(event.get("status") in {"blocked", "fail"}, "positive final cannot reopen")
    verify(snapshot)
    freeze_validation(snapshot, source)
    archive_id, archive_bytes = capture_archive(snapshot)
    event = {"timestamp": now_iso(), "stage": "reopen", "role": "orchestrator",
             "stable_agent_name": "Orchestrator", "stable_agent_slug": "orchestrator", "status": "done",
             "summary": reason, "artifacts": [],
             "next_step": "Obtain fresh acceptance for the reopened generation",
             "archive_id": archive_id, "archive_sha256": digest(archive_bytes),
             "generation": snapshot.generation + 1, "final_index": final_index,
             "final_sha256": final_sha256}
    validate_event(event)
    if barrier:
        barrier("captured")
    def mutation(current):
        if barrier:
            barrier("locked")
        verify(replace(current, external_inputs=snapshot.external_inputs))
        documents = {"timeline.jsonl": current.documents.get("timeline.jsonl", b"") + (encode_json_line(event) + "\n").encode()}
        if current.exists("delegation-summary.json"):
            summary = json.loads(current.read_text("delegation-summary.json"))
            for key in ("qa", "reviewer"):
                summary.get("verification", {})[key] = None
            documents["delegation-summary.json"] = (encode_json(summary, pretty=True) + "\n").encode()
        if barrier:
            barrier("before-commit")
        return documents, {"command": event["stage"], "archive_id": archive_id,
                           "archive_sha256": digest(archive_bytes), "generation": event["generation"]}
    result = _transact(run_dir, identifier, payload, mutation, lifecycle="reopen")
    if barrier:
        barrier("after-commit")
    return result


