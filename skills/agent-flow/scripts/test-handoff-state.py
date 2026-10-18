#!/usr/bin/env python3
"""Fixture tests for writer-projected handoff_state and the unconditional gate."""

from __future__ import annotations

import json
import subprocess
import sys
import importlib.util
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import journal_io

TEMP_ROOT = "/private/tmp" if sys.platform == "darwin" else "/tmp"
from journal_io import JournalSnapshot, initialize_journal, update_handoff_state


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record-agent-trace.py"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load("handoff_state_base_fixtures", "test-verification-evidence.py")
recorder = load("handoff_state_recorder", "record-agent-trace.py")
validator = load("handoff_state_validator", "validate-run.py")


def worker_lane(lane_id, **overrides):
    lane = {"id": lane_id, "type": "implementation", "role": "python-worker", "wave": 1, "critical": True,
            "execution_mode": "role-lane", "status": "planned", "handoff": f"handoffs/{lane_id}.md",
            "unknown_lane": ["keep"]}
    lane.update(overrides)
    return lane


def write_run(run_dir, lanes, extra_records=(), **extra):
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 2, "budget": "standard", "unknown_root": {"preserve": True}, "lanes": lanes, **extra}
    records = [{"lane_id": lane["id"], "role": lane["role"], "status": lane["status"],
                "trace": "agents/python-worker/trace.jsonl"} for lane in lanes]
    records.extend(extra_records)
    summary = {"version": 1, "subagents_used": False, "role_lanes_used": bool(records), "notes": "Handoff state fixture",
               "subagents": [], "role_lanes": records, "verification": base.empty_verification()}
    documents = {"lane-map.json": (json.dumps(data, indent=2) + "\n").encode(),
                 "delegation-summary.json": json.dumps(summary).encode()}
    initialize_journal(run_dir.resolve(), documents, source_root=run_dir.parent.resolve())


def read_lane_map(run_dir):
    return json.loads(JournalSnapshot.open(run_dir.resolve()).read_text("lane-map.json"))


def lane_state(run_dir, lane_id):
    data = read_lane_map(run_dir)
    if data.get("unknown_root") != {"preserve": True}:
        raise AssertionError("writer must preserve unknown root fields")
    lane = next(lane for lane in data["lanes"] if lane["id"] == lane_id)
    if lane.get("unknown_lane") != ["keep"]:
        raise AssertionError("writer must preserve unknown lane fields")
    return lane, lane.get("handoff_state")


def record_event(run_dir, lane_id, status, *extra, operation=None, ok=True):
    args = ["--run-dir", str(run_dir), "--role", "python-worker", "--execution-mode", "role-lane",
            "--lane-id", lane_id, "--stage", "checks", "--status", status,
            "--summary", "Fixture assignment event", *extra]
    if operation:
        args += ["--operation-id", operation]
    with redirect_stdout(StringIO()):
        try:
            recorder.main(args)
        except SystemExit as exc:
            if ok:
                raise AssertionError(f"recorder rejected: {exc}")
            return str(exc)
    if not ok:
        raise AssertionError("recorder must reject this call")


