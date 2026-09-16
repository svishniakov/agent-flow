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
                        validate_assignment_event, _transact_completion)
from journal_io import (now_iso, encode_json, validate_append,
                        render_final, safe_path_segment, display_path, unique_paths)

from verification_evidence import (
    CodexSessionSource, EvidenceError, completed_turn, empty_verification,
    evidence_path, reference_bytes, result_hash, sha256, validate_verification,
    resolve_completion, completion_follows, validate_summary_shape,
    changed_paths, require, accepted_record, require_own_reviewer_handoff,
    CapturedSource, ensure_result_contract, require_workspace_or_historical,
    historical_result_hash,
)
from task_workspace import registered_workspace


def prepare_summary(run_dir: Path, args, artifact_paths: list[str], source, *, snapshot=None) -> tuple[dict | None, dict]:
    """Validate everything before writing any trace, directory or index."""
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    if not (args.verification_json or args.completion_turn_id or args.lane_id or args.resolve_session):
        return None, {}
    path = run_dir / "delegation-summary.json"
    lane_path = run_dir / "lane-map.json"
    lane_map = json.loads(snapshot.read_text(lane_path)) if snapshot.exists(lane_path) else {}
    data = json.loads(snapshot.read_text(path)) if snapshot.exists(path) else {
        "version": 1, "subagents_used": False, "role_lanes_used": False,
        "subagents": [], "role_lanes": [], "notes": "Recorded agent assignments.",
        "verification": empty_verification(),
    }
    validate_summary_shape(data)
    if args.verification_json:
        raw = args.verification_json
        if not raw.lstrip().startswith("{"):
            raw = Path(raw).expanduser().read_text()
        verification = json.loads(raw)
        if not isinstance(verification, dict):
            raise EvidenceError("--verification-json must contain the verification object")
        data["verification"] = verification
    verification = data.get("verification")
    if not isinstance(verification, dict):
        raise EvidenceError("verification must be an object")
    for field in ("initial_snapshot", "task_scope"):
        ref = verification.get(field)
        if isinstance(ref, dict) and "sha256" not in ref:
            ref["sha256"] = sha256(reference_bytes(run_dir, ref, snapshot=snapshot))
    if verification.get("task_kind") == "change":
        workspace = registered_workspace(snapshot)
        if workspace is not None and "seal" in workspace:
            verification["result_files"] = workspace["seal"]["changed_paths"]
            verification["run_changed_files"] = workspace["seal"]["changed_paths"]
        completing = bool(args.completion_turn_id or args.resolve_session and args.stage == "handoff")
        if completing and workspace is None and snapshot.operation_receipt("result-contract-v2") is not None:
            verification["result_hash"] = historical_result_hash(run_dir, verification, snapshot=snapshot)
        else:
            verification["result_hash"] = result_hash(run_dir, verification, snapshot=snapshot, require_ready=completing)
        selected = [r for r in data["subagents"] if r.get("lane_id") in (verification.get("qa"), verification.get("reviewer"))]
        if any(r.get("reviewed_result_hash") != verification["result_hash"] for r in selected):
            verification["qa"] = verification["reviewer"] = None

    extra = {}
    if args.execution_mode == "subagent" and args.lane_id:
        existing = next((r for r in data["subagents"] if r.get("lane_id") == args.lane_id), None)
        record = dict(existing or {})
        thread_id = args.codex_thread_id or record.get("codex_thread_id")
        role = "reviewer" if args.role == "reviewer.qa" else args.role
        if args.resolve_session:
            extra.update(source.resolve_session(args.agent_path, verification.get("root_thread_id"), role,
                                                thread_id=thread_id))
            thread_id = extra["codex_thread_id"]
            args.codex_thread_id = thread_id
            record.update({k: extra[k] for k in ("agent_path", "observed_spawn_at", "session_meta_event", "task_started_event")})
        if not thread_id:
            raise EvidenceError("subagent assignment requires --codex-thread-id")
        if existing and (existing.get("codex_thread_id") != thread_id or existing.get("role") != args.role):
            raise EvidenceError("assignment identity changed; use a new lane_id")
        record.update(lane_id=args.lane_id, role=args.role, codex_thread_id=thread_id,
                      trace=f"agents/{safe_path_segment(args.role)}/trace.jsonl")
        if snapshot.storage_version == 2:
            obligation_id = getattr(args, "obligation_id", None) or args.lane_id
            record.setdefault("obligation", {"id": obligation_id, "required": True, "state": "current"})
            require(not getattr(args, "obligation_id", None) or record["obligation"]["id"] == obligation_id,
                    "obligation identity changed; use a new lane")
            if existing and existing.get("status") in {"pass", "pass-with-risks", "fail", "blocked"}:
                require(existing["status"] == args.status, "terminal outcome is immutable; use a new lane")
            record["status"] = args.status
            if args.stage == "handoff" and args.status in {"pass", "pass-with-risks"}:
                require(bool(artifact_paths), "accepted handoff requires artifact")
                require(bool(snapshot.read_bytes(artifact_paths[0]).strip()), "accepted handoff artifact missing or empty")
        if args.role == "qa-verifier" and args.stage == "spawned":
            verification["qa"] = verification["reviewer"] = None
        elif args.role in {"reviewer", "reviewer.qa"} and args.stage == "spawned":
            verification["reviewer"] = None
        if args.resolve_session and args.stage == "handoff" and not args.completion_turn_id:
            args.completion_turn_id = resolve_completion(source, thread_id, verification.get("root_thread_id"),
                                                         role, verification.get("result_hash"), artifact_paths)
        if args.completion_turn_id:
            if args.stage != "handoff":
                raise EvidenceError("--completion-turn-id requires stage=handoff")
            role = "reviewer" if args.role == "reviewer.qa" else args.role
            if role not in {"qa-verifier", "reviewer"}:
                raise EvidenceError("completion evidence is limited to QA and reviewer")
            completion = completed_turn(source, thread_id, args.completion_turn_id, verification.get("root_thread_id"), role)
            answer = completion["answer"]
            if not isinstance(answer.get("verdict"), str) or answer["verdict"] not in {"passed", "pass-with-risks"} or args.status not in {"pass", "pass-with-risks"}:
                raise EvidenceError("successful handoff requires accepted source verdict and status")
            handoff = answer.get("handoff")
            if handoff not in artifact_paths:
                raise EvidenceError("source handoff must be included in --artifact; pass its exact path relative to the run directory")
            if answer.get("reviewed_result_hash") != verification.get("result_hash"):
                raise EvidenceError("source reviewed_result_hash does not match current result; recompute with --verification-json and obtain acceptance of that current hash")
            if sha256(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot)) != answer.get("handoff_sha256"):
                raise EvidenceError("source handoff_sha256 mismatch")
            extra.update({k: answer[k] for k in ("reviewed_result_hash", "handoff", "handoff_sha256")})
            extra["completion_turn_id"] = args.completion_turn_id
            extra.update({k: completion[k] for k in ("session_meta_event", "task_started_event", "task_complete_event")})
            record.update(extra)
            record["evidence"] = [{"path": p, "sha256": sha256(reference_bytes(run_dir, {"path": p}, snapshot=snapshot))}
                                  for p in artifact_paths if p != handoff]
            if not record["evidence"]:
                raise EvidenceError("QA/reviewer handoff requires at least one --artifact evidence reference")
            handoff_text = reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot).decode()
            for ref in record["evidence"]:
                require(ref["path"] in handoff_text and ref["sha256"] in handoff_text,
                        "evidence reference and sha256 must appear in source-bound handoff")
            require(thread_id not in [verification.get("root_thread_id"), *verification.get("author_thread_ids", [])],
                    "QA, reviewer, root and authors must be distinct")
            if role == "reviewer":
                qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), {})
                require_own_reviewer_handoff(run_dir, handoff, qa.get("handoff"), snapshot=snapshot)
                if answer.get("qa_handoff_sha256") != qa.get("handoff_sha256") or not qa.get("handoff_sha256"):
                    raise EvidenceError("source qa_handoff_sha256 mismatch")
                qa_completion = accepted_record(run_dir, qa, verification, "qa-verifier", source, snapshot=snapshot)
                if not completion_follows(completion, qa_completion):
                    raise EvidenceError("reviewer acceptance must follow QA completion")
                record["qa_handoff_sha256"] = qa["handoff_sha256"]
            fields = ("role", "lane_id", "codex_thread_id", "completion_turn_id", "reviewed_result_hash", "handoff", "handoff_sha256")
            same = existing and all(existing.get(k) == record.get(k) for k in fields)
            refs_key = lambda refs: sorted(encode_json(r) for r in refs)
            same = same and refs_key(existing.get("evidence", [])) == refs_key(record["evidence"])
            selected_key = "reviewer" if role == "reviewer" else "qa"
            if same and verification.get(selected_key) == args.lane_id:
                errors = validate_verification(run_dir, data, lane_map, "ship" if verification.get("reviewer") else "pending", source, snapshot=snapshot)
                if errors:
                    raise EvidenceError("; ".join(errors))
                extra["unchanged"] = True
                return data, extra
            if role == "qa-verifier":
                verification["reviewer"] = None
            verification["reviewer" if role == "reviewer" else "qa"] = args.lane_id
        elif args.stage == "handoff" and artifact_paths:
            record["handoff"] = artifact_paths[0]
        if existing:
            data["subagents"][data["subagents"].index(existing)] = record
        else:
            data["subagents"].append(record)
        data["subagents_used"] = True
        if args.completion_turn_id:
            require_workspace_or_historical(snapshot, data)
    elif args.execution_mode == "role-lane" and args.lane_id and snapshot.storage_version == 2:
        existing = next((r for r in data["role_lanes"] if r.get("lane_id") == args.lane_id), None)
        record = dict(existing or {})
        require(existing is None or existing.get("role") == args.role, "assignment identity changed; use a new lane_id")
        obligation_id = getattr(args, "obligation_id", None) or args.lane_id
        record.setdefault("obligation", {"id": obligation_id, "required": True, "state": "current"})
        require(not getattr(args, "obligation_id", None) or record["obligation"]["id"] == obligation_id,
                "obligation identity changed; use a new lane")
        record.update(lane_id=args.lane_id, role=args.role, status=args.status,
                      trace=f"agents/{safe_path_segment(args.role)}/trace.jsonl")
        record.setdefault("reason", args.summary)
        if args.stage == "handoff":
            require(bool(artifact_paths), "accepted handoff requires artifact")
            require(bool(snapshot.read_bytes(artifact_paths[0]).strip()), "handoff artifact missing or empty")
            record["handoff"] = artifact_paths[0]
        if existing:
            data["role_lanes"][data["role_lanes"].index(existing)] = record
        else:
            data["role_lanes"].append(record)
        data["role_lanes_used"] = True
    elif args.completion_turn_id:
        raise EvidenceError("--completion-turn-id requires subagent and --lane-id")
    errors = validate_verification(run_dir, data, lane_map, "pending", source, snapshot=snapshot)
    if errors:
        raise EvidenceError("; ".join(errors))
    return data, extra


