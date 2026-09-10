#!/usr/bin/env python3
"""Focused regressions for mandatory QA/reviewer evidence; synthetic data only."""

import importlib.util
import json
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
                                   reference_bytes, result_hash, sha256, changed_paths, confined_path)

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
    digest = result_hash(run, verification)
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
        self.root = Path(self.temp.name)

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
        self.assertTrue(summary.exists())
        self.assertIn("verification", json.loads(summary.read_text()))


class EvidenceRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "run.md").write_text("# Synthetic compact change\n")
        (self.run / "checks.md").write_text("# Checks\n\nFixture assertions.\n")
        self.summary, self.source = acceptance_pack(self.run)

    def validate(self, **kwargs):
        write_json(self.run / "delegation-summary.json", self.summary)
        return validator.validate_run(self.run, session_source=self.source, **kwargs)

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
        events[2]["payload"]["model"] = "gpt-5.6-sol"
        self.rejected("source model must be")
        events[2]["payload"]["model"] = "gpt-6-astra"
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
        final = self.run / "final.md"
        final.write_text(final.read_text().replace("- `result.txt`", "- `result.txt`\n- `omitted.txt`"))
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
        refs[0]["prepared_event"] = 1
        check = {"criterion_id": "S10", "session_thread_id": scenario, "strict_inputs": True,
                 "verifier_thread_id": REVIEWER_ID, "handoff": reference(self.run, "handoffs/evidence-reviewer.md"),
                 "inputs": refs[:1], "outputs": refs[1:], "required_order": [[4, 5]]}
        self.summary["verification"]["behavioral_checks"] = [check]
        return check, events

    def test_behavioral_early_inputs_and_actual_outputs(self):
        self.behavioral_fixture()
        self.assertEqual(self.validate(), [])

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
        before = (self.run / "timeline.jsonl").read_bytes()
        args = ["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", record["lane_id"],
                "--codex-thread-id", QA_ID, "--stage", "handoff", "--status", "pass", "--summary", "QA finished",
                "--completion-turn-id", record["completion_turn_id"], "--artifact", record["handoff"], "--artifact", "checks.md"]
        with redirect_stdout(StringIO()):
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        self.assertTrue((self.run / "timeline.jsonl").read_bytes().startswith(before))
        state = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        args.extend(["--verification-json", "[]"])
        with self.assertRaises(SystemExit):
            recorder.main(args, session_source=self.source)
        self.assertEqual(state, {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()})

    def test_init_reuse_preserves_filled_summary(self):
        args = [sys.executable, str(SCRIPTS / "init-run.py"), "--repo", str(self.root), "--slug", "reuse"]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        path = Path(result.stdout.strip()) / "delegation-summary.json"
        write_json(path, self.summary)
        before = path.read_bytes()
        result = subprocess.run([*args, "--reuse"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_bytes(), before)

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
        self.source.sessions[REVIEWER_ID][-1]["timestamp"] = "2026-09-10T09:00:00+00:00"
        self.rejected("must follow QA completion")

    def test_changed_baseline_with_refreshed_hash_requires_acceptance(self):
        (self.run / "plan.md").write_text("Expanded task boundaries\n")
        self.summary["verification"]["task_scope"] = reference(self.run, "plan.md")
        self.rejected("reviewed_result_hash is stale")

    def test_pending_spawn_does_not_require_successful_handoff(self):
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
        recorder = module("record_agent_trace", "record-agent-trace.py")
        record = self.summary["subagents"][0]
        (self.run / "checks.md").write_text("Fresh targeted checks\n")
        handoff = self.run / record["handoff"]
        handoff.write_text("# Fresh QA\n\nchecks.md " + reference(self.run, "checks.md")["sha256"] + "\n")
        answer = {"verdict": "passed", "reviewed_result_hash": record["reviewed_result_hash"],
                  "handoff": record["handoff"], "handoff_sha256": sha256(handoff.read_bytes())}
        self.source.sessions[QA_ID] = session(QA_ID, "qa-verifier", answer, 30)
        record["completion_turn_id"] += "-refresh"
        for event in self.source.sessions[QA_ID]:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = record["completion_turn_id"]
        before = (self.run / "timeline.jsonl").read_bytes()
        args = ["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", record["lane_id"],
                "--codex-thread-id", QA_ID, "--completion-turn-id", record["completion_turn_id"],
                "--stage", "handoff", "--status", "pass", "--summary", "Fresh QA",
                "--artifact", record["handoff"], "--artifact", "checks.md"]
        with redirect_stdout(StringIO()):
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        self.summary = json.loads((self.run / "delegation-summary.json").read_text())
        self.assertIsNone(self.summary["verification"]["reviewer"])
        self.assertEqual(len(self.summary["subagents"]), 2)
        self.assertTrue((self.run / "timeline.jsonl").read_bytes().startswith(before))


if __name__ == "__main__":
    unittest.main()