def projection_fixtures():
    with tempfile.TemporaryDirectory(prefix="agent-flow-handoff-state-") as temp_dir:
        root = Path(temp_dir).resolve()

        create_run = root / "create"
        write_run(create_run, [worker_lane("worker-a")])
        record_event(create_run, "worker-a", "planned", "--from", "architecture-contract",
                     "--task", "worker-a-implementation")
        lane, state = lane_state(create_run, "worker-a")
        if not isinstance(state, dict) or state.get("version") != 1:
            raise AssertionError("writer must project handoff_state version 1")
        if state.get("mode") != "task":
            raise AssertionError("handoff_state.mode must default to task")
        if state.get("status") != "queued":
            raise AssertionError(f"handoff_state.status mismatch: {state.get('status')!r}")
        if state.get("from") != "architecture-contract":
            raise AssertionError("writer must carry --from into handoff_state")
        if state.get("to") != "worker-a":
            raise AssertionError("handoff_state.to must default to lane id")
        if state.get("task") != "worker-a-implementation":
            raise AssertionError("writer must carry --task into handoff_state")
        if state.get("handoff") != "handoffs/worker-a.md":
            raise AssertionError("handoff_state.handoff must match lane handoff")
        if not state.get("queued_at"):
            raise AssertionError("queued status must set queued_at")

        progress_run = root / "progress"
        queued = {"version": 1, "mode": "task", "status": "queued", "to": "worker-a",
                  "handoff": "handoffs/worker-a.md", "queued_at": "2026-01-01T00:00:00Z"}
        write_run(progress_run, [worker_lane("worker-a", status="active", handoff_state=queued)])
        for status in ("active", "pass"):
            record_event(progress_run, "worker-a", status)
        lane, state = lane_state(progress_run, "worker-a")
        if lane.get("status") != "pass" or state.get("status") != "completed":
            raise AssertionError("terminal caller status must project completed handoff_state")
        if state.get("queued_at") != "2026-01-01T00:00:00Z":
            raise AssertionError("projection must preserve an earlier queued_at timestamp")
        for field in ("accepted_at", "completed_at"):
            if not state.get(field):
                raise AssertionError(f"{field} must be set after status progression")

        mapping_run = root / "mapping"
        statuses = {"planned": "queued", "active": "accepted", "pass": "completed", "pass-with-risks": "completed",
                    "blocked": "blocked", "fail": "failed", "timed-out": "blocked", "replaced": "blocked"}
        write_run(mapping_run, [worker_lane(f"lane-{index}", status="active") for index in range(len(statuses))])
        for index, (status, domain) in enumerate(statuses.items()):
            record_event(mapping_run, f"lane-{index}", status)
            lane, state = lane_state(mapping_run, f"lane-{index}")
            if state.get("status") != domain:
                raise AssertionError(f"caller status {status} must project {domain}, got {state.get('status')!r}")

        batch_run = root / "batch"
        write_run(batch_run, [worker_lane("review-batch", status="active"), worker_lane("qa-item", status="active")])
        record_event(batch_run, "review-batch", "active", "--batch-id", "batch-1", "--batch-item", "qa-item")
        _, state = lane_state(batch_run, "review-batch")
        if state.get("mode") != "batch" or state.get("batch") != {"id": "batch-1", "items": ["qa-item"]}:
            raise AssertionError(f"batch state mismatch: {state}")
        record_event(batch_run, "review-batch", "active", "--batch-item", "other-item")
        _, state = lane_state(batch_run, "review-batch")
        if state["batch"]["items"] != ["qa-item", "other-item"]:
            raise AssertionError("batch items must accumulate across events")
        record_event(batch_run, "review-batch", "active", "--batch-item", "qa-item")
        _, state = lane_state(batch_run, "review-batch")
        if state["batch"]["items"] != ["qa-item", "other-item"]:
            raise AssertionError("batch items must stay deduplicated")
        error = record_event(batch_run, "qa-item", "active", "--batch-id", "batch-x", ok=False)
        if "batch mode requires at least one" not in error:
            raise AssertionError(f"empty batch must be rejected: {error}")
        result = subprocess.run([sys.executable, "-B", str(RECORDER), "--run-dir", str(batch_run),
                                 "--role", "python-worker", "--execution-mode", "role-lane",
                                 "--stage", "checks", "--status", "active", "--summary", "Laneless batch",
                                 "--batch-id", "batch-3"], cwd=root, text=True, capture_output=True, check=False)
        if result.returncode == 0 or "--lane-id" not in result.stderr:
            raise AssertionError(f"batch metadata without lane must be rejected: {result.stderr}")
        record_event(batch_run, "review-batch", "pass")
        _, state = lane_state(batch_run, "review-batch")
        if state.get("status") != "completed":
            raise AssertionError("terminal batch lane must project completed")

        fallback_run = root / "fallback"
        lane = worker_lane("summary-only")
        del lane["handoff"]
        write_run(fallback_run, [lane])
        record_event(fallback_run, "summary-only", "planned")
        _, state = lane_state(fallback_run, "summary-only")
        if state.get("handoff") != "handoffs/summary-only.md":
            raise AssertionError(f"summary-only lane must use the default handoff path: {state}")

        unknown_run = root / "unknown-lane"
        write_run(unknown_run, [worker_lane("worker-a")],
                  extra_records=[{"lane_id": "missing", "role": "python-worker", "status": "active",
                                  "trace": "agents/python-worker/trace.jsonl"}])
        error = record_event(unknown_run, "missing", "planned", ok=False)
        if "unknown lane" not in error:
            raise AssertionError(f"unknown lane ids must be rejected: {error}")

        invalid_run = root / "invalid-status"
        write_run(invalid_run, [worker_lane("worker-a")])
        error = record_event(invalid_run, "worker-a", "done", ok=False)
        if "unknown caller status" not in error:
            raise AssertionError(f"invalid statuses must be rejected: {error}")

        try:
            update_handoff_state(lane=worker_lane("mismatch"), lane_id="mismatch", status="queued", mode=None,
                                 from_lane=None, to_lane=None, task=None, handoff="handoffs/other.md",
                                 batch_id=None, batch_items=[])
        except journal_io.JournalError as exc:
            if "handoff must match lane handoff" not in str(exc):
                raise AssertionError(str(exc))
        else:
            raise AssertionError("mismatched handoff paths must be rejected")
        try:
            update_handoff_state(lane=worker_lane("batch-misuse"), lane_id="batch-misuse", status="queued",
                                 mode="task", from_lane=None, to_lane=None, task=None, handoff=None,
                                 batch_id="batch-x", batch_items=[])
        except journal_io.JournalError as exc:
            if "require --mode batch" not in str(exc):
                raise AssertionError(str(exc))
        else:
            raise AssertionError("task mode must reject batch metadata")
        print("PASS writer projects handoff_state on every assignment event with batch accumulation and fallbacks")