def prepare_conclusion(run_dir, args, artifacts, source, *, snapshot=None):
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
    require(len(artifacts) >= 2, "prepare-conclusion requires handoff first and evidence artifacts")
    if args.role in {"reviewer", "reviewer.qa"}:
        qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), None)
        require(qa is not None, "reviewer requires current accepted QA")
        require_own_reviewer_handoff(run_dir, artifacts[0], qa.get("handoff"), snapshot=snapshot)
    digest = result_hash(run_dir, verification, snapshot=snapshot) if verification.get("task_kind") == "change" else verification.get("result_hash")
    require(digest == verification.get("result_hash") and isinstance(digest, str), "current result_hash is stale or missing")
    handoff = artifacts[0]
    text = reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot).decode()
    for path in artifacts[1:]:
        sha = sha256(reference_bytes(run_dir, {"path": path}, snapshot=snapshot))
        require(path in text and sha in text, "evidence reference and sha256 must appear in handoff")
    answer = {"verdict": "passed" if args.status == "pass" else args.status,
              "reviewed_result_hash": digest, "handoff": handoff,
              "handoff_sha256": sha256(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot))}
    if args.role in {"reviewer", "reviewer.qa"}:
        qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), None)
        require(qa is not None, "reviewer requires current accepted QA")
        require_own_reviewer_handoff(run_dir, handoff, qa.get("handoff"), snapshot=snapshot)
        completion = accepted_record(run_dir, qa, verification, "qa-verifier", source, snapshot=snapshot)
        answer["qa_handoff_sha256"] = completion["handoff_sha256"]
    return answer



