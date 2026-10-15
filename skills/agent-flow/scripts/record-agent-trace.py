#!/usr/bin/env python3
"""Record one subagent or role-lane event in Agent Flow traces.

For a real subagent, record `stage=spawned` with `--codex-thread-id`, then a
terminal handoff/blocked/fail event. Successful subagent lanes need a terminal
handoff event that references the lane handoff artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from types import MappingProxyType
from pathlib import Path

from agent_config import AgentConfigError
from journal_io import (load_artifact_index, upsert_artifacts, validate_directory, JournalSnapshot,
                        transact, operation_id, digest, capture_file, capture_external_references,
                        validate_assignment_event, _transact_completion, UNSUPPORTED_STORAGE_VERSION)
from journal_io import (now_iso, encode_json, encode_json_line, validate_append,
                        render_final, safe_path_segment, display_path, unique_paths)

from verification_evidence import (
    CodexSessionSource, EvidenceError, completed_turn, empty_verification,
    evidence_path, reference_bytes, result_hash, sha256, validate_verification,
    resolve_completion, completion_follows, validate_summary_shape,
    changed_paths, require, accepted_record, require_own_reviewer_handoff,
    CapturedSource, ensure_result_contract, require_workspace_or_historical,
    historical_result_hash, SHA256, delivery_scope,
    source_bound_record,
)
from task_workspace import registered_workspace
from journal_io import assignment_lane, worker_start_preflight, prepare_summary, require_subagent_identity, record_event





def prepare_conclusion(run_dir, args, artifacts, source, *, snapshot=None):
    from journal_structure import validate_request
    sections = json.loads(capture_file(Path(args.sections_file)).decode()) if getattr(args, "sections_file", None) else []
    if args.summary is not None:
        validate_request("handoff", {"summary": args.summary, "status": args.status, "lane_id": args.lane_id, "sections": sections})
    if args.status in {"blocked", "fail"}:
        require(isinstance(args.reason, str) and args.reason.strip(), "negative conclusion requires --reason")
        require(bool(args.unverified), "negative conclusion requires --unverified")
        unique_paths(args.unverified)
    classification = None
    if args.context_classification_json:
        raw = args.context_classification_json
        classification = json.loads(raw if raw.lstrip().startswith("{") else capture_file(Path(raw).expanduser()).decode())
        require(isinstance(classification, dict), "context_classification must be an object")
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    data = json.loads(snapshot.read_text("delegation-summary.json"))
    validate_summary_shape(data)
    verification = data["verification"]
    lane_path = run_dir / "lane-map.json"
    lane_map = json.loads(snapshot.read_text(lane_path)) if snapshot.exists(lane_path) else {}
    errors = validate_verification(run_dir, data, lane_map, "pending", source, snapshot=snapshot)
    require(not errors, "; ".join(errors))
    record = next((r for r in data["subagents"] if r.get("lane_id") == args.lane_id and r.get("role") == args.role), None)
    require(record is not None, "prepare-conclusion requires existing role assignment")
    from verification_evidence import review_subject_kind, review_input_receipt
    preliminary = review_subject_kind(record) == "requirements-plan"
    gates = []
    tool_documents = {}
    if preliminary:
        from journal_io import validate_preliminary_gates
        review_input_receipt(snapshot, record, current=True)
        if getattr(args, "readiness_gates_file", None):
            gates = json.loads(capture_file(Path(args.readiness_gates_file)).decode())
            require(type(gates) is list, "readiness gates must be an array")
            for gate in gates:
                require(type(gate) is dict and type(gate.get("evidence")) is list, "malformed readiness gate")
                derived = []
                for ref in gate["evidence"]:
                    if type(ref) is dict and "source_event" in ref:
                        from verification_evidence import readiness_tool_evidence
                        raw, item = readiness_tool_evidence(snapshot, record, ref, source, capture_required=False)
                        require(not snapshot.exists(item["path"]) or snapshot.read_bytes(item["path"]) == raw, "readiness tool attachment already differs")
                        require(item["path"] not in tool_documents or tool_documents[item["path"]] == raw, "conflicting readiness tool capture path")
                        tool_documents[item["path"]] = raw
                        derived.append(item)
                    else:
                        require(type(ref) is dict and set(ref) in ({"path"}, {"path", "sha256"}), "unknown readiness evidence fields")
                        raw = reference_bytes(run_dir, ref, snapshot=snapshot)
                        require("sha256" not in ref or ref["sha256"] == sha256(raw), "readiness evidence hash assertion mismatch")
                        derived.append({"path": ref["path"], "sha256": sha256(raw)})
                gate["evidence"] = derived
            snapshot = replace(snapshot, documents=MappingProxyType({**snapshot.documents, **tool_documents}))
            validate_preliminary_gates(snapshot, gates, record=record, source=source, execution_required=True)
            require(all(ref["path"] in artifacts[1:] for gate in gates for ref in gate["evidence"]), "gate references require conclusion evidence")
            require(not any(section["section"] == "Verification Gate Results" for section in sections), "gate results section is writer-owned")
            sections.append({"section": "Verification Gate Results", "summary": "\n".join(gate["axis"] + "/" + gate["facet"] + ": " + gate["readiness"] + " — " + gate["check"] + ". " + gate["notes"] for gate in gates)})
    else:
        require(not getattr(args, "readiness_gates_file", None), "readiness gates require preliminary subject")
    require(len(artifacts) >= 2, "prepare-conclusion requires handoff first and evidence artifacts")
    negative = args.status in {"blocked", "fail"}
    if args.role in {"reviewer", "reviewer.qa"} and not negative and not preliminary:
        qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), None)
        require(qa is not None, "reviewer requires current accepted QA")
        require_own_reviewer_handoff(run_dir, artifacts[0], qa.get("handoff"), snapshot=snapshot)
        accepted_record(run_dir, qa, verification, "qa-verifier", source, snapshot=snapshot)
    digest = None if preliminary else result_hash(run_dir, verification, snapshot=snapshot, require_ready=not negative) if verification.get("task_kind") == "change" else verification.get("result_hash")
    require(preliminary or digest == verification.get("result_hash") and (negative or isinstance(digest, str)), "current result_hash is stale or missing")
    handoff = artifacts[0]
    require(not tool_documents or args.summary is not None, "readiness tool capture requires generated handoff summary")
    if args.summary is not None:
        from journal_structure import validate_request, render_document
        from journal_io import write_entry
        request = {"summary": args.summary, "status": args.status, "lane_id": args.lane_id,
                   "evidence": [{"path": path} for path in artifacts[1:]], "sections": sections}
        validate_request("handoff", request)
        metadata = {"evidence": [{"path": path, "sha256": sha256(reference_bytes(run_dir, {"path": path}, snapshot=snapshot))} for path in artifacts[1:]], "sections": sections}
        generated = render_document("handoff", args.summary, args.status, metadata)
        def publish(current):
            for ref in metadata["evidence"]:
                raw = tool_documents.get(ref["path"])
                require(raw is None or not current.exists(ref["path"]) or current.read_bytes(ref["path"]) == raw, "readiness tool attachment changed")
                require(sha256(raw if raw is not None else reference_bytes(run_dir, {"path": ref["path"]}, snapshot=current)) == ref["sha256"],
                        "evidence changed while preparing conclusion: " + ref["path"])
            current_data = json.loads(current.read_text("delegation-summary.json"))
            current_record = next(r for r in current_data["subagents"] if r.get("lane_id") == args.lane_id)
            require(current_record == record, "assignment changed while preparing conclusion")
            require(not current_record.get("completion_turn_id"), "terminal conclusion is immutable")
            current_lanes = json.loads(current.read_text("lane-map.json")) if current.exists("lane-map.json") else {}
            errors = validate_verification(run_dir, current_data, current_lanes, "pending", source, snapshot=current)
            require(not errors, "; ".join(errors))
            if preliminary:
                review_input_receipt(current, record, current=True)
                captured_snapshot = replace(current, documents=MappingProxyType({**current.documents, **tool_documents}))
                validate_preliminary_gates(captured_snapshot, gates, record=record, source=source, execution_required=True)
            else:
                require(result_hash(run_dir, current_data["verification"], snapshot=current, require_ready=not negative) == digest,
                        "current result_hash changed while preparing conclusion")
            return {**tool_documents, handoff: generated}, {}
        write_entry(run_dir, "handoff", request, identifier=args.operation_id, mutation=publish)
        snapshot = JournalSnapshot.open(run_dir)
    text = reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot).decode()
    for path in artifacts[1:]:
        sha = sha256(reference_bytes(run_dir, {"path": path}, snapshot=snapshot))
        require(path in text and sha in text, "evidence reference and sha256 must appear in handoff")
    answer = {"verdict": "passed" if args.status == "pass" else args.status,
              "reviewed_result_hash": digest, "handoff": handoff,
              "handoff_sha256": sha256(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot))}
    if "review_subject" in record:
        answer["review_subject"] = record["review_subject"]
    if preliminary:
        answer.update(review_input_operation_id=record["review_input_operation_id"], readiness_gates=gates)
    if negative:
        require(isinstance(args.reason, str) and args.reason.strip(), "negative conclusion requires --reason")
        require(bool(args.unverified), "negative conclusion requires --unverified")
        answer.update(reason=args.reason, unverified=unique_paths(args.unverified))
    if classification is not None:
        answer["context_classification"] = classification
    if args.role in {"reviewer", "reviewer.qa"} and not negative and not preliminary:
        qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), None)
        require(qa is not None, "reviewer requires current accepted QA")
        require_own_reviewer_handoff(run_dir, handoff, qa.get("handoff"), snapshot=snapshot)
        completion = accepted_record(run_dir, qa, verification, "qa-verifier", source, snapshot=snapshot)
        answer["qa_handoff_sha256"] = completion["handoff_sha256"]
    return answer




def record_mode_correction(run_dir, correction, *, identifier, session_source=None, barrier=None):
    from journal_lifecycle import capture_validation, freeze_validation
    from verification_evidence import verify_trace_mode_correction, effective_trace_modes
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
    require(isinstance(correction, dict) and identifier == correction.get('operation_id'), 'mode correction operation ID mismatch')
    payload = {'command': 'trace-mode-correction', 'correction': correction}
    # Persist the exact request before any transaction; receipt replay precedes mutable guards.
    identifier = operation_id(run_dir, 'trace-mode-correction', payload, identifier=identifier)
    prior = snapshot.operation_receipt(identifier)
    if prior is not None:
        require(prior['payload_sha256'] == digest(encode_json(payload).encode()), 'operation ID payload conflict')
        return prior['result']
    require(not snapshot.closed, 'journal generation is closed')
    snapshot, source = capture_validation(snapshot, session_source=session_source)
    def verify(current):
        require((current.run_uuid, current.generation, current.revision) ==
                (correction['run_uuid'], correction['generation'], correction['revision']), 'mode correction preconditions changed')
        addresses = verify_trace_mode_correction(current, correction, source)
        previous = effective_trace_modes(current, source)
        require(not any(('agents/orchestrator/trace.jsonl', i) in previous for i in addresses), 'target already has a mode correction')
        for key in ('timeline', 'trace'):
            require(len(current.read_bytes(correction[key]['path'])) == correction[key]['size'], 'mode correction prefix is not current')
    verify(snapshot)
    freeze_validation(snapshot, source)
    if barrier:
        barrier('captured')
    event = {'timestamp': now_iso(), 'stage': 'trace-mode-corrected', 'role': 'orchestrator',
             'stable_agent_name': 'orchestrator', 'stable_agent_slug': 'orchestrator', 'status': 'done',
             'summary': correction['reason'], 'artifacts': [], 'next_step': '', 'execution_mode': 'role-lane',
             'generation': snapshot.generation, 'mode_correction': correction,
             'agent_trace': 'agents/orchestrator/trace.jsonl'}
    raw = (encode_json_line(event)+'\n').encode()
    def mutation(current):
        current = replace(current, external_inputs=snapshot.external_inputs)
        verify(current)
        validate_append(run_dir / 'timeline.jsonl', event, snapshot=current)
        if barrier:
            barrier('locked')
        return {path: current.read_bytes(path)+raw for path in ('timeline.jsonl', 'agents/orchestrator/trace.jsonl')}, {'corrected': len(correction['targets']), 'generation': current.generation}
    result = transact(run_dir, identifier, payload, mutation)
    if barrier:
        barrier('after-commit')
    return result


def main(argv=None, *, session_source=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--operation-id", help="Saved machine request ID for retry.")
    parser.add_argument("--role")
    parser.add_argument("--status")
    parser.add_argument("--stage")
    parser.add_argument("--summary")
    parser.add_argument("--sections-file", help="Private typed [{section, summary}] semantic input for generated conclusion headings.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-conclusion", action="store_true")
    modes.add_argument("--prepare-review-input", action="store_true")
    modes.add_argument("--render-final", action="store_true")
    modes.add_argument("--correction-file", help="Captured root trace mode correction JSON.")
    parser.add_argument("--next-step", default="")
    parser.add_argument("--stable-agent-name")
    parser.add_argument("--stable-agent-slug")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--execution-mode", choices=["subagent", "role-lane"], help="Assert actual mode; omitted mode follows registered assignment or source metadata.")
    parser.add_argument("--codex-thread-id")
    parser.add_argument("--resolve-session", action="store_true", help="Resolve exact child session metadata and current handoff completion.")
    parser.add_argument("--agent-path", help="Exact canonical task path returned by the spawn tool.")
    parser.add_argument("--runtime-nickname")
    parser.add_argument("--lane-id")
    parser.add_argument("--obligation-id", help="Stable responsibility shared by an assignment and its replacement.")
    parser.add_argument("--wave", type=int)
    parser.add_argument("--critical", action="store_true", default=None, help="Assert critical assignment; omitted value follows planned metadata.")
    parser.add_argument("--completion-turn-id")
    parser.add_argument("--verification-json", help="Verification object as JSON or a local file path.")
    parser.add_argument("--reason", help="Reason for a negative prepared conclusion.")
    parser.add_argument("--unverified", action="append", default=[], help="Unperformed check in a negative prepared conclusion.")
    parser.add_argument("--context-classification-json", help="Context classification conclusion JSON or private capture file.")
    parser.add_argument("--review-subject", choices=["requirements-plan", "product-result"])
    parser.add_argument("--review-source", action="append", default=[])
    parser.add_argument("--review-input-operation-id")
    parser.add_argument("--readiness-gates-file", help="Typed preliminary QA gate observations.")
    args = parser.parse_args(argv)
    if args.prepare_review_input:
        supplied = {arg.split("=", 1)[0] for arg in (argv if argv is not None else sys.argv[1:]) if arg.startswith("--")}
        require(not supplied - {"--run-dir", "--prepare-review-input", "--lane-id", "--review-subject", "--review-source", "--operation-id"}, "prepare-review-input accepts only declared input fields")
        require(args.review_subject == "requirements-plan", "prepare-review-input requires requirements-plan subject")
        from journal_io import prepare_review_input
        print(encode_json(prepare_review_input(Path(args.run_dir).expanduser().resolve(), args.lane_id, args.review_source, identifier=args.operation_id)))
        return 0
    del args.prepare_review_input
    require(not args.review_source, "review-source requires prepare-review-input")
    if not args.prepare_conclusion:
        require(not args.readiness_gates_file, "readiness-gates-file requires prepare-conclusion")
    wave_supplied = args.wave is not None
    critical_supplied = args.critical is not None
    if not args.correction_file:
        snapshot = JournalSnapshot.open(Path(args.run_dir).expanduser().resolve())
        summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
        registrations = [(row, mode) for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane"))
                         for row in summary.get(group, []) if args.lane_id and row.get("lane_id") == args.lane_id]
        if len(registrations) == 1 and registrations[0][1] == "role-lane":
            args.codex_thread_id = args.codex_thread_id or registrations[0][0].get("codex_thread_id")
        if args.execution_mode is None:
            if len(registrations) == 1:
                args.execution_mode = registrations[0][1]
            elif args.codex_thread_id:
                from verification_evidence import own_metadata, child_spawn
                source = session_source or CodexSessionSource()
                try:
                    _, metadata = own_metadata(source.read(args.codex_thread_id), args.codex_thread_id)
                except EvidenceError as exc:
                    raise SystemExit(str(exc)) from exc
                args.execution_mode = "subagent" if child_spawn(metadata) else "role-lane"
            else:
                args.execution_mode = "subagent"
        if args.lane_id:
            events = [event for event in snapshot.timeline() if event.get("lane_id") == args.lane_id]
            if events:
                if events[0].get("stable_agent_name") != args.role:
                    args.stable_agent_name = args.stable_agent_name or events[0].get("stable_agent_name")
                if events[0].get("stable_agent_slug") != safe_path_segment(args.role):
                    args.stable_agent_slug = args.stable_agent_slug or events[0].get("stable_agent_slug")
    for field in ("verification_json", "context_classification_json"):
        raw = getattr(args, field)
        if raw is None:
            continue
        try:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                if raw.lstrip().startswith(("{", "[", '"')):
                    raise
                raw = capture_file(Path(raw).expanduser()).decode()
                value = json.loads(raw)
            require(isinstance(value, dict), "--" + field.replace("_", "-") + " must be a JSON object")
            setattr(args, field, raw)
        except (EvidenceError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.correction_file:
        supplied = {arg.split('=', 1)[0] for arg in (argv if argv is not None else sys.argv[1:]) if arg.startswith('--')}
        if supplied - {'--run-dir', '--operation-id', '--correction-file'} or not args.operation_id:
            parser.error('correction-file requires only run-dir and saved operation-id')
        try:
            run_dir = Path(args.run_dir).expanduser().resolve()
            correction = json.loads(capture_file(Path(args.correction_file).expanduser()).decode())
            print('receipt: ' + encode_json({'operation_id': args.operation_id, **record_mode_correction(
                run_dir, correction, identifier=args.operation_id, session_source=session_source)}))
        except (EvidenceError, OSError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if args.sections_file and (not args.prepare_conclusion or args.summary is None):
        parser.error("--sections-file requires --prepare-conclusion with --summary")
    if args.prepare_conclusion or args.render_final:
        allowed = {"--run-dir", "--render-final", "--operation-id"} if args.render_final else {"--run-dir", "--prepare-conclusion", "--role", "--lane-id", "--status", "--artifact", "--reason", "--unverified", "--context-classification-json", "--summary", "--sections-file", "--operation-id", "--readiness-gates-file"}
        supplied = {arg.split("=", 1)[0] for arg in (argv if argv is not None else sys.argv[1:]) if arg.startswith("--")}
        if supplied - allowed:
            parser.error("incompatible parameters: " + ", ".join(sorted(supplied - allowed)))
        if args.stage or args.verification_json or args.completion_turn_id or args.resolve_session or args.codex_thread_id or args.agent_path:
            parser.error("prepare-conclusion/render-final cannot register events or verification")
        if args.prepare_conclusion and (args.role not in {"qa-verifier", "reviewer", "reviewer.qa"} or not args.lane_id or args.status not in {"pass", "pass-with-risks", "fail", "blocked"}):
            parser.error("prepare-conclusion requires QA/reviewer role, lane-id and explicit status")
        if args.render_final and (args.role or args.status or args.lane_id or args.artifact):
            parser.error("render-final accepts only --run-dir")
        run_dir = Path(args.run_dir).expanduser().resolve()
        try:
            snapshot = JournalSnapshot.open(run_dir) if args.prepare_conclusion else ensure_result_contract(run_dir, session_source)
            if args.prepare_conclusion:
                print(encode_json(prepare_conclusion(run_dir, args, unique_paths([display_path(p, run_dir) for p in args.artifact]), session_source or CodexSessionSource(), snapshot=snapshot)))
            else:
                payload = {"command": "render-final"}
                identifier = operation_id(run_dir, "render-final", payload, identifier=args.operation_id)
                from journal_lifecycle import capture_validation, freeze_validation
                snapshot, captured = capture_validation(snapshot, session_source=session_source)
                inputs = snapshot.external_inputs
                def check_acceptance(current):
                    current = replace(current, external_inputs=inputs)
                    summary = json.loads(current.read_text("delegation-summary.json"))
                    lanes = json.loads(current.read_text("lane-map.json")) if current.exists("lane-map.json") else {}
                    errors = validate_verification(run_dir, summary, lanes, "ship", captured, snapshot=current)
                    require(not errors, "; ".join(errors))
                check_acceptance(snapshot)
                freeze_validation(snapshot, captured)
                def render(snapshot):
                    snapshot = replace(snapshot, external_inputs=inputs)
                    check_acceptance(snapshot)
                    data = json.loads(snapshot.read_text("delegation-summary.json"))
                    validate_summary_shape(data)
                    lanes = json.loads(snapshot.read_text("lane-map.json")) if snapshot.exists("lane-map.json") else {}
                    if data["verification"].get("task_kind") == "change":
                        require_workspace_or_historical(snapshot, data)
                    paths = changed_paths(run_dir, lanes, data["verification"], snapshot=snapshot)
                    text = render_final(snapshot.read_text("final.md"), data, paths,
                                        declared="run_changed_files" in data["verification"] or bool(paths),
                                        scope=delivery_scope(snapshot, data["verification"], summary=data))
                    return {"final.md": text}, {}
                receipt = transact(run_dir, identifier, payload, render, replay_guard=check_acceptance)
                print(f"rendered revision: {receipt['revision']}")
        except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if not all((args.role, args.status, args.stage, args.summary)):
        parser.error("ordinary registration requires --role, --status, --stage and --summary")
    if args.reason or args.unverified or args.context_classification_json:
        parser.error("reason/unverified/context-classification-json require --prepare-conclusion")
    # Prepare-only fields never enter ordinary recorder receipts/correction payloads.
    for key in ("reason", "unverified", "context_classification_json"):
        delattr(args, key)

    if args.resolve_session:
        if not args.agent_path or args.execution_mode != "subagent" or not args.lane_id or args.stage not in {"spawned", "handoff", "blocked", "fail"}:
            parser.error("--resolve-session requires --agent-path, --lane-id, subagent mode and stage spawned or handoff")
    elif args.agent_path:
        parser.error("--agent-path requires --resolve-session")
    if args.execution_mode == "subagent" and args.stage == "spawned" and not (args.codex_thread_id or args.resolve_session):
        raise SystemExit("spawned subagent events require --codex-thread-id or --resolve-session --agent-path")

    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run dir not found: {run_dir}")

    try:
        validate_assignment_event(vars(args))
        from journal_structure import validate_request
        validate_request("event", {"summary": args.summary, "status": args.status, "stage": args.stage, "role": args.role})
        snapshot = JournalSnapshot.open(run_dir)
        require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
        if args.operation_id and snapshot.operation_receipt(args.operation_id) is not None and (
            args.execution_mode == 'subagent' and not args.codex_thread_id and not args.lane_id
            and not args.resolve_session and args.role == 'orchestrator'
            and args.stage in {'verification-prepared', 'verification-ready'}
        ):
            request = json.loads(capture_file(run_dir / '.journal/requests' / args.operation_id))
            supplied = {k: v for k, v in vars(args).items() if k not in {'run_dir', 'operation_id', 'correction_file'}}
            for key in ("review_subject", "review_input_operation_id", "readiness_gates_file", "review_source"):
                if not supplied.get(key):
                    supplied.pop(key, None)
            supplied['critical'] = bool(args.critical)
            supplied['artifact'] = sorted(unique_paths([display_path(path, run_dir) for path in args.artifact]))
            require(supplied == {k: v for k, v in request['payload'].items() if k != 'captured_references'},
                    'operation ID payload conflict')
            operation_id(run_dir, 'record-agent-trace', request['payload'], identifier=args.operation_id)
            prior = snapshot.operation_receipt(args.operation_id)
            require(prior['payload_sha256'] == digest(encode_json(request['payload']).encode()), 'operation ID payload conflict')
            print('receipt: ' + encode_json({'operation_id': args.operation_id, **prior['result']}))
            return 0
        require_subagent_identity(snapshot, args, session_source or CodexSessionSource())
        lane_map, lane = assignment_lane(snapshot, args)
        if args.operation_id and not args.completion_turn_id and (args.stage in {"spawned", "implementation", "integration", "fix"} or args.lane_id and args.status == "active") and snapshot.operation_receipt(args.operation_id) is not None:
            envelope = json.loads(capture_file(run_dir / ".journal/requests" / args.operation_id))
            prior = envelope["payload"]
            supplied = {key: value for key, value in vars(args).items() if key not in {"run_dir", "operation_id", "correction_file"}}
            for key in ("review_subject", "review_input_operation_id", "readiness_gates_file", "review_source"):
                if not supplied.get(key):
                    supplied.pop(key, None)
            supplied["critical"] = bool(args.critical)
            if not wave_supplied and prior.get("wave") is None:
                supplied["wave"] = None
            if not critical_supplied and prior.get("critical") is False:
                supplied["critical"] = False
            supplied["artifact"] = sorted(unique_paths([display_path(path, run_dir) for path in args.artifact]))
            require(supplied == {key: value for key, value in prior.items() if key != "captured_references"}, "operation ID payload conflict")
            if "captured_references" in prior:
                require(args.verification_json is not None, "operation ID payload conflict")
                references = capture_external_references(run_dir, {"delegation-summary.json": encode_json({"verification": json.loads(args.verification_json)}).encode()}, snapshot.source_root, previous=snapshot.documents)
                require(prior["captured_references"] == {key: digest(raw) for key, raw in references.items() if raw is not None and key not in {"delegation-summary.json", "artifacts/source-references.json"}}, "operation ID payload conflict")
            operation_id(run_dir, envelope["command"], prior, identifier=args.operation_id)
            saved = snapshot.operation_receipt(args.operation_id)
            require(saved["payload_sha256"] == digest(encode_json(prior).encode()), "operation ID payload conflict")
            print("receipt: " + encode_json({"operation_id": args.operation_id, **saved["result"]}))
            return 0
        if args.verification_json:
            prepare_summary(run_dir, args, unique_paths([display_path(p, run_dir) for p in args.artifact]),
                            session_source or CodexSessionSource(), snapshot=snapshot)
        if args.lane_id and snapshot.exists("delegation-summary.json"):
            registrations = json.loads(snapshot.read_text("delegation-summary.json"))
            existing = next((record for group in ("subagents", "role_lanes") for record in registrations.get(group, [])
                             if record.get("lane_id") == args.lane_id), None)
            if existing and existing.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}:
                require(existing["status"] == args.status, "terminal outcome is immutable; use a new lane")
        if lane is not None and lane.get("type") in {"implementation", "integration"}:
            from journal_io import worker_readiness_snapshot
            snapshot, _ = worker_readiness_snapshot(snapshot, args, session_source or CodexSessionSource())
            lane_map = json.loads(snapshot.read_text("lane-map.json"))
            lane = next(row for row in lane_map["lanes"] if row["id"] == args.lane_id)
        summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
        verification = json.loads(args.verification_json) if args.verification_json else summary.get("verification", {})
        worker_start_preflight(snapshot, lane_map, lane, verification, args)
        snapshot = ensure_result_contract(run_dir, session_source)
    except (EvidenceError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    artifact_paths = unique_paths([display_path(path, run_dir) for path in args.artifact])
    from journal_lifecycle import capture_validation, freeze_validation
    snapshot, source = capture_validation(snapshot, session_source=session_source)
    inputs = snapshot.external_inputs
    try:
        captured = {}
        if args.verification_json:
            candidate = encode_json({"verification": json.loads(args.verification_json)}).encode()
            captured = capture_external_references(run_dir, {"delegation-summary.json": candidate},
                                                   snapshot.source_root, previous=snapshot.documents)
            captured.pop("delegation-summary.json")
        def with_captures(current):
            documents = dict(current.documents)
            documents.update(captured)
            index_path = "artifacts/source-references.json"
            if index_path in captured:
                index = json.loads(current.documents.get(index_path, b"{}"))
                index.update(json.loads(captured[index_path]))
                documents[index_path] = encode_json(index).encode()
            from journal_io import worker_readiness_snapshot
            prepared = replace(current, documents=MappingProxyType(documents), external_inputs=inputs)
            return worker_readiness_snapshot(prepared, args, source)[0]
        snapshot = with_captures(snapshot)
        require_subagent_identity(snapshot, args, source)
        preflight_summary, preflight_fields = prepare_summary(run_dir, args, artifact_paths, source, snapshot=snapshot)
        freeze_validation(snapshot, source)
        payload = {key: value for key, value in vars(args).items() if key not in {"run_dir", "operation_id", "correction_file"}}
        for key in ("review_subject", "review_input_operation_id", "readiness_gates_file", "review_source"):
            if not payload.get(key):
                payload.pop(key, None)
        payload["critical"] = bool(args.critical)
        if args.operation_id and snapshot.operation_receipt(args.operation_id) is not None:
            prior = json.loads(capture_file(run_dir / ".journal/requests" / args.operation_id))["payload"]
            if not wave_supplied and prior.get("wave") is None:
                payload["wave"] = None
            if not critical_supplied and prior.get("critical") is False:
                payload["critical"] = False
        payload["artifact"] = sorted(artifact_paths)
        if args.completion_turn_id:
            assignment = next(record for record in preflight_summary["subagents"] if record["lane_id"] == args.lane_id)
            args.codex_thread_id = assignment["codex_thread_id"]
            payload = {"stage": args.stage, "role": args.role, "lane_id": args.lane_id,
                       "codex_thread_id": args.codex_thread_id, "completion_turn_id": args.completion_turn_id,
                       "handoff": preflight_fields["handoff"], "handoff_sha256": preflight_fields["handoff_sha256"],
                       "artifacts": {path: sha256(reference_bytes(run_dir, {"path": path}, snapshot=snapshot))
                                     for path in sorted(artifact_paths)}}
            payload.update({key: preflight_fields[key] for key in ("reviewed_result_hash", "source_completion_sha256")
                            if key in preflight_fields})
        if captured:
            payload["captured_references"] = {name: digest(data) for name, data in captured.items()
                                               if data is not None and name != "artifacts/source-references.json"}
        identifier = args.operation_id
        if identifier is None and args.completion_turn_id:
            identifier = digest(encode_json([snapshot.run_uuid, args.codex_thread_id, args.lane_id,
                                             args.completion_turn_id, args.stage]).encode())
        def mutation(current):
            prepared = with_captures(current)
            documents, result = record_event(run_dir, args, artifact_paths, source, prepared)
            additions = {name: prepared.documents[name] for name in captured}
            return ({**additions, **documents} if documents else {}), result
        def replay_guard(current):
            prepare_summary(run_dir, args, artifact_paths, source, snapshot=with_captures(current))
        from journal_io import write_entry
        receipt = write_entry(run_dir, "event", {"summary": args.summary, "status": args.status,
            "stage": args.stage, "role": args.role}, identifier=identifier, payload=payload,
            mutation=mutation, replay_guard=replay_guard,
            completion={"source": source, "lane_id": args.lane_id, "external_inputs": inputs} if args.completion_turn_id else None)
        identifier = receipt["operation_id"]
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    if receipt.get("unchanged") or preflight_fields.get("unchanged"):
        print("unchanged: existing completion retained")
    if receipt.get("result_hash"):
        print(f"result_hash: {receipt['result_hash']}")
    if args.resolve_session:
        fields = ("codex_thread_id", "root_thread_id", "agent_path", "observed_spawn_at", "session_meta_event",
                  "task_started_event", "completion_turn_id", "task_complete_event")
        resolved = receipt.get("completion_fields", preflight_fields)
        print("resolver: " + encode_json({key: resolved[key] for key in fields if key in resolved}))
    print("receipt: " + encode_json({"operation_id": identifier, **receipt}))
    return 0




if __name__ == "__main__":
    raise SystemExit(main())
