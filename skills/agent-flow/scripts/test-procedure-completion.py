#!/usr/bin/env python3
"""Procedure regressions using anonymized local session records."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import uuid
from journal_io import JournalError, JournalSnapshot, transact, connect_database
from journal_lifecycle import finalize
from unittest.mock import patch

import verification_evidence as evidence
import task_workspace as workspace
from importlib.util import module_from_spec, spec_from_file_location

SCRIPTS = Path(__file__).resolve().parent


def load(name, filename):
    spec = spec_from_file_location(name, SCRIPTS / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load("evidence_fixtures", "test-verification-evidence.py")
recorder = load("procedure_recorder", "record-agent-trace.py")


def publish_fixture(run, name, content):
    transact(run, str(uuid.uuid4()), {}, lambda snapshot: ({name: content}, {}))


def publish_actual_fixture_author(run, verification):
    snapshot = JournalSnapshot.open(run)
    case = SimpleNamespace(run=run, summary=json.loads(snapshot.read_text("delegation-summary.json")))
    case.summary["verification"].update(verification)
    base.publish_workspace_authors(case, workspace.registered_workspace(snapshot))


def corrupt_fixture_document(run, name, content):
    data = content.encode()
    connection = connect_database(run)
    try:
        connection.execute("UPDATE documents SET content=?, sha256=? WHERE path=?",
                           (data, evidence.sha256(data), name))
    finally:
        connection.close()


class ProcedureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = base.SyntheticSource()
        self.events = base.session(base.QA_ID, "qa-verifier", {"verdict": "passed"}, 10)
        self.source.sessions[base.QA_ID] = self.events
        self.turn = base.QA_ID + "-turn"

    def complete(self):
        return evidence.completed_turn(self.source, base.QA_ID, self.turn, base.ROOT_ID, "qa-verifier")

    def numeric(self, outer="2026-09-10T13:41:41.240Z"):
        self.events[-1]["payload"]["completed_at"] = 1789047701
        if outer is None:
            self.events[-1].pop("timestamp")
        else:
            self.events[-1]["timestamp"] = outer

    def test_numeric_observed_format(self):
        self.numeric()
        self.assertEqual(self.complete()["completed_at"], datetime.fromisoformat("2026-09-10T13:41:41+00:00"))

    def test_numeric_adjacent_publication_accepted(self):
        self.numeric("2026-09-10T13:41:42.240Z")
        self.assertIsNotNone(self.complete()["completed_before"])

    def test_invalid_present_inner_not_replaced(self):
        for value in (False, True, None, "", 1.25, [], 10**30):
            with self.subTest(value=value):
                self.events[-1]["payload"]["completed_at"] = value
                with self.assertRaises(evidence.EvidenceError):
                    self.complete()

    def test_numeric_precision_is_interval(self):
        self.numeric(None)
        completion = self.complete()
        self.assertEqual((completion["completed_before"] - completion["completed_at"]).total_seconds(), 1)

    def test_production_reader_preserves_missing_outer_timestamp(self):
        self.numeric(None)
        self.write_session(self.events)
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
            result = evidence.completed_turn(evidence.CodexSessionSource(), base.QA_ID, self.turn, base.ROOT_ID, "qa-verifier")
        self.assertIsNotNone(result["completed_before"])

    def test_completion_before_own_start_rejected(self):
        self.events[-1]["timestamp"] = "2026-09-10T09:00:00Z"
        with self.assertRaisesRegex(evidence.EvidenceError, "before.*task_started"):
            self.complete()

    def test_malformed_metadata_has_evidence_diagnostic(self):
        directory = self.root / "codex/sessions"
        directory.mkdir(parents=True)
        path = directory / "malformed.jsonl"
        for value in ([], {"type": "session_meta", "payload": []}):
            path.write_text(json.dumps(value) + "\n")
            with self.assertRaises(evidence.EvidenceError):
                self.resolve()

    def nested_source(self):
        conversation = "00000000-0000-4000-8000-000000000009"
        child = self.metadata()
        child[0]["payload"]["session_id"] = conversation
        child[0]["payload"]["source"]["subagent"]["thread_spawn"]["depth"] = 2
        parent = self.metadata(base.ROOT_ID, parent=conversation)
        parent[0]["payload"]["session_id"] = conversation
        ancestor = [{"type": "session_meta", "payload": {"id": conversation, "session_id": conversation}}]
        self.source.sessions = {base.QA_ID: child, base.ROOT_ID: parent, conversation: ancestor}
        return conversation

    def test_nested_conversation_id_verified_through_source_ancestry(self):
        self.nested_source()
        self.assertEqual(self.complete()["answer"], {"verdict": "passed"})

    def test_nested_conversation_id_rejects_unconfirmed_ancestry(self):
        for defect in ("foreign", "missing", "cycle", "parent-conflict", "id-conflict", "session-conflict", "ancestor-not-root"):
            with self.subTest(defect=defect):
                conversation = self.nested_source()
                parent = self.source.sessions[base.ROOT_ID][0]["payload"]
                if defect == "foreign":
                    self.source.sessions[base.QA_ID][0]["payload"]["session_id"] = base.REVIEWER_ID
                elif defect == "missing":
                    del self.source.sessions[base.ROOT_ID]
                elif defect == "cycle":
                    parent["parent_thread_id"] = base.ROOT_ID
                    parent["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = base.ROOT_ID
                elif defect == "parent-conflict":
                    parent["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = base.REVIEWER_ID
                elif defect == "id-conflict":
                    parent["id"] = base.REVIEWER_ID
                elif defect == "session-conflict":
                    parent["session_id"] = base.REVIEWER_ID
                else:
                    self.source.sessions[conversation][0]["payload"]["parent_thread_id"] = base.ROOT_ID
                with self.assertRaises(evidence.EvidenceError):
                    self.complete()

    def test_production_resolver_reads_nested_conversation_ancestry(self):
        self.nested_source()
        for events in self.source.sessions.values():
            self.write_session(events)
        self.assertEqual(self.resolve()["codex_thread_id"], base.QA_ID)

    def test_unfinished_continuation_rejects_old_acceptance(self):
        self.events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "next"}})
        with self.assertRaisesRegex(evidence.EvidenceError, "continuation|stale"):
            self.complete()

    def metadata(self, thread_id=base.QA_ID, parent=base.ROOT_ID, role="qa-verifier", path="/root/qa"):
        events = base.session(thread_id, role, {"verdict": "passed"}, 10)
        meta = events[0]["payload"]
        meta["agent_path"] = path
        meta["parent_thread_id"] = parent
        meta["session_id"] = parent
        meta["source"]["subagent"]["thread_spawn"].update(agent_path=path, parent_thread_id=parent)
        return events

    def write_session(self, events, thread_id=None):
        directory = self.root / "codex/sessions/2026/09/10"
        directory.mkdir(parents=True, exist_ok=True)
        thread_id = thread_id or events[0]["payload"]["id"]
        (directory / f"rollout-{thread_id}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def resolve(self, **kwargs):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
            return evidence.CodexSessionSource().resolve_session("/root/qa", base.ROOT_ID, "qa-verifier", **kwargs)

    def test_resolve_exact_metadata_and_line_numbers(self):
        self.write_session(self.metadata())
        self.write_session(self.metadata(base.REVIEWER_ID, parent=base.REVIEWER_ID))
        resolved = self.resolve()
        self.assertEqual(resolved["codex_thread_id"], base.QA_ID)
        self.assertEqual(resolved["session_meta_event"], 1)
        self.assertEqual(resolved["task_started_event"], 2)

    def test_resolve_zero_and_multiple(self):
        with self.assertRaisesRegex(evidence.EvidenceError, "missing|no .*session"):
            self.resolve()
        self.write_session(self.metadata())
        self.write_session(self.metadata(base.REVIEWER_ID))
        with self.assertRaisesRegex(evidence.EvidenceError, "ambiguous|multiple"):
            self.resolve()

    def test_resolve_conflicting_explicit_id(self):
        self.write_session(self.metadata())
        with self.assertRaisesRegex(evidence.EvidenceError, "conflict"):
            self.resolve(thread_id=base.REVIEWER_ID)

    def test_resolve_conflicting_metadata(self):
        events = self.metadata()
        events[0]["payload"]["source"]["subagent"]["thread_spawn"]["agent_path"] = "/root/other"
        self.write_session(events)
        with self.assertRaisesRegex(evidence.EvidenceError, "conflict"):
            self.resolve()

    def inherited_session(self, own_first=True):
        from copy import deepcopy
        parent = base.session(base.ROOT_ID, "qa-verifier", {"verdict": "inherited"}, 5)
        parent[0] = {"type": "session_meta", "payload": {"id": base.ROOT_ID, "session_id": base.ROOT_ID}}
        own = self.metadata()
        events = ([own[0], *deepcopy(parent), *own[1:]] if own_first
                  else [*deepcopy(parent), *own])
        self.source.sessions = {base.ROOT_ID: parent, base.QA_ID: events}
        return events, parent

    def test_inherited_metadata_orders_resolve_and_complete_with_original_indexes(self):
        for own_first in (True, False):
            with self.subTest(own_first=own_first):
                events, parent = self.inherited_session(own_first)
                self.write_session(parent)
                self.write_session(events, base.QA_ID)
                resolved = self.resolve()
                expected_meta = 1 if own_first else len(parent) + 1
                self.assertEqual(resolved["session_meta_event"], expected_meta)
                self.assertEqual(resolved["task_started_event"], len(parent) + 2)
                with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
                    result = evidence.completed_turn(evidence.CodexSessionSource(), base.QA_ID,
                                                     self.turn, base.ROOT_ID, "qa-verifier")
                self.assertEqual(result["answer"], {"verdict": "passed"})
                self.assertEqual(result["session_meta_event"], expected_meta)
                self.assertEqual(result["task_complete_event"], len(events))

    def test_inherited_complete_turn_after_own_metadata_is_not_child_completion(self):
        events, parent = self.inherited_session()
        self.source.sessions[base.QA_ID] = events[:len(parent) + 1]
        with self.assertRaisesRegex(evidence.EvidenceError, "own completed turn missing"):
            evidence.completed_turn(self.source, base.QA_ID, base.ROOT_ID + "-turn", base.ROOT_ID, "qa-verifier")
        self.write_session(parent)
        self.write_session(self.source.sessions[base.QA_ID], base.QA_ID)
        with self.assertRaisesRegex(evidence.EvidenceError, "own task_started missing"):
            self.resolve()

    def test_inherited_copy_in_descendant_is_not_another_resolution_candidate(self):
        events, parent = self.inherited_session()
        descendant = self.metadata(base.REVIEWER_ID, parent=base.QA_ID, role="reviewer", path="/root/qa/review")
        self.write_session(parent)
        self.write_session(events, base.QA_ID)
        self.write_session([descendant[0], *events, *descendant[1:]], base.REVIEWER_ID)
        self.assertEqual(self.resolve()["codex_thread_id"], base.QA_ID)

    def test_resolver_stops_at_unrelated_own_header_but_rejects_duplicate_candidate(self):
        events = self.metadata()
        self.write_session(events)
        unrelated = self.metadata(base.REVIEWER_ID, parent=base.REVIEWER_ID)
        self.write_session(unrelated)
        path = next((self.root / "codex/sessions").glob("**/*" + base.REVIEWER_ID + ".jsonl"))
        path.write_text(json.dumps(unrelated[0]) + "\nunfinished unrelated event\n")
        self.assertEqual(self.resolve()["codex_thread_id"], base.QA_ID)
        self.write_session([*events, events[0]])
        with self.assertRaisesRegex(evidence.EvidenceError, "conflicting session metadata"):
            self.resolve()

    def test_metadata_selector_rejects_missing_duplicate_and_malformed_identity(self):
        from copy import deepcopy
        own = self.metadata()[0]
        for events in ([], [own, deepcopy(own)], [{"type": "session_meta", "payload": []}],
                       [{"type": "session_meta", "payload": {"id": "invalid"}}],
                       [{"type": "session_meta", "payload": {}}],
                       [{"type": "session_meta", "payload": {"id": base.REVIEWER_ID}}]):
            with self.subTest(events=events), self.assertRaises(evidence.EvidenceError):
                evidence.own_metadata(events, base.QA_ID)

    def test_inherited_metadata_requires_matching_available_ancestor(self):
        for defect in ("missing", "foreign", "cycle", "ancestor-cycle", "role", "parent", "copied-role", "copied-parent", "copied-spawn"):
            with self.subTest(defect=defect):
                events, parent = self.inherited_session()
                if defect == "missing":
                    del self.source.sessions[base.ROOT_ID]
                elif defect == "foreign":
                    events[1]["payload"]["id"] = base.REVIEWER_ID
                elif defect == "cycle":
                    events[0]["payload"]["parent_thread_id"] = base.QA_ID
                    events[0]["payload"]["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = base.QA_ID
                elif defect == "ancestor-cycle":
                    fields = {"parent_thread_id": base.QA_ID, "agent_role": "qa-verifier"}
                    parent[0]["payload"].update(fields, source={"subagent": {"thread_spawn": fields}})
                    events[1]["payload"].update(parent[0]["payload"])
                elif defect == "copied-spawn":
                    events[1]["payload"]["source"] = {"subagent": {"thread_spawn": {"agent_role": "reviewer"}}}
                elif defect in ("role", "parent"):
                    field = "agent_role" if defect == "role" else "parent_thread_id"
                    events[0]["payload"][field] = "reviewer" if defect == "role" else base.REVIEWER_ID
                else:
                    field = "agent_role" if defect == "copied-role" else "parent_thread_id"
                    events[1]["payload"][field] = "reviewer" if defect == "copied-role" else base.REVIEWER_ID
                with self.assertRaises(evidence.EvidenceError):
                    self.complete()

    def init(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                               "--slug", "procedure", "--date", "2026-09-10", *extra], capture_output=True, text=True)

    def test_compact_init_and_partial_registration(self):
        result = self.init("--mode", "compact")
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        self.assertEqual({Path(name).name for name, data in JournalSnapshot.open(run).documents.items() if data is not None},
                         {"run.md", "checks.md", "final.md", "context.md", "delegation-summary.json", "timeline.jsonl"})
        self.assertFalse(JournalSnapshot.open(run).exists("lane-map.json"))
        publish_fixture(run, "context.md", "# Context\n\n## Initial Worktree Snapshot\n\nClean.\n")
        publish_fixture(run, "run.md", "# Task\n\nSynthetic scope.\n")
        verification = {**evidence.empty_verification(), "root_thread_id": base.ROOT_ID,
                        "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                        "task_scope": {"path": "run.md"}}
        args = ["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification", "--status", "active", "--summary", "Scope captured",
                "--verification-json", json.dumps(verification)]
        with redirect_stdout(StringIO()):
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        stored = json.loads(JournalSnapshot.open(run).read_text("delegation-summary.json"))["verification"]
        self.assertNotIn("result_hash", stored)
        for field in ("initial_snapshot", "task_scope"):
            self.assertEqual(stored[field]["sha256"], evidence.sha256(evidence.reference_bytes(run, stored[field])))

    def test_compact_conflicts_before_writes(self):
        for flag in ("--with-lanes", "--architecture-gate", "--budget=standard", "--worker-lane=x:implementation:python-worker"):
            with self.subTest(flag=flag):
                result = self.init("--mode", "compact", flag)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / ".agent-work").exists())

    def test_reuse_rejects_format_change_and_malformed_summary(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        before = dict(JournalSnapshot.open(run).documents)
        self.assertNotEqual(self.init("--reuse", "--mode", "compact").returncode, 0)
        self.assertEqual(before, dict(JournalSnapshot.open(run).documents))
        corrupt_fixture_document(run, "delegation-summary.json", "{broken")
        self.assertNotEqual(self.init("--reuse").returncode, 0)
        self.assertEqual(JournalSnapshot.open(run).read_text("delegation-summary.json"), "{broken")

    def test_reuse_requires_summary_flags_and_notes(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        path = run / "delegation-summary.json"
        original = json.loads(JournalSnapshot.open(run).read_text(path))
        for field, value in (("subagents_used", "false"), ("role_lanes_used", None), ("notes", "")):
            with self.subTest(field=field):
                changed = {**original, field: value}
                if value is None:
                    del changed[field]
                publish_fixture(run, "delegation-summary.json", json.dumps(changed))
                before = dict(JournalSnapshot.open(run).documents)
                self.assertNotEqual(self.init("--reuse").returncode, 0)
                self.assertEqual(before, dict(JournalSnapshot.open(run).documents))

    def test_reuse_missing_summary_preserves_existing_run(self):
        for mode in ("compact", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                self.root = Path(directory)
                result = self.init("--mode", mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                run = Path(result.stdout.strip())
                summary = run / "delegation-summary.json"
                connection = connect_database(run)
                connection.execute("DELETE FROM documents WHERE path='delegation-summary.json'")
                connection.close()
                before = dict(JournalSnapshot.open(run).documents)
                entries = set(run.rglob("*"))
                reused = self.init("--reuse")
                self.assertNotEqual(reused.returncode, 0)
                self.assertIn("delegation-summary.json", reused.stderr)
                self.assertFalse(JournalSnapshot.open(run).exists(summary))
                self.assertEqual(before, dict(JournalSnapshot.open(run).documents))
                self.assertEqual(entries, set(run.rglob("*")))

    def test_reuse_existing_empty_directory_requires_summary(self):
        run = self.root / ".agent-work/runs/2026-09-10-procedure"
        run.mkdir(parents=True)
        reused = self.init("--reuse")
        self.assertNotEqual(reused.returncode, 0)
        self.assertIn("delegation-summary.json", reused.stderr)
        self.assertEqual(list(run.iterdir()), [])

    def test_same_second_completion_order_requires_precision(self):
        self.numeric()
        qa = self.complete()
        self.events[-1]["timestamp"] = "2026-09-10T13:41:41.600Z"
        reviewer = self.complete()
        self.assertTrue(evidence.completion_follows(reviewer, qa))
        self.assertFalse(evidence.completion_follows(qa, reviewer))
        self.numeric(None)
        coarse = self.complete()
        self.assertFalse(evidence.completion_follows(reviewer, coarse))
        self.assertFalse(evidence.completion_follows(coarse, qa))
        self.events[-1]["payload"]["completed_at"] += 1
        self.assertTrue(evidence.completion_follows(self.complete(), coarse))

    def test_exact_json_and_fenced_json_only(self):
        for text in ('{"verdict":"passed"}', 'Report\n```json\n{"verdict":"passed"}\n```'):
            self.assertEqual(evidence.final_json(text), {"verdict": "passed"})
        for text in ('Report\n{"verdict":"passed"}', '[]', '{"verdict":"passed"}\nTrailing prose'):
            with self.assertRaises(evidence.EvidenceError):
                evidence.final_json(text)

    def test_completed_turn_keeps_source_identity_guards(self):
        from copy import deepcopy
        original = deepcopy(self.events)
        for mutation in ("parent", "role", "model", "start", "context", "duplicate", "final"):
            with self.subTest(mutation=mutation):
                self.events[:] = deepcopy(original)
                if mutation == "parent":
                    self.events[0]["payload"]["parent_thread_id"] = base.REVIEWER_ID
                elif mutation == "role":
                    self.events[0]["payload"]["agent_role"] = "reviewer"
                elif mutation == "model":
                    self.events[2]["payload"]["model"] = "incorrect"
                elif mutation == "start":
                    del self.events[1]
                elif mutation == "context":
                    del self.events[2]
                elif mutation == "duplicate":
                    self.events.append(deepcopy(self.events[-1]))
                else:
                    self.events[-2]["payload"]["content"][0]["text"] = '{}'
                with self.assertRaises(evidence.EvidenceError):
                    self.complete()

    def test_public_command_functions_complete_procedure(self):
        result = self.init("--mode", "compact")
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        publish_fixture(run, "context.md", "# Context\n\n## Initial Worktree Snapshot\n\nClean.\n")
        publish_fixture(run, "run.md", "# Run\n\n## Task Scope\n\nChange result.txt.\n")
        verification = {**evidence.empty_verification(), "task_kind": "analysis", "root_thread_id": base.ROOT_ID,
                        "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                        "task_scope": {"path": "run.md", "section": "Task Scope"}}
        source = evidence.CodexSessionSource()

        def record(*args):
            output = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}), redirect_stdout(output):
                self.assertEqual(recorder.main(["--run-dir", str(run), "--summary", "Synthetic procedure", *args], session_source=source), 0)
            return output.getvalue()

        def stored():
            return json.loads(JournalSnapshot.open(run).read_text("delegation-summary.json"))

        def no_writes(*args):
            before = dict(JournalSnapshot.open(run).documents)
            with self.assertRaises(SystemExit):
                record(*args)
            self.assertEqual(before, dict(JournalSnapshot.open(run).documents))

        register = ["--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active"]
        record(*register, "--verification-json", json.dumps(verification))
        self.assertNotIn("result_hash", stored()["verification"])
        qa_args = ["--role", "qa-verifier", "--lane-id", "qa", "--resolve-session", "--agent-path", "/root/qa"]
        no_writes(*qa_args, "--stage", "spawned", "--status", "active")
        self.write_session(self.metadata())
        no_writes(*qa_args, "--codex-thread-id", base.REVIEWER_ID, "--stage", "spawned", "--status", "active")
        output = record(*qa_args, "--stage", "spawned", "--status", "active")
        resolved = json.loads(next(line.removeprefix("resolver: ") for line in output.splitlines() if line.startswith("resolver: ")))
        self.assertEqual(resolved["codex_thread_id"], base.QA_ID)
        event = json.loads(JournalSnapshot.open(run).read_text("timeline.jsonl").splitlines()[-1])
        self.assertGreater(evidence.timestamp(event["timestamp"]), evidence.timestamp(event["observed_spawn_at"]))
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        retained = tempfile.TemporaryDirectory()
        self.addCleanup(retained.cleanup)
        prepared = workspace.prepare(run, self.root.resolve(), Path(retained.name).resolve() / "bundle")
        (Path(prepared["working_root"]) / "result.txt").write_text("Synthetic result\n")
        sealed = workspace.seal(run)
        verification = stored()["verification"]
        verification.update(task_kind="change", result_files=["result.txt"], author_thread_ids=[base.ROOT_ID])
        publish_actual_fixture_author(run, verification)
        record(*register, "--verification-json", json.dumps(verification))
        digest = stored()["verification"]["result_hash"]
        publish_fixture(run, "checks.md", "Synthetic tests passed.\n")
        (run / "handoffs").mkdir(exist_ok=True)
        qa_hash = None
        for role, thread, lane in (("qa-verifier", base.QA_ID, "qa"), ("reviewer", base.REVIEWER_ID, "reviewer")):
            args = ["--role", role, "--lane-id", lane, "--resolve-session", "--agent-path", f"/root/{lane}"]
            handoff = f"handoffs/{lane}.md"
            publish_fixture(run, handoff, "checks.md " + evidence.sha256(JournalSnapshot.open(run).read_bytes("checks.md")) + "\n")
            answer = {"verdict": "passed", "reviewed_result_hash": digest, "handoff": handoff,
                      "review_subject": {"kind": "product-result"},
                      "handoff_sha256": evidence.sha256(JournalSnapshot.open(run).read_bytes(handoff))}
            if qa_hash:
                answer["qa_handoff_sha256"] = qa_hash
            events = self.metadata(thread, role=role, path=f"/root/{lane}")
            events[-1]["payload"]["completed_at"] = 1789047701
            events[-1]["timestamp"] = "2026-09-10T13:41:41.240Z" if lane == "qa" else "2026-09-10T13:41:41.600Z"

            def write_answer(value):
                events[-1]["payload"]["last_agent_message"] = json.dumps(value)
                events[-2]["payload"]["content"][0]["text"] = json.dumps(value)
                self.write_session(events)

            write_answer(answer)
            if lane == "reviewer":
                record(*args, "--stage", "spawned", "--status", "active")
            handoff_args = [*args, "--stage", "handoff", "--status", "pass", "--artifact", handoff, "--artifact", "checks.md"]
            for field, value in (("reviewed_result_hash", "a" * 64), ("handoff", "wrong.md"), ("handoff_sha256", "b" * 64)):
                write_answer({**answer, field: value})
                no_writes(*handoff_args)
            if qa_hash:
                write_answer({**answer, "qa_handoff_sha256": "c" * 64})
                no_writes(*handoff_args)
            write_answer(answer)
            record(*handoff_args)
            if lane == "qa":
                qa_hash = answer["handoff_sha256"]
        summary = stored()
        publish_fixture(run, "final.md", "# Final\n\nVerdict: ship\n\n" + base.delegation_section(summary))
        no_writes("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "final", "--status", "pass")
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
            snapshot = JournalSnapshot.open(run)
            finalize(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                     expected_generation=snapshot.generation, identifier="procedure-finalize",
                     final_summary="Actual procedure result independently accepted", verdict="ship", session_source=source)
            self.assertEqual(base.validator.validate_run(run, mode="auto", session_source=source), [])
            self.assertEqual(evidence.validate_verification(run, summary, {}, "ship", source), [])
            (self.root / "unrelated.txt").write_text("Parallel work")
            self.assertEqual(evidence.validate_verification(run, summary, {}, "ship", source), [])
            (Path(sealed["candidate_root"]) / "result.txt").write_text("New revision")
            self.assertTrue(evidence.validate_verification(run, summary, {}, "ship", source))
        self.assertEqual(summary["verification"]["author_thread_ids"], [base.ROOT_ID])
        self.assertEqual([r["codex_thread_id"] for r in summary["subagents"]], [base.QA_ID, base.REVIEWER_ID])

    def test_completion_resolver_ambiguity_and_pending(self):
        answer = {"reviewed_result_hash": "a" * 64, "handoff": "qa.md"}
        events = base.session(base.QA_ID, "qa-verifier", answer, 10)
        self.source.sessions[base.QA_ID] = events
        self.assertEqual(evidence.resolve_completion(self.source, base.QA_ID, base.ROOT_ID, "qa-verifier", "a" * 64, ["qa.md"]), self.turn)
        events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "pending"}})
        with self.assertRaisesRegex(evidence.EvidenceError, "stale|unfinished"):
            evidence.resolve_completion(self.source, base.QA_ID, base.ROOT_ID, "qa-verifier", "a" * 64, ["qa.md"])
        events.pop()
        continuation = base.session(base.QA_ID, "qa-verifier", answer, 20)[1:]
        for event in continuation:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = "new-turn"
        events.extend(continuation)
        with self.assertRaisesRegex(evidence.EvidenceError, "ambiguous"):
            evidence.resolve_completion(self.source, base.QA_ID, base.ROOT_ID, "qa-verifier", "a" * 64, ["qa.md"])
        self.assertEqual(evidence.completed_turn(self.source, base.QA_ID, "new-turn", base.ROOT_ID, "qa-verifier")["answer"], answer)


class AnalysisCompletionTests(unittest.TestCase):
    """Fresh v2 analysis registration, with real CLI and source completion bytes."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "source"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.source = base.SyntheticSource()
        self.sessions = self.root.parent / "sessions"
        self.sessions.mkdir()
        bootstrap = self.root.parent / "bootstrap"
        bootstrap.mkdir()
        (bootstrap / "sitecustomize.py").write_text(
            "from pathlib import Path\nimport sys\n"
            f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
            "from verification_evidence import CodexSessionSource\n"
            f"CodexSessionSource.session_dir = lambda self: Path({str(self.sessions)!r})\n")
        self.env = {**os.environ, "PYTHONPATH": str(bootstrap), "PYTHONDONTWRITEBYTECODE": "1"}
        self.run = Path(self.cli("init-run.py", "--repo", str(self.root), "--slug", "analysis",
                                 "--mode", "compact", wrapper=True).stdout.strip())
        snapshot = JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.storage_version, 2)
        self.assertEqual(evidence.result_contract(snapshot)["result_contract_version"], 2)
        publish_fixture(self.run, "scope.md", "Exclude only the source AgentFlow runtime namespace.\n")
        publish_fixture(self.run, "checks.md", "Scope boundaries checked.\n")
        publish_fixture(self.run, "other.md", "Independent scope evidence.\n")
        self.verification = {**evidence.empty_verification(), "task_kind": "analysis",
                             "root_thread_id": base.ROOT_ID, "author_thread_ids": [],
                             "result_hash": evidence.sha256(JournalSnapshot.open(self.run).read_bytes("scope.md"))}
        self.register()

    def cli(self, name, *args, wrapper=False, ok=True):
        script = SCRIPTS.parents[2] / "scripts" / name if wrapper else SCRIPTS / name
        result = subprocess.run([sys.executable, "-B", str(script), *args], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def register(self, ok=True):
        return self.cli("record-agent-trace.py", "--run-dir", str(self.run), "--role", "orchestrator",
                        "--execution-mode", "role-lane", "--stage", "verification", "--status", "active",
                        "--summary", "Scope registered", "--verification-json", json.dumps(self.verification), ok=ok)

    def stored(self):
        return json.loads(JournalSnapshot.open(self.run).read_text("delegation-summary.json"))

    def write_session(self, thread, events):
        self.source.sessions[thread] = events
        (self.sessions / f"synthetic-{thread}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def assignment(self, role="qa-verifier", thread=base.QA_ID, lane="scope-qa", second=10):
        args = ["--run-dir", str(self.run), "--role", role, "--lane-id", lane]
        events = base.session(thread, role, {}, second)
        self.write_session(thread, events[:3])
        self.cli("record-agent-trace.py", *args, "--codex-thread-id", thread, "--stage", "spawned",
                 "--status", "active", "--summary", "Assigned")
        handoff = f"handoffs/{lane}.md"
        publish_fixture(self.run, handoff, "Scope accepted.\n" + "\n".join(
            path + " " + evidence.sha256(JournalSnapshot.open(self.run).read_bytes(path))
            for path in ("checks.md", "other.md")))
        artifacts = ["--artifact", handoff, "--artifact", "checks.md", "--artifact", "other.md"]
        before = JournalSnapshot.open(self.run)
        answer = json.loads(self.cli("record-agent-trace.py", *args, "--prepare-conclusion", "--status", "pass", *artifacts).stdout)
        self.assertEqual(before.revision, JournalSnapshot.open(self.run).revision)
        self.assertEqual(dict(before.documents), dict(JournalSnapshot.open(self.run).documents))
        self.assertEqual(dict(before.receipts), dict(JournalSnapshot.open(self.run).receipts))
        self.assertEqual(self.source.sessions[thread], events[:3])
        self.write_session(thread, base.session(thread, role, answer, second))
        complete = [*args, "--completion-turn-id", thread + "-turn", "--stage", "handoff", "--status", "pass",
                    "--summary", "Accepted", *artifacts]
        return thread, answer, complete

    def test_first_analysis_registration_without_workspace(self):
        for role, thread, lane, second in (("qa-verifier", base.QA_ID, "scope-qa", 10),
                                          ("reviewer", base.REVIEWER_ID, "scope-reviewer", 20)):
            _, answer, args = self.assignment(role, thread, lane, second)
            self.cli("record-agent-trace.py", *args, wrapper=True)
            stored = self.stored()
            key = "qa" if role == "qa-verifier" else "reviewer"
            self.assertEqual(stored["verification"][key], lane)
            self.assertEqual(stored["verification"]["author_thread_ids"], [])
            record = next(r for r in stored["subagents"] if r["lane_id"] == lane)
            self.assertEqual(record["codex_thread_id"], thread)
            self.assertEqual(record["completion_turn_id"], thread + "-turn")
            self.assertEqual(record["reviewed_result_hash"], self.verification["result_hash"])
            self.assertEqual(record["handoff"], answer["handoff"])
            self.assertEqual(record["handoff_sha256"], evidence.sha256(
                JournalSnapshot.open(self.run).read_bytes(answer["handoff"])))
            self.assertEqual(record["evidence"], [{"path": path, "sha256": evidence.sha256(
                JournalSnapshot.open(self.run).read_bytes(path))} for path in ("checks.md", "other.md")])
            if role == "reviewer":
                qa = next(r for r in stored["subagents"] if r["lane_id"] == "scope-qa")
                self.assertEqual(record["qa_handoff_sha256"], qa["handoff_sha256"])
        self.assertIsNone(workspace.registered_workspace(JournalSnapshot.open(self.run)))

    def test_quiet_native_command_worker_handoff_and_completion(self):
        from journal_io import write_entry, require_command_evidence
        disposable = self.root.parent / "quiet-check-copy"
        shutil.copytree(self.root, disposable)
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                lane = "quiet-worker-" + str(exit_code)
                thread = base.REVIEWER_ID if exit_code else base.QA_ID
                args = ["--run-dir", str(self.run), "--role", "python-worker", "--lane-id", lane]
                self.write_session(thread, base.session(thread, "python-worker", {}, 10)[:3])
                self.cli("record-agent-trace.py", *args, "--codex-thread-id", thread,
                         "--stage", "spawned", "--status", "active", "--summary", "Assigned quiet check")
                receipt = write_entry(self.run, "check", {"summary": "Quiet actual command", "status": "pass",
                    "command": [sys.executable, "-B", "-c", "raise SystemExit(" + str(exit_code) + ")"],
                    "cwd": str(disposable)}, identifier="quiet-check-" + str(exit_code))
                refs = receipt["evidence"]
                output = next(ref for ref in refs if ref["path"].endswith(".output"))
                report = next(ref["path"] for ref in refs if ref["path"].endswith(".json"))
                snapshot = JournalSnapshot.open(self.run)
                self.assertEqual(evidence.reference_bytes(self.run, output, snapshot=snapshot), b"")
                evidence.verify_reference(self.run, output, snapshot=snapshot)
                require_command_evidence(snapshot, [report], successful=exit_code == 0)
                rejected = self.cli("record-agent-trace.py", *args, "--stage", "handoff", "--status", "pass",
                                    "--summary", "Empty output cannot be handoff", "--artifact", output["path"], ok=False)
                self.assertIn("handoff artifact missing or empty", rejected.stdout + rejected.stderr)
                if exit_code:
                    with self.assertRaisesRegex(JournalError, "successful actual command"):
                        require_command_evidence(snapshot, [report], successful=True)
                status = "fail" if exit_code else "pass"
                request = {"summary": "Quiet check completed", "status": status, "lane_id": lane,
                           "evidence": [{"path": ref["path"]} for ref in refs]}
                capture = self.root.parent / "quiet-handoff-request.json"
                capture.write_text(json.dumps(request))
                self.cli("journal.py", "--run-dir", str(self.run), "record", "--kind", "handoff",
                         "--request-file", str(capture), "--operation-id", "quiet-handoff-" + str(exit_code))
                handoff = "handoffs/" + lane + ".md"
                artifacts = [item for path in [handoff, *[ref["path"] for ref in refs]] for item in ("--artifact", path)]
                self.write_session(thread, base.session(thread, "python-worker", "Quiet command completed", 10))
                self.cli("record-agent-trace.py", *args, "--completion-turn-id", thread + "-turn",
                         "--stage", "fail" if exit_code else "handoff", "--status", status,
                         "--summary", "Quiet command source completed", *artifacts)
                record = next(row for row in self.stored()["subagents"] if row["lane_id"] == lane)
                self.assertEqual(record["status"], status)
                self.assertIn(output, record["evidence"])

    def test_empty_evidence_requires_native_command_receipt(self):
        from journal_io import write_entry
        disposable = self.root.parent / "quiet-check-copy"
        shutil.copytree(self.root, disposable)
        receipt = write_entry(self.run, "check", {"summary": "Quiet command receipt", "status": "pass",
            "command": [sys.executable, "-B", "-c", "pass"], "cwd": str(disposable)}, identifier="quiet-negative")
        output = next(ref for ref in receipt["evidence"] if ref["path"].endswith(".output"))
        report = next(ref["path"] for ref in receipt["evidence"] if ref["path"].endswith(".json"))
        whitespace = write_entry(self.run, "check", {"summary": "Actual whitespace command", "status": "pass",
            "command": [sys.executable, "-B", "-c", "print(' ')"], "cwd": str(disposable)}, identifier="whitespace-negative")
        whitespace_output = next(ref for ref in whitespace["evidence"] if ref["path"].endswith(".output"))
        with self.assertRaisesRegex(JournalError, "empty evidence"):
            evidence.reference_bytes(self.run, whitespace_output)
        for path, data in (("artifacts/empty.txt", b""), ("handoffs/empty.md", b""),
                           ("checks/command-forged.output", b""), ("checks/whitespace.output", b" \n")):
            publish_fixture(self.run, path, data)
            with self.assertRaises(JournalError):
                evidence.reference_bytes(self.run, {"path": path}, snapshot=JournalSnapshot.open(self.run))
        with self.assertRaises(JournalError):
            evidence.reference_bytes(self.run, {**output, "section": "Empty"})
        publish_fixture(self.run, "artifacts/section.md", b"## Empty\n\n")
        with self.assertRaises(JournalError):
            evidence.reference_bytes(self.run, {"path": "artifacts/section.md", "section": "Empty"})
        publish_fixture(self.run, "checks/command-forged.json", JournalSnapshot.open(self.run).read_bytes(report))
        with self.assertRaisesRegex(JournalError, "matching native receipt"):
            evidence.reference_bytes(self.run, {"path": "checks/command-forged.output"})
        publish_fixture(self.run, report, JournalSnapshot.open(self.run).read_text(report) + " ")
        with self.assertRaisesRegex(JournalError, "matching native receipt"):
            evidence.reference_bytes(self.run, output)

    def unchanged_failure(self, args, contains=None):
        before = JournalSnapshot.open(self.run)
        result = self.cli("record-agent-trace.py", *args, ok=False)
        after = JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.generation), (after.revision, after.generation))
        self.assertEqual(dict(before.documents), dict(after.documents))
        self.assertEqual(dict(before.receipts), dict(after.receipts))
        self.assertEqual(before.read_bytes("timeline.jsonl"), after.read_bytes("timeline.jsonl"))
        for path in before.documents:
            if path.startswith("agents/") and path.endswith("/trace.jsonl"):
                self.assertEqual(before.read_bytes(path), after.read_bytes(path))
        if contains:
            self.assertIn(contains, result.stdout + result.stderr)
        return result

    def test_analysis_reviewer_resolver_and_semantic_replay(self):
        qa, answer, args = self.assignment()
        self.cli("record-agent-trace.py", *args)
        reviewer, _, review_args = self.assignment("reviewer", base.REVIEWER_ID, "scope-reviewer", 20)
        events = self.source.sessions[reviewer]
        events[0]["payload"]["agent_path"] = "/root/scope_reviewer"
        events[0]["payload"]["source"]["subagent"]["thread_spawn"]["agent_path"] = "/root/scope_reviewer"
        self.write_session(reviewer, events)
        resolved = list(review_args)
        index = resolved.index("--completion-turn-id")
        del resolved[index:index + 2]
        resolved += ["--resolve-session", "--agent-path", "/root/scope_reviewer"]
        self.cli("record-agent-trace.py", *resolved, wrapper=True)
        before = JournalSnapshot.open(self.run)
        for replay in (args, review_args):
            replay = list(replay)
            replay[replay.index("Accepted")] = "Different display summary"
            replay[-4:] = ["--artifact", "other.md", "--artifact", "checks.md"]
            self.assertIn("unchanged", self.cli("record-agent-trace.py", *replay).stdout)
        self.assertEqual(before.revision, JournalSnapshot.open(self.run).revision)
        self.assertEqual(dict(before.documents), dict(JournalSnapshot.open(self.run).documents))
        self.assertEqual(dict(before.receipts), dict(JournalSnapshot.open(self.run).receipts))
        self.assertEqual(self.stored()["verification"]["reviewer"], "scope-reviewer")

    def test_source_digest_shape_and_current_digest_mismatch(self):
        from copy import deepcopy
        thread, answer, args = self.assignment()
        for value in (None, "", "z" * 64, "a" * 63, "a" * 65, 123, "a" * 64, "missing"):
            with self.subTest(source=value):
                altered = deepcopy(answer)
                if value == "missing":
                    del altered["reviewed_result_hash"]
                else:
                    altered["reviewed_result_hash"] = value
                self.write_session(thread, base.session(thread, "qa-verifier", altered, 10))
                self.unchanged_failure(args, "does not match current result" if value == "a" * 64 else
                                       "source reviewed_result_hash must be SHA-256")
        self.write_session(thread, base.session(thread, "qa-verifier", answer, 10))
        for value in (None, "", "g" * 64, "b" * 63, "b" * 65, "b" * 64, "missing"):
            with self.subTest(current=value):
                verification = deepcopy(self.verification)
                if value == "missing":
                    del verification["result_hash"]
                else:
                    verification["result_hash"] = value
                self.unchanged_failure([*args, "--verification-json", json.dumps(verification)],
                                       "does not match current result")
        # Equal invalid hashes must fail the source shape check before equality.
        for value in (None, "missing", "", "g" * 64, "b" * 63, "b" * 65):
            verification, altered = deepcopy(self.verification), deepcopy(answer)
            if value == "missing":
                del verification["result_hash"]
                del altered["reviewed_result_hash"]
            else:
                verification["result_hash"] = altered["reviewed_result_hash"] = value
            self.write_session(thread, base.session(thread, "qa-verifier", altered, 10))
            self.unchanged_failure([*args, "--verification-json", json.dumps(verification)],
                                   "source reviewed_result_hash must be SHA-256")

    def test_no_exemption_for_other_task_kinds_or_unsealed_change(self):
        from copy import deepcopy
        _, _, args = self.assignment()
        for kind in (None, "missing", "unknown", "change"):
            verification = deepcopy(self.verification)
            if kind == "missing":
                del verification["task_kind"]
            else:
                verification["task_kind"] = kind
            self.unchanged_failure([*args, "--verification-json", json.dumps(verification)])
        self.cli("task-workspace.py", "--run-dir", str(self.run), "prepare", "--source", str(self.root),
                 "--destination", str(self.root.parent / "workspace"))
        verification = {**self.verification, "task_kind": "change"}
        self.unchanged_failure([*args, "--verification-json", json.dumps(verification)], "sealed")

    def test_analysis_rejects_product_paths_and_worker_lanes(self):
        _, _, args = self.assignment()
        (self.root / "product.txt").write_text("Product")
        for field in ("result_files", "run_changed_files"):
            verification = {**self.verification, field: ["product.txt"]}
            self.unchanged_failure([*args, "--verification-json", json.dumps(verification)])
        summary = json.loads(JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        lanes = [{"id": record["lane_id"], "role": record["role"], "execution_mode": "subagent",
                  "status": record["status"], "review_subject": record["review_subject"]}
                 for record in summary["subagents"]]
        lanes.append({"id": "worker", "type": "implementation", "role": "python-worker",
                      "execution_mode": "subagent", "status": "planned"})
        publish_fixture(self.run, "lane-map.json", json.dumps({"lanes": lanes}))
        self.unchanged_failure(args, "analysis contradicts product changes or worker lanes")

    def test_identity_and_completion_defects_preserve_spawn(self):
        from copy import deepcopy
        thread, _, args = self.assignment()
        original = deepcopy(self.source.sessions[thread])
        for defect in ("parent", "role", "model", "session", "missing", "unfinished", "author"):
            with self.subTest(defect=defect):
                events = deepcopy(original)
                extra = []
                meta = events[0]["payload"]
                if defect == "parent":
                    meta["parent_thread_id"] = base.REVIEWER_ID
                    meta["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = base.REVIEWER_ID
                elif defect == "role":
                    meta["agent_role"] = "reviewer"
                    meta["source"]["subagent"]["thread_spawn"]["agent_role"] = "reviewer"
                elif defect == "model":
                    events[2]["payload"]["model"] = "wrong-model"
                elif defect == "session":
                    meta["session_id"] = base.REVIEWER_ID
                elif defect == "missing":
                    events.pop()
                elif defect == "unfinished":
                    events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "next"}})
                else:
                    extra = ["--verification-json", json.dumps({**self.verification, "author_thread_ids": [thread]})]
                self.write_session(thread, events)
                self.unchanged_failure([*args, *extra])
                self.assertEqual(self.stored()["subagents"][0]["status"], "active")

    def test_tampered_handoff_evidence_and_reviewer_order(self):
        from copy import deepcopy
        thread, answer, args = self.assignment()
        handoff = answer["handoff"]
        original = JournalSnapshot.open(self.run).read_text(handoff)
        publish_fixture(self.run, handoff, original + "Tampered")
        self.unchanged_failure(args, "handoff_sha256 mismatch")
        publish_fixture(self.run, handoff, original)
        publish_fixture(self.run, "checks.md", "Tampered evidence")
        self.unchanged_failure(args, "evidence reference and sha256")
        publish_fixture(self.run, "checks.md", "Scope boundaries checked.\n")
        self.cli("record-agent-trace.py", *args)
        thread, review, review_args = self.assignment("reviewer", base.REVIEWER_ID, "scope-reviewer", 20)
        altered = deepcopy(review)
        altered["qa_handoff_sha256"] = "f" * 64
        self.write_session(thread, base.session(thread, "reviewer", altered, 20))
        self.unchanged_failure(review_args, "qa_handoff_sha256 mismatch")
        self.write_session(thread, base.session(thread, "reviewer", review, 5))
        self.unchanged_failure(review_args, "must follow QA completion")
        self.write_session(thread, base.session(thread, "reviewer", review, 20))
        self.cli("record-agent-trace.py", *review_args)
        accepted_evidence = JournalSnapshot.open(self.run).read_text("checks.md")
        publish_fixture(self.run, "checks.md", accepted_evidence + "Changed after acceptance")
        self.unchanged_failure(args)
        self.unchanged_failure(review_args)
        publish_fixture(self.run, "checks.md", accepted_evidence)
        self.write_session(thread, base.session(thread, "reviewer", altered, 20))
        self.unchanged_failure(review_args)
        events = base.session(thread, "reviewer", review, 20)
        events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "later"}})
        self.write_session(thread, events)
        self.unchanged_failure(review_args)

    def test_open_analysis_scope_to_sealed_product_delivery(self):
        scope_sessions = {}
        old_args = []
        for role, thread, lane, second in (("qa-verifier", base.QA_ID, "scope-qa", 10),
                                          ("reviewer", base.REVIEWER_ID, "scope-reviewer", 20)):
            _, _, args = self.assignment(role, thread, lane, second)
            self.cli("record-agent-trace.py", *args)
            record = next(r for r in self.stored()["subagents"] if r["lane_id"] == lane)
            proof = {"thread_id": record["codex_thread_id"], "completion_turn_id": record["completion_turn_id"],
                     "completion": evidence.completed_turn(self.source, record["codex_thread_id"],
                                                           record["completion_turn_id"], base.ROOT_ID, role)}
            publish_fixture(self.run, f"proofs/{lane}.json", json.dumps(proof, default=str))
            scope_sessions[thread] = (self.sessions / f"synthetic-{thread}.jsonl").read_bytes()
            old_args.append(args)
        self.assertFalse(JournalSnapshot.open(self.run).closed)
        proof_bytes = {path: JournalSnapshot.open(self.run).read_bytes(path)
                       for path in ("proofs/scope-qa.json", "proofs/scope-reviewer.json")}
        (self.root / ".agent-work/runtime.txt").write_text("Runtime metadata")
        (self.root / "product/.agent-work").mkdir(parents=True)
        (self.root / "product/.agent-work/owned.txt").write_text("Product content")
        metadata_args = ["--metadata-namespace", "agent-flow-runtime", "--metadata-authorized-source", str(self.root)]
        for flag, path in (("scope", "scope.md"), ("qa-proof", "proofs/scope-qa.json"),
                           ("review-proof", "proofs/scope-reviewer.json")):
            metadata_args += ["--metadata-" + flag + "-ref", path,
                              evidence.sha256(JournalSnapshot.open(self.run).read_bytes(path))]
        prepared = json.loads(self.cli("task-workspace.py", "--run-dir", str(self.run), "prepare",
                                       "--source", str(self.root), "--destination", str(self.root.parent / "workspace"),
                                       *metadata_args).stdout)
        working = Path(prepared["working_root"])
        baseline = Path(prepared["baseline_root"])
        self.assertFalse((baseline / ".agent-work").exists())
        self.assertEqual((baseline / "product/.agent-work/owned.txt").read_text(), "Product content")
        (working / "product.txt").write_text("Accepted product\n")
        sealed = json.loads(self.cli("task-workspace.py", "--run-dir", str(self.run), "seal").stdout)
        publish_fixture(self.run, "context.md", "# Context\n\n## Initial Worktree Snapshot\n\nRetained baseline.\n")
        publish_fixture(self.run, "run.md", "# Run\n\n## Task Scope\n\nAdd product.txt.\n")
        self.verification = {**self.stored()["verification"], "task_kind": "change",
                             "author_thread_ids": [base.ROOT_ID],
                             "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                             "task_scope": {"path": "run.md", "section": "Task Scope"}}
        scope_hash = self.verification["result_hash"]
        publish_actual_fixture_author(self.run, self.verification)
        self.register()
        registered = self.stored()["verification"]
        self.assertNotEqual(scope_hash, registered["result_hash"])
        self.assertEqual(registered["result_files"], ["product.txt"])
        self.assertEqual((registered["qa"], registered["reviewer"]), (None, None))
        for args in old_args:
            self.unchanged_failure(args, "does not match current result")
        for role, thread, lane, second in (
                ("qa-verifier", "00000000-0000-4000-8000-000000000004", "product-qa", 30),
                ("reviewer", "00000000-0000-4000-8000-000000000005", "product-reviewer", 40)):
            _, _, args = self.assignment(role, thread, lane, second)
            self.cli("record-agent-trace.py", *args, wrapper=True)
        publish_fixture(self.run, "final.md", "# Итог\n\nVerdict: ship\n\nДобавлен product.txt.\n")
        self.cli("record-agent-trace.py", "--run-dir", str(self.run), "--render-final")
        snapshot = JournalSnapshot.open(self.run)
        self.cli("journal.py", "--run-dir", str(self.run), "finalize", "--expected-run-uuid", snapshot.run_uuid,
                 "--expected-revision", str(snapshot.revision), "--expected-generation", str(snapshot.generation),
                 "--operation-id", "analysis-to-product-finalize", "--summary", "Product product.txt independently accepted after scoped analysis",
                 "--status", "pass", wrapper=True)
        self.cli("validate-run.py", "--run-dir", str(self.run), wrapper=True)
        delivered = json.loads(self.cli("task-workspace.py", "--run-dir", str(self.run), "delivery").stdout)
        self.assertEqual(delivered["candidate_root"], sealed["candidate_root"])
        self.assertEqual(delivered["result_hash"], registered["result_hash"])
        self.assertEqual(proof_bytes, {path: JournalSnapshot.open(self.run).read_bytes(path) for path in proof_bytes})
        for thread, content in scope_sessions.items():
            self.assertEqual(content, (self.sessions / f"synthetic-{thread}.jsonl").read_bytes())


class JournalCLITests(unittest.TestCase):
    """Real entrypoints; controlled source files via the internal reader seam."""
    def test_complete_cli_procedure_and_stale_negatives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "source"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            codex_home = root.parent / "synthetic-codex"
            sessions = codex_home / "sessions"
            sessions.mkdir(parents=True)
            # Explicit synthetic source files; production reader and identity validation.
            (sessions / f"synthetic-{base.ROOT_ID}.jsonl").write_text(json.dumps(
                {"type": "session_meta", "payload": {"id": base.ROOT_ID}}) + "\n")
            env = {**os.environ, "CODEX_HOME": str(codex_home), "PYTHONDONTWRITEBYTECODE": "1"}

            def cli(name, *args, wrapper=False, ok=True):
                script = SCRIPTS.parents[2] / "scripts" / name if wrapper else SCRIPTS / name
                result = subprocess.run([sys.executable, "-B", str(script), *args], env=env, capture_output=True, text=True)
                if ok:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            result = cli("init-run.py", "--repo", str(root), "--slug", "cli", "--mode", "compact", "--summary", "Change result.txt and verify actual output",
                         "--plan-summary", "Implement result.txt, execute checks and obtain independent acceptance", wrapper=True)
            run = Path(result.stdout.strip())
            preliminary = {**evidence.empty_verification(), "task_kind": "analysis", "root_thread_id": base.ROOT_ID,
                           "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                           "task_scope": {"path": "run.md", "section": "Task Scope"}}
            cli("record-agent-trace.py", "--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification", "--status", "active", "--summary", "Root and scope registered",
                "--verification-json", json.dumps(preliminary))
            prepared = json.loads(cli("task-workspace.py", "--run-dir", str(run), "prepare", "--source", str(root),
                                      "--destination", str(root.parent / "workspace")).stdout)
            cli("record-agent-trace.py", "--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification", "--status", "active", "--summary", "Prepared change scope registered",
                "--verification-json", json.dumps({**preliminary, "task_kind": "change"}))
            # A completed author cannot predict the next review correction. Keep
            # its handoff and boundary unchanged while another assignment fixes it.
            nested = "tests/browser/server/draft.txt"
            draft = Path(prepared["working_root"]) / nested
            draft.parent.mkdir(parents=True)
            draft.write_text("Draft product\n")
            cli("record-agent-trace.py", "--run-dir", str(run), "--role", "python-worker", "--execution-mode", "role-lane",
                "--lane-id", "cli-draft", "--stage", "spawned", "--status", "active", "--summary", "Assigned nested draft")
            request_capture = root.parent / "semantic-request.json"
            request_capture.write_text(json.dumps({"summary": "Written " + nested, "status": "pass", "lane_id": "cli-draft"}))
            cli("journal.py", "--run-dir", str(run), "record", "--kind", "handoff", "--request-file", str(request_capture), "--operation-id", "draft-handoff")
            proof_capture = root.parent / "draft-edit.json"
            proof_capture.write_text(json.dumps({"written_paths": [nested], "author_thread_id": base.ROOT_ID}))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "artifacts/draft-edit.json", str(proof_capture))
            first = JournalSnapshot.open(run)
            old_handoff = first.read_bytes("handoffs/cli-draft.md")
            declaration_capture = root.parent / "draft-declaration.json"
            declaration_capture.write_text(json.dumps({"lane_id": "cli-draft", "authored_paths": [nested],
                "handoff": {"path": "handoffs/cli-draft.md", "sha256": evidence.sha256(old_handoff)},
                "edit_evidence": [{"path": "artifacts/draft-edit.json", "sha256": evidence.sha256(first.read_bytes("artifacts/draft-edit.json"))}], "coauthors": {}}))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "checks/draft-declaration.json", str(declaration_capture))
            cli("task-workspace.py", "--run-dir", str(run), "seal")
            cli("record-lane-boundary.py", "--run-dir", str(run), "--lane-id", "cli-draft", "--declaration-json", "checks/draft-declaration.json")
            old_boundary = JournalSnapshot.open(run).read_bytes("checks/lane-boundary-cli-draft.json")
            request_capture.write_text(json.dumps({"summary": "Review finding: nested draft must contain corrected product bytes", "status": "active"}))
            cli("journal.py", "--run-dir", str(run), "record", "--kind", "progress", "--request-file", str(request_capture), "--operation-id", "draft-review-finding")
            draft.write_text("Corrected product\n")
            cli("record-agent-trace.py", "--run-dir", str(run), "--role", "python-worker", "--execution-mode", "role-lane",
                "--lane-id", "cli-author", "--stage", "spawned", "--status", "active", "--summary", "Assigned result.txt ownership")
            (Path(prepared["working_root"]) / "result.txt").write_text("Synthetic product\n")
            request_capture = root.parent / "semantic-request.json"
            def semantic(kind, request, identifier, ok=True):
                request_capture.write_text(json.dumps(request))
                return cli("journal.py", "--run-dir", str(run), "record", "--kind", kind,
                           "--request-file", str(request_capture), "--operation-id", identifier, ok=ok)
            semantic("progress", {"summary": "Implementation completed; checks follow", "status": "active"}, "cli-progress")
            disposable = root.parent / "check-copy"
            shutil.copytree(Path(prepared["working_root"]), disposable)
            check_receipt = json.loads(semantic("check", {"summary": "Actual result bytes verified", "status": "pass",
                      "command": [sys.executable, "-B", "-c", "from pathlib import Path; assert Path('result.txt').read_text() == 'Synthetic product\\n'; assert Path('tests/browser/server/draft.txt').read_text() == 'Corrected product\\n'; print('verified result')"],
                      "cwd": str(disposable)}, "cli-check").stdout)
            check_snapshot = JournalSnapshot.open(run)
            check_paths = [ref["path"] for ref in check_receipt["evidence"]]
            report_path = next(name for name in check_paths if name.endswith(".json"))
            report = json.loads(check_snapshot.read_text(report_path))
            self.assertEqual((report["exit_code"], report["status"]), (0, "pass"))
            positive_output = next(path for path in check_paths if path.endswith(".output"))
            negative_receipt = json.loads(semantic("check", {
                "summary": "Negative fixture rejects changed result bytes", "status": "pass",
                "command": [sys.executable, "-B", "-c", "from pathlib import Path\nvalue=Path('result.txt').read_text()\ntry:\n assert value == 'Wrong result\\n'\nexcept AssertionError:\n print('negative fixture rejected wrong bytes')\nelse:\n raise SystemExit('wrong bytes were accepted')"],
                "cwd": str(disposable)}, "cli-negative-check").stdout)
            negative_output = next(ref["path"] for ref in negative_receipt["evidence"] if ref["path"].endswith(".output"))
            check_paths.extend(ref["path"] for ref in negative_receipt["evidence"])
            # An attachment with a meaningful ID lets the generated Evidence section
            # name the claim without hand-authoring a journal or a Markdown heading.
            claim_capture = root.parent / "actual-claim-output"
            claim_capture.write_bytes(JournalSnapshot.open(run).read_bytes(positive_output))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "checks/cli-result-claim.output", str(claim_capture))
            check_paths.append("checks/cli-result-claim.output")
            verification = {**evidence.empty_verification(), "root_thread_id": base.ROOT_ID,
                            "task_kind": "change", "author_thread_ids": [base.ROOT_ID],
                            "result_files": ["result.txt"], "run_changed_files": ["result.txt"],
                            "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                            "task_scope": {"path": "run.md", "section": "Task Scope"}}
            edit_capture = root.parent / "actual-edit.json"
            edit_capture.write_text(json.dumps({"written_paths": ["result.txt", nested], "author_thread_id": base.ROOT_ID}))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "artifacts/cli-author-edit.json", str(edit_capture))
            semantic("handoff", {"summary": "Written result.txt and " + nested + "; corrected review finding", "status": "pass", "lane_id": "cli-author",
                                "evidence": [{"path": "artifacts/cli-author-edit.json"}]}, "cli-author-handoff")
            own = JournalSnapshot.open(run)
            declaration = {"lane_id": "cli-author", "authored_paths": ["result.txt", nested],
                           "handoff": {"path": "handoffs/cli-author.md", "sha256": evidence.sha256(own.read_bytes("handoffs/cli-author.md"))},
                           "edit_evidence": [{"path": "artifacts/cli-author-edit.json", "sha256": evidence.sha256(own.read_bytes("artifacts/cli-author-edit.json"))}],
                           "coauthors": {}}
            # Boundary declaration remains a domain proof; hashes are computed from writer output.
            edit_capture.write_text(json.dumps(declaration))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "checks/cli-author-declaration.json", str(edit_capture))
            sealed = json.loads(cli("task-workspace.py", "--run-dir", str(run), "seal").stdout)
            cli("record-lane-boundary.py", "--run-dir", str(run), "--lane-id", "cli-author",
                "--declaration-json", "checks/cli-author-declaration.json")
            verification.update(result_files=sealed["changed_paths"], run_changed_files=sealed["changed_paths"])
            self.assertEqual(old_handoff, JournalSnapshot.open(run).read_bytes("handoffs/cli-draft.md"))
            self.assertEqual(old_boundary, JournalSnapshot.open(run).read_bytes("checks/lane-boundary-cli-draft.json"))
            cli("record-agent-trace.py", "--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification", "--status", "active", "--summary", "Registered result",
                "--verification-json", json.dumps(verification))
            semantic("risk", {"id": "cli-independent-check-gap", "summary": "Result bytes await independent source acceptance",
                               "status": "active", "category": "verification-gap", "impact": "Wrong bytes could be delivered",
                               "affected_scope": "result.txt", "owner_lane": "cli-author", "detected_by": "cli-author",
                               "evidence": [{"path": report_path}]}, "cli-risk")
            semantic("harness-finding", {"id": "cli-practice", "summary": "Use native process evidence before marking result accepted",
                                          "status": "active", "finding_type": "local-practice-candidate", "problem_class": "process-proof",
                                          "architecture_context": [], "architecture_capabilities": [], "outcome": "success",
                                          "lesson": "Assert actual result bytes in a disposable copy", "reuse_when": "A task changes a stored result",
                                          "do_not_reuse_when": "There is no executable verification target", "evidence": [{"path": report_path}]}, "cli-harness-finding")
            semantic("harness-proposal", {"id": "cli-practice-proposal", "finding_id": "cli-practice", "proposal_type": "evidence-record",
                                           "summary": "Retain the actual check as project Evidence Records", "status": "planned",
                                           "evidence": [{"path": report_path}]}, "cli-harness-proposal")
            semantic("harness-evaluation", {"summary": "Actual finding and proposal await source reviewer", "status": "active",
                                             "evidence": [{"path": report_path}]}, "cli-harness-needs-review")
            harness_before = JournalSnapshot.open(run)
            self.assertEqual(json.loads(harness_before.read_text("harness-evaluation.json"))["status"], "needs-review")
            # Review input is an immutable byte capture of writer output. Updating
            # canonical state later must not invalidate the accepted source evidence.
            harness_capture = root.parent / "harness-review-input.json"
            harness_capture.write_bytes(harness_before.read_bytes("harness-evaluation.json"))
            cli("journal.py", "--run-dir", str(run), "publish", "--file", "artifacts/harness-review-input.json", str(harness_capture))
            repeat = []
            for role, thread, lane, second in (("qa-verifier", base.QA_ID, "qa", 10), ("reviewer", base.REVIEWER_ID, "reviewer", 20)):
                args = ["--run-dir", str(run), "--role", role, "--lane-id", lane]
                started = [e for e in base.session(thread, role, {}, second)
                           if e['type'] in {'session_meta', 'turn_context'} or e.get('payload', {}).get('type') == 'task_started']
                (sessions / f"synthetic-{thread}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in started))
                cli("record-agent-trace.py", *args, "--codex-thread-id", thread, "--stage", "spawned", "--status", "active", "--summary", "Assigned")
                handoff = f"handoffs/{lane}.md"
                artifact_args = ["--artifact", handoff, *[item for path in check_paths + ["artifacts/harness-review-input.json"] for item in ("--artifact", path)]]
                before = dict(JournalSnapshot.open(run).documents)
                prepared = cli("record-agent-trace.py", *args, "--prepare-conclusion", "--status", "pass", "--summary", "Independent actual checks accepted", *artifact_args)
                self.assertNotEqual(before, dict(JournalSnapshot.open(run).documents))
                before = dict(JournalSnapshot.open(run).documents)
                answer = json.loads(prepared.stdout)
                handoff_args = [*args, "--completion-turn-id", thread + "-turn", "--stage", "handoff", "--status", "pass", "--summary", "Accepted", *artifact_args]
                cli("record-agent-trace.py", *handoff_args, ok=False)
                self.assertEqual(before, dict(JournalSnapshot.open(run).documents))
                events = base.session(thread, role, answer, second)
                (sessions / f"synthetic-{thread}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
                cli("record-agent-trace.py", *handoff_args, wrapper=True)
                repeat.append(handoff_args)
            source_before = {p: p.read_bytes() for p in sessions.iterdir()}
            before = dict(JournalSnapshot.open(run).documents)
            for args in repeat:
                repeated = cli("record-agent-trace.py", *args)
                self.assertIn("unchanged", repeated.stdout)
            self.assertEqual(before, dict(JournalSnapshot.open(run).documents))
            claim_request = {"id": "cli-result-claim", "summary": "Actual bytes match the requested product",
                                "status": "pass", "subjects": ["result.txt"], "owner_lane": "qa",
                                "reviewed_by": "reviewer", "section": "Evidence",
                                "evidence": [{"path": positive_output, "markers": ["verified result"]}]}
            acceptance_request = {"id": "cli-result-acceptance", "summary": "Stored result matches requested bytes and rejects wrong bytes",
                                    "status": "pass", "source": "run.md", "subjects": ["result.txt"],
                                    "contract_types": ["storage"],
                                    "surface_expectations": [{"surface": "service", "polarity": "positive", "proof_kinds": ["unit-test"]},
                                                             {"surface": "service", "polarity": "negative", "proof_kinds": ["unit-test"]}],
                                    "evidence": [{"path": positive_output, "markers": ["verified result"], "surface": "service", "polarity": "positive", "proof_kind": "unit-test"}],
                                    "negative_fixture_evidence": [{"path": negative_output, "markers": ["negative fixture rejected wrong bytes"], "surface": "service", "polarity": "negative", "proof_kind": "unit-test"}]}
            def service_rejected(kind, request, identifier):
                before = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()}
                semantic(kind, request, identifier, ok=False)
                self.assertEqual(before, {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()})
            # Existing compact assignments stay compact: materializing a map would
            # retroactively impose boundary obligations on historical registrations.
            self.assertFalse(JournalSnapshot.open(run).exists("lane-map.json"))
            service_rejected("lane", {"id": "late-architecture", "summary": "Late topology change is not a migration",
                                      "status": "planned", "lane_type": "architecture", "role": "architect",
                                      "execution_mode": "subagent", "wave": 1, "critical": False}, "cli-late-map")
            service_rejected("harness-evaluation", {"summary": "An author cannot substitute for independent reviewer",
                                                   "status": "pass", "reviewed_by": "cli-author", "evidence": [{"path": report_path}]}, "cli-harness-wrong-reviewer")
            semantic("harness-evaluation", {"summary": "Source reviewer accepted the actual finding and retained proposal",
                                             "status": "pass", "reviewed_by": "reviewer", "evidence": [{"path": report_path}]}, "cli-harness-evaluated")
            evaluated = json.loads(JournalSnapshot.open(run).read_text("harness-evaluation.json"))
            self.assertEqual(evaluated["status"], "evaluated")
            self.assertEqual((evaluated["proposals"][0]["status"], evaluated["proposals"][0]["target"], evaluated["proposals"][0]["requires_human_approval"]),
                             ("proposed", "Evidence Records", False))
            service_rejected("claim", {**claim_request, "evidence": [{"path": positive_output, "markers": ["invented marker"]}]}, "cli-missing-marker")
            service_rejected("claim", {**claim_request, "owner_lane": "cli-author"}, "cli-wrong-owner")
            service_rejected("claim", {**claim_request, "reviewed_by": "qa"}, "cli-wrong-reviewer")
            service_rejected("acceptance", {**acceptance_request, "evidence": [{**acceptance_request["evidence"][0], "surface": "storage"}]}, "cli-wrong-surface")
            service_rejected("acceptance", {**acceptance_request, "evidence": [{**acceptance_request["evidence"][0], "proof_kind": "screenshot"}]}, "cli-wrong-proof")
            service_rejected("acceptance", {**acceptance_request, "negative_fixture_evidence": [{**acceptance_request["negative_fixture_evidence"][0], "polarity": "positive"}]}, "cli-positive-negative-fixture")
            semantic("claim", claim_request, "cli-claim")
            semantic("acceptance", acceptance_request, "cli-acceptance")
            resolution_request = {"risk_id": "cli-independent-check-gap", "summary": "QA and reviewer accepted actual check evidence in completed source turns",
                                  "status": "pass", "resolution_type": "evidence-added", "effect": "fix",
                                  "verification": "Actual process output and completed independent source acceptance match result.txt",
                                  "owner_lane": "cli-author", "verified_by": "qa", "reviewed_by": "reviewer",
                                  "evidence": [{"path": report_path}, {"path": "handoffs/qa.md"}, {"path": "handoffs/reviewer.md"}]}
            service_rejected("resolution", {**resolution_request, "verified_by": "cli-author"}, "cli-resolution-without-qa")
            semantic("resolution", resolution_request, "cli-resolution")
            claims = json.loads(JournalSnapshot.open(run).read_text("claim-evidence.json"))["claims"]
            acceptance = json.loads(JournalSnapshot.open(run).read_text("acceptance-traceability.json"))["acceptance"]
            self.assertEqual((claims[0]["status"], acceptance[0]["status"]), ("supported", "supported"))
            self.assertEqual(json.loads(JournalSnapshot.open(run).read_text("risk-resolutions.json"))["resolutions"][0]["status"], "fixed")
            snapshot = JournalSnapshot.open(run)
            cli("append-timeline.py", "--run-dir", str(run), "--role", "orchestrator", "--stage", "final", "--status", "pass", "--summary", "Complete", wrapper=True, ok=False)
            self.assertEqual(snapshot.revision, JournalSnapshot.open(run).revision)
            raw_final = root.parent / "raw-final-bypass.md"
            raw_final.write_text("# Arbitrary caller format\n\nVerdict: ship\n\nCaller-owned metadata.\n")
            before_raw = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()}
            rejected = cli("journal.py", "--run-dir", str(run), "finalize", "--expected-run-uuid", snapshot.run_uuid,
                           "--expected-revision", str(snapshot.revision), "--expected-generation", str(snapshot.generation),
                           "--operation-id", "cli-raw-final-bypass", "--final-file", str(raw_final), "--verdict", "ship", ok=False)
            self.assertIn("--summary", rejected.stdout + rejected.stderr)
            self.assertEqual(before_raw, {str(path.relative_to(run)): path.read_bytes() for path in run.rglob("*") if path.is_file()})
            finalize_args = ["--run-dir", str(run), "finalize", "--expected-run-uuid", snapshot.run_uuid,
                             "--expected-revision", str(snapshot.revision), "--expected-generation", str(snapshot.generation),
                             "--operation-id", "cli-finalize", "--summary", "Actual result checked and independently accepted", "--status", "pass"]
            cli("journal.py", *finalize_args, wrapper=True)
            finalized = JournalSnapshot.open(run)
            final = finalized.read_bytes("final.md")
            cli("journal.py", *finalize_args)
            self.assertEqual(finalized.revision, JournalSnapshot.open(run).revision)
            cli("validate-run.py", "--run-dir", str(run))
            cli("validate-run.py", "--run-dir", str(run), "--mode", "compact", wrapper=True)
            delivered = json.loads(cli("task-workspace.py", "--run-dir", str(run), "delivery").stdout)
            self.assertEqual(delivered["candidate_root"], sealed["candidate_root"])
            before = dict(JournalSnapshot.open(run).documents)
            cli("init-run.py", "--repo", str(root), "--slug", "cli", "--reuse")
            self.assertEqual(before, dict(JournalSnapshot.open(run).documents))
            (Path(sealed["candidate_root"]) / "result.txt").write_text("Changed product\n")
            stale = cli("validate-run.py", "--run-dir", str(run), ok=False)
            self.assertIn("candidate changed", stale.stdout + stale.stderr)
            (Path(sealed["candidate_root"]) / "result.txt").write_text("Synthetic product\n")
            before = JournalSnapshot.open(run)
            with self.assertRaisesRegex(JournalError, "closed"):
                publish_fixture(run, "checks.md", "Changed QA evidence\n")
            self.assertEqual(before.revision, JournalSnapshot.open(run).revision)
            self.assertEqual(dict(before.documents), dict(JournalSnapshot.open(run).documents))
            corrupt_fixture_document(run, report_path, "Changed QA evidence\n")
            stale = cli("validate-run.py", "--run-dir", str(run), ok=False)
            self.assertIn("sha256", stale.stdout + stale.stderr)
            self.assertEqual(source_before, {p: p.read_bytes() for p in sessions.iterdir()})


class BehavioralDialectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / ".agent-work/runs/dialect"
        self.run.mkdir(parents=True)
        self.source = base.SyntheticSource()
        self.child_path = "/root/scenario"
        self.events = base.session(base.QA_ID, "qa-verifier", {}, 10)
        meta = self.events[0]["payload"]
        meta["agent_path"] = self.child_path
        meta["source"]["subagent"]["thread_spawn"]["agent_path"] = self.child_path
        self.events[1]["timestamp"] = "2026-09-10T09:00:00Z"
        self.events[2:4] = [
            {"type": "response_item", "timestamp": "2026-09-10T09:00:02Z", "payload": {
                "type": "agent_message", "author": "/root", "recipient": self.child_path,
                "content": [{"type": "input_text", "text": "Full synthetic input"}]}},
            {"type": "response_item", "timestamp": "2026-09-10T09:00:03Z", "payload": {
                "type": "custom_tool_call_output", "call_id": "synthetic-call", "output": [
                    {"type": "input_text", "text": "Script completed\n"},
                    {"type": "input_text", "text": '{"output":"Read source"}'}]}},
        ]
        self.source.sessions = {base.QA_ID: self.events, base.ROOT_ID: [
            {"type": "session_meta", "payload": {"id": base.ROOT_ID, "session_id": base.ROOT_ID}}]}
        texts = {"input.txt": "Full synthetic input", "output.txt": 'Script completed\n{"output":"Read source"}', "review.md": "Independent evidence review"}
        for name, text in texts.items():
            (self.run / name).write_text(text)
        input_ref = {**base.reference(self.run, "input.txt"), "source_event": 3, "prepared_event": 1}
        output_ref = {**base.reference(self.run, "output.txt"), "source_event": 4, "source_call_id": "synthetic-call"}
        self.check = {"criterion_id": "P6", "session_thread_id": base.QA_ID, "strict_inputs": True,
                      "verifier_thread_id": base.REVIEWER_ID, "handoff": base.reference(self.run, "review.md"),
                      "inputs": [input_ref], "outputs": [output_ref]}
        self.capture = {"stage": "behavior-input-prepared", "timestamp": "2026-09-10T09:00:01Z",
                        "input_sha256": input_ref["sha256"], "artifacts": ["input.txt"]}
        self.write_capture()

    def write_capture(self):
        (self.run / "timeline.jsonl").write_text(json.dumps(self.capture) + "\n")

    def validate(self):
        evidence.validate_behavioral_checks(self.run, [self.check], self.source, base.REVIEWER_ID)

    def test_plain_parent_agent_message_and_custom_tool_output(self):
        self.validate()

    def test_prepared_event_requires_behavior_input_prepared_stage(self):
        self.validate()
        self.capture["stage"] = "unrelated-event"
        self.write_capture()
        with self.assertRaisesRegex(evidence.EvidenceError, "behavior-input-prepared"):
            self.validate()

    def test_mixed_encrypted_input_never_proves_strict_bytes(self):
        self.events[2]["payload"]["content"].append({"type": "encrypted_content", "encrypted_content": "SYNTHETIC_CIPHERTEXT"})
        with self.assertRaisesRegex(evidence.EvidenceError, "strict behavioral inputs unconfirmed"):
            self.validate()
        self.check["strict_inputs"] = False
        self.validate()
        self.capture["timestamp"] = "2026-09-10T09:00:04Z"
        self.write_capture()
        with self.assertRaisesRegex(evidence.EvidenceError, "prepared after invocation"):
            self.validate()

    def test_foreign_author_recipient_and_parent_source_rejected(self):
        for field in ("author", "recipient"):
            old = self.events[2]["payload"][field]
            self.events[2]["payload"][field] = "/root/foreign"
            with self.assertRaises(evidence.EvidenceError):
                self.validate()
            self.events[2]["payload"][field] = old
        del self.source.sessions[base.ROOT_ID]
        with self.assertRaises(evidence.EvidenceError):
            self.validate()

    def test_custom_tool_output_hash_call_id_and_encryption(self):
        for defect in ("hash", "call-id", "encrypted"):
            with self.subTest(defect=defect):
                from copy import deepcopy
                before = deepcopy(self.events[3]["payload"])
                if defect == "hash":
                    self.events[3]["payload"]["output"][1]["text"] += "changed"
                elif defect == "call-id":
                    self.events[3]["payload"]["call_id"] = "foreign"
                else:
                    self.events[3]["payload"]["output"].append({"type": "encrypted_content", "encrypted_content": "SYNTHETIC_CIPHERTEXT"})
                with self.assertRaises(evidence.EvidenceError):
                    self.validate()
                self.events[3]["payload"] = before

    def test_legacy_user_message_partial_encryption_is_unconfirmed(self):
        self.events[2]["payload"] = {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Full synthetic input"},
            {"type": "encrypted_content", "encrypted_content": "SYNTHETIC_CIPHERTEXT"}]}
        with self.assertRaisesRegex(evidence.EvidenceError, "strict behavioral inputs unconfirmed"):
            self.validate()
        self.check["strict_inputs"] = False
        self.validate()
        self.capture["input_sha256"] = "a" * 64
        self.write_capture()
        with self.assertRaisesRegex(evidence.EvidenceError, "preparation does not bind input"):
            self.validate()

    def test_agent_message_cannot_supply_behavioral_output(self):
        self.events[3]["payload"] = {"type": "agent_message", "author": self.child_path, "recipient": "/root",
                                    "content": [{"type": "input_text", "text": (self.run / "output.txt").read_text()}]}
        with self.assertRaisesRegex(evidence.EvidenceError, "output source must be assistant message"):
            self.validate()

    def test_production_reader_strips_ciphertext_and_preserves_dialect(self):
        self.events[2]["payload"]["content"].append({"type": "encrypted_content", "encrypted_content": "SYNTHETIC_CIPHERTEXT"})
        directory = self.root / "codex/sessions"
        directory.mkdir(parents=True)
        for thread, events in self.source.sessions.items():
            (directory / f"rollout-{thread}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
            self.source = evidence.CodexSessionSource()
            read = self.source.read(base.QA_ID, event_indices=[3, 4])
            self.assertNotIn("SYNTHETIC_CIPHERTEXT", json.dumps(read))
            self.assertEqual(read[2]["payload"]["type"], "agent_message")
            self.assertTrue(read[2]["payload"]["content_unavailable"])
            self.assertEqual(read[3]["payload"]["type"], "custom_tool_call_output")
            with self.assertRaisesRegex(evidence.EvidenceError, "strict behavioral inputs unconfirmed"):
                self.validate()
            self.check["strict_inputs"] = False
            self.validate()


if __name__ == "__main__":
    unittest.main()