def require_subagent_identity(snapshot, args, source):
    if args.execution_mode != 'subagent':
        return
    from verification_evidence import session_metadata
    data = json.loads(snapshot.read_text('delegation-summary.json')) if snapshot.exists('delegation-summary.json') else {}
    verification = data.get('verification', {})
    root = verification.get('root_thread_id')
    thread = args.codex_thread_id
    if args.resolve_session:
        thread = source.resolve_session(args.agent_path, root, args.role, thread_id=thread)['codex_thread_id']
    if not thread and args.lane_id:
        candidates = [r for r in data.get('subagents', []) if r.get('lane_id') == args.lane_id and r.get('role') == args.role]
        require(len(candidates) == 1, 'subagent lane identity missing or ambiguous; root metadata requires explicit --execution-mode role-lane')
        thread = candidates[0].get('codex_thread_id')
    require(thread and thread != root, 'subagent identity required; root metadata requires explicit --execution-mode role-lane')
    session_metadata(source.read(thread), thread, root, 'reviewer' if args.role == 'reviewer.qa' else args.role, source=source)
    if args.stage != 'spawned':
        trace = f'agents/{safe_path_segment(args.role)}/trace.jsonl'
        events = [json.loads(line) for line in snapshot.read_text(trace).splitlines()] if snapshot.exists(trace) else []
        require(any(e.get('stage') == 'spawned' and e.get('codex_thread_id') == thread
                    and (not args.lane_id or e.get('lane_id') == args.lane_id) for e in events),
                'subagent event requires its own registered spawn')
    args.codex_thread_id = thread


