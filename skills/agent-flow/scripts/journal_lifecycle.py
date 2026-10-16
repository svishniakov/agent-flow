"""Domain finalization of one journal generation, using the complete validator."""
from dataclasses import replace
import json
from pathlib import Path
import re
from types import MappingProxyType, ModuleType

from journal_io import (JournalSnapshot, _transact, require, digest, encode_json, encode_json_line,
                        now_iso, validate_event, JournalError, operation_id, update_handoff_state, UNSUPPORTED_STORAGE_VERSION)
from validation_inputs import ValidationInputs


def resolve_obligation(run_dir, *, lane_id, replacement, reason, expected_revision, identifier):
    from verification_evidence import evaluate_obligations, verify_reference
    require(isinstance(reason, str) and bool(reason.strip()), "resolution reason required")
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
    payload = {"command": "resolve-obligation", "lane_id": lane_id, "replacement": replacement,
               "reason": reason, "expected_revision": expected_revision}
    identifier = operation_id(run_dir, "resolve-obligation", payload, identifier=identifier)
    def mutation(snapshot):
        require(snapshot.revision == expected_revision, "obligation preconditions changed")
        summary = json.loads(snapshot.read_bytes("delegation-summary.json"))
        records = {record["lane_id"]: record for kind in ("subagents", "role_lanes")
                   for record in summary.get(kind, [])}
        require(lane_id in records and replacement in records, "obligation assignment missing")
        old, new = records[lane_id], records[replacement]
        require(isinstance(old.get("obligation"), dict) and isinstance(new.get("obligation"), dict),
                "assignments require explicit obligations")
        handoff = new.get("handoff")
        require(isinstance(handoff, str), "replacement handoff missing")
        evidence = {"path": handoff, "sha256": digest(snapshot.read_bytes(handoff))}
        verify_reference(snapshot.artifact_root, evidence, snapshot=snapshot)
        old["obligation"].update(state="resolved", resolution={
            "original_lane": lane_id, "replacement": replacement, "reason": reason,
            "evidence": [evidence], "timestamp": now_iso()})
        resolved, errors = evaluate_obligations(snapshot, summary, require_current=False)
        require(lane_id in resolved, "obligation resolution failed: " + "; ".join(errors))
        documents = {"delegation-summary.json": (encode_json(summary, pretty=True) + "\n").encode()}
        if snapshot.exists("lane-map.json"):
            lane_map = json.loads(snapshot.read_bytes("lane-map.json"))
            lanes = {lane["id"]: lane for lane in lane_map["lanes"]}
            require(replacement in lanes and lane_id in lanes, "lane-map replacement missing")
            require(lanes[replacement].get("status") == new.get("status"), "replacement lane status conflicts with its outcome")
            require(lanes[lane_id].get("status") == old.get("status"), "original lane status conflicts with its outcome")
            lanes[lane_id].update(status="replaced", replacement=replacement)
            lanes[lane_id]["handoff_state"] = update_handoff_state(lane=lanes[lane_id], lane_id=lane_id, status="blocked",
                mode=None, from_lane=None, to_lane=None, task=None, handoff=None, batch_id=None, batch_items=[])
            documents["lane-map.json"] = (encode_json(lane_map, pretty=True) + "\n").encode()
        return documents, {
            "lane_id": lane_id, "replacement": replacement}
    from journal_io import transact
    return transact(run_dir, identifier, payload, mutation)


def load_validator(raw):
    path = Path(__file__).with_name("validate-run.py")
    module = ModuleType("lifecycle_validator")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def capture_validation(snapshot, *, session_source=None):
    from verification_evidence import CapturedSource, CodexSessionSource
    inputs = ValidationInputs()
    source = CapturedSource(session_source or CodexSessionSource(), inputs=inputs)
    # Imported dependencies remain part of the trusted process runtime.
    inputs.file(Path(__file__).with_name("validate-run.py"))
    return replace(snapshot, external_inputs=inputs), source


def freeze_validation(snapshot, source):
    # Include the complete source projections used for latest-turn and ancestry checks.
    for key, events in source.events.items():
        snapshot.external_inputs.capture("session:" + repr(key), lambda events=events: events)
    for key, resolution in source.resolutions.items():
        snapshot.external_inputs.capture("resolution:" + repr(key), lambda resolution=resolution: resolution)
    source.frozen = True
    snapshot.external_inputs.freeze()


