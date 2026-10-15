#!/usr/bin/env python3
"""Focused regressions for mandatory QA/reviewer evidence; synthetic data only."""

import importlib.util
import json
from journal_io import JournalSnapshot, import_legacy, transact
import subprocess
import sys
import tempfile
import unittest
import os
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from verification_evidence import (EvidenceError, empty_verification, project_root,
                                   reference_bytes, result_hash, historical_result_hash, sha256, changed_paths, confined_path)

SCRIPTS = Path(__file__).resolve().parent


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


validator = module("validate_run", "validate-run.py")

ROOT_ID = "00000000-0000-4000-8000-000000000001"
QA_ID = "00000000-0000-4000-8000-000000000002"
REVIEWER_ID = "00000000-0000-4000-8000-000000000003"


class SyntheticSource:
    def __init__(self):
        self.sessions = {}

    def read(self, thread_id, *, event_indices=()):
        if thread_id not in self.sessions:
            raise EvidenceError("execution unconfirmed: synthetic session missing")
        return deepcopy(self.sessions[thread_id])


def session(thread_id, role, answer, second):
    spawn = {"parent_thread_id": ROOT_ID, "agent_role": role, "depth": 1}
    turn = thread_id + "-turn"
    from agent_config import default_agents_dir, read_frontmatter, resolve_role_path, role_config
    model_id = role_config(read_frontmatter(resolve_role_path(default_agents_dir(), role)), role)["model"]
    stamp = f"2026-09-10T10:00:{second:02d}+00:00"
    return [
        {"type": "session_meta", "payload": {"id": thread_id, "session_id": ROOT_ID, **spawn, "source": {"subagent": {"thread_spawn": spawn}}}},
        {"type": "event_msg", "timestamp": stamp, "payload": {"type": "task_started", "turn_id": turn}},
        {"type": "turn_context", "payload": {"turn_id": turn, "model": model_id}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": json.dumps(answer)}]}},
        {"type": "event_msg", "timestamp": stamp, "payload": {"type": "task_complete", "turn_id": turn, "last_agent_message": json.dumps(answer)}},
    ]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def reference(run, path, section=None):
    ref = {"path": path}
    if section:
        ref["section"] = section
    ref["sha256"] = sha256(reference_bytes(run, ref))
    return ref


def trace_event(record, stage):
    return {"timestamp": "2026-09-10T10:00:00+00:00", "stage": stage,
            "role": record["role"], "stable_agent_name": record["role"], "stable_agent_slug": record["role"],
            "status": "pass" if stage == "handoff" else "active", "summary": "Synthetic evidence fixture.",
            "artifacts": [record["handoff"]] if stage == "handoff" else [], "next_step": "final",
            "execution_mode": "subagent", "codex_thread_id": record["codex_thread_id"], "lane_id": record["lane_id"]}


def acceptance_pack(run, *, files=None):
    """Explicit fixture setup. Never called automatically for focused mutations."""
    source = SyntheticSource()
    root = project_root(run)
    files = files or ["result.txt"]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic product result.\n")
    (run / "context.md").write_text("# Context\n\n## Initial Worktree Snapshot\n\nClean synthetic worktree.\n")
    (run / "plan.md").write_text("# Plan\n\nChange synthetic result files.\n")
    verification = {**empty_verification(), "task_kind": "change", "root_thread_id": ROOT_ID,
                    "author_thread_ids": [ROOT_ID], "result_files": files,
                    "initial_snapshot": reference(run, "context.md", "Initial Worktree Snapshot"),
                    "task_scope": reference(run, "plan.md"), "qa": "evidence-qa", "reviewer": "evidence-reviewer"}
    summary = {"version": 1, "subagents_used": True, "role_lanes_used": False,
               "subagents": [], "role_lanes": [], "notes": "Synthetic verification fixture.", "verification": verification}
    digest = historical_result_hash(run, verification)
    events = []
    qa_hash = None
    for key, role, thread_id, second in [("qa", "qa-verifier", QA_ID, 10), ("reviewer", "reviewer", REVIEWER_ID, 20)]:
        handoff = f"handoffs/evidence-{key}.md"
        (run / handoff).parent.mkdir(exist_ok=True)
        (run / handoff).write_text(f"# Synthetic {key} evidence\n\nAccepted fixture result.\n")
        with (run / handoff).open("a") as handle:
            handle.write(f"Evidence: checks.md {reference(run, 'checks.md')['sha256']}\n")
        record = {"lane_id": verification[key], "role": role, "codex_thread_id": thread_id,
                  "trace": f"agents/{role}/trace.jsonl", "handoff": handoff,
                  "completion_turn_id": thread_id + "-turn", "reviewed_result_hash": digest,
                  "handoff_sha256": sha256((run / handoff).read_bytes()), "evidence": [reference(run, "checks.md")]}
        answer = {"verdict": "passed", **{k: record[k] for k in ("reviewed_result_hash", "handoff", "handoff_sha256")}}
        if key == "qa":
            qa_hash = record["handoff_sha256"]
        else:
            answer["qa_handoff_sha256"] = qa_hash
        source.sessions[thread_id] = session(thread_id, role, answer, second)
        summary["subagents"].append(record)
        lane_events = [trace_event(record, "spawned"), trace_event(record, "handoff")]
        trace = run / record["trace"]
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text("".join(json.dumps(e) + "\n" for e in lane_events))
        events.extend(lane_events)
    events.append({**events[-1], "stage": "final", "role": "orchestrator", "lane_id": None})
    (run / "timeline.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    write_json(run / "delegation-summary.json", summary)
    (run / "final.md").write_text("# Final\n\nVerdict: ship\n\n## Worktree Hygiene\n\nRun-owned changed files:\n" +
                                 "".join(f"- `{name}`\n" for name in files) + "\n" + delegation_section(summary))
    return summary, source


def publish_workspace_authors(case, workspace, specs=None):
    """Record actual synthetic author writes as native v2 proofs, without a map."""
    sealed = workspace["seal"]
    specs = specs or [{"lane_id": "fixture-root-author", "thread": ROOT_ID, "role_lane": True,
                       "paths": sealed["changed_paths"]}]
    old_delegation = delegation_section(case.summary)
    documents = {}
    timeline = JournalSnapshot.open(case.run).documents.get("timeline.jsonl", b"").decode()
    from journal_io import timestamp
    author_time = max((json.loads(line)["timestamp"] for line in timeline.splitlines() if line.strip()),
                      key=timestamp, default="2026-09-10T10:00:00+00:00")
    for spec in specs:
        lane_id, thread = spec["lane_id"], spec["thread"]
        role_lane = spec.get("role_lane", False)
        handoff = f"handoffs/{lane_id}.md"
        trace = f"agents/{lane_id}/trace.jsonl"
        record = {"lane_id": lane_id, "role": "python-worker", "trace": trace, "handoff": handoff,
                  "status": "pass", "notes": "Actual synthetic fixture author write.",
                  "reason": "Fixture product bytes were written by this author.",
                  "obligation": {"id": lane_id, "required": True, "state": "current"}}
        if not role_lane:
            record["codex_thread_id"] = thread
        case.summary["role_lanes" if role_lane else "subagents"].append(record)
        event = {"timestamp": author_time, "stage": "handoff", "role": "python-worker",
                 "stable_agent_name": "Python Worker", "stable_agent_slug": "python-worker", "status": "pass",
                 "summary": "Actual synthetic author write.", "artifacts": [handoff], "next_step": "QA",
                 "execution_mode": "role-lane" if role_lane else "subagent", "lane_id": lane_id}
        if not role_lane:
            event["codex_thread_id"] = thread
        text = "Written paths: " + ", ".join(spec["paths"]) + "\n"
        proof = json.dumps({"author_thread_id": thread, "written_paths": spec["paths"]})
        proof_path = f"artifacts/{lane_id}-edit.json"
        declaration = {"lane_id": lane_id, "authored_paths": spec["paths"],
                       "handoff": {"path": handoff, "sha256": sha256(text.encode())},
                       "edit_evidence": [{"path": proof_path, "sha256": sha256(proof.encode())}], "coauthors": {}}
        assignment = {"codex_thread_id": thread, "role": "python-worker", "trace": trace}
        artifact = {"version": 2, "status": "captured", "workspace_id": workspace["workspace_id"],
                    "candidate_id": sealed["candidate_id"], "baseline_digest": workspace["baseline_digest"],
                    "candidate_digest": sealed["candidate_digest"], "changed_paths": sealed["changed_paths"],
                    "tracked_changed_paths": [], "untracked_paths": [], "assignment": assignment, **declaration,
                    "base_ref": "workspace-baseline", "head_ref": sealed["candidate_id"],
                    "command": "actual synthetic fixture write", "notes": "Native v2 fixture author proof."}
        events = [{**event, "stage": "spawned", "status": "active", "artifacts": []}, event] if not role_lane else [event]
        trace_text = "".join(json.dumps(item) + "\n" for item in events)
        documents.update({handoff: text, trace: trace_text, proof_path: proof,
                          f"checks/lane-boundary-{lane_id}.json": json.dumps(artifact)})
        timeline += trace_text
    case.summary["role_lanes_used"] = bool(case.summary["role_lanes"])
    case.summary["verification"]["author_thread_ids"] = sorted({spec["thread"] for spec in specs if spec["paths"]})
    documents["delegation-summary.json"] = json.dumps(case.summary)
    documents["timeline.jsonl"] = timeline
    snapshot = JournalSnapshot.open(case.run)
    if snapshot.exists("final.md"):
        documents["final.md"] = snapshot.read_text("final.md").replace(old_delegation, delegation_section(case.summary))
    transact(case.run, "fixture-native-authors", {}, lambda current: (documents, {}))


def workspace_pack(case, files=None, authors=None):
    """Explicitly upgrade tests exercising NEW conclusions to a sealed workspace."""
    from task_workspace import prepare, seal
    subprocess.run(["git", "init", "-q", str(case.root)], check=True)
    initialize_open_fixture(case.run)
    temporary = tempfile.TemporaryDirectory()
    case.addCleanup(temporary.cleanup)
    workspace = prepare(case.run, case.root, Path(temporary.name).resolve() / "bundle")
    for name in files or ["result.txt"]:
        target = Path(workspace["working_root"]) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("New synthetic workspace result.\n")
    sealed = seal(case.run)
    verification = case.summary["verification"]
    verification["result_files"] = sealed["changed_paths"]
    verification["run_changed_files"] = sealed["changed_paths"]
    publish_workspace_authors(case, {**workspace, "seal": sealed}, authors)
    snapshot = JournalSnapshot.open(case.run)
    digest = result_hash(case.run, verification, snapshot=snapshot)
    verification["result_hash"] = digest
    qa_hash = None
    for index, record in enumerate(record for record in case.summary["subagents"]
                                   if record["role"] in {"qa-verifier", "reviewer"}):
        record["reviewed_result_hash"] = digest
        record["handoff_sha256"] = sha256(snapshot.read_bytes(record["handoff"]))
        answer = {"verdict": "passed", "reviewed_result_hash": digest, "handoff": record["handoff"],
                  "handoff_sha256": record["handoff_sha256"]}
        if index:
            answer["qa_handoff_sha256"] = qa_hash
        else:
            qa_hash = record["handoff_sha256"]
        case.source.sessions[record["codex_thread_id"]] = session(record["codex_thread_id"], record["role"], answer, 10 + index * 10)
    transact(case.run, "fixture-workspace-summary", {},
             lambda current: ({"delegation-summary.json": json.dumps(case.summary)}, {}))
    return workspace


def initialize_open_fixture(run):
    """Create a new synthetic v2 journal; this is not a legacy upgrade."""
    from journal_io import initialize_journal
    snapshot = JournalSnapshot.open(run)
    if snapshot.durable:
        return snapshot
    return initialize_journal(run, dict(snapshot.documents), source_root=snapshot.source_root,
                              identities=dict(snapshot.identities), result_contract_version=1)


def delegation_section(summary):
    return ("## Delegation Trace\n\n"
            f"Subagents Used: {'yes' if summary['subagents_used'] else 'no'}\n"
            f"Role Lanes Used: {'yes' if summary['role_lanes_used'] else 'no'}\n"
            "Subagent Lanes: " + (", ".join(r["lane_id"] for r in summary["subagents"]) or "none") + "\n"
            "Role Lanes: " + (", ".join(r["lane_id"] for r in summary["role_lanes"]) or "none") + "\n"
            "Subagent Trace Evidence: " + (", ".join(r["trace"] for r in summary["subagents"]) or "none") + "\n")


def neighboring_pack(run, *, complete=True):
    """Upgrade temporary neighboring fixtures; leave their targeted defects intact.

    Existing architectural QA/reviewer sections stay intact; synthetic evidence
    references are appended only to temporary handoffs.
    Missing architectural lanes are never filled: their negative gates still run.
    Focused verification cases must not call this adapter.
    """
    source = SyntheticSource()
    summary_path = run / "delegation-summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {
        "version": 1, "subagents_used": False, "role_lanes_used": False,
        "subagents": [], "role_lanes": [], "notes": "Synthetic neighboring fixture."}
    if not isinstance(summary, dict):
        return source
    lane_path = run / "lane-map.json"
    lane_map = json.loads(lane_path.read_text()) if lane_path.exists() else {}
    if not isinstance(lane_map, dict) or not isinstance(lane_map.get("lanes", []), list):
        return source
    lanes = lane_map.get("lanes", [])
    try:
        files = changed_paths(run, lane_map)
    except (EvidenceError, json.JSONDecodeError):
        files = set()  # Keep malformed Boundary Evidence for its targeted assertion.
    is_change = bool(files) or any(isinstance(l, dict) and l.get("type") in {"implementation", "integration"} for l in lanes)
    verification = {**empty_verification(), "task_kind": "change" if is_change else "analysis",
                    "root_thread_id": ROOT_ID, "author_thread_ids": [ROOT_ID] if is_change else [],
                    "blocker": "Synthetic blocked fixture; acceptance is not claimed."}
    summary["verification"] = verification
    if is_change:
        root = project_root(run)
        safe_files = []
        for name in sorted(files):
            try:
                confined_path(root, name, relative=True)
                safe_files.append(name)
            except EvidenceError:
                pass  # An intentionally invalid path stays in the original Boundary Evidence.
        files = safe_files or ["synthetic-result.txt"]
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("Synthetic neighboring product bytes.\n")
        verification["result_files"] = files
        context = run / "context.md"
        context.write_text((context.read_text() if context.exists() else "# Context\n") +
                           "\n## Initial Worktree Snapshot\n\nClean synthetic worktree.\n")
        plan = run / "plan.md"
        if not plan.exists():
            plan.write_text("# Synthetic task scope\n")
        verification["initial_snapshot"] = reference(run, "context.md", "Initial Worktree Snapshot")
        verification["task_scope"] = reference(run, "plan.md")
        digest = result_hash(run, verification)
        check = run / "checks/synthetic-verification.md"
        check.parent.mkdir(exist_ok=True)
        check.write_text("Synthetic evidence for neighboring contract fixtures.\n")
        new_events = []
        qa_hash = None
        for key, canonical, thread_id, second in [("qa", "qa-verifier", QA_ID, 10), ("reviewer", "reviewer", REVIEWER_ID, 20)]:
            if not complete:
                continue
            lane = next((l for l in lanes if isinstance(l, dict)
                         and l.get("type") == ("qa" if key == "qa" else "review")
                         and l.get("role") in ({canonical, "reviewer.qa"} if key == "reviewer" else {canonical})
                         and l.get("status") in {"pass", "passed", "pass-with-risks"}
                         and l.get("id") not in lane_map.get("verification_readiness", {}).get("lanes", [])), None)
            if lane is None:
                if (lane_map.get("architecture_contract_required") or key == "reviewer" or
                    not any(isinstance(l, dict) and l.get("type") == "review" and l.get("status") in {"pass", "pass-with-risks"} for l in lanes)):
                    continue
                lane = {"id": f"synthetic-{key}", "type": "qa" if key == "qa" else "review",
                        "role": canonical, "status": "pass", "wave": max([l.get("wave", 0) for l in lanes if isinstance(l, dict) and type(l.get("wave")) is int] or [0]) + 1,
                        "critical": False, "handoff": f"handoffs/synthetic-{key}.md", "evidence": ["checks/synthetic-verification.md"],
                        "execution_mode": "subagent", "replacement": None}
                if lane_path.exists():
                    lanes.append(lane)
                (run / lane["handoff"]).parent.mkdir(exist_ok=True)
                (run / lane["handoff"]).write_text(f"Synthetic {key} handoff.\n")
            handoff = lane.get("handoff")
            if not isinstance(handoff, str) or not (run / handoff).is_file():
                continue
            with (run / handoff).open("a") as handle:
                handle.write(f"\nSynthetic evidence: checks/synthetic-verification.md {reference(run, 'checks/synthetic-verification.md')['sha256']}\n")
            # Preserve malformed existing subagent traces for their negative tests.
            existing = next((r for r in summary["subagents"] if r.get("lane_id") == lane["id"]), None)
            record = dict(existing or {"lane_id": lane["id"], "role": lane["role"], "trace": f"agents/{lane['role']}/trace.jsonl", "handoff": handoff})
            record.update(codex_thread_id=thread_id, completion_turn_id=thread_id + "-turn", reviewed_result_hash=digest,
                          handoff_sha256=sha256((run / handoff).read_bytes()), evidence=[reference(run, "checks/synthetic-verification.md")])
            if existing:
                summary["subagents"][summary["subagents"].index(existing)] = record
                trace = run / record["trace"]
                if trace.exists():
                    events = [json.loads(line) for line in trace.read_text().splitlines()]
                    for event in events:
                        if event.get("lane_id") == lane["id"] and event.get("codex_thread_id"):
                            event["codex_thread_id"] = thread_id
                    trace.write_text("".join(json.dumps(e) + "\n" for e in events))
            else:
                summary["role_lanes"] = [r for r in summary["role_lanes"] if r.get("lane_id") != lane["id"]]
                summary["subagents"].append(record)
                lane["execution_mode"] = "subagent"
                events = [trace_event(record, "spawned"), trace_event(record, "handoff")]
                trace = run / record["trace"]
                trace.parent.mkdir(parents=True, exist_ok=True)
                with trace.open("a") as handle:
                    timeline = [json.loads(line) for line in (run / "timeline.jsonl").read_text().splitlines()]
                    for event in events:
                        event["timestamp"] = timeline[-1]["timestamp"]
                    handle.write("".join(json.dumps(e) + "\n" for e in events))
                new_events.extend(events)
            verification[key] = lane["id"]
            answer = {"verdict": "passed", **{field: record[field] for field in ["reviewed_result_hash", "handoff", "handoff_sha256"]}}
            if key == "qa":
                qa_hash = record["handoff_sha256"]
            else:
                answer["qa_handoff_sha256"] = qa_hash
            source.sessions[thread_id] = session(thread_id, canonical, answer, second)
        timeline_path = run / "timeline.jsonl"
        if timeline_path.exists():
            timeline = [json.loads(line) for line in timeline_path.read_text().splitlines()]
            for event in timeline:
                record = next((r for r in summary["subagents"] if r.get("lane_id") == event.get("lane_id")), None)
                if record and event.get("codex_thread_id"):
                    event["codex_thread_id"] = record["codex_thread_id"]
            final_index = next((i for i, e in enumerate(timeline) if e.get("stage") == "final"), len(timeline))
            timeline[final_index:final_index] = new_events
            timeline_path.write_text("".join(json.dumps(e) + "\n" for e in timeline))
        if lane_path.exists():
            write_json(lane_path, lane_map)
    summary["subagents_used"] = bool(summary["subagents"])
    summary["role_lanes_used"] = bool(summary["role_lanes"])
    write_json(summary_path, summary)
    final = run / "final.md"
    import re
    text = final.read_text()
    section = delegation_section(summary)
    if "## Delegation Trace" in text:
        for line in section.splitlines()[2:]:
            label = line.split(":", 1)[0]
            text = re.sub(r"(?m)^" + re.escape(label) + r":.*$", lambda _: line, text)
    else:
        text += "\n" + section
    final.write_text(text)
    return source


class OriginalRegressions(unittest.TestCase):
    def setUp(self):
        global fixtures
        fixtures = module("lane_fixtures", "test-validate-run-lanes.py")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_bare_compact_cannot_ship(self):
        run = fixtures.write_compact_run(self.root, verdict="ship", verification_pack=False)
        self.assertTrue(validator.validate_compact_run(run, False, False))

    def test_malformed_compact_summary_cannot_ship(self):
        run = fixtures.write_compact_run(self.root, verdict="ship", verification_pack=False)
        (run / "delegation-summary.json").write_text("{broken")
        self.assertTrue(validator.validate_compact_run(run, False, False))

    def test_removing_full_lane_map_cannot_ship(self):
        run = fixtures.write_run(self.root, verification_pack=False)
        self.assertTrue(validator.validate_full_run(run, False, False, False))

    def test_worker_and_self_reported_reviewer_are_insufficient(self):
        run = fixtures.write_run(
            self.root,
            lanes=[fixtures.lane("worker-a", lane_type="implementation", role="typescript-worker", wave=2),
                   fixtures.reviewer_control_lane(wave=3)],
            lane_map_extra={"schema_version": 2, "budget": "standard", "architecture_contract_required": False},
            verification_pack=False,
        )
        self.assertTrue(validator.validate_full_run(run, False, False, False))

    def test_init_without_lanes_creates_verification(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                                 "--slug", "evidence"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = Path(result.stdout.strip()) / "delegation-summary.json"
        snapshot = JournalSnapshot.open(summary.parent)
        self.assertTrue(snapshot.exists(summary))
        self.assertIn("verification", json.loads(snapshot.read_text(summary)))


class EvidenceRegressions(unittest.TestCase):
    def test_draft_missing_required_boundary_validates_every_present_proof(self):
        from dataclasses import replace
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        snapshot = JournalSnapshot.open(self.run)
        mapping = {"lanes": [{"id": "a-missing", "type": "implementation"},
                             {"id": "fixture-root-author", "type": "implementation"}]}
        docs = {**snapshot.documents, "lane-map.json": json.dumps(mapping).encode()}
        current = replace(snapshot, documents=docs)
        verification = {**self.summary["verification"], "result_hash": None}
        self.assertIsNone(result_hash(self.run, verification, snapshot=current, require_ready=False))
        with self.assertRaisesRegex(EvidenceError, "author boundary artifact missing"):
            result_hash(self.run, verification, snapshot=current)
        with self.assertRaisesRegex(EvidenceError, "existing result_hash"):
            result_hash(self.run, self.summary["verification"], snapshot=current, require_ready=False)
        name = "checks/lane-boundary-fixture-root-author.json"
        proof = json.loads(docs[name])
        mutations = [("json", None, ""), ("version", "version", 1),
                     ("candidate", "candidate_id", "stale"), ("assignment", "assignment", {}),
                     ("handoff", "handoff", {**proof["handoff"], "sha256": "0" * 64}),
                     ("edit", "edit_evidence", [{**proof["edit_evidence"][0], "sha256": "0" * 64}])]
        for label, field, value in mutations:
            changed = deepcopy(proof)
            if field:
                changed[field] = value
            malformed = {**docs, name: json.dumps(changed).encode() if field else b"{broken"}
            with self.subTest(mutation=label), self.assertRaises((EvidenceError, ValueError)):
                result_hash(self.run, verification, snapshot=replace(snapshot, documents=malformed), require_ready=False)
        with self.assertRaisesRegex(EvidenceError, "full workspace delta"):
            result_hash(self.run, {**verification, "result_files": []}, snapshot=current, require_ready=False)
        from task_workspace import registered_workspace, verify_candidate
        workspace = registered_workspace(snapshot)
        sealed = verify_candidate(workspace)
        with patch("verification_evidence.verify_candidate", return_value={**sealed, "result_hash_version": 99}):
            with self.assertRaisesRegex(EvidenceError, "unsupported result_hash_version"):
                result_hash(self.run, verification, snapshot=current, require_ready=False)
        candidate_file = Path(sealed["candidate_root"]) / "result.txt"
        candidate_file.chmod(0o644)
        candidate_file.write_text("Candidate drift after seal\n")
        with self.assertRaises(EvidenceError):
            result_hash(self.run, verification, snapshot=current, require_ready=False)

    def test_complete_draft_and_strict_v5_payload_are_identical_without_map(self):
        from verification_evidence import semantic_result_payload
        from task_workspace import registered_workspace, verify_candidate
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        snapshot = JournalSnapshot.open(self.run)
        workspace = registered_workspace(snapshot)
        sealed = verify_candidate(workspace)
        self.assertFalse(snapshot.exists("lane-map.json"))
        verification = self.summary["verification"]
        self.assertEqual(result_hash(self.run, verification, snapshot=snapshot, require_ready=False),
                         result_hash(self.run, verification, snapshot=snapshot))
        self.assertEqual(semantic_result_payload(snapshot, workspace, sealed, None, require_complete=False),
                         semantic_result_payload(snapshot, workspace, sealed, None))

    def test_phase1_compact_three_authors_without_root_and_negative_proof_controls(self):
        from dataclasses import replace
        from verification_evidence import resolve_author_boundaries, validate_verification
        from task_workspace import registered_workspace
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        specs = [{"lane_id": "author-" + str(index), "thread": f"00000000-0000-4000-8000-{index:012d}",
                  "paths": ["written-" + str(index) + ".txt"]} for index in (4, 5, 6)]
        workspace_pack(self, files=[spec["paths"][0] for spec in specs], authors=specs)
        for record in self.summary["subagents"]:
            if record["role"] in {"qa-verifier", "reviewer"}:
                record["status"] = "pass"
                record["obligation"] = {"id": record["lane_id"], "required": True, "state": "current"}
        transact(self.run, "compact-accepted-assignments", {},
                 lambda current: ({"delegation-summary.json": json.dumps(self.summary)}, {}))
        snapshot = JournalSnapshot.open(self.run)
        workspace = registered_workspace(snapshot)
        verification = self.summary["verification"]
        self.assertNotIn(ROOT_ID, verification["author_thread_ids"])
        self.assertFalse(snapshot.exists("lane-map.json"))
        entries = resolve_author_boundaries(snapshot, workspace)
        self.assertEqual({entry["assignment"]["codex_thread_id"] for entry in entries}, {spec["thread"] for spec in specs})
        self.assertEqual(validate_verification(self.run, self.summary, {}, "ship", self.source, snapshot=snapshot,
                                              presentation_required=False), [])
        self.assertEqual(validator.validate_product_acceptance(self.run, verdict="ship", session_source=self.source,
                                                              snapshot=snapshot), [])
        changed_summary = deepcopy(self.summary)
        changed_summary["verification"]["author_thread_ids"].append(ROOT_ID)
        self.assertIn("proven native author assignments", " ".join(validate_verification(self.run, changed_summary, {}, "ship",
                      self.source, snapshot=snapshot, presentation_required=False)))
        entry = entries[0]
        name, data = entry["artifact"], entry["data"]
        for mutation, expected in (("missing", "proven native author assignments"), ("candidate", "retained seal missing"),
                                   ("sha", "sha256 mismatch"), ("assignment", "author assignment mismatch"),
                                   ("trace", "trace identity mismatch"), ("explicit-missing", "artifact missing")):
            docs = dict(snapshot.documents)
            altered = deepcopy(data)
            if mutation == "missing":
                docs.pop(name)
            elif mutation == "candidate":
                altered["candidate_id"] = "wrong-candidate"
            elif mutation == "sha":
                altered["handoff"]["sha256"] = "0" * 64
            elif mutation == "assignment":
                altered["assignment"]["codex_thread_id"] = ROOT_ID
            elif mutation == "trace":
                events = [json.loads(line) for line in docs[entry["record"]["trace"]].decode().splitlines()]
                events[0]["codex_thread_id"] = ROOT_ID
                docs[entry["record"]["trace"]] = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
            else:
                docs["lane-map.json"] = json.dumps({"lanes": [{"id": entry["lane_id"], "type": "implementation",
                    "boundary": {"changed_paths_artifact": "checks/explicit-missing.json"}}]}).encode()
            if mutation != "missing":
                docs[name] = json.dumps(altered).encode()
            with self.subTest(mutation=mutation):
                current = replace(snapshot, documents=docs)
                selected_summary = deepcopy(self.summary)
                if mutation == "missing":
                    selected_summary["verification"]["result_hash"] = result_hash(self.run, verification, snapshot=current)
                errors = validate_verification(self.run, selected_summary, {}, "ship", self.source,
                                              snapshot=current, presentation_required=False)
                self.assertIn(expected, " ".join(errors))
        original = result_hash(self.run, verification, snapshot=snapshot)
        altered = deepcopy(data)
        altered["notes"] = "Formatting changed only"
        docs = {**snapshot.documents, name: json.dumps(altered, indent=3).encode()}
        self.assertEqual(result_hash(self.run, verification, snapshot=replace(snapshot, documents=docs)), original)
        altered["authored_paths"] = []
        docs[name] = json.dumps(altered).encode()
        self.assertNotEqual(result_hash(self.run, verification, snapshot=replace(snapshot, documents=docs)), original)
        docs = dict(snapshot.documents)
        changed_summary = deepcopy(self.summary)
        author = next(record for record in changed_summary["subagents"] if record["lane_id"] == entry["lane_id"])
        author["codex_thread_id"] = ROOT_ID
        docs["delegation-summary.json"] = json.dumps(changed_summary).encode()
        events = [json.loads(line) for line in docs[entry["record"]["trace"]].decode().splitlines()]
        for event in events:
            event["codex_thread_id"] = ROOT_ID
        docs[entry["record"]["trace"]] = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
        altered = deepcopy(data)
        altered["assignment"]["codex_thread_id"] = ROOT_ID
        docs[name] = json.dumps(altered).encode()
        self.assertNotEqual(result_hash(self.run, verification, snapshot=replace(snapshot, documents=docs)), original)
        for malformed in ([], {"lanes": [1]}, {"lanes": [{"id": entry["lane_id"], "boundary": []}]}):
            docs = {**snapshot.documents, "lane-map.json": json.dumps(malformed).encode()}
            with self.subTest(malformed=malformed):
                with self.assertRaises(EvidenceError):
                    result_hash(self.run, verification, snapshot=replace(snapshot, documents=docs))

    def test_phase1_author_presentation_product_audit_and_proof_integrity(self):
        from verification_evidence import verify_author_declaration
        record = self.summary["subagents"][0]
        peer = self.summary["subagents"][1]["lane_id"]
        handoff = self.run / record["handoff"]
        evidence = self.run / "checks.md"
        declaration = {"lane_id": record["lane_id"], "authored_paths": ["result.txt"],
                       "handoff": {"path": record["handoff"], "sha256": ""},
                       "edit_evidence": [{"path": "checks.md", "sha256": sha256(evidence.read_bytes())}],
                       "coauthors": {"result.txt": [peer]}}
        for text, audit_error in (("Completed implementation.\n", "confirm authored_paths"),
                                  ("Changed result.txt.\n", "coauthors must be disclosed"),
                                  (f"Changed result.txt with {peer}.\n", None)):
            with self.subTest(text=text):
                handoff.write_text(text)
                declaration["handoff"]["sha256"] = sha256(handoff.read_bytes())
                snapshot = JournalSnapshot.open(self.run)
                verified = verify_author_declaration(snapshot, declaration, lane_id=record["lane_id"],
                                                     presentation_required=False)
                self.assertEqual(verified["codex_thread_id"], record["codex_thread_id"])
                if audit_error:
                    with self.assertRaisesRegex(EvidenceError, audit_error):
                        verify_author_declaration(snapshot, declaration, lane_id=record["lane_id"])
                else:
                    self.assertEqual(verify_author_declaration(snapshot, declaration, lane_id=record["lane_id"]), verified)
        handoff.write_text("Altered proof after declared SHA.\n")
        with self.assertRaisesRegex(EvidenceError, "sha256 mismatch"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id=record["lane_id"],
                                      presentation_required=False)
        declaration["handoff"]["sha256"] = sha256(handoff.read_bytes())
        evidence.write_text("Altered edit proof after declared SHA.\n")
        with self.assertRaisesRegex(EvidenceError, "sha256 mismatch"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id=record["lane_id"],
                                      presentation_required=False)
        declaration["edit_evidence"][0]["sha256"] = sha256(evidence.read_bytes())
        declaration["authored_paths"] = ["../outside"]
        with self.assertRaisesRegex(EvidenceError, "authored_paths invalid"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id=record["lane_id"],
                                      presentation_required=False)
        declaration["authored_paths"] = ["result.txt"]
        declaration["coauthors"]["result.txt"] = [record["lane_id"]]
        with self.assertRaisesRegex(EvidenceError, "coauthors must be disclosed"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id=record["lane_id"],
                                      presentation_required=False)
        declaration["coauthors"]["result.txt"] = [peer]
        declaration["lane_id"] = "unassigned-author"
        with self.assertRaisesRegex(EvidenceError, "assignment missing"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id="unassigned-author",
                                      presentation_required=False)
        declaration["lane_id"] = record["lane_id"]
        trace = self.run / record["trace"]
        trace.write_text(json.dumps({"lane_id": record["lane_id"], "role": "foreign-role",
                                     "codex_thread_id": record["codex_thread_id"]}) + "\n")
        with self.assertRaisesRegex(EvidenceError, "trace identity mismatch"):
            verify_author_declaration(JournalSnapshot.open(self.run), declaration, lane_id=record["lane_id"],
                                      presentation_required=False)

    def test_phase1_v5_unchanged_hash_does_not_accept_altered_source_proof(self):
        from verification_evidence import accepted_record
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        verification = self.summary["verification"]
        original = result_hash(self.run, verification)
        record = self.summary["subagents"][0]
        transact(self.run, "tampered-qa-proof", {}, lambda current: ({record["handoff"]: "changed conclusion bytes"}, {}))
        self.assertEqual(result_hash(self.run, verification), original)
        with self.assertRaisesRegex(EvidenceError, "sha256 mismatch"):
            accepted_record(self.run, record, verification, "qa-verifier", self.source,
                            snapshot=JournalSnapshot.open(self.run), presentation_required=False)

    def test_phase1_v5_requirements_binding_relocation_and_unknown_marker(self):
        from unittest.mock import patch
        from task_workspace import registered_workspace
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        snapshot = JournalSnapshot.open(self.run)
        verification = deepcopy(self.summary["verification"])
        original = result_hash(self.run, verification, snapshot=snapshot)
        requirements = reference_bytes(self.run, verification["task_scope"], snapshot=snapshot)
        relocated = {"path": "artifacts/relocated-requirements.txt", "sha256": sha256(requirements)}
        transact(self.run, "relocated-requirements", {}, lambda current: ({relocated["path"]: requirements}, {}))
        verification["task_scope"] = relocated
        self.assertEqual(result_hash(self.run, verification), original)
        changed = requirements + b"\nAdditional substantive requirement.\n"
        transact(self.run, "changed-requirements", {}, lambda current: ({relocated["path"]: changed}, {}))
        verification["task_scope"]["sha256"] = sha256(changed)
        current_digest = result_hash(self.run, verification)
        self.assertNotEqual(current_digest, original)
        verification["result_hash"] = current_digest
        from verification_evidence import accepted_record
        with self.assertRaisesRegex(EvidenceError, "reviewed_result_hash is stale"):
            accepted_record(self.run, self.summary["subagents"][0], verification, "qa-verifier", self.source,
                            snapshot=JournalSnapshot.open(self.run))
        snapshot = JournalSnapshot.open(self.run)
        retained = registered_workspace(snapshot)
        retained["seal"]["result_hash_version"] = 99
        with patch("verification_evidence.registered_workspace", return_value=retained):
            with self.assertRaisesRegex(EvidenceError, "unsupported result_hash_version"):
                result_hash(self.run, verification, snapshot=snapshot)

    def test_phase1_v5_presentation_invariant_permissions_stale_and_legacy_exact(self):
        from unittest.mock import patch
        from task_workspace import registered_workspace
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                     if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        snapshot = JournalSnapshot.open(self.run)
        verification = self.summary["verification"]
        original = result_hash(self.run, verification, snapshot=snapshot)
        lanes = {"lanes": [{"id": record["lane_id"], "role": record["role"],
                             "execution_mode": "subagent", "codex_thread_id": record["codex_thread_id"]}
                            for record in self.summary["subagents"]]}
        lanes["lanes"].append({"id": "fixture-root-author", "role": "python-worker", "execution_mode": "role-lane", "boundary": {"allowed_paths": ["result.txt"],
                              "changed_paths_artifact": "checks/author.json"}})
        declaration = json.loads(snapshot.read_text("checks/lane-boundary-fixture-root-author.json"))
        transact(self.run, "declared-boundary", {}, lambda current: ({"lane-map.json": json.dumps(lanes),
                 "checks/author.json": json.dumps(declaration)}, {}))
        bound = result_hash(self.run, verification)
        self.assertNotEqual(bound, original)
        declaration["notes"] = "different presentation"
        transact(self.run, "formatted-boundary", {}, lambda current: ({"checks/author.json": json.dumps(declaration, indent=4)}, {}))
        self.assertEqual(result_hash(self.run, verification), bound)
        lanes["lanes"][-1]["boundary"]["forbidden_paths"] = ["result.txt"]
        transact(self.run, "changed-permission", {}, lambda current: ({"lane-map.json": json.dumps(lanes)}, {}))
        self.assertNotEqual(result_hash(self.run, verification), bound)
        snapshot = JournalSnapshot.open(self.run)
        retained = registered_workspace(snapshot)
        retained["seal"].pop("result_hash_version")
        with patch("verification_evidence.registered_workspace", return_value=retained):
            legacy = result_hash(self.run, verification, snapshot=snapshot)
            refs = {name: {"path": verification[name]["path"], "section": verification[name].get("section"),
                          "sha256": sha256(reference_bytes(self.run, verification[name], snapshot=snapshot))}
                    for name in ("initial_snapshot", "task_scope")}
            payload = {"hash_version": 4, "workspace_id": retained["workspace_id"],
                       "candidate_id": retained["seal"]["candidate_id"], "baseline_manifest_digest": retained["baseline_digest"],
                       "candidate_manifest_digest": retained["seal"]["candidate_digest"], "delta": retained["seal"]["delta"],
                       "metadata_scope": retained.get("metadata_scope"), **refs, "delivery_scope": None,
                       "boundary_declarations": [{"lane_id": "fixture-root-author", "path": "checks/author.json",
                                                 "sha256": sha256(snapshot.read_bytes("checks/author.json"))}]}
            self.assertEqual(legacy, sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()))

    def test_negative_gaps_keep_declared_evidence_integrity(self):
        claim = {"id": "workflow", "claim": "Workflow complete", "subjects": ["candidate"],
                 "status": "gap", "owner_lane": "qa", "reviewed_by": "review", "evidence": []}
        lanes = {"qa": {"type": "qa", "status": "blocked"}, "review": {"type": "review", "status": "planned"}}
        statuses = {"qa": "blocked", "review": "planned"}
        def claims(verdict):
            write_json(self.run / "claim-evidence.json", {"version": 1, "claims": [claim]})
            return validator.validate_claim_evidence_records(self.run, required_claim_ids=["workflow", "unverified-criterion"],
                required=False, final_verdict=verdict, lane_by_id=lanes, normalized_status_by_id=statuses)
        self.assertEqual(claims("blocked"), [])
        self.assertTrue(claims("ship"))
        claim["status"] = "supported"
        self.assertTrue(claims("blocked"))
        claim.update(status="gap", evidence=[{"path": "missing-proof.txt", "markers": ["observed"]}])
        self.assertIn("not found", " ".join(claims("blocked")))
        claim["evidence"] = [{"path": "checks.md", "markers": ["fabricated-marker"]}]
        self.assertIn("not found", " ".join(claims("fail")))
        claim["owner_lane"] = "unknown"
        self.assertIn("owner_lane not found", " ".join(claims("blocked")))
        acceptance = {"id": "workflow", "source": "Approved plan", "requirement": "Runtime workflow complete",
            "subjects": ["candidate"], "contract_types": ["cli"], "status": "gap",
            "surface_expectations": [{"surface": "cli", "polarity": "positive", "proof_kinds": ["integration-test"]}],
            "evidence": [], "negative_fixture_evidence": []}
        def criteria(verdict):
            write_json(self.run / "acceptance-traceability.json", {"version": 1, "acceptance": [acceptance]})
            return validator.validate_acceptance_traceability_records(self.run, required_acceptance_ids=["workflow", "unverified-criterion"],
                required=False, final_verdict=verdict)
        self.assertEqual(criteria("blocked"), [])
        self.assertTrue(criteria("pass-with-risks"))
        acceptance["status"] = "supported"
        self.assertTrue(criteria("fail"))
        acceptance.update(status="gap", evidence=[{"path": "missing-proof.txt", "markers": ["observed"],
            "surface": acceptance["surface_expectations"][0]["surface"], "polarity": "positive",
            "proof_kind": acceptance["surface_expectations"][0]["proof_kinds"][0]}])
        self.assertIn("not found", " ".join(criteria("blocked")))

    def test_v2_common_root_context_workflow_acceptance_and_delivery(self):
        from journal_io import initialize_journal
        from journal_lifecycle import finalize
        import task_workspace as workspace
        recorder = module("scope_recorder", "record-agent-trace.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            primary, secondary, context = root / "primary", root / "secondary", root / "instructions.md"
            primary.mkdir(); secondary.mkdir()
            workspace.git_command(primary, "init", "-q")
            (primary / "result.txt").write_text("before\n")
            (secondary / "extra.txt").write_text("secondary\n")
            context.write_text("Task instructions only\n")
            (primary / "AGENTS.md").symlink_to(context)
            run = root / ".agent-work/runs/neutral-positive"
            verification = {**empty_verification(), "task_kind": "change", "root_thread_id": ROOT_ID,
                            "author_thread_ids": [ROOT_ID]}
            summary = {"version": 1, "notes": "Common-root real workflow", "subagents_used": False,
                "role_lanes_used": False, "subagents": [], "role_lanes": [], "verification": verification}
            initialize_journal(run, {"run.md": b"# Neutral change\n", "checks.md": b"# Checks\n",
                "context.md": b"# Context\n\n## Initial Worktree Snapshot\n\nKnown complete topology.\n",
                "plan.md": b"# Plan\n\nChange primary/result.txt.\n", "final.md": b"Verdict: pending\n",
                "delegation-summary.json": json.dumps(summary).encode()}, source_root=root)
            prepared = workspace.prepare(run, [primary, secondary], root / "owned", context_roots=[context], identifier="scope")
            (Path(prepared["working_root"]) / "primary/result.txt").write_text("after\n")
            self.assertEqual(changed_paths(run, {}, verification), {"primary/result.txt"})
            self.assertIsNone(result_hash(run, verification, require_ready=False))
            sealed = workspace.seal(run, identifier="candidate")
            snapshot = JournalSnapshot.open(run)
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            verification = summary["verification"]
            verification["result_files"] = sealed["changed_paths"]
            verification.update(initial_snapshot=reference(run, "context.md", "Initial Worktree Snapshot"),
                                task_scope=reference(run, "plan.md"))
            from types import SimpleNamespace
            publish_workspace_authors(SimpleNamespace(run=run, summary=summary), {**prepared, "seal": sealed})
            verification["result_hash"] = result_hash(run, verification)
            transact(run, "scope-refs", {}, lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
            source = SyntheticSource()
            workflow_process = subprocess.run([sys.executable, "-B", "-c",
                "from pathlib import Path; root=Path('.'); assert (root/'primary/result.txt').read_text()=='after\\n'; assert (root/'secondary/extra.txt').read_text()=='secondary\\n'; print('Candidate workflow loaded complete product topology successfully.')"],
                cwd=sealed["candidate_root"], capture_output=True, text=True)
            self.assertEqual(workflow_process.returncode, 0, workflow_process.stderr)
            observed = workflow_process.stdout
            workflow = {"path": "checks/workflow.txt", "sha256": sha256(observed.encode()), "source_event": 4, "source_call_id": "workflow-call"}
            aliases = [item["relative_path"] for item in prepared["task_scope"]["context_exclusions"]]
            self.assertEqual(aliases, ["primary/AGENTS.md"])
            for role, thread, second in (("qa-verifier", QA_ID, 10), ("reviewer", REVIEWER_ID, 20)):
                lane = "scope-" + role
                handoff = "handoffs/" + lane + ".md"
                handoff_data = ("# Independent completeness assessment\n\nExcluded alias is instructions only; candidate workflow covers runtime dependencies.\n\n" + workflow["path"] + " " + workflow["sha256"] + "\n").encode()
                transact(run, "publish-" + lane, {}, lambda s: ({handoff: handoff_data, workflow["path"]: observed.encode()}, {}))
                source.sessions[thread] = session(thread, role, {}, second)
                common = ["--run-dir", str(run), "--role", role, "--lane-id", lane]
                with redirect_stdout(StringIO()):
                    recorder.main([*common, "--codex-thread-id", thread, "--stage", "spawned", "--status", "active",
                                   "--summary", "Independent candidate assessment"], session_source=source)
                classification = {"task_scope_digest": sealed["task_scope_digest"], "context_exclusions": aliases}
                if role == "qa-verifier":
                    classification["workflow_evidence"] = [workflow]
                with redirect_stdout(StringIO()) as output:
                    recorder.main([*common, "--prepare-conclusion", "--status", "pass", "--artifact", handoff,
                        "--artifact", workflow["path"], "--context-classification-json", json.dumps(classification)], session_source=source)
                answer = json.loads(output.getvalue())
                source.sessions[thread] = session(thread, role, answer, second)
                if role == "qa-verifier":
                    source.sessions[thread].insert(3, {"type": "response_item", "payload": {
                        "type": "function_call_output", "call_id": "workflow-call", "output": observed}})
                with redirect_stdout(StringIO()):
                    recorder.main([*common, "--codex-thread-id", thread, "--completion-turn-id", thread + "-turn",
                        "--stage", "handoff", "--status", "pass", "--summary", "Accepted complete candidate",
                        "--artifact", handoff, "--artifact", workflow["path"]], session_source=source)
            snapshot = JournalSnapshot.open(run)
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            finalize(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                expected_generation=snapshot.generation, identifier="scope-final", final_summary="Scoped local result independently accepted", verdict="ship", session_source=source)
            delivered = workspace.delivery(run, identifier="scope-delivery", session_source=source)
            self.assertEqual(delivered["result_hash"], verification["result_hash"])
            self.assertEqual((primary / "result.txt").read_text(), "before\n")
            original_session = deepcopy(source.sessions[QA_ID])
            for field, value in (("task_scope_digest", "0" * 64), ("context_exclusions", []), ("workflow_evidence", [])):
                changed = deepcopy(original_session)
                answer = json.loads(changed[-1]["payload"]["last_agent_message"])
                answer["context_classification"][field] = value
                changed[-1]["payload"]["last_agent_message"] = json.dumps(answer)
                changed[-2]["payload"]["content"][0]["text"] = json.dumps(answer)
                source.sessions[QA_ID] = changed
                self.assertTrue(validator.validate_run(run, session_source=source))
            source.sessions[QA_ID] = original_session
            from verification_evidence import validate_verification
            negative = deepcopy(summary)
            negative["verification"].update(blocker="Further workflow unverified", unverified=["Further QA"])
            self.assertEqual(validate_verification(run, negative, {}, "blocked", source), [])
            negative["verification"]["result_files"] = []
            self.assertIn("full workspace delta", " ".join(validate_verification(run, negative, {}, "blocked", source)))

    def test_negative_source_completions_before_prepare_and_retry(self):
        from journal_io import initialize_journal
        from journal_lifecycle import finalize
        from task_workspace import delivery
        recorder = module("negative_recorder", "record-agent-trace.py")
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve() / "run"
            verification = {**empty_verification(), "task_kind": "change", "root_thread_id": ROOT_ID}
            summary = {"version": 1, "notes": "Neutral halted workflow", "subagents_used": False,
                       "role_lanes_used": False, "subagents": [], "role_lanes": [], "verification": verification}
            # Historical starts predate prepare-first; this tests their new negative
            # completion, not admission of a fresh unprepared change assignment.
            documents = {}
            starts = []
            for role, thread in (("qa-verifier", QA_ID), ("reviewer", REVIEWER_ID)):
                record = {"lane_id": "negative-" + role, "role": role, "codex_thread_id": thread,
                          "status": "active", "trace": "agents/" + role + "/trace.jsonl"}
                summary["subagents"].append(record)
                event = trace_event({**record, "handoff": "handoffs/negative-" + role + ".md"}, "spawned")
                starts.append(json.dumps(event) + "\n")
                documents[record["trace"]] = starts[-1].encode()
            summary["subagents_used"] = True
            documents["timeline.jsonl"] = "".join(starts).encode()
            initialize_journal(run, {"run.md": b"# Neutral run\n", "checks.md": b"# Unverified\n",
                "final.md": b"Verdict: pending\n", "delegation-summary.json": json.dumps(summary).encode(), **documents},
                source_root=run.parent)
            class ResolvedNegativeSource(SyntheticSource):
                def resolve_session(self, agent_path, root_id, role, *, thread_id=None):
                    selected = REVIEWER_ID if role == "reviewer" else QA_ID
                    assert root_id == ROOT_ID and thread_id in {None, selected}
                    return {"codex_thread_id": selected, "agent_path": agent_path,
                            "observed_spawn_at": self.sessions[selected][1]["timestamp"],
                            "session_meta_event": 1, "task_started_event": 2}
            source = ResolvedNegativeSource()
            for role, thread, status in (("qa-verifier", QA_ID, "blocked"), ("reviewer", REVIEWER_ID, "fail")):
                lane = "negative-" + role
                evidence = "checks/" + lane + ".txt"
                handoff = "handoffs/" + lane + ".md"
                data = b"Observed local limitation; workflow not executed.\n"
                handoff_data = ("# Negative conclusion\n\n" + evidence + " " + sha256(data) + "\n").encode()
                transact(run, "publish-" + lane, {}, lambda s: ({evidence: data, handoff: handoff_data}, {}))
                source.sessions[thread] = session(thread, role, {}, 10 if role == "qa-verifier" else 20)
                common = ["--run-dir", str(run), "--role", role, "--lane-id", lane]
                with redirect_stdout(StringIO()) as stdout:
                    recorder.main([*common, "--prepare-conclusion", "--status", status, "--reason", "Snapshot unavailable",
                        "--unverified", "Candidate workflow", "--artifact", handoff, "--artifact", evidence], session_source=source)
                answer = json.loads(stdout.getvalue())
                self.assertIsNone(answer["reviewed_result_hash"])
                self.assertNotIn("qa_handoff_sha256", answer)
                source.sessions[thread] = session(thread, role, answer, 10 if role == "qa-verifier" else 20)
                argv = [*common, "--codex-thread-id", thread, "--completion-turn-id", thread + "-turn",
                        "--stage", status, "--status", status, "--summary", "Actual negative source completion",
                        "--artifact", handoff, "--artifact", evidence, "--operation-id", "complete-" + lane]
                if role == "reviewer":
                    argv = [*common, "--resolve-session", "--agent-path", "/root/negative-reviewer", "--stage", status,
                            "--status", status, "--summary", "Resolved actual negative completion", "--artifact", handoff,
                            "--artifact", evidence, "--operation-id", "complete-" + lane]
                with redirect_stdout(StringIO()):
                    self.assertEqual(recorder.main(argv, session_source=source), 0)
                    revision = JournalSnapshot.open(run).revision
                    self.assertEqual(recorder.main(argv, session_source=source), 0)
                self.assertEqual(JournalSnapshot.open(run).revision, revision)
            snapshot = JournalSnapshot.open(run)
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            self.assertIsNone(summary["verification"]["qa"])
            self.assertIsNone(summary["verification"]["reviewer"])
            self.assertEqual([r["status"] for r in summary["subagents"]], ["blocked", "fail"])
            finalize(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                     expected_generation=snapshot.generation, identifier="negative-final", final_summary="Snapshot unavailable; candidate workflow remains unverified",
                     verdict="fail", session_source=source)
            with self.assertRaisesRegex(EvidenceError, "product acceptance requires a positive verdict"):
                delivery(run, identifier="negative-delivery", session_source=source)
            source.sessions[QA_ID][-1]["payload"]["last_agent_message"] = json.dumps({"verdict": "passed"})
            self.assertTrue(validator.validate_run(run, session_source=source))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "run.md").write_text("# Synthetic compact change\n")
        (self.run / "checks.md").write_text("# Checks\n\nFixture assertions.\n")
        self.summary, self.source = acceptance_pack(self.run)

    def validate(self, **kwargs):
        write_json(self.run / "delegation-summary.json", self.summary)
        return validator.validate_run(self.run, session_source=self.source, **kwargs)

    def test_impl018_exact_accepted_registered_v1_reader_recorder_and_delivery_replay(self):
        from journal_lifecycle import finalize
        from task_workspace import verify_candidate, delivery, registered_workspace
        from journal_io import encode_json
        timeline = self.run / "timeline.jsonl"
        historical_final_event = next(json.loads(line) for line in timeline.read_text().splitlines() if json.loads(line).get("stage") == "final")
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines() if json.loads(line).get("stage") != "final") + "\n")
        workspace_pack(self)
        workspace = registered_workspace(JournalSnapshot.open(self.run))
        sealed = verify_candidate(workspace)
        for record in self.summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        transact(self.run, "legacy-current-accepted", {}, lambda current: ({"delegation-summary.json": json.dumps(self.summary)}, {}))
        lane_id = "historical-author"
        artifact = {"version": 1, "lane_id": lane_id, "status": "captured", "base_ref": "workspace-baseline",
            "head_ref": sealed["candidate_id"], "changed_paths": sealed["changed_paths"], "tracked_changed_paths": [], "untracked_paths": [],
            "command": "task-workspace full baseline-to-candidate inventory", "workspace_id": workspace["workspace_id"],
            "candidate_id": sealed["candidate_id"], "baseline_digest": workspace["baseline_digest"], "candidate_digest": sealed["candidate_digest"],
            "notes": "Full workspace boundary evidence for historical-author."}
        path = "checks/lane-boundary-historical-author.json"
        # Historical recorder receipt retained byte-for-byte; new v1 captures remain forbidden.
        transact(self.run, "old-boundary-operation", artifact, lambda current: ({path: encode_json(artifact, pretty=True) + "\n"}, {}))
        recorder = module("legacy_boundary_replay", "record-lane-boundary.py")
        with self.assertRaisesRegex(EvidenceError, "requires explicit author declaration"):
            recorder.capture_boundary(run_dir=self.run, lane_id=lane_id, repo_root=None, base_ref="HEAD", head_ref="working-tree", identifier="old-boundary-operation")
        snapshot = JournalSnapshot.open(self.run)
        # Explicit historical accepted receipt, with actual validation and
        # captured source inputs. Fresh byte-based finalization is not used.
        from journal_io import _transact, digest
        from journal_lifecycle import capture_validation, freeze_validation, load_validator
        from dataclasses import replace
        documents = {"final.md": snapshot.read_bytes("final.md"),
                     "timeline.jsonl": snapshot.read_bytes("timeline.jsonl") + (encode_json(historical_final_event) + "\n").encode()}
        proposed = replace(snapshot, documents={**snapshot.documents, **documents})
        captured, source = capture_validation(proposed, session_source=self.source)
        validator_bytes = captured.external_inputs.file(SCRIPTS / "validate-run.py")
        historical_validator = load_validator(validator_bytes)
        self.assertEqual(historical_validator.validate_product_acceptance(self.run, snapshot=captured, session_source=source, verdict="ship"), [])
        freeze_validation(captured, source)
        payload = {"command": "finalize", "run_uuid": snapshot.run_uuid, "revision": snapshot.revision,
                   "generation": snapshot.generation, "final_sha256": digest(documents["final.md"]), "verdict": "ship"}
        receipt = {"command": "finalize", "generation": snapshot.generation, "validated_revision": snapshot.revision,
                   "validator_version": 2, "validator_sha256": digest(validator_bytes), "input_hashes": captured.external_inputs.hashes(),
                   "final_sha256": payload["final_sha256"], "verdict": "ship"}
        historical = _transact(self.run, "legacy-accepted-final", payload, lambda current: (documents, receipt), lifecycle="finalize")
        self.assertEqual(finalize(self.run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision, expected_generation=snapshot.generation,
                                 identifier="legacy-accepted-final", final_bytes=documents["final.md"], verdict="ship"), historical)
        accepted = JournalSnapshot.open(self.run)
        revision = accepted.revision
        self.assertEqual(recorder.capture_boundary(run_dir=self.run, lane_id=lane_id, repo_root=None, base_ref="HEAD", head_ref="working-tree", identifier="old-boundary-operation"), self.run / path)
        self.assertEqual(JournalSnapshot.open(self.run).revision, revision)
        lane = {"id": lane_id, "type": "implementation", "status": "pass", "wave": 1,
            "boundary": {"allowed_paths": sealed["changed_paths"], "forbidden_paths": [], "changed_paths_artifact": path, "notes": "Historical accepted full boundary"}, "evidence": [path]}
        self.assertEqual(validator.validate_lane_boundary(self.run, lane, lane_id, required=True, allow_pending=False, snapshot=accepted)[1], [])
        from dataclasses import replace
        from types import MappingProxyType
        full_gate = json.dumps({"schema_version": 2, "architecture_contract_required": True, "lanes": [lane]}).encode()
        projected = replace(accepted, documents=MappingProxyType({**accepted.documents, "lane-map.json": full_gate}))
        def author_gate_errors(snapshot):
            return [error for error in validator.validate_lane_map(self.run, snapshot=snapshot) if error.startswith("author ") or "boundary version 2" in error]
        self.assertEqual(author_gate_errors(projected), [])
        # Exact accepted generation author gate differs from a new open v1 result.
        open_timeline = b"\n".join(line for line in projected.documents["timeline.jsonl"].splitlines() if json.loads(line).get("stage") != "final") + b"\n"
        reopened = replace(projected, documents=MappingProxyType({**projected.documents, "timeline.jsonl": open_timeline}))
        self.assertTrue(any("boundary version 2" in error for error in author_gate_errors(reopened)))
        self.assertEqual(validator.validate_run(self.run, session_source=self.source), [])
        supplied = delivery(self.run, session_source=self.source)
        self.assertEqual(delivery(self.run, session_source=self.source), supplied)
        with self.assertRaisesRegex(EvidenceError, "requires explicit author declaration"):
            recorder.capture_boundary(run_dir=self.run, lane_id=lane_id, repo_root=None, base_ref="HEAD", head_ref="working-tree", identifier="new-v1-operation")

    def clear_assignment_history(self):
        import shutil
        shutil.rmtree(self.run / "agents")
        (self.run / "timeline.jsonl").unlink()

    def rejected(self, needle):
        self.assertIn(needle, "\n".join(self.validate()))

    def test_compact_and_auto_accept_complete_evidence(self):
        self.assertEqual(self.validate(mode="compact"), [])
        self.assertEqual(self.validate(), [])

    def test_missing_each_reviewer(self):
        for role in ["qa", "reviewer"]:
            with self.subTest(role=role):
                old = self.summary["verification"][role]
                self.summary["verification"][role] = None
                self.rejected(f"verification.{role} requires")
                self.summary["verification"][role] = old

    def test_author_or_same_session_cannot_review(self):
        for thread in [ROOT_ID, QA_ID]:
            self.summary["subagents"][1]["codex_thread_id"] = thread
            self.rejected("must be distinct")

    def test_qa_alias_cannot_replace_reviewer(self):
        self.source.sessions[REVIEWER_ID][0]["payload"]["agent_role"] = "qa-verifier"
        self.rejected("source agent_role must be reviewer")

    def test_wrong_model_and_inherited_context(self):
        events = self.source.sessions[QA_ID]
        original_model = events[2]["payload"]["model"]
        events[2]["payload"]["model"] = "gpt-5.6-sol"
        self.rejected("source model must be")
        events[2]["payload"]["model"] = original_model
        events.insert(1, events.pop(2))
        self.rejected("own task_started and turn_context")

    def test_foreign_and_conflicting_metadata(self):
        meta = self.source.sessions[QA_ID][0]["payload"]
        for key in ["id", "parent_thread_id", "session_id"]:
            with self.subTest(key=key):
                old = meta[key]
                meta[key] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
                self.assertTrue(self.validate())
                meta[key] = old
        meta["source"]["subagent"]["thread_spawn"]["agent_role"] = "reviewer"
        self.rejected("agent_role")

    def test_missing_and_foreign_completion(self):
        events = self.source.sessions[QA_ID]
        last = events.pop()
        self.rejected("own completed turn missing")
        last["payload"]["turn_id"] = "foreign-turn"
        events.append(last)
        self.rejected("own completed turn missing")

    def test_negative_source_verdict_cannot_be_promoted(self):
        events = self.source.sessions[QA_ID]
        answer = json.loads(events[-1]["payload"]["last_agent_message"])
        answer["verdict"] = "fail"
        events[-1]["payload"]["last_agent_message"] = json.dumps(answer)
        events[-2]["payload"]["content"][0]["text"] = json.dumps(answer)
        self.rejected("source verdict does not accept")

    def test_source_missing(self):
        del self.source.sessions[QA_ID]
        self.rejected("execution unconfirmed")

    def test_historical_product_source_cannot_hide_subject_tag(self):
        from verification_evidence import source_bound_record
        record = self.summary["subagents"][0]
        original = deepcopy(self.source.sessions[QA_ID])
        for tag in (None, {"kind": "unknown"}, {"kind": "product-result"}, {"kind": "requirements-plan"}):
            with self.subTest(tag=tag):
                events = deepcopy(original)
                answer = json.loads(events[-1]["payload"]["last_agent_message"])
                answer["review_subject"] = tag
                events[-1]["payload"]["last_agent_message"] = json.dumps(answer)
                events[-2]["payload"]["content"][0]["text"] = json.dumps(answer)
                self.source.sessions[QA_ID] = events
                with self.assertRaises(EvidenceError):
                    source_bound_record(self.run, record, self.summary["verification"], "qa-verifier", self.source, current=False)
        self.source.sessions[QA_ID] = original
        source_bound_record(self.run, record, self.summary["verification"], "qa-verifier", self.source, current=False)

    def test_mutated_deleted_and_new_result(self):
        path = self.root / "result.txt"
        path.write_text("Changed")
        self.rejected("reviewed_result_hash is stale")
        path.unlink()
        self.rejected("reviewed_result_hash is stale")
        self.summary["verification"]["result_files"].append("new.txt")
        (self.root / "new.txt").write_text("untracked")
        self.rejected("reviewed_result_hash is stale")

    def test_omitted_owned_path(self):
        self.summary["verification"]["run_changed_files"] = ["result.txt", "omitted.txt"]
        self.rejected("result_files omits run-owned paths: omitted.txt")

    def test_parallel_file_does_not_invalidate(self):
        (self.root / "unrelated.txt").write_text("parallel author")
        self.assertEqual(self.validate(), [])

    def test_snapshot_scope_and_evidence_tampering(self):
        for path in ["context.md", "plan.md", "checks.md", "handoffs/evidence-qa.md"]:
            with self.subTest(path=path):
                file = self.run / path
                before = file.read_bytes()
                file.write_bytes(before + b"changed\n")
                self.rejected("sha256 mismatch")
                file.write_bytes(before)

    def test_updated_qa_handoff_still_requires_new_reviewer(self):
        record = self.summary["subagents"][0]
        handoff = self.run / record["handoff"]
        handoff.write_text(handoff.read_text() + "Updated QA checks\n")
        record["handoff_sha256"] = sha256(handoff.read_bytes())
        answer = {"verdict": "passed", **{key: record[key] for key in ["reviewed_result_hash", "handoff", "handoff_sha256"]}}
        self.source.sessions[QA_ID] = session(QA_ID, "qa-verifier", answer, 11)
        self.rejected("source qa_handoff_sha256 mismatch")

    def test_analysis_requires_no_product_changes(self):
        self.clear_assignment_history()
        self.summary = {**self.summary, "subagents_used": False, "subagents": [],
                        "verification": {**empty_verification(), "task_kind": "analysis", "root_thread_id": ROOT_ID}}
        (self.run / "final.md").write_text("# Final\n\nVerdict: ship\n\n" + delegation_section(self.summary))
        self.assertEqual(self.validate(), [])
        self.summary["verification"]["result_files"] = ["result.txt"]
        self.rejected("analysis contradicts")

    def test_positive_final_allow_flags_cannot_bypass(self):
        self.summary["verification"]["qa"] = None
        self.assertTrue(self.validate(allow_pending=True, allow_no_check=True))

    def test_paths_cannot_escape(self):
        for name in ["../escape", str(self.root / "result.txt"), ".agent-work/private"]:
            with self.subTest(name=name):
                self.summary["verification"]["result_files"] = [name]
                self.assertTrue(self.validate())
        (self.root / "link").symlink_to(self.root.parent)
        self.summary["verification"]["result_files"] = ["link/outside"]
        self.rejected("path escapes project")

    def test_full_has_same_contract_and_lane_crosscheck(self):
        for name in ["manifest.md", "route.md", "definition-of-done.md", "decisions.md"]:
            (self.run / name).write_text("# Full fixture\n\nVerdict: ship\n")
        (self.run / "artifacts").mkdir()
        (self.run / "checks").mkdir()
        (self.run / "checks/smoke.md").write_text("# Synthetic checks\n")
        write_json(self.run / "artifacts.json", [])
        self.assertEqual(self.validate(mode="full"), [])
        lanes = [{"id": r["lane_id"], "role": r["role"], "type": "qa" if i == 0 else "review",
                  "execution_mode": "subagent", "status": "pass", "wave": i + 1, "critical": False,
                  "handoff": r["handoff"], "evidence": ["checks.md"], "replacement": None}
                 for i, r in enumerate(self.summary["subagents"])]
        write_json(self.run / "lane-map.json", {"schema_version": 1, "lanes": lanes})
        self.assertEqual(self.validate(mode="full"), [])
        lanes[1]["execution_mode"] = "role-lane"
        write_json(self.run / "lane-map.json", {"schema_version": 1, "lanes": lanes})
        self.rejected("is not execution_mode=subagent")

    def test_new_acceptance_after_narrow_fix(self):
        (self.root / "result.txt").write_text("Narrow correction\n")
        digest = result_hash(self.run, self.summary["verification"])
        for index, record in enumerate(self.summary["subagents"]):
            record["reviewed_result_hash"] = digest
            answer = {"verdict": "passed", **{k: record[k] for k in ["reviewed_result_hash", "handoff", "handoff_sha256"]}}
            if index:
                answer["qa_handoff_sha256"] = self.summary["subagents"][0]["handoff_sha256"]
            # Same session can supply a fresh completed turn; no reasoning change.
            events = session(record["codex_thread_id"], record["role"], answer, 30 + index)
            record["completion_turn_id"] += "-retry"
            for event in events:
                if "turn_id" in event["payload"]:
                    event["payload"]["turn_id"] = record["completion_turn_id"]
            self.source.sessions[record["codex_thread_id"]].extend(events[1:])
        self.assertEqual(self.validate(), [])

    def test_pending_blocked_and_malformed(self):
        self.clear_assignment_history()
        self.summary = {**self.summary, "subagents_used": False, "subagents": [], "verification": empty_verification()}
        final = self.run / "final.md"
        final.write_text("# Final\n\nVerdict: pending\n")
        self.assertEqual(self.validate(allow_pending=True), [])
        self.summary["verification"]["task_kind"] = []
        self.assertTrue(self.validate(allow_pending=True))
        self.summary["verification"] = empty_verification()
        final.write_text("# Final\n\nVerdict: blocked\n")
        self.rejected("requires blocker reason")
        self.summary["verification"]["blocker"] = "Local source unavailable; no acceptance claimed."
        self.assertEqual(self.validate(), [])

    def test_cli_preliminary_and_no_source_override(self):
        self.clear_assignment_history()
        self.summary = {**self.summary, "subagents_used": False, "subagents": [], "verification": empty_verification()}
        (self.run / "final.md").write_text("# Final\n\nVerdict: pending\n")
        write_json(self.run / "delegation-summary.json", self.summary)
        command = [sys.executable, str(SCRIPTS / "validate-run.py"), "--run-dir", str(self.run), "--allow-pending"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PRELIMINARY", result.stdout)
        self.assertNotIn("PASS", result.stdout)
        rejected = subprocess.run([*command, "--session-source", "fake.json"], capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)

    def test_production_source_ignores_run_exports(self):
        from verification_evidence import CodexSessionSource
        write_json(self.run / "export.json", self.source.sessions[QA_ID])
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "empty-codex"), "AGENT_FLOW_SESSION_SOURCE": str(self.run / "export.json")}):
            with self.assertRaisesRegex(EvidenceError, "source session.*missing"):
                CodexSessionSource().read(QA_ID)

    def test_local_source_reads_only_selected_uuid_and_observed_shape(self):
        from verification_evidence import CodexSessionSource
        codex = self.root / "codex"
        sessions = codex / "sessions/2026/09/10"
        sessions.mkdir(parents=True)
        (sessions / "unrelated.jsonl").write_text("not JSON, must never be read")
        events = self.source.sessions[QA_ID]
        events.insert(1, {"type": "response_item", "payload": {"type": "reasoning", "summary": "private"}})
        (sessions / f"rollout-synthetic-{QA_ID}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        with patch.dict(os.environ, {"CODEX_HOME": str(codex)}):
            read = CodexSessionSource().read(QA_ID)
        self.assertEqual(read[1]["payload"], {})
        self.assertEqual(read[0]["payload"]["id"], QA_ID)

    def behavioral_fixture(self):
        scenario = "00000000-0000-4000-8000-000000000004"
        texts = ["Synthetic initial input", "Read canonical source", "Question after reading"]
        events = [{"type": "session_meta", "payload": {"id": scenario}},
                  {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "scenario-turn"}}]
        for index, text in enumerate(texts):
            events.append({"type": "response_item", "timestamp": f"2026-09-10T09:00:0{index + 1}+00:00",
                           "payload": {"type": "message", "role": "user" if index == 0 else "assistant",
                                       "content": [{"type": "input_text" if index == 0 else "output_text", "text": text}]}})
        self.source.sessions[scenario] = events
        refs = []
        for index, text in enumerate(texts):
            name = f"scenario-{index}.txt"
            (self.run / name).write_text(text)
            refs.append({**reference(self.run, name), "source_event": index + 3})
        timeline = self.run / "timeline.jsonl"
        event = {**trace_event(self.summary["subagents"][0], "behavior-input-prepared"),
                 "timestamp": "2026-09-10T09:00:00+00:00", "artifacts": [refs[0]["path"]], "input_sha256": refs[0]["sha256"]}
        timeline.write_text(json.dumps(event) + "\n" + timeline.read_text())
        trace = self.run / self.summary["subagents"][0]["trace"]
        trace.write_text(json.dumps(event) + "\n" + trace.read_text())
        refs[0]["prepared_event"] = 1
        check = {"criterion_id": "S10", "session_thread_id": scenario, "strict_inputs": True,
                 "verifier_thread_id": REVIEWER_ID, "handoff": reference(self.run, "handoffs/evidence-reviewer.md"),
                 "inputs": refs[:1], "outputs": refs[1:], "required_order": [[4, 5]]}
        self.summary["verification"]["behavioral_checks"] = [check]
        return check, events

    def test_behavioral_early_inputs_and_actual_outputs(self):
        self.behavioral_fixture()
        self.assertEqual(self.validate(), [])

    def test_behavioral_own_first_metadata_excludes_inherited_turn(self):
        from copy import deepcopy
        check, events = self.behavioral_fixture()
        events[0]["payload"].update(parent_thread_id=ROOT_ID, agent_role="qa-verifier",
                                    source={"subagent": {"thread_spawn": {
                                        "parent_thread_id": ROOT_ID, "agent_role": "qa-verifier"}}})
        parent = [{"type": "session_meta", "payload": {"id": ROOT_ID}},
                  {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "parent-turn"}},
                  deepcopy(events[2]),
                  {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "parent-turn"}}]
        self.source.sessions[ROOT_ID] = parent
        events[1:1] = deepcopy(parent)
        for ref in [*check["inputs"], *check["outputs"]]:
            ref["source_event"] += len(parent)
        check["required_order"] = [[8, 9]]
        self.assertEqual(self.validate(), [])
        check["inputs"][0]["source_event"] = 4
        self.rejected("must belong to own session turn")

    def test_behavioral_late_copy_and_changed_copy(self):
        check, events = self.behavioral_fixture()
        events[2]["timestamp"] = "2026-09-10T08:59:00+00:00"
        self.rejected("prepared after invocation")
        events[2]["timestamp"] = "2026-09-10T09:00:01+00:00"
        (self.run / check["inputs"][0]["path"]).write_text("Late changed copy")
        self.rejected("sha256 mismatch")

    def test_behavioral_encrypted_inputs_cannot_claim_strict_acceptance(self):
        check, events = self.behavioral_fixture()
        events[2]["payload"]["content"] = []
        self.rejected("strict behavioral inputs unconfirmed")
        check["strict_inputs"] = False
        self.assertEqual(self.validate(), [])

    def test_behavioral_synthetic_reordering_rejected(self):
        check, events = self.behavioral_fixture()
        events[3], events[4] = events[4], events[3]
        self.rejected("source bytes mismatch")
        check["outputs"][0]["source_event"] = 5
        check["outputs"][1]["source_event"] = 4
        self.rejected("preserve source order")

    def test_recorder_appends_and_rejects_invalid_before_writes(self):
        recorder = module("record_agent_trace", "record-agent-trace.py")
        record = self.summary["subagents"][0]
        write_json(self.run / "artifacts.json", [])
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(timeline.read_text().splitlines()[:-1]) + "\n")
        before = (self.run / "timeline.jsonl").read_bytes()
        args = ["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", record["lane_id"],
                "--codex-thread-id", QA_ID, "--stage", "handoff", "--status", "pass", "--summary", "QA finished",
                "--completion-turn-id", record["completion_turn_id"], "--artifact", record["handoff"], "--artifact", "checks.md"]
        with redirect_stdout(StringIO()):
            initialize_open_fixture(self.run)
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        self.assertTrue(JournalSnapshot.open(self.run).read_bytes("timeline.jsonl").startswith(before))
        state = dict(JournalSnapshot.open(self.run).documents)
        args.extend(["--verification-json", "[]"])
        with self.assertRaises(SystemExit):
            recorder.main(args, session_source=self.source)
        self.assertEqual(state, dict(JournalSnapshot.open(self.run).documents))

    def test_init_reuse_preserves_filled_summary(self):
        args = [sys.executable, str(SCRIPTS / "init-run.py"), "--repo", str(self.root), "--slug", "reuse"]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(result.stdout.strip()) / "delegation-summary.json"
        content = json.dumps(self.summary).encode()
        matching = {"delegation-summary.json": content}
        timeline = []
        for record in self.summary["subagents"]:
            events = [trace_event(record, "spawned"), trace_event(record, "handoff")]
            matching[record["trace"]] = "".join(json.dumps(event) + "\n" for event in events)
            timeline.extend(events)
        timeline.sort(key=lambda event: event["timestamp"])
        matching["timeline.jsonl"] = "".join(json.dumps(event) + "\n" for event in timeline)
        transact(path.parent, "fixture-summary", {}, lambda snapshot: (
            {**matching, "timeline.jsonl": snapshot.read_text("timeline.jsonl") + matching["timeline.jsonl"]}, {}))
        before = JournalSnapshot.open(path.parent).read_bytes(path)
        result = subprocess.run([*args, "--reuse"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(JournalSnapshot.open(path.parent).read_bytes(path), before)

    def test_malformed_fields_reject_without_crashing(self):
        original = deepcopy(self.summary)
        invalid = {"task_kind": [], "root_thread_id": {}, "author_thread_ids": None,
                   "result_files": {}, "initial_snapshot": [], "task_scope": 1,
                   "qa": [], "reviewer": {}, "behavioral_checks": None}
        for key, value in invalid.items():
            with self.subTest(key=key):
                self.summary = deepcopy(original)
                self.summary["verification"][key] = value
                self.assertTrue(self.validate(allow_pending=True))
        for key, value in {"subagents": None, "role_lanes": {}, "version": True}.items():
            with self.subTest(key=key):
                self.summary = deepcopy(original)
                self.summary[key] = value
                self.assertTrue(self.validate())

    def test_refreshed_summary_cannot_rebind_changed_evidence(self):
        (self.run / "checks.md").write_text("Different QA proof\n")
        self.summary["subagents"][0]["evidence"] = [reference(self.run, "checks.md")]
        self.summary["subagents"][1]["evidence"] = [reference(self.run, "checks.md")]
        self.rejected("must appear in source-bound handoff")

    def test_inherited_completed_turn_before_child_metadata_rejected(self):
        events = self.source.sessions[QA_ID]
        events.append(events.pop(0))
        self.rejected("own completed turn missing")

    def test_reviewer_acceptance_must_follow_qa(self):
        self.source.sessions[REVIEWER_ID][1]["timestamp"] = "2026-09-10T08:00:00+00:00"
        self.source.sessions[REVIEWER_ID][-1]["timestamp"] = "2026-09-10T09:00:00+00:00"
        self.rejected("must follow QA completion")

    def test_changed_baseline_with_refreshed_hash_requires_acceptance(self):
        (self.run / "plan.md").write_text("Expanded task boundaries\n")
        self.summary["verification"]["task_scope"] = reference(self.run, "plan.md")
        self.rejected("reviewed_result_hash is stale")

    def test_pending_spawn_does_not_require_successful_handoff(self):
        reviewer_trace = self.run / self.summary["subagents"][1]["trace"]
        reviewer_trace.unlink()
        reviewer_trace.parent.rmdir()
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines()
                                      if json.loads(line).get("lane_id") != "evidence-reviewer") + "\n")
        self.summary["verification"] = empty_verification()
        self.summary["subagents"] = [{k: v for k, v in self.summary["subagents"][0].items()
                                      if k in {"lane_id", "role", "trace", "codex_thread_id"}}]
        (self.run / "final.md").write_text("# Final\n\nVerdict: pending\n")
        self.assertEqual(self.validate(allow_pending=True), [])

    def test_behavioral_inherited_events_rejected(self):
        check, events = self.behavioral_fixture()
        events[1]["payload"]["type"] = "task_complete"
        self.rejected("must belong to own session turn")

    def test_behavioral_tool_output_requires_call_id(self):
        check, events = self.behavioral_fixture()
        events[3]["payload"] = {"type": "function_call_output", "call_id": "read-source", "output": "Read canonical source"}
        check["outputs"][0]["source_call_id"] = "read-source"
        self.assertEqual(self.validate(), [])
        check["outputs"][0]["source_call_id"] = "another-call"
        self.rejected("call_id mismatch")

    def test_reviewer_alias_requires_actual_canonical_role(self):
        record = self.summary["subagents"][1]
        old_trace = self.run / record["trace"]
        record["role"] = "reviewer.qa"
        record["trace"] = "agents/reviewer.qa/trace.jsonl"
        new_trace = self.run / record["trace"]
        new_trace.parent.mkdir()
        events = [json.loads(line) for line in old_trace.read_text().splitlines()]
        for event in events:
            event["role"] = "reviewer.qa"
        new_trace.write_text("".join(json.dumps(e) + "\n" for e in events))
        old_trace.unlink()
        old_trace.parent.rmdir()
        timeline = self.run / "timeline.jsonl"
        events = [json.loads(line) for line in timeline.read_text().splitlines()]
        for event in events:
            if event.get("role") == "reviewer":
                event["role"] = "reviewer.qa"
        timeline.write_text("".join(json.dumps(e) + "\n" for e in events))
        final = self.run / "final.md"
        final.write_text(final.read_text().replace("agents/reviewer/trace.jsonl", record["trace"]))
        self.assertEqual(self.validate(), [])
        self.source.sessions[REVIEWER_ID][0]["payload"]["agent_role"] = "qa-verifier"
        self.rejected("source agent_role must be reviewer")

    def test_recorder_invalidates_old_review_when_qa_is_refreshed(self):
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(timeline.read_text().splitlines()[:-1]) + "\n")
        recorder = module("record_agent_trace", "record-agent-trace.py")
        record = self.summary["subagents"][0]
        (self.run / "fresh-checks.md").write_text("Fresh targeted checks\n")
        handoff = self.run / "handoffs/fresh-qa.md"
        handoff.write_text("# Fresh QA\n\nfresh-checks.md " + reference(self.run, "fresh-checks.md")["sha256"] + "\n")
        workspace_pack(self)
        answer = {"verdict": "passed", "reviewed_result_hash": record["reviewed_result_hash"],
                  "handoff": "handoffs/fresh-qa.md", "handoff_sha256": sha256(handoff.read_bytes())}
        turn = record["completion_turn_id"] + "-refresh"
        fresh_session = session(QA_ID, "qa-verifier", answer, 30)
        for event in fresh_session:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = turn
        self.source.sessions[QA_ID].extend(fresh_session[1:])
        before = (self.run / "timeline.jsonl").read_bytes()
        args = ["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", record["lane_id"],
                "--codex-thread-id", QA_ID, "--completion-turn-id", turn,
                "--stage", "handoff", "--status", "pass", "--summary", "Fresh QA",
                "--artifact", "handoffs/fresh-qa.md", "--artifact", "fresh-checks.md"]
        with redirect_stdout(StringIO()):
            initialize_open_fixture(self.run)
            with self.assertRaisesRegex(SystemExit, "terminal assignment"):
                recorder.main(args, session_source=self.source)
            # Keep the terminal legacy rejection untagged; only the fresh
            # native assignment's synthetic completion uses its default tag.
            answer["review_subject"] = {"kind": "product-result"}
            fresh_session[-1]["payload"]["last_agent_message"] = json.dumps(answer)
            fresh_session[-2]["payload"]["content"][0]["text"] = json.dumps(answer)
            args[args.index("--lane-id") + 1] = "fresh-qa"
            recorder.main(["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", "fresh-qa",
                           "--codex-thread-id", QA_ID, "--stage", "spawned", "--status", "active",
                           "--summary", "Fresh QA assignment"], session_source=self.source)
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        self.summary = json.loads(JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertIsNone(self.summary["verification"]["reviewer"])
        self.assertEqual(len(self.summary["subagents"]), 3)
        self.assertTrue(JournalSnapshot.open(self.run).read_bytes("timeline.jsonl").startswith(before))


class LocalDeliveryScopeTests(unittest.TestCase):
    def setUp(self):
        EvidenceRegressions.setUp(self)
        self.scope = {"version": 1, "completion_target": "local-deliverable", "external_followup": {
            "acceptance_ids": ["provider-workflow"], "claim_ids": ["provider-accepted"], "lane_ids": [], "gate_refs": [], "risk_ids": []}}
        (self.run / "plan.md").write_text("Local result; provider-workflow provider-accepted remain external follow-up.\n")
        self.summary["verification"].update(delivery_scope=self.scope, task_scope=reference(self.run, "plan.md"))
        self.acceptance = {"version": 1, "acceptance": [{"id": "provider-workflow", "status": "gap", "source": "plan.md",
            "requirement": "Real provider workflow unavailable", "subjects": ["provider"], "contract_types": ["cli"],
            "surface_expectations": [{"surface": "cli", "polarity": "positive", "proof_kinds": ["integration-test"]}], "evidence": [], "negative_fixture_evidence": []}]}
        self.claims = {"version": 1, "claims": [{"id": "provider-accepted", "status": "gap", "claim": "Real provider acceptance unverified",
            "subjects": ["provider"], "owner_lane": None, "reviewed_by": None, "section": None, "evidence": []}]}
        write_json(self.run / "acceptance-traceability.json", self.acceptance)
        write_json(self.run / "claim-evidence.json", self.claims)
        for record in self.summary["subagents"]:
            record.update(obligation={"id": record["role"], "required": True, "state": "current"})
        write_json(self.run / "delegation-summary.json", self.summary)

    def test_impl018_scope_parser_rejects_unknown_duplicate_and_source_drift(self):
        from verification_evidence import delivery_scope
        snapshot = JournalSnapshot.open(self.run)
        self.assertEqual(delivery_scope(snapshot), self.scope)
        for mutate, error in [
            (lambda s: s.update(version=2), "version"),
            (lambda s: s.update(extra=True), "fields"),
            (lambda s: s["external_followup"]["acceptance_ids"].append("provider-workflow"), "duplicates"),
            (lambda s: s["external_followup"]["acceptance_ids"].append("unknown"), "unknown"),
            (lambda s: s["external_followup"].pop("risk_ids"), "fields")]:
            scope = deepcopy(self.scope)
            mutate(scope)
            verification = {**self.summary["verification"], "delivery_scope": scope}
            with self.subTest(error=error), self.assertRaisesRegex(EvidenceError, error):
                delivery_scope(snapshot, verification)
        (self.run / "plan.md").write_text("changed classification\n")
        with self.assertRaisesRegex(EvidenceError, "sha256 mismatch"):
            delivery_scope(JournalSnapshot.open(self.run))

    def test_impl018_scope_cannot_hide_product_risk_or_selected_verifier(self):
        from verification_evidence import delivery_scope
        plan = (self.run / "plan.md").read_text() + " product-defect evidence-qa evidence-source-integrity risk_gates\n"
        (self.run / "plan.md").write_text(plan)
        verification = {**self.summary["verification"], "task_scope": reference(self.run, "plan.md")}
        for risk_key, expected in (("id", "known product defect"), ("risk_id", "unknown risk_ids")):
            write_json(self.run / "risk-mitigations.json", {"risks": [{risk_key: "product-defect", "category": "incomplete-implementation"}]})
            scope = deepcopy(self.scope); scope["external_followup"]["risk_ids"] = ["product-defect"]
            with self.subTest(risk_key=risk_key), self.assertRaisesRegex(EvidenceError, expected):
                delivery_scope(JournalSnapshot.open(self.run), {**verification, "delivery_scope": scope})
        scope = deepcopy(self.scope); scope["external_followup"]["lane_ids"] = [verification["qa"]]
        with self.assertRaisesRegex(EvidenceError, "selected verifier"):
            delivery_scope(JournalSnapshot.open(self.run), {**verification, "delivery_scope": scope})
        write_json(self.run / "lane-map.json", {"architecture_context": {"risk_gates": ["evidence-source-integrity"]}, "lanes": []})
        scope = deepcopy(self.scope); scope["external_followup"]["gate_refs"] = [{"axis": "risk_gates", "facet": "evidence-source-integrity"}]
        with self.assertRaisesRegex(EvidenceError, "integrity gate"):
            delivery_scope(JournalSnapshot.open(self.run), {**verification, "delivery_scope": scope})

    def test_impl018_scoped_gap_preserves_proof_and_rejects_local_gap_or_mock(self):
        snapshot = JournalSnapshot.open(self.run)
        self.assertEqual(validator.validate_acceptance_traceability_records(self.run, required_acceptance_ids=[], required=False, final_verdict="ship", snapshot=snapshot), [])
        self.assertEqual(validator.validate_claim_evidence_records(self.run, required_claim_ids=[], required=False, final_verdict="ship", lane_by_id={}, normalized_status_by_id={}, snapshot=snapshot), [])
        local = deepcopy(self.acceptance["acceptance"][0]); local["id"] = "local-defect"
        self.acceptance["acceptance"].append(local)
        write_json(self.run / "acceptance-traceability.json", self.acceptance)
        errors = validator.validate_acceptance_traceability_records(self.run, required_acceptance_ids=[], required=False, final_verdict="ship", snapshot=JournalSnapshot.open(self.run))
        self.assertTrue(any("must be supported" in error for error in errors), errors)
        self.acceptance["acceptance"].pop()
        self.acceptance["acceptance"][0]["status"] = "supported"
        write_json(self.run / "acceptance-traceability.json", self.acceptance)
        errors = validator.validate_acceptance_traceability_records(self.run, required_acceptance_ids=[], required=False, final_verdict="ship", snapshot=JournalSnapshot.open(self.run))
        self.assertTrue(any("non-empty array" in error for error in errors), errors)
        for status, surface, marker in [("gap", "cli", "FORGED PROVIDER SUCCESS"), ("supported", "parser", "Fixture assertions")]:
            record = self.acceptance["acceptance"][0]; record["status"] = status
            record["evidence"] = [{"path": "checks.md", "markers": [marker], "surface": surface, "polarity": "positive", "proof_kind": "integration-test"}]
            write_json(self.run / "acceptance-traceability.json", self.acceptance)
            errors = validator.validate_acceptance_traceability_records(self.run, required_acceptance_ids=[], required=False, final_verdict="ship", snapshot=JournalSnapshot.open(self.run))
            self.assertTrue(any("not found" in error or "not covered" in error for error in errors), errors)

    def test_impl018_scoped_readiness_and_results_keep_local_gate_strict(self):
        context = {"verification_gates": ["unit", "integration"]}
        self.scope["external_followup"]["gate_refs"] = [{"axis": "verification_gates", "facet": "integration"}]
        with (self.run / "plan.md").open("a") as handle:
            handle.write("External verification_gates integration; local unit stays mandatory.\n")
        self.summary["verification"]["task_scope"] = reference(self.run, "plan.md")
        write_json(self.run / "delegation-summary.json", self.summary)
        qa = {"id": "evidence-qa", "role": "qa-verifier", "type": "qa", "critical": True, "status": "pass", "handoff": "handoffs/evidence-qa.md"}
        with (self.run / qa["handoff"]).open("a") as handle: handle.write("\n## Verification Gate Results\nunit ready; integration unavailable.\n")
        lane_map = {"architecture_context": context, "lanes": [qa], "verification_readiness": {"artifact": "verification-readiness.json", "lanes": [qa["id"]]}}
        write_json(self.run / "lane-map.json", lane_map)
        gates = [{"axis": "verification_gates", "facet": facet, "readiness": status, "check": "fixture", "notes": "Recorded real local fixture error",
                  "evidence": ["checks.md"]} for facet, status in [("unit", "ready"), ("integration", "blocked")]]
        readiness = {"version": 1, "status": "ready", "attempts": [{"id": "local-readiness", "lane": qa["id"], "status": "ready", "gates": gates,
            "blockers": ["integration"], "approval_requests": []}], "approval_requests": [], "approval_executions": []}
        write_json(self.run / "verification-readiness.json", readiness)
        def check():
            return validator.validate_verification_readiness(self.run, lane_map, required=True, architecture_context_by_axis=context,
                lane_by_id={qa["id"]: qa}, normalized_status_by_id={qa["id"]: "pass"}, final_verdict="ship", snapshot=JournalSnapshot.open(self.run))[1]
        self.assertEqual(check(), [])
        gates[0]["readiness"] = "blocked"
        write_json(self.run / "verification-readiness.json", readiness)
        self.assertTrue(any("all be ready" in error for error in check()))
        results = {"status": "pass", "gates": [{"axis": "verification_gates", "facet": facet, "status": status, "notes": "Honest recorded result", "evidence": ["checks.md"]}
            for facet, status in [("unit", "pass"), ("integration", "blocked")]]}
        lane = {**qa, "verification_results": results}
        def result_errors():
            return validator.validate_qa_verification_results_shape(self.run, lane, qa["id"], selected_pairs=[("verification_gates", "unit"), ("verification_gates", "integration")], snapshot=JournalSnapshot.open(self.run))[1]
        self.assertEqual(result_errors(), [])
        results["gates"][0]["status"] = "blocked"
        self.assertTrue(any("all gates pass" in error for error in result_errors()))

    def test_impl018_external_risk_resolution_keeps_open_attempt_and_supplied_proof_strict(self):
        plan = (self.run / "plan.md").read_text() + " external-env\n"
        (self.run / "plan.md").write_text(plan)
        self.scope["external_followup"]["risk_ids"] = ["external-env"]
        self.summary["verification"]["task_scope"] = reference(self.run, "plan.md")
        write_json(self.run / "delegation-summary.json", self.summary)
        risks = [{"id": "external-env", "category": "external-blocker"}]
        write_json(self.run / "risk-mitigations.json", {"risks": risks})
        self.assertEqual(validator.validate_risk_resolutions(self.run, "pass-with-risks", risks, snapshot=JournalSnapshot.open(self.run))[1], [])
        resolution = {"risk_id": "external-env", "status": "unresolved", "resolution_type": "not-resolved", "evidence": ["checks.md"],
            **{key: "Honest external follow-up" for key in ("owner_lane", "resolution", "verification", "verified_by", "reviewed_by")}}
        resolution["attempts"] = [{**resolution, "attempt": 1}]
        write_json(self.run / "risk-resolutions.json", {"version": 1, "resolutions": [resolution]})
        def errors():
            return validator.validate_risk_resolutions(self.run, "pass-with-risks", risks, snapshot=JournalSnapshot.open(self.run))[1]
        self.assertEqual(errors(), [])
        resolution["attempts"][0]["evidence"] = ["missing-proof.md"]
        write_json(self.run / "risk-resolutions.json", {"version": 1, "resolutions": [resolution]})
        self.assertTrue(any("not found" in error for error in errors()))

    def test_impl018_native_scope_history_rejects_delayed_and_allows_explicit_update(self):
        from verification_evidence import validate_delivery_scope_history
        timeline = self.run / "timeline.jsonl"
        timeline.write_text("\n".join(line for line in timeline.read_text().splitlines() if json.loads(line).get("stage") != "final") + "\n")
        initialize_open_fixture(self.run)
        recorder = module("scope_history_recorder", "record-agent-trace.py")
        def bind(verification):
            with redirect_stdout(StringIO()):
                recorder.main(["--run-dir", str(self.run), "--role", "orchestrator", "--execution-mode", "role-lane",
                    "--stage", "verification-prepared", "--status", "active", "--summary", "Explicit scope binding",
                    "--verification-json", json.dumps(verification)], session_source=self.source)
        bind(self.summary["verification"])
        with self.assertRaisesRegex(ValueError, "after corresponding check"):
            validate_delivery_scope_history(JournalSnapshot.open(self.run), self.scope, self.summary["verification"])
        updated = deepcopy(self.summary["verification"])
        updated["qa"] = "fresh-qa"; updated["reviewer"] = "fresh-review"
        updated["delivery_scope"]["external_followup"]["claim_ids"] = []
        def fresh_assignments(current):
            summary = json.loads(current.read_text("delegation-summary.json"))
            documents = {}
            for index, key in enumerate(("qa", "reviewer"), 91):
                former = next(record for record in summary["subagents"] if record["lane_id"] == self.summary["verification"][key])
                record = {"lane_id": updated[key], "role": former["role"],
                    "codex_thread_id": f"00000000-0000-4000-8000-{index:012d}", "trace": f"agents/{updated[key]}/trace.jsonl"}
                summary["subagents"].append(record)
                event = trace_event(record, "spawned")
                event["timestamp"] = current.timeline()[-1]["timestamp"]
                documents[record["trace"]] = json.dumps(event) + "\n"
                documents["timeline.jsonl"] = documents.get("timeline.jsonl", current.read_text("timeline.jsonl")) + json.dumps(event) + "\n"
            documents["delegation-summary.json"] = json.dumps(summary)
            return documents, {}
        transact(self.run, "new-scope-check-assignments", {}, fresh_assignments)
        bind(updated)
        # Old checks stay in history; the updated scope governs new assignments.
        snapshot = JournalSnapshot.open(self.run)
        validate_delivery_scope_history(snapshot, updated["delivery_scope"], updated)
        self.assertGreaterEqual(len([event for event in snapshot.timeline() if event.get("delivery_scope_binding")]), 2)

    def test_impl018_compact_auto_finalize_delivery_and_scope_hash_drift(self):
        from journal_io import render_final
        from journal_lifecycle import finalize
        from task_workspace import delivery
        events = [json.loads(line) for line in (self.run / "timeline.jsonl").read_text().splitlines()]
        (self.run / "timeline.jsonl").write_text("")
        initialize_open_fixture(self.run)
        recorder = module("scope_intake_recorder", "record-agent-trace.py")
        with patch("journal_io.now_iso", return_value="2026-09-10T09:59:59+00:00"), redirect_stdout(StringIO()):
            recorder.main(["--run-dir", str(self.run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification-prepared", "--status", "active", "--summary", "Scope fixed before checks",
                "--verification-json", json.dumps(self.summary["verification"])], session_source=self.source)
        transact(self.run, "scope-check-attempts", {}, lambda current: ({"timeline.jsonl": current.read_text("timeline.jsonl") + "\n".join(json.dumps(event) for event in events[:-1]) + "\n"}, {}))
        workspace_pack(self)
        for record in self.summary["subagents"]:
            record["status"] = "pass"
        transact(self.run, "scope-accepted-assignments", {}, lambda current: ({"delegation-summary.json": json.dumps(self.summary)}, {}))
        snapshot = JournalSnapshot.open(self.run)
        text = render_final(snapshot.read_text("final.md"), self.summary, self.summary["verification"]["result_files"], declared=True, scope=self.scope)
        transact(self.run, "scope-final-report", {}, lambda current: ({"final.md": text}, {}))
        snapshot = JournalSnapshot.open(self.run)
        digest = result_hash(self.run, self.summary["verification"], snapshot=snapshot)
        changed = deepcopy(self.summary["verification"])
        changed["delivery_scope"]["external_followup"]["acceptance_ids"] = []
        self.assertNotEqual(result_hash(self.run, changed, snapshot=snapshot, require_ready=False), digest)
        # No network access is used: synthetic sessions prove only this local CLI workflow.
        with patch("socket.socket", side_effect=AssertionError("external network forbidden in local delivery")):
            finalize(self.run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                expected_generation=snapshot.generation, identifier="scope-finalize", final_summary="Local delivery scope independently accepted; external follow-up retained", verdict="ship", session_source=self.source)
        for mode in ("compact", "auto"):
            self.assertEqual(validator.validate_run(self.run, mode=mode, session_source=self.source), [])
        with patch("socket.socket", side_effect=AssertionError("external network forbidden in local delivery")):
            supplied = delivery(self.run, session_source=self.source)
        self.assertEqual(supplied["result_hash"], digest)
        self.assertEqual(supplied["delivery_scope"], self.scope)
        self.assertIn("остаётся незавершённой", Path(supplied["description"]).read_text())


class WorkerCompletionRegressions(unittest.TestCase):
    def setUp(self):
        self.source = SyntheticSource()
        self.thread = "00000000-0000-4000-8000-000000000004"
        self.text = "Изменения выполнены; проверка описана в handoff."
        self.events = session(self.thread, "python-worker", {}, 5)
        self.events[-2]["payload"]["content"][0]["text"] = self.text
        self.events[-1]["payload"]["last_agent_message"] = self.text
        self.source.sessions[self.thread] = self.events

    def test_plaintext_is_completion_fact_and_qa_stays_json(self):
        from verification_evidence import completed_turn, resolve_completion
        turn = resolve_completion(self.source, self.thread, ROOT_ID, "python-worker", None, [])
        self.assertEqual(completed_turn(self.source, self.thread, turn, ROOT_ID, "python-worker")["answer"], self.text)
        self.source.sessions[QA_ID] = session(QA_ID, "qa-verifier", {}, 10)
        self.source.sessions[QA_ID][-2]["payload"]["content"][0]["text"] = self.text
        self.source.sessions[QA_ID][-1]["payload"]["last_agent_message"] = self.text
        with self.assertRaisesRegex(EvidenceError, "JSON"):
            completed_turn(self.source, QA_ID, QA_ID + "-turn", ROOT_ID, "qa-verifier")

    def test_invalid_own_worker_source_is_rejected(self):
        from verification_evidence import resolve_completion
        pristine = deepcopy(self.events)
        for kind in ("parent", "role", "model", "start", "context", "complete", "final",
                     "duplicate", "continuation", "empty", "time", "uuid"):
            self.events[:] = deepcopy(pristine)
            meta = self.events[0]["payload"]
            if kind in {"parent", "role"}:
                key = "parent_thread_id" if kind == "parent" else "agent_role"
                meta[key] = meta["source"]["subagent"]["thread_spawn"][key] = "wrong"
            elif kind == "model":
                self.events[2]["payload"]["model"] = "wrong"
            elif kind in {"start", "context", "complete"}:
                del self.events[{"start": 1, "context": 2, "complete": 4}[kind]]
            elif kind == "final":
                self.events[-2]["payload"]["content"][0]["text"] = "Другой final."
            elif kind == "duplicate":
                self.events.append(deepcopy(self.events[-1]))
            elif kind == "continuation":
                self.events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new"}})
            elif kind == "empty":
                self.events[-2]["payload"]["content"][0]["text"] = self.events[-1]["payload"]["last_agent_message"] = ""
            elif kind == "time":
                self.events[-1]["timestamp"] = "2026-09-10T09:00:00+00:00"
            with self.subTest(kind=kind), self.assertRaises(EvidenceError):
                resolve_completion(self.source, "invalid" if kind == "uuid" else self.thread,
                                   ROOT_ID, "python-worker", None, [])


if __name__ == "__main__":
    unittest.main()