def record_mode_correction(run_dir, correction, *, identifier, session_source=None, barrier=None):
    from journal_lifecycle import capture_validation, freeze_validation
    from verification_evidence import verify_trace_mode_correction, effective_trace_modes
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, 'flat legacy requires explicit import before trace mode correction')
    require(isinstance(correction, dict) and identifier == correction.get('operation_id'), 'mode correction operation ID mismatch')
    payload = {'command': 'trace-mode-correction', 'correction': correction}
    # Persist the exact request before any transaction; receipt replay precedes mutable guards.
    identifier = operation_id(run_dir, 'trace-mode-correction', payload, identifier=identifier)
    prior = snapshot.operation_receipt(identifier)
    if prior is not None:
        require(prior['payload_sha256'] == digest(encode_json(payload).encode()), 'operation ID payload conflict')
        return prior['result']
    require(not snapshot.closed, 'journal generation is closed')
    require(snapshot.storage_version == 2, 'trace mode correction requires storage version 2')
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
    raw = (encode_json(event)+'\n').encode()
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare-conclusion", action="store_true")
    modes.add_argument("--render-final", action="store_true")
    modes.add_argument("--correction-file", help="Captured root trace mode correction JSON.")
    parser.add_argument("--next-step", default="")
    parser.add_argument("--stable-agent-name")
    parser.add_argument("--stable-agent-slug")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--execution-mode", choices=["subagent", "role-lane"], default="subagent")
    parser.add_argument("--codex-thread-id")
    parser.add_argument("--resolve-session", action="store_true", help="Resolve exact child session metadata and current handoff completion.")
    parser.add_argument("--agent-path", help="Exact canonical task path returned by the spawn tool.")
    parser.add_argument("--runtime-nickname")
    parser.add_argument("--lane-id")
    parser.add_argument("--obligation-id", help="Stable responsibility shared by an assignment and its replacement.")
    parser.add_argument("--wave", type=int)
    parser.add_argument("--critical", action="store_true")
    parser.add_argument("--completion-turn-id")
    parser.add_argument("--verification-json", help="Verification object as JSON or a local file path.")
    args = parser.parse_args(argv)
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
    if args.prepare_conclusion or args.render_final:
        allowed = {"--run-dir", "--render-final", "--operation-id"} if args.render_final else {"--run-dir", "--prepare-conclusion", "--role", "--lane-id", "--status", "--artifact"}
        supplied = {arg.split("=", 1)[0] for arg in (argv if argv is not None else sys.argv[1:]) if arg.startswith("--")}
        if supplied - allowed:
            parser.error("incompatible parameters: " + ", ".join(sorted(supplied - allowed)))
        if args.stage or args.summary or args.verification_json or args.completion_turn_id or args.resolve_session or args.codex_thread_id or args.agent_path:
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
                                        declared="run_changed_files" in data["verification"] or bool(paths))
                    return {"final.md": text}, {}
                receipt = transact(run_dir, identifier, payload, render, replay_guard=check_acceptance)
                print(f"rendered revision: {receipt['revision']}")
        except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from exc
        return 0
    if not all((args.role, args.status, args.stage, args.summary)):
        parser.error("ordinary registration requires --role, --status, --stage and --summary")

    if args.resolve_session:
        if not args.agent_path or args.execution_mode != "subagent" or not args.lane_id or args.stage not in {"spawned", "handoff"}:
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
        snapshot = JournalSnapshot.open(run_dir)
        require(snapshot.durable, 'flat legacy requires explicit import before trace registration')
        if args.operation_id and snapshot.operation_receipt(args.operation_id) is not None and (
            args.execution_mode == 'subagent' and not args.codex_thread_id and not args.lane_id
            and not args.resolve_session and args.role == 'orchestrator'
            and args.stage in {'verification-prepared', 'verification-ready'}
        ):
            request = json.loads(capture_file(run_dir / '.journal/requests' / args.operation_id))
            supplied = {k: v for k, v in vars(args).items() if k not in {'run_dir', 'operation_id', 'correction_file'}}
            if args.verification_json and not args.verification_json.lstrip().startswith('{'):
                supplied['verification_json'] = capture_file(Path(args.verification_json).expanduser()).decode()
            supplied['artifact'] = sorted(unique_paths([display_path(path, run_dir) for path in args.artifact]))
            require(supplied == {k: v for k, v in request['payload'].items() if k != 'captured_references'},
                    'operation ID payload conflict')
            operation_id(run_dir, 'record-agent-trace', request['payload'], identifier=args.operation_id)
            prior = snapshot.operation_receipt(args.operation_id)
            require(prior['payload_sha256'] == digest(encode_json(request['payload']).encode()), 'operation ID payload conflict')
            print('receipt: ' + encode_json({'operation_id': args.operation_id, **prior['result']}))
            return 0
        require_subagent_identity(snapshot, args, session_source or CodexSessionSource())
        snapshot = ensure_result_contract(run_dir, session_source)
    except (EvidenceError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    artifact_paths = unique_paths([display_path(path, run_dir) for path in args.artifact])
    from journal_lifecycle import capture_validation, freeze_validation
    snapshot, source = capture_validation(snapshot, session_source=session_source)
    inputs = snapshot.external_inputs
    try:
        if args.verification_json and not args.verification_json.lstrip().startswith("{"):
            args.verification_json = capture_file(Path(args.verification_json).expanduser()).decode()
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
            return replace(current, documents=MappingProxyType(documents), external_inputs=inputs)
        snapshot = with_captures(snapshot)
        require_subagent_identity(snapshot, args, source)
        preflight_summary, preflight_fields = prepare_summary(run_dir, args, artifact_paths, source, snapshot=snapshot)
        freeze_validation(snapshot, source)
        payload = {key: value for key, value in vars(args).items() if key not in {"run_dir", "operation_id", "correction_file"}}
        payload["artifact"] = sorted(artifact_paths)
        if args.completion_turn_id:
            assignment = next(record for record in preflight_summary["subagents"] if record["lane_id"] == args.lane_id)
            args.codex_thread_id = assignment["codex_thread_id"]
            payload = {"stage": args.stage, "role": args.role, "lane_id": args.lane_id,
                       "codex_thread_id": args.codex_thread_id, "completion_turn_id": args.completion_turn_id,
                       "reviewed_result_hash": preflight_fields["reviewed_result_hash"],
                       "handoff": preflight_fields["handoff"], "handoff_sha256": preflight_fields["handoff_sha256"],
                       "artifacts": {path: sha256(reference_bytes(run_dir, {"path": path}, snapshot=snapshot))
                                     for path in sorted(artifact_paths)}}
        if captured:
            payload["captured_references"] = {name: digest(data) for name, data in captured.items()
                                               if data is not None and name != "artifacts/source-references.json"}
        identifier = args.operation_id
        if identifier is None and args.completion_turn_id:
            identifier = digest(encode_json([snapshot.run_uuid, args.codex_thread_id, args.lane_id,
                                             args.completion_turn_id, args.stage]).encode())
        identifier = operation_id(run_dir, "record-agent-trace", payload, identifier=identifier)
        def mutation(current):
            prepared = with_captures(current)
            documents, result = record_event(run_dir, args, artifact_paths, source, prepared)
            additions = {name: prepared.documents[name] for name in captured}
            return ({**additions, **documents} if documents else {}), result
        def replay_guard(current):
            prepare_summary(run_dir, args, artifact_paths, source, snapshot=with_captures(current))
        if args.completion_turn_id:
            receipt = _transact_completion(run_dir, identifier, payload, mutation, replay_guard=replay_guard,
                                           source=source, lane_id=args.lane_id, external_inputs=inputs)
        else:
            receipt = transact(run_dir, identifier, payload, mutation, replay_guard=replay_guard)
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



def record_event(run_dir, args, artifact_paths, source, snapshot):
    validate_assignment_event(vars(args))
    require_subagent_identity(snapshot, args, source)
    role_segment = safe_path_segment(args.role)
    stable_agent_name = args.stable_agent_name or args.role
    stable_agent_slug = args.stable_agent_slug or role_segment
    timestamp = now_iso()

    timeline_path = run_dir / "timeline.jsonl"
    agent_dir = run_dir / "agents" / role_segment
    agent_trace_path = agent_dir / "trace.jsonl"
    agent_artifact_dir = run_dir / "artifacts" / "agents" / role_segment
    artifacts_path = run_dir / "artifacts.json"
    require(not snapshot.exists(agent_trace_path) or snapshot.is_file(agent_trace_path),
            f"write target must be a file: {agent_trace_path}")

    try:
        for path in artifact_paths:
            evidence_path(run_dir, path, snapshot=snapshot)
        summary_data, completion_fields = prepare_summary(run_dir, args, artifact_paths, source, snapshot=snapshot)
        if completion_fields.pop("unchanged", False):
            return {}, {"unchanged": True}
        load_artifact_index(artifacts_path, snapshot=snapshot)
        if args.stage == "behavior-input-prepared":
            if len(artifact_paths) != 1:
                raise EvidenceError("behavior-input-prepared requires exactly one --artifact input")
            completion_fields["input_sha256"] = sha256(reference_bytes(run_dir, {"path": artifact_paths[0]}, snapshot=snapshot))
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    event = {
        "timestamp": timestamp,
        "stage": args.stage,
        "role": args.role,
        "stable_agent_name": stable_agent_name,
        "stable_agent_slug": stable_agent_slug,
        "status": args.status,
        "summary": args.summary,
        "artifacts": artifact_paths,
        "next_step": args.next_step,
        "execution_mode": args.execution_mode,
        "agent_trace": agent_trace_path.relative_to(run_dir).as_posix(),
        "agent_artifact_dir": agent_artifact_dir.relative_to(run_dir).as_posix(),
        **completion_fields,
    }
    if args.codex_thread_id:
        event["codex_thread_id"] = args.codex_thread_id
    if args.runtime_nickname:
        event["runtime_nickname"] = args.runtime_nickname
    if args.lane_id:
        event["lane_id"] = args.lane_id
    if args.wave is not None:
        event["wave"] = args.wave
    if args.critical:
        event["critical"] = args.critical

    try:
        validate_append(timeline_path, event, snapshot=snapshot)
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    indexed_count, artifact_data = upsert_artifacts(
        artifacts_path,
        artifact_paths,
        role=args.role,
        execution_mode=args.execution_mode,
        stable_agent_name=stable_agent_name,
        stable_agent_slug=stable_agent_slug,
        timestamp=timestamp,
        lane_id=args.lane_id,
        wave=args.wave,
        critical=args.critical,
        snapshot=snapshot,
    )

    encoded_event = encode_json(event) + "\n"
    contents = {timeline_path: encoded_event, agent_trace_path: encoded_event}
    if artifact_data is not None:
        contents[artifacts_path] = encode_json(artifact_data, pretty=True) + "\n"
    if summary_data is not None:
        contents[run_dir / "delegation-summary.json"] = encode_json(summary_data, pretty=True) + "\n"
    documents = {}
    for path, text in contents.items():
        key = snapshot.key(path)
        if path in (timeline_path, agent_trace_path) and snapshot.exists(path):
            text = snapshot.read_text(path) + text
        documents[key] = text
    documents[snapshot.key(agent_artifact_dir)] = None
    result = {"indexed_artifacts": indexed_count, "completion_fields": completion_fields}
    if summary_data is not None:
        result["result_hash"] = summary_data["verification"].get("result_hash")
    return documents, result


if __name__ == "__main__":
    raise SystemExit(main())