def declared_lane_projection_fixture():
    with tempfile.TemporaryDirectory(prefix="agent-flow-handoff-declared-") as temp_dir:
        root = Path(temp_dir).resolve()
        run_dir = root / "run"
        run_dir.mkdir()
        initialize_journal(run_dir, {"lane-map.json": json.dumps({"schema_version": 1, "lanes": []}).encode()},
                           source_root=root)
        journal_io.write_entry(run_dir, "lane", {"id": "worker-b", "summary": "Declared worker lane",
                                                 "status": "planned", "lane_type": "implementation",
                                                 "role": "python-worker", "execution_mode": "role-lane",
                                                 "wave": 1, "critical": False}, identifier="declare-worker-b")
        data = read_lane_map(run_dir)
        lane = next(lane for lane in data["lanes"] if lane["id"] == "worker-b")
        state = lane.get("handoff_state")
        if not isinstance(state, dict) or state.get("status") != "queued" or not state.get("queued_at"):
            raise AssertionError(f"lane declaration must project queued handoff_state: {state}")
        if state.get("handoff") != "handoffs/worker-b.md" or state.get("to") != "worker-b":
            raise AssertionError(f"declared lane handoff_state fields mismatch: {state}")
        print("PASS lane declarations project queued handoff_state")


def obligation_replacement_projection_fixture():
    with tempfile.TemporaryDirectory(prefix="agent-flow-handoff-obligation-") as temp_dir:
        root = Path(temp_dir).resolve()
        run_dir = root / "run"
        run_dir.mkdir()
        handoff_text = b"Replacement covers the same duty\n"
        old_handoff = b"Original blocked conclusion.\n"
        records = [
            {"lane_id": "old", "role": "python-worker", "trace": "agents/old/trace.jsonl", "status": "blocked",
             "handoff": "handoffs/old.md",
             "evidence": [{"path": "handoffs/old.md", "sha256": base.sha256(old_handoff)}],
             "obligation": {"id": "duty", "required": True, "state": "current"}},
            {"lane_id": "new", "role": "python-worker", "trace": "agents/new/trace.jsonl", "status": "pass",
             "handoff": "handoffs/new.md", "obligation": {"id": "duty", "required": True, "state": "current"},
             "evidence": [{"path": "handoffs/new.md", "sha256": base.sha256(handoff_text)}]},
        ]
        lanes = [worker_lane("old", status="blocked"), worker_lane("new", status="pass")]

        def trace_event(record, stage, status):
            return {"timestamp": "2026-01-01T00:00:00Z", "stage": stage, "role": record["role"],
                    "stable_agent_name": "Python Worker", "stable_agent_slug": "python-worker",
                    "status": status, "summary": "Fixture obligation event.", "artifacts": [], "next_step": "",
                    "execution_mode": "role-lane", "lane_id": record["lane_id"]}

        events = [trace_event(records[0], "spawned", "active"), trace_event(records[0], "blocked", "blocked"),
                  trace_event(records[1], "spawned", "active"),
                  {**trace_event(records[1], "handoff", "pass"), "artifacts": ["handoffs/new.md"]}]
        timeline = "".join(json.dumps(event) + "\n" for event in events)
        traces = {"agents/old/trace.jsonl": ("".join(json.dumps(e) + "\n" for e in events[:2])).encode(),
                  "agents/new/trace.jsonl": ("".join(json.dumps(e) + "\n" for e in events[2:])).encode()}
        initialize_journal(run_dir, {
            "delegation-summary.json": json.dumps({"version": 1, "subagents_used": False, "role_lanes_used": True,
                "notes": "Obligation fixture", "subagents": [], "role_lanes": records,
                "verification": base.empty_verification()}).encode(),
            "lane-map.json": json.dumps({"schema_version": 1, "lanes": lanes}).encode(),
            "timeline.jsonl": timeline.encode(),
            **traces,
            "handoffs/old.md": old_handoff,
            "handoffs/new.md": handoff_text}, source_root=root)
        from journal_lifecycle import resolve_obligation
        snapshot = JournalSnapshot.open(run_dir)
        resolve_obligation(run_dir, lane_id="old", replacement="new", reason="Same duty replacement",
                           expected_revision=snapshot.revision, identifier="resolve-duty")
        lane = next(lane for lane in read_lane_map(run_dir)["lanes"] if lane["id"] == "old")
        if lane.get("status") != "replaced":
            raise AssertionError("resolved obligation must mark the original lane replaced")
        state = lane.get("handoff_state")
        if not isinstance(state, dict) or state.get("status") != "blocked" or not state.get("completed_at"):
            raise AssertionError(f"replaced lane must project blocked handoff_state: {state}")
        print("PASS obligation replacement projects blocked handoff_state")


