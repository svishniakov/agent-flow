#!/usr/bin/env python3
"""Procedure regressions using anonymized local session records."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import verification_evidence as evidence
from importlib.util import module_from_spec, spec_from_file_location

SCRIPTS = Path(__file__).resolve().parent


def load(name, filename):
    spec = spec_from_file_location(name, SCRIPTS / filename)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load("evidence_fixtures", "test-verification-evidence.py")
recorder = load("procedure_recorder", "record-agent-trace.py")


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
        self.assertEqual(self.complete()["completed_at"], datetime.fromisoformat("2026-09-10T13:41:41.240+00:00"))

    def test_numeric_conflicting_outer_rejected(self):
        self.numeric("2026-09-10T13:41:42.240Z")
        with self.assertRaisesRegex(evidence.EvidenceError, "conflict"):
            self.complete()

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

    def write_session(self, events):
        directory = self.root / "codex/sessions/2026/09/10"
        directory.mkdir(parents=True, exist_ok=True)
        thread_id = events[0]["payload"]["id"]
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

    def init(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                               "--slug", "procedure", "--date", "2026-09-10", *extra], capture_output=True, text=True)

    def test_compact_init_and_partial_registration(self):
        result = self.init("--mode", "compact")
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        self.assertEqual({p.name for p in run.iterdir() if p.is_file()},
                         {"run.md", "checks.md", "final.md", "context.md", "delegation-summary.json", "timeline.jsonl"})
        self.assertFalse((run / "lane-map.json").exists())
        (run / "context.md").write_text("# Context\n\n## Initial Worktree Snapshot\n\nClean.\n")
        (run / "run.md").write_text("# Task\n\nSynthetic scope.\n")
        verification = {**evidence.empty_verification(), "root_thread_id": base.ROOT_ID,
                        "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                        "task_scope": {"path": "run.md"}}
        args = ["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                "--stage", "verification", "--status", "active", "--summary", "Scope captured",
                "--verification-json", json.dumps(verification)]
        with redirect_stdout(StringIO()):
            self.assertEqual(recorder.main(args, session_source=self.source), 0)
        stored = json.loads((run / "delegation-summary.json").read_text())["verification"]
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
        before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
        self.assertNotEqual(self.init("--reuse", "--mode", "compact").returncode, 0)
        self.assertEqual(before, {p: p.read_bytes() for p in run.rglob("*") if p.is_file()})
        (run / "delegation-summary.json").write_text("{broken")
        self.assertNotEqual(self.init("--reuse").returncode, 0)
        self.assertEqual((run / "delegation-summary.json").read_text(), "{broken")

    def test_reuse_requires_summary_flags_and_notes(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = Path(result.stdout.strip())
        path = run / "delegation-summary.json"
        original = json.loads(path.read_text())
        for field, value in (("subagents_used", "false"), ("role_lanes_used", None), ("notes", "")):
            with self.subTest(field=field):
                changed = {**original, field: value}
                if value is None:
                    del changed[field]
                path.write_text(json.dumps(changed))
                before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
                self.assertNotEqual(self.init("--reuse").returncode, 0)
                self.assertEqual(before, {p: p.read_bytes() for p in run.rglob("*") if p.is_file()})

    def test_reuse_missing_summary_preserves_existing_run(self):
        for mode in ("compact", "full"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                self.root = Path(directory)
                result = self.init("--mode", mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                run = Path(result.stdout.strip())
                summary = run / "delegation-summary.json"
                summary.unlink()
                before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
                entries = set(run.rglob("*"))
                reused = self.init("--reuse")
                self.assertNotEqual(reused.returncode, 0)
                self.assertIn("delegation-summary.json", reused.stderr)
                self.assertFalse(summary.exists())
                self.assertEqual(before, {p: p.read_bytes() for p in run.rglob("*") if p.is_file()})
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
        (run / "context.md").write_text("# Context\n\n## Initial Worktree Snapshot\n\nClean.\n")
        (run / "run.md").write_text("# Run\n\n## Task Scope\n\nChange result.txt.\n")
        verification = {**evidence.empty_verification(), "root_thread_id": base.ROOT_ID,
                        "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
                        "task_scope": {"path": "run.md", "section": "Task Scope"}}
        source = evidence.CodexSessionSource()

        def record(*args):
            output = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}), redirect_stdout(output):
                self.assertEqual(recorder.main(["--run-dir", str(run), "--summary", "Synthetic procedure", *args], session_source=source), 0)
            return output.getvalue()

        def stored():
            return json.loads((run / "delegation-summary.json").read_text())

        def no_writes(*args):
            before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
            with self.assertRaises(SystemExit):
                record(*args)
            self.assertEqual(before, {p: p.read_bytes() for p in run.rglob("*") if p.is_file()})

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
        event = json.loads((run / "timeline.jsonl").read_text().splitlines()[-1])
        self.assertGreater(evidence.timestamp(event["timestamp"]), evidence.timestamp(event["observed_spawn_at"]))
        (self.root / "result.txt").write_text("Synthetic result\n")
        verification = stored()["verification"]
        verification.update(task_kind="change", result_files=["result.txt"], author_thread_ids=[base.ROOT_ID])
        record(*register, "--verification-json", json.dumps(verification))
        digest = stored()["verification"]["result_hash"]
        (run / "checks.md").write_text("Synthetic tests passed.\n")
        (run / "handoffs").mkdir(exist_ok=True)
        qa_hash = None
        for role, thread, lane in (("qa-verifier", base.QA_ID, "qa"), ("reviewer", base.REVIEWER_ID, "reviewer")):
            args = ["--role", role, "--lane-id", lane, "--resolve-session", "--agent-path", f"/root/{lane}"]
            handoff = f"handoffs/{lane}.md"
            (run / handoff).write_text("checks.md " + evidence.sha256((run / "checks.md").read_bytes()) + "\n")
            answer = {"verdict": "passed", "reviewed_result_hash": digest, "handoff": handoff,
                      "handoff_sha256": evidence.sha256((run / handoff).read_bytes())}
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
        (run / "final.md").write_text("# Final\n\nVerdict: ship\n\n" + base.delegation_section(summary))
        record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "final", "--status", "pass")
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}):
            self.assertEqual(base.validator.validate_run(run, mode="auto", session_source=source), [])
            self.assertEqual(evidence.validate_verification(run, summary, {}, "ship", source), [])
            (self.root / "unrelated.txt").write_text("Parallel work")
            self.assertEqual(evidence.validate_verification(run, summary, {}, "ship", source), [])
            (self.root / "result.txt").write_text("New revision")
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
