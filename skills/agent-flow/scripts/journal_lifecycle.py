"""Domain finalization of one journal generation, using the complete validator."""
from dataclasses import replace
import json
from pathlib import Path
import re
from types import MappingProxyType, ModuleType

from journal_io import (JournalSnapshot, _transact, require, digest, encode_json,
                        now_iso, validate_event, JournalError, operation_id)
from validation_inputs import ValidationInputs


def resolve_obligation(run_dir, *, lane_id, replacement, reason, expected_revision, identifier):
    from verification_evidence import evaluate_obligations, verify_reference
    require(isinstance(reason, str) and bool(reason.strip()), "resolution reason required")
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, "flat legacy requires explicit import before obligation resolution")
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
        resolved, errors = evaluate_obligations(snapshot, summary)
        require(lane_id in resolved, "obligation resolution failed: " + "; ".join(errors))
        documents = {"delegation-summary.json": (encode_json(summary, pretty=True) + "\n").encode()}
        original_status = old.get("status")
        original_lane = None
        legacy = old.get("legacy")
        if legacy:
            from journal_recovery import assignments
            original = snapshot.archive(legacy["archive_id"])
            facts = assignments(original).get(lane_id)
            require(facts is not None, "original assignment missing from archive")
            original_lane = facts["lane"]
            terminal = legacy["terminal"]
            event = terminal["event"]
            evidence = snapshot if legacy.get("continued") else original
            events = assignments(evidence)[lane_id]["events"]
            require(terminal in events and event.get("lane_id") == lane_id
                    and event.get("stage") in {"handoff", "blocked", "fail"},
                    "original terminal conflicts with its assignment")
            require(legacy["classification"].get("lane_id") == lane_id
                    and legacy["classification"].get("obligation_id") == old["obligation"]["id"],
                    "original classification conflicts with its obligation")
            identity = facts["summary"] or event
            require(all(old.get(key) == identity.get(key) for key in ("role", "codex_thread_id"))
                    and all(item["event"].get(key) == old.get(key)
                            for item in facts["events"] for key in ("role", "codex_thread_id"))
                    and all(event.get(key) == old.get(key) for key in ("role", "codex_thread_id")),
                    "original assignment identity conflicts with its source")
            if facts["summary"]:
                require(old.get("trace") == facts["summary"].get("trace"),
                        "original assignment trace conflicts with its source")
            latest = max(assignments(snapshot)[lane_id]["events"],
                         key=lambda item: (item["event"].get("timestamp", ""), item["index"]))
            require(latest["event"] == event, "legacy assignment has newer unresolved work")
            original_status = original_lane["status"] if original_lane else event.get("status")
        if snapshot.exists("lane-map.json"):
            lane_map = json.loads(snapshot.read_bytes("lane-map.json"))
            lanes = {lane["id"]: lane for lane in lane_map["lanes"]}
            require(replacement in lanes and (lane_id in lanes or legacy and original_lane is None),
                    "lane-map replacement missing")
            require(lanes[replacement].get("status") == new.get("status"), "replacement lane status conflicts with its outcome")
            if lane_id in lanes:
                require(lanes[lane_id].get("status") == original_status, "original lane status conflicts with its outcome")
                if legacy:
                    require(all(lanes[lane_id][key] == old.get(key)
                                for key in ("role", "codex_thread_id", "trace") if key in lanes[lane_id]),
                            "original lane identity conflicts with its source")
                lanes[lane_id].update(status="replaced", replacement=replacement)
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
             identifier, final_bytes, verdict, session_source=None, barrier=None):
    require(verdict in {"ship", "blocked", "fail", "pass-with-risks"}, "unsupported final verdict")
    require(isinstance(identifier, str) and bool(identifier.strip()), "finalize requires saved operation ID")
    require(isinstance(final_bytes, bytes), "final file must contain bytes")
    fields = re.findall(r"^Verdict:\s*(.*?)\s*$", final_bytes.decode("utf-8"), re.MULTILINE)
    require(fields == [verdict], "final verdict differs from requested verdict")
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, "flat legacy requires explicit import before finalization")
    payload = {"command": "finalize", "run_uuid": expected_run_uuid,
               "revision": expected_revision, "generation": expected_generation,
               "final_sha256": digest(final_bytes), "verdict": verdict}
    identifier = operation_id(run_dir, "finalize", payload, identifier=identifier)
    receipt = snapshot.operation_receipt(identifier)
    if receipt is not None:
        require(receipt["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return receipt["result"]
    require(snapshot.storage_version == 2, "journal storage version 1 is read-only; explicit upgrade required")
    require(not snapshot.closed, "journal generation is closed")
    require((snapshot.run_uuid, snapshot.revision, snapshot.generation) ==
            (expected_run_uuid, expected_revision, expected_generation), "finalize preconditions changed")
    event = {"timestamp": now_iso(), "stage": "final", "role": "orchestrator",
             "stable_agent_name": "Orchestrator", "stable_agent_slug": "orchestrator",
             "status": "pass" if verdict == "ship" else verdict, "summary": "Validated final report",
             "artifacts": ["final.md"], "next_step": "", "generation": expected_generation}
    validate_event(event)
    documents = {"final.md": final_bytes,
                 "timeline.jsonl": snapshot.documents.get("timeline.jsonl", b"") + (encode_json(event) + "\n").encode()}
    def proposed(current):
        return replace(current, documents=MappingProxyType({**current.documents, **documents}))
    captured_snapshot, source = capture_validation(proposed(snapshot), session_source=session_source)
    validator_bytes = captured_snapshot.external_inputs.file(Path(__file__).with_name("validate-run.py"))
    validator = load_validator(validator_bytes)
    errors = validator.validate_run(Path(run_dir), snapshot=captured_snapshot, session_source=source)
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
        errors = validator.validate_run(Path(run_dir), snapshot=future, session_source=source)
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