def closed_journal_and_gate_fixtures():
    with tempfile.TemporaryDirectory(prefix="agent-flow-handoff-gate-") as temp_dir:
        root = Path(temp_dir).resolve()

        closed_run = root / "closed"
        closed_run.mkdir()
        (closed_run / "run.md").write_text("# Synthetic compact run\n")
        (closed_run / "checks.md").write_text("# Checks\n\nSynthetic assertions.\n")
        summary, source = base.acceptance_pack(closed_run)
        for record in summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        base.write_json(closed_run / "delegation-summary.json", summary)
        timeline = (closed_run / "timeline.jsonl").read_bytes().splitlines(keepends=True)
        (closed_run / "timeline.jsonl").write_bytes(b"".join(timeline[:-1]))
        documents = {p.relative_to(closed_run).as_posix(): p.read_bytes() if p.is_file() else None
                     for p in closed_run.rglob("*")}
        initialize_journal(closed_run, documents, source_root=root, result_contract_version=1)
        snapshot = JournalSnapshot.open(closed_run)
        from journal_lifecycle import finalize
        finalize(closed_run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                 expected_generation=snapshot.generation, identifier="close-run",
                 final_summary="Actual product independently accepted", verdict="ship", session_source=source)
        closed_snapshot = JournalSnapshot.open(closed_run)
        if not closed_snapshot.closed:
            raise AssertionError("fixture must produce a closed generation")
        before_revision = closed_snapshot.revision
        with redirect_stdout(StringIO()):
            try:
                recorder.main(["--run-dir", str(closed_run), "--role", "orchestrator", "--execution-mode", "role-lane",
                               "--stage", "checks", "--status", "pass", "--summary", "Late event"])
            except SystemExit as exc:
                error = str(exc)
            else:
                raise AssertionError("closed journal must refuse new events")
        if "closed" not in error:
            raise AssertionError(f"closed journal must refuse new events: {error}")
        if JournalSnapshot.open(closed_run).revision != before_revision:
            raise AssertionError("closed journal refusal must not write a revision")

        # Durable lane-map without the opt-in flag: the gate applies because the
        # writer guarantees the projection. Flat legacy maps keep flag-only reads.
        gated_run = root / "gated"
        write_run(gated_run, [worker_lane("worker-a")])
        snapshot = JournalSnapshot.open(gated_run)
        if "handoff_state_required" in read_lane_map(gated_run):
            raise AssertionError("fixture must not opt into the gate")
        errors = validator.validate_lane_map(gated_run, snapshot=snapshot)
        if not any("missing handoff_state" in error for error in errors):
            raise AssertionError(f"durable lane-map without handoff_state must fail the gate: {errors}")
        flat = root / "flat"
        flat.mkdir()
        (flat / "lane-map.json").write_bytes(snapshot.read_bytes("lane-map.json"))
        flat_snapshot = JournalSnapshot.open(flat)
        if flat_snapshot.durable:
            raise AssertionError("flat fixture must not be durable")
        flat_errors = validator.validate_lane_map(flat, snapshot=flat_snapshot)
        if any("missing handoff_state" in error for error in flat_errors):
            raise AssertionError(f"flat legacy lane-map must keep flag-only gate semantics: {flat_errors}")
        print("PASS closed journal refusal and unconditional durable gate with flat compatibility")