def finalize(run_dir, *, expected_run_uuid, expected_revision, expected_generation,
             identifier, final_bytes=None, verdict, session_source=None, barrier=None, final_summary=None):
    require(verdict in {"ship", "blocked", "fail", "pass-with-risks"}, "unsupported final verdict")
    require(isinstance(identifier, str) and bool(identifier.strip()), "finalize requires saved operation ID")
    if final_summary is not None:
        from journal_structure import validate_request, render_document
        validate_request("final", {"summary": final_summary, "status": "pass" if verdict == "ship" else verdict})
        require(final_bytes is None, "provide semantic text or raw final bytes, not both")
        final_bytes = render_document("final", final_summary, "pass" if verdict == "ship" else verdict).encode()
    require(isinstance(final_bytes, bytes), "final file must contain bytes")
    snapshot = JournalSnapshot.open(run_dir)
    payload = {"command": "finalize", "run_uuid": expected_run_uuid,
               "revision": expected_revision, "generation": expected_generation,
               "final_sha256": digest(final_bytes), "verdict": verdict}
    receipt = snapshot.operation_receipt(identifier)
    if final_summary is None:
        require(receipt is not None and receipt["result"].get("command") == "finalize",
                "new finalization requires semantic final_summary; final_bytes is available only for exact historical receipt replay")
        require(receipt["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return receipt["result"]
    require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
    summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
    product_positive = verdict in {"ship", "pass-with-risks"} and summary.get("verification", {}).get("task_kind") == "change"
    if final_summary is not None:
        from journal_io import render_final, unique_paths
        from verification_evidence import changed_paths, delivery_scope
        lanes = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
        verification = summary["verification"]
        if verdict in {"blocked", "fail"} and isinstance(verification.get("blocker"), str) and verification["blocker"].strip():
            from task_workspace import registered_workspace
            workspace = registered_workspace(snapshot)
            if workspace is not None and "seal" in workspace:
                paths, declared = workspace["seal"]["changed_paths"], True
            elif "run_changed_files" in verification:
                paths, declared = unique_paths(verification["run_changed_files"]), True
            else:
                paths, declared = [], False
        else:
            paths = changed_paths(run_dir, lanes, verification, snapshot=snapshot)
            declared = "run_changed_files" in verification or bool(paths)
        final_bytes = render_final(final_bytes.decode(), summary, paths,
            declared=declared,
            scope=delivery_scope(snapshot, summary["verification"], summary=summary)).encode()
        from journal_io import render_service_sections
        final_bytes = (final_bytes.decode() + render_service_sections(snapshot)).encode()
    payload["final_sha256"] = digest(final_bytes)
    if receipt is not None:
        require(receipt["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return receipt["result"]
    fields = re.findall(r"^Verdict:\s*(.*?)\s*$", final_bytes.decode("utf-8"), re.MULTILINE)
    require(fields == [verdict], "final verdict differs from requested verdict")
    identifier = operation_id(run_dir, "finalize", payload, identifier=identifier)
    require(not snapshot.closed, "journal generation is closed")
    require((snapshot.run_uuid, snapshot.revision, snapshot.generation) ==
            (expected_run_uuid, expected_revision, expected_generation), "finalize preconditions changed")
    event = {"timestamp": now_iso(), "stage": "final", "role": "orchestrator",
             "stable_agent_name": "Orchestrator", "stable_agent_slug": "orchestrator",
             "status": "pass" if verdict == "ship" else verdict, "summary": "Validated final report",
             "artifacts": ["final.md"], "next_step": "", "generation": expected_generation}
    validate_event(event)
    documents = {"final.md": final_bytes,
                 "timeline.jsonl": snapshot.documents.get("timeline.jsonl", b"") + (encode_json_line(event) + "\n").encode()}
    def proposed(current):
        return replace(current, documents=MappingProxyType({**current.documents, **documents}))
    captured_snapshot, source = capture_validation(proposed(snapshot), session_source=session_source)
    validator_bytes = captured_snapshot.external_inputs.file(Path(__file__).with_name("validate-run.py"))
    validator = load_validator(validator_bytes)
    validate = validator.validate_product_acceptance if product_positive else validator.validate_run
    validation_args = {"verdict": verdict} if product_positive else {}
    errors = validate(Path(run_dir), snapshot=captured_snapshot, session_source=source, **validation_args)
    require(not errors, "finalize validation failed: " + "; ".join(errors))
    freeze_validation(captured_snapshot, source)
    if barrier is not None:
        barrier("captured")
    def commit(current):
        require((current.run_uuid, current.revision, current.generation) ==
                (expected_run_uuid, expected_revision, expected_generation), "finalize preconditions changed")
        future = replace(proposed(current), external_inputs=captured_snapshot.external_inputs)
        if barrier is not None:
            barrier("locked")
        errors = validate(Path(run_dir), snapshot=future, session_source=source, **validation_args)
        if barrier is not None:
            barrier("validated")
        require(not errors, "finalize validation failed: " + "; ".join(errors))
        if barrier is not None:
            barrier("before-commit")
        return documents, {"command": "finalize", "generation": expected_generation,
                           "validated_revision": expected_revision, "validator_version": 2,
                           "validator_sha256": digest(validator_bytes),
                           "input_hashes": captured_snapshot.external_inputs.hashes(),
                           "final_sha256": digest(final_bytes), "verdict": verdict}
    result = _transact(run_dir, identifier, payload, commit, lifecycle="finalize")
    if barrier is not None:
        barrier("after-commit")
    return result
