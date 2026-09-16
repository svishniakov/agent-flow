"""Explicit recovery of the same journal and result, with immutable SQLite history."""
from dataclasses import replace
import json
from pathlib import Path
import re

from journal_io import (JournalSnapshot, JournalError, require, digest, encode_json,
                        capture_archive, _transact, now_iso, validate_event, operation_id)
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


def recover(run_dir, *, upgrade, reopen, expected_run_uuid, expected_revision,
            expected_generation, identifier, reason, identity, final_index=None,
            final_sha256=None, failed_validation_path=None, barrier=None):
    require(bool(identifier) and isinstance(reason, str) and bool(reason.strip()), "saved operation ID and reason required")
    current = JournalSnapshot.open(run_dir)
    require(current.durable, "flat legacy requires explicit import before upgrade")
    payload = {"command": "reopen" if reopen else "upgrade", "upgrade": upgrade,
               "run_uuid": expected_run_uuid, "revision": expected_revision, "generation": expected_generation,
               "reason": reason, "identity": identity, "final_index": final_index,
               "final_sha256": final_sha256, "failed_validation_path": failed_validation_path}
    identifier = operation_id(run_dir, payload["command"], payload, identifier=identifier)
    receipt = current.operation_receipt(identifier)
    if receipt:
        require(receipt["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return receipt["result"]
    require(upgrade == (current.storage_version == 1), "explicit upgrade required exactly for version 1")
    require(reopen or upgrade, "upgrade applies only to version 1")
    snapshot, source = capture_validation(current)
    def verify(snapshot):
        require((snapshot.run_uuid, snapshot.revision, snapshot.generation) ==
                (expected_run_uuid, expected_revision, expected_generation), "recovery preconditions changed")
        require(recovery_identity(snapshot) == identity, "recovery identity changed")
        delivery_history(snapshot)
        if not reopen:
            require(not snapshot.closed, "closed version 1 requires atomic reopen --upgrade")
            return
        require(snapshot.closed, "reopen requires closed generation")
        finals = [(i, e) for i, e in enumerate(snapshot.timeline(), 1) if e.get("stage") == "final"]
        require(len([e for e in snapshot.current_events() if e.get("stage") == "final"]) == 1,
                "ambiguous final history")
        index, event = finals[-1]
        require(index == final_index and digest(snapshot.read_bytes("final.md")) == final_sha256,
                "reopen final reference mismatch")
        verdicts = re.findall(r"^Verdict:\s*(.*?)\s*$", snapshot.read_text("final.md"), re.MULTILINE)
        require(len(verdicts) == 1 and verdicts[0] in {"blocked", "fail"}, "reopen requires negative current final")
        if event.get("status") in {"blocked", "fail"}:
            return
        require(snapshot.storage_version == 1 and event.get("status") in {"pass", "pass-with-risks"},
                "positive version 2 final cannot reopen")
        require(isinstance(failed_validation_path, str), "legacy attempt requires failed validation")
        failed = json.loads(snapshot.read_bytes(failed_validation_path))
        require(failed.get("exit_code") == 1 and isinstance(failed.get("stdout"), str)
                and failed["stdout"].startswith("FAIL "), "failed full validation evidence missing")
        event_hashes = {digest(encode_json(event).encode()),
                        digest(encode_json({k: v for k, v in event.items() if k != "timestamp"}).encode())}
        matching = [json.loads(raw) for raw in snapshot.receipts.values()
                    if json.loads(raw).get("payload_sha256") in event_hashes]
        require(len(matching) == 1 and matching[0]["revision"] == failed.get("revision") <= snapshot.revision,
                "failed validation is not bound to this final attempt revision")
    verify(snapshot)
    freeze_validation(snapshot, source)
    archive_id, archive_bytes = capture_archive(snapshot)
    event = {"timestamp": now_iso(), "stage": "reopen" if reopen else "upgrade", "role": "orchestrator",
             "stable_agent_name": "Orchestrator", "stable_agent_slug": "orchestrator", "status": "done",
             "summary": reason, "artifacts": [failed_validation_path] if failed_validation_path else [],
             "next_step": "Classify historical assignments and obtain fresh acceptance",
             "archive_id": archive_id, "archive_sha256": digest(archive_bytes),
             "generation": snapshot.generation + bool(reopen), "final_index": final_index,
             "final_sha256": final_sha256}
    validate_event(event)
    if barrier:
        barrier("captured")
    def mutation(current):
        if barrier:
            barrier("locked")
        verify(replace(current, external_inputs=snapshot.external_inputs))
        documents = {"timeline.jsonl": current.documents.get("timeline.jsonl", b"") + (encode_json(event) + "\n").encode()}
        if current.exists("delegation-summary.json"):
            summary = json.loads(current.read_text("delegation-summary.json"))
            for key in ("qa", "reviewer"):
                summary.get("verification", {})[key] = None
            documents["delegation-summary.json"] = (encode_json(summary, pretty=True) + "\n").encode()
        if barrier:
            barrier("before-commit")
        return documents, {"command": event["stage"], "archive_id": archive_id,
                           "archive_sha256": digest(archive_bytes), "generation": event["generation"]}
    result = _transact(run_dir, identifier, payload, mutation,
                       lifecycle="reopen-upgrade" if reopen and upgrade else "reopen" if reopen else "upgrade")
    if barrier:
        barrier("after-commit")
    return result


def classify_legacy(run_dir, classification, *, expected_revision, identifier, session_source=None):
    """Attach explicit historical facts; accepting a replacement remains a separate operation."""
    from verification_evidence import verify_reference, session_metadata
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, "flat legacy requires explicit import before classification")
    snapshot, source = capture_validation(snapshot, session_source=session_source)
    payload = {"command": "classify-legacy", "revision": expected_revision, "classification": classification}
    identifier = operation_id(run_dir, "classify-legacy", payload, identifier=identifier)
    prior = snapshot.operation_receipt(identifier)
    if prior:
        require(prior["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        return prior["result"]
    archive_id = classification.get("archive_id")
    original = snapshot.archive(archive_id)
    require(original.storage_version == 1, "classification requires original version 1 archive")
    inventory = assignments(original)
    entries = classification.get("assignments")
    require(isinstance(entries, list) and len({e.get("lane_id") for e in entries}) == len(entries),
            "classification requires distinct assignments")
    require({e.get("lane_id") for e in entries} == set(inventory), "classification must cover all legacy assignments")
    def reference(ref, evidence=original):
        verify_reference(evidence.artifact_root, ref, snapshot=evidence)
        text = evidence.read_text(ref["path"])
        require(isinstance(ref.get("quote"), str) and bool(ref["quote"].strip()) and ref["quote"] in text,
                "classification quote absent from original evidence")
        return text
    def prepare(current):
        require(current.revision == expected_revision, "classification preconditions changed")
        summary = json.loads(current.read_text("delegation-summary.json"))
        records = {r["lane_id"]: r for kind in ("subagents", "role_lanes") for r in summary.get(kind, [])}
        corrections, planned = {}, {}
        current_inventory = assignments(current)
        for entry in entries:
            lane_id = entry["lane_id"]
            facts = inventory[lane_id]
            require(isinstance(entry.get("reason"), str) and bool(entry["reason"].strip()), "classification reason required")
            if "correction" in entry:
                require(facts["summary"] is None and facts["lane"] is None, "real planned/summary assignment cannot be excluded")
                text = reference(entry["correction"])
                ids = {e["event"].get("codex_thread_id") for e in facts["events"] if e["event"].get("codex_thread_id")}
                require(len(ids) == 1 and next(iter(ids)) in text and
                        any(word in text.lower() for word in ("incorrect", "wrong", "ошиб", "неверн")),
                        "registration correction must identify the erroneous UUID")
                corrections[lane_id] = entry
                continue
            require(isinstance(entry.get("obligation_id"), str) and bool(entry["obligation_id"].strip()), "obligation id required")
            scope = reference(entry["scope"])
            if entry.get("planned") is True:
                require(facts["summary"] is None and not facts["events"] and
                        (facts["lane"] or {}).get("status") == "planned",
                        "planned classification requires an originally unstarted lane")
                require(not entry.get("optional") and entry.get("outcome", "unresolved") == "unresolved"
                        and not entry.get("source") and not entry.get("handoff"),
                        "planned assignment remains required and unresolved")
                planned[lane_id] = entry
                continue
            events = current_inventory[lane_id]["events"]
            latest = max(events, key=lambda e: (e["event"].get("timestamp", ""), e["index"]), default=None)
            require(latest is not None and latest["event"].get("stage") in {"handoff", "blocked", "fail"},
                    "active/planned assignment needs explicit completion before classification")
            terminal = latest
            continued = terminal["event"] not in [e["event"] for e in facts["events"]]
            evidence = current if continued else original
            handoff = entry.get("handoff")
            if handoff:
                text = reference(handoff, evidence)
                require(handoff["path"] in terminal["event"].get("artifacts", [])
                        or (facts["summary"] or {}).get("handoff") == handoff["path"], "handoff not linked by original assignment")
            outcome = entry.get("outcome", "unresolved")
            require(outcome in {"unresolved", "pass", "pass-with-risks", "blocked", "fail"}, "invalid historical outcome")
            if outcome in {"pass", "pass-with-risks"}:
                require(terminal["event"].get("stage") == "handoff" and
                        terminal["event"].get("status") in {"done", "pass", "pass-with-risks"},
                        "explicit terminal failure requires a current replacement")
            if outcome != "unresolved":
                require(handoff is not None and re.findall(r"(?im)^Verdict:\s*(\S+)\s*$", text) == [outcome],
                        "outcome requires explicit original handoff verdict")
            record = records.get(lane_id)
            if record is None:
                event = terminal["event"]
                record = {"lane_id": lane_id, "role": event.get("role"),
                          "trace": terminal["path"], "reason": entry["reason"]}
                if event.get("execution_mode") == "subagent":
                    record["codex_thread_id"] = event.get("codex_thread_id")
                    kind = "subagents"
                else:
                    kind = "role_lanes"
                summary[kind].append(record)
                summary[kind + "_used"] = True
            original_record = facts["summary"]
            if original_record:
                require(all(record.get(key) == original_record.get(key) for key in ("role", "codex_thread_id", "trace")),
                        "original assignment identity changed before classification")
            else:
                original_events = [item["event"] for item in facts["events"]]
                require(all(event.get("role") == record.get("role") and
                            event.get("codex_thread_id") == record.get("codex_thread_id") for event in original_events),
                        "original assignment identity conflicts with its trace")
            require("legacy" not in record, "legacy classification is immutable")
            if record.get("codex_thread_id") and not entry.get("source"):
                require(outcome == "unresolved" and not entry.get("optional"),
                        "missing historical source leaves the assignment unresolved")
            if record.get("codex_thread_id") and entry.get("source"):
                proof = entry["source"]
                require(proof.get("thread_id") == record["codex_thread_id"], "historical source UUID mismatch")
                session = source.read(proof["thread_id"])
                session_metadata(session, proof["thread_id"], summary["verification"]["root_thread_id"], record["role"], source=source)
                completions = [e["payload"] for e in session if e.get("type") == "event_msg"
                               and e.get("payload", {}).get("type") == "task_complete"
                               and e["payload"].get("turn_id") == proof.get("turn_id")]
                from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config
                expected_model = captured(current.external_inputs, "historical-role:" + record["role"],
                    lambda: role_config(read_frontmatter(resolve_role_path(default_agents_dir(), record["role"]),
                        inputs=current.external_inputs), record["role"])["model"])
                active = None
                models = []
                own_complete = False
                for observed in session:
                    body = observed.get("payload", {})
                    if observed.get("type") == "event_msg" and body.get("type") == "task_started":
                        active = body.get("turn_id")
                    elif observed.get("type") == "turn_context" and active == proof.get("turn_id"):
                        require(body.get("turn_id") == active, "foreign historical turn context")
                        models.append(body.get("model"))
                    elif observed.get("type") == "event_msg" and body.get("type") == "task_complete":
                        if body.get("turn_id") == proof.get("turn_id"):
                            own_complete = active == proof.get("turn_id")
                        active = None
                require(own_complete and models and all(m == expected_model for m in models),
                        "historical own turn or model mismatch")
                require(len(completions) == 1 and isinstance(proof.get("quote"), str) and bool(proof["quote"].strip())
                        and proof["quote"] in completions[0].get("last_agent_message", ""),
                        "historical own completion quote missing")
                require(handoff is not None and proof["quote"] in text,
                        "historical source quote must also occur in its original handoff")
                if outcome != "unresolved":
                    from verification_evidence import final_json, EvidenceError
                    answer_text = completions[0]["last_agent_message"]
                    try:
                        source_outcome = final_json(answer_text).get("verdict")
                    except EvidenceError:
                        verdicts = re.findall(r"(?im)^(?:Verdict|Вердикт):\s*(\S+)\s*$", answer_text)
                        source_outcome = verdicts[-1] if len(verdicts) == 1 else None
                    require(source_outcome in ({"pass", "passed"} if outcome == "pass" else {outcome}),
                            "historical source outcome does not confirm handoff verdict")
            optional = entry.get("optional", False)
            require(type(optional) is bool, "optional flag must be boolean")
            if optional:
                require(outcome in {"pass", "pass-with-risks"} and
                        any(word in entry["scope"]["quote"].lower() for word in ("consultation", "консультац")),
                        "optional consultation needs original scope and accepted outcome")
            record["obligation"] = {"id": entry["obligation_id"], "required": not optional, "state": "current"}
            record["legacy"] = {"archive_id": archive_id, "classification": entry, "terminal": terminal,
                                "outcome": outcome, "continued": continued, "classified_revision": current.revision}
            if optional:
                record["obligation"].update(state="exempt", resolution={"original_lane": lane_id,
                    "reason": entry["reason"], "evidence": [entry["scope"], handoff]})
        return {"delegation-summary.json": encode_json(summary, pretty=True) + "\n",
                "artifacts/lifecycle/legacy-classification.json": encode_json({"archive_id": archive_id, "corrections": corrections, "planned": planned}, pretty=True) + "\n"}, {"classified": len(entries)}
    prepare(snapshot)
    freeze_validation(snapshot, source)
    return _transact(run_dir, identifier, payload,
                     lambda current: prepare(replace(current, external_inputs=snapshot.external_inputs)), lifecycle="classify")