def root_metadata_rejection_fixtures():
    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as directory:
        root = Path(directory).resolve()
        for index, (stage, status) in enumerate([('verification-prepared', 'active'), ('verification-prepared', 'active'), ('verification-ready', 'pass')]):
            current = root / str(index)
            current.mkdir()
            initialize_journal(current, {'run.md': b'Root metadata test'}, source_root=root)
            def tree():
                return {str(p.relative_to(current)): ('link', str(p.readlink())) if p.is_symlink()
                        else ('dir', None) if p.is_dir() else ('file', p.read_bytes()) for p in current.rglob('*')}
            before = tree()
            result = subprocess.run([sys.executable, '-B', str(RECORDER), '--run-dir', str(current),
                                     '--role', 'orchestrator', '--stage', stage, '--status', status,
                                     '--summary', 'Root metadata', '--verification-json', '{}'],
                                    cwd=root, text=True, capture_output=True, check=False)
            if result.returncode == 0 or 'explicit --execution-mode role-lane' not in result.stderr or tree() != before:
                raise AssertionError('root metadata must reject before result contract, request, or trace writes')
        print("PASS root metadata rejection before contract preserved")


def main() -> int:
    projection_fixtures()
    declared_lane_projection_fixture()
    obligation_replacement_projection_fixture()
    closed_journal_and_gate_fixtures()
    root_metadata_rejection_fixtures()
    print("PASS handoff state fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
