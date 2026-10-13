#!/usr/bin/env python3
"""Journal regressions. All session records here are explicitly synthetic."""
import json
import os
import sys
import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO

from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path


def load(name, filename):
    spec = spec_from_file_location(name, Path(__file__).with_name(filename))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load("journal_fixtures", "test-verification-evidence.py")
recorder = load("journal_recorder", "record-agent-trace.py")
import verification_evidence as evidence
import journal_io


class SemanticRequestRegressions(unittest.TestCase):
    """Caller errors must not allocate even a request envelope."""

    def tree(self, root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def invalid_requests(self):
        from journal_structure import request_contract
        class StringSubclass(str):
            pass
        contract = request_contract()
        for kind, example in contract["examples"].items():
            for field in ("summary", "status"):
                for value in (None, False, 0, [], {}, StringSubclass("active")):
                    yield kind, {**example, field: value}
            yield kind, {**example, "unknown": "rejected"}
            yield kind, {**example, "status": "invented-status"}
            for field in ("output", "patch", "document", "values"):
                yield kind, {**example, field: {"status": "supported"}}
            # Exercise a malformed semantic field separately from summary/status.
            # A schema error must win before missing lane or evidence checks.
            for field, value in example.items():
                if field in {"summary", "status"}:
                    continue
                if type(value) is str:
                    yield kind, {**example, field: False}
                elif type(value) is int:
                    yield kind, {**example, field: True}
                elif type(value) is bool:
                    yield kind, {**example, field: 1}
                elif type(value) is list:
                    yield kind, {**example, field: {}}
                    for index, row in enumerate(value):
                        if type(row) is dict:
                            rows = [dict(item) if type(item) is dict else item for item in value]
                            rows[index]["invented_nested_field"] = "rejected"
                            yield kind, {**example, field: rows}
                            for nested_name, nested_value in row.items():
                                if type(nested_value) is str:
                                    rows = [dict(item) if type(item) is dict else item for item in value]
                                    rows[index][nested_name] = False
                                    yield kind, {**example, field: rows}
                elif type(value) is dict:
                    yield kind, {**example, field: {**value, "invented_nested_field": "rejected"}}
            for field, specification in contract["fields"][kind].items():
                field_type = specification.rstrip("?")
                if field_type in contract["nested_fields"]:
                    # Include optional shapes omitted from minimal examples.
                    for malformed in (False, {"unexpected": "field"}, [{"unexpected": "field"}]):
                        yield kind, {**example, field: malformed}
                elif field_type == "integer":
                    yield kind, {**example, field: True}
                elif field_type == "boolean":
                    yield kind, {**example, field: 1}
            for field in contract["semantic_choices"].get(kind, {}):
                invalid_choice = ["invented-choice"] if type(example.get(field)) is list else "invented-choice"
                yield kind, {**example, field: invalid_choice}
        valid = {"summary": "Проверен настоящий результат", "status": "active"}
        for value in (None, False, 0, [], {}, b"text"):
            yield "progress", {**valid, "summary": value}
            yield "progress", {**valid, "status": value}
        for value in ("spawned", "done", "passed", "pass_with_risks", "unknown"):
            yield "progress", {**valid, "status": value}
        yield "progress", {**valid, "unexpected": "field"}
        for evidence in (None, {}, ["checks.txt"], [{"path": 1}],
                         [{"path": "checks.txt", "sha256": "invented"}]):
            yield "progress", {**valid, "evidence": evidence}
        for command in ("python3 -V", [], [False], ["python3", {}]):
            yield "check", {**valid, "command": command}
        yield "event", {**valid, "role": "orchestrator", "stage": "spawned", "status": "pass"}
        for kind in ("task", "plan", "progress", "lesson", "final"):
            for target in ("../outside", "handoffs/escape.md", "delegation-summary.json"):
                yield kind, {"summary": "Meaningful text", "status": "pass", "path": target}
        yield "unknown", valid
        yield "lane", {"summary": "Invalid registered role", "status": "planned", "id": "unknown-role",
                       "lane_type": "implementation", "role": "invented-role", "execution_mode": "subagent",
                       "wave": 1, "critical": False}

    def test_invalid_input_preserves_whole_tree_and_does_not_create_new_run(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            existing = root / "existing"
            journal_io.initialize_journal(existing, {}, source_root=root)
            for run in (existing, root / "new"):
                for index, (kind, request) in enumerate(self.invalid_requests()):
                    with self.subTest(run=run.name, kind=kind, request=request):
                        before = self.tree(root)
                        with self.assertRaises((ValueError, journal_io.JournalError)):
                            journal_io.write_entry(run, kind, request, identifier="invalid-" + str(index))
                        self.assertEqual(self.tree(root), before)
                        if run.name == "new":
                            self.assertFalse(run.exists())

    def test_generated_contract_examples_are_accepted_and_required_fields_are_enforced(self):
        from journal_structure import CALLER_STATUSES, request_contract, validate_request
        contract = request_contract()
        required_service_kinds = {
            "claim", "acceptance", "risk", "resolution", "resolution-attempt",
            "senior-qa-recovery", "architect-recovery", "supervising-recovery",
            "readiness-attempt", "readiness-gate", "readiness-approval", "readiness-execution",
            "continuation", "continuation-blocker", "harness-finding", "harness-proposal",
            "harness-evaluation", "lane", "lane-compliance", "lane-simplicity", "lane-scope",
            "lane-gate", "simplicity-scope", "route", "decision", "dod", "checks", "coverage", "lane-report",
        }
        self.assertTrue(required_service_kinds <= set(contract["examples"]))
        self.assertEqual(set(contract["statuses"]), CALLER_STATUSES)
        for kind, request in contract["examples"].items():
            with self.subTest(kind=kind):
                self.assertEqual(validate_request(kind, request), request)
                for name, specification in contract["fields"][kind].items():
                    if not specification.endswith("?"):
                        missing = {key: value for key, value in request.items() if key != name}
                        with self.assertRaises(ValueError):
                            validate_request(kind, missing)
        for status in CALLER_STATUSES:
            validate_request("progress", {"summary": "Meaningful result", "status": status})

    def test_renderer_preserves_semantic_text_and_negative_result(self):
        from journal_structure import render_document
        summary = "Найдена ошибка. Следующая проверка заблокирована."
        for status in ("fail", "blocked"):
            for kind in ("handoff", "final", "task", "lesson"):
                with self.subTest(kind=kind, status=status):
                    rendered = render_document(kind, summary, status)
                    self.assertIn(summary, rendered)
                    self.assertIn(status, rendered)
                    self.assertTrue(rendered.startswith("# "))

    def test_claim_upsert_preserves_other_ids_replay_and_atomic_concurrent_updates(self):
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=root)
            def request(identifier, summary):
                return {"id": identifier, "summary": summary, "status": "blocked", "subjects": ["semantic writer"]}
            first = request("first-claim", "First observation")
            journal_io.write_entry(run, "claim", first, identifier="claim-first")
            journal_io.write_entry(run, "claim", request("second-claim", "Keep second observation"), identifier="claim-second")
            updated = request("first-claim", "Updated first observation")
            journal_io.write_entry(run, "claim", updated, identifier="claim-updated")
            before = journal_io.JournalSnapshot.open(run)
            rows = json.loads(before.read_text("claim-evidence.json"))["claims"]
            self.assertEqual([row["id"] for row in rows], ["first-claim", "second-claim"])
            self.assertEqual([row["claim"] for row in rows], ["Updated first observation", "Keep second observation"])
            self.assertEqual([row["status"] for row in rows], ["gap", "gap"])
            journal_io.write_entry(run, "claim", updated, identifier="claim-updated")
            with self.assertRaises(journal_io.JournalError):
                journal_io.write_entry(run, "claim", request("first-claim", "Conflicting retry"), identifier="claim-updated")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))
            original = journal_io._put_documents
            def fault(connection, documents):
                original(connection, documents)
                raise OSError("injected service transaction failure")
            with patch.object(journal_io, "_put_documents", fault):
                with self.assertRaisesRegex(OSError, "injected service transaction failure"):
                    journal_io.write_entry(run, "claim", request("third-claim", "Must roll back"), identifier="claim-fault")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))
            barrier = Barrier(2)
            def write(identifier):
                barrier.wait(timeout=5)
                return journal_io.write_entry(run, "claim", request(identifier, identifier), identifier="concurrent-" + identifier)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(write, name) for name in ("third-claim", "fourth-claim")]
                for future in futures:
                    future.result(timeout=15)
            rows = json.loads(journal_io.JournalSnapshot.open(run).read_text("claim-evidence.json"))["claims"]
            self.assertEqual(len(rows), 4)
            self.assertEqual({row["id"] for row in rows}, {"first-claim", "second-claim", "third-claim", "fourth-claim"})
            self.assertEqual(rows[1]["claim"], "Keep second observation")

    def test_approval_binds_actual_root_user_quote_without_exact_word_or_time_parser(self):
        import tempfile
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            quote = "Да, запускайте эту безопасную проверку на расходной копии."
            user_event = {"type": "response_item", "timestamp": "2000-01-01T00:00:00Z",
                          "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": quote}]}}
            synthetic = base.SyntheticSource()
            declined_quote = "Эту проверку пока не запускайте."
            synthetic.sessions[base.ROOT_ID] = [user_event, {**user_event, "payload": {**user_event["payload"], "role": "assistant"}},
                                                 {**user_event, "payload": {**user_event["payload"], "content": [{"type": "input_text", "text": declined_quote}]}}]
            summary = {"verification": {"root_thread_id": base.ROOT_ID}, "subagents": [], "role_lanes": []}
            journal_io.initialize_journal(run, {"delegation-summary.json": json.dumps(summary).encode(),
                                                "artifacts/safe-command.md": b"python -B on a disposable copy"}, source_root=root)
            request = {"id": "approval-one", "summary": "Permission for a documented local check", "status": "planned",
                       "affected_gates": ["unit"], "commands": [{"cwd": str(root), "command": "python -B -V", "source": "artifacts/safe-command.md"}],
                       "manual_instruction": "Run the documented command only within authorized scope"}
            with patch.object(evidence, "CodexSessionSource", return_value=synthetic):
                journal_io.write_entry(run, "readiness-approval", request, identifier="approval-request")
                approved = {**request, "status": "pass", "decision": "approve", "source_event": 1, "source_quote": quote}
                for index, malformed in enumerate(({**approved, "source_event": 2}, {**approved, "source_quote": "Invented quote"},
                                                   {key: value for key, value in approved.items() if key != "source_quote"},
                                                   {key: value for key, value in approved.items() if key != "source_event"})):
                    with self.subTest(request=malformed):
                        before = self.tree(root)
                        with self.assertRaises((ValueError, journal_io.JournalError)):
                            journal_io.write_entry(run, "readiness-approval", malformed, identifier="invalid-approval-" + str(index))
                        self.assertEqual(self.tree(root), before)
                saved = deepcopy(synthetic.sessions)
                synthetic.sessions = {base.QA_ID: [user_event]}
                before = self.tree(root)
                with self.assertRaises((ValueError, journal_io.JournalError)):
                    journal_io.write_entry(run, "readiness-approval", approved, identifier="foreign-approval")
                self.assertEqual(self.tree(root), before)
                synthetic.sessions = saved
                journal_io.write_entry(run, "readiness-approval", approved, identifier="actual-approval")
                second = {**request, "id": "approval-two"}
                journal_io.write_entry(run, "readiness-approval", second, identifier="second-approval-request")
                journal_io.write_entry(run, "readiness-approval", {**second, "status": "blocked", "decision": "decline", "source_event": 3,
                                                                    "source_quote": declined_quote}, identifier="actual-decline")
            data = json.loads(journal_io.JournalSnapshot.open(run).read_text("verification-readiness.json"))
            observation = data["approval_requests"][0]["source_observation"]
            self.assertEqual((observation["thread_id"], observation["event"], observation["quote"], observation["decision"]),
                             (base.ROOT_ID, 1, quote, "approved"))
            self.assertTrue(observation["sha256"])
            self.assertEqual(data["approval_executions"], [])
            self.assertEqual(data["approval_requests"][1]["source_observation"]["decision"], "declined")

    def test_blocked_resolution_attempt_requires_recovery_before_next_attempt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            disposable = root / "copy"
            disposable.mkdir()
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=source)
            journal_io.write_entry(run, "lane", {"id": "worker", "summary": "Planned local owner",
                                                "status": "planned", "lane_type": "implementation", "role": "python-worker",
                                                "execution_mode": "subagent", "wave": 1, "critical": False}, identifier="recovery-owner")
            check = journal_io.write_entry(run, "check", {"summary": "Actual local test failure", "status": "pass", "cwd": str(disposable),
                                                          "command": [sys.executable, "-B", "-c", "print('actual failure'); raise SystemExit(7)"]}, identifier="recovery-failure")
            refs = [{"path": ref["path"]} for ref in check["evidence"]]
            journal_io.write_entry(run, "risk", {"id": "failed-check-risk", "summary": "Local check failed",
                                                "status": "blocked", "category": "test-gap", "impact": "Unverified result",
                                                "affected_scope": "local check", "owner_lane": "worker", "detected_by": "worker", "evidence": refs}, identifier="recovery-risk")
            attempt = {"risk_id": "failed-check-risk", "id": "first-attempt", "summary": "Retry needs test design recovery",
                       "status": "blocked", "resolution_type": "test-added", "effect": "fix", "verification": "Actual command exited 7",
                       "owner_lane": "worker", "evidence": refs, "blocked_reason": "qa-execution-gap",
                       "rollback": {"disposition": "not-needed", "summary": "Disposable check made no source edits"},
                       "lesson": {"disposition": "quarantined", "classification": "qa-execution-gap", "summary": "Do not repeat this failed check without recovery",
                                  "forbidden_repeat": ["repeat unchanged failed check"]}}
            journal_io.write_entry(run, "resolution-attempt", attempt, identifier="first-blocked-attempt")
            before = self.tree(root)
            with self.assertRaises(journal_io.JournalError):
                journal_io.write_entry(run, "resolution-attempt", {**attempt, "id": "second-attempt"}, identifier="second-without-recovery")
            self.assertEqual(self.tree(root), before)
            row = json.loads(journal_io.JournalSnapshot.open(run).read_text("risk-resolutions.json"))["resolutions"][0]
            self.assertEqual([(item["id"], item["attempt"], item["status"]) for item in row["attempts"]], [("first-attempt", 1, "blocked")])
            for lane_id, role, lane_type, wave, section in (
                ("senior-recovery", "senior-qa-verifier", "qa", 2, "Senior QA Test Design Review"),
                ("architect-recovery", "architect", "architecture", 3, "Resolution Architect Review"),
            ):
                journal_io.write_entry(run, "lane", {"id": lane_id, "summary": "Recovery observation for failed-check-risk", "status": "planned",
                                                     "lane_type": lane_type, "role": role, "execution_mode": "role-lane", "wave": wave, "critical": False}, identifier="declare-" + lane_id)
                journal_io.write_entry(run, "handoff", {"lane_id": lane_id, "summary": "Reviewed actual failed check; failed-check-risk needs a revised check",
                                                        "status": "pass", "evidence": refs,
                                                        "sections": [{"section": section, "summary": "failed-check-risk: Verify actual expected bytes on the disposable copy"}]}, identifier="conclude-" + lane_id)
            recovery = {"risk_id": "failed-check-risk", "lane_id": "senior-recovery", "summary": "Actual failed check needs a revised execution",
                        "status": "pass", "decision": "criteria-adequate", "recheck": "fail", "evidence": refs,
                        **{name: [] for name in ("covered_acceptance_criteria", "missing_acceptance_criteria", "ambiguous_acceptance_criteria", "added_acceptance_criteria",
                                               "external_blockers", "test_cases", "edge_cases", "negative_cases", "regression_cases", "integration_cases", "data_state_cases", "environment_cases")}}
            recovery["covered_acceptance_criteria"] = ["read actual result"]
            recovery["test_cases"] = ["assert result bytes"]
            recovery.update({"edge_cases": ["empty result must not match expected bytes"],
                             "negative_cases": ["wrong expected bytes must fail"],
                             "regression_cases": ["native failed check must remain fail on replay"],
                             "integration_cases": ["receipt and raw output must agree on failure"],
                             "data_state_cases": ["missing result must not be treated as success"],
                             "environment_cases": ["unlaunchable executable must retain its launch error"]})
            for kind, request in (("senior-qa-recovery", {**recovery, "status": "blocked"}),
                                  ("architect-recovery", {"risk_id": "failed-check-risk", "lane_id": "architect-recovery", "summary": "Negative cannot approve a retry",
                                                          "status": "fail", "decision": "revised-approach", "instruction": "Verify actual expected bytes on the disposable copy",
                                                          "forbidden_repeat": ["repeat unchanged failed check"], "evidence": refs})):
                before = self.tree(root)
                with self.assertRaises(journal_io.JournalError):
                    journal_io.write_entry(run, kind, request, identifier="inconsistent-" + kind)
                self.assertEqual(self.tree(root), before)
            journal_io.write_entry(run, "senior-qa-recovery", recovery, identifier="senior-recovery-record")
            journal_io.write_entry(run, "architect-recovery", {"risk_id": "failed-check-risk", "lane_id": "architect-recovery", "summary": "Use corrected assertion in the next attempt",
                                                               "status": "pass", "decision": "revised-approach", "instruction": "Verify actual expected bytes on the disposable copy",
                                                               "forbidden_repeat": ["repeat unchanged failed check"], "evidence": refs}, identifier="architect-recovery-record")
            journal_io.write_entry(run, "lane", {"id": "retry-worker", "summary": "Assigned revised check after architecture recovery", "status": "planned",
                                                 "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 4, "critical": False}, identifier="retry-owner")
            retry = journal_io.write_entry(run, "check", {"summary": "Revised assertion still rejects incorrect bytes", "status": "pass", "cwd": str(disposable),
                                                          "command": [sys.executable, "-B", "-c", "assert b'actual' == b'expected'"]}, identifier="revised-check")
            second = {**attempt, "id": "second-attempt", "owner_lane": "retry-worker", "evidence": [{"path": ref["path"]} for ref in retry["evidence"]]}
            journal_io.write_entry(run, "resolution-attempt", second, identifier="second-after-recovery")
            row = json.loads(journal_io.JournalSnapshot.open(run).read_text("risk-resolutions.json"))["resolutions"][0]
            self.assertEqual([item["attempt"] for item in row["attempts"]], [1, 2])
            before = self.tree(root)
            with self.assertRaises(journal_io.JournalError):
                journal_io.write_entry(run, "resolution-attempt", {**second, "id": "third-attempt"}, identifier="third-without-supervisor")
            self.assertEqual(self.tree(root), before)
            instruction = "Execute an independent negative assertion before the last retry"
            journal_io.write_entry(run, "lane", {"id": "supervisor", "summary": "Independent supervising recovery", "status": "planned", "lane_type": "architecture",
                                                 "role": "supervising-architect", "execution_mode": "role-lane", "wave": 5, "critical": False}, identifier="supervisor-declaration")
            journal_io.write_entry(run, "handoff", {"lane_id": "supervisor", "summary": "Reviewed failed-check-risk and the actual revised assertion failure", "status": "pass", "evidence": second["evidence"],
                                                    "sections": [{"section": "Supervising Architect Review", "summary": "failed-check-risk: " + instruction}]}, identifier="supervisor-handoff")
            journal_io.write_entry(run, "supervising-recovery", {"risk_id": "failed-check-risk", "lane_id": "supervisor", "summary": "A distinct negative assertion is the final retry",
                                                                 "status": "pass", "decision": "revised-approach", "instruction": instruction,
                                                                 "forbidden_repeat": ["repeat unchanged failed check"], "evidence": second["evidence"]}, identifier="supervisor-recovery")
            journal_io.write_entry(run, "lane", {"id": "final-retry-worker", "summary": "Final retry after independent supervisor", "status": "planned",
                                                 "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 6, "critical": False}, identifier="final-retry-owner")
            final_check = journal_io.write_entry(run, "check", {"summary": "Independent negative assertion remains a real failure", "status": "pass", "cwd": str(disposable),
                                                                "command": [sys.executable, "-B", "-c", "assert len(b'actual') == 0"]}, identifier="last-negative-check")
            journal_io.write_entry(run, "resolution-attempt", {**second, "id": "third-attempt", "owner_lane": "final-retry-worker",
                                                               "evidence": [{"path": ref["path"]} for ref in final_check["evidence"]]}, identifier="third-after-supervisor")
            row = json.loads(journal_io.JournalSnapshot.open(run).read_text("risk-resolutions.json"))["resolutions"][0]
            self.assertEqual([item["attempt"] for item in row["attempts"]], [1, 2, 3])

    def test_semantic_progress_replay_and_fault_leave_no_partial_documents(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=root)
            request = {"summary": "Доказательства ещё не получены", "status": "blocked"}
            journal_io.write_entry(run, "progress", request, identifier="semantic-progress")
            before = journal_io.JournalSnapshot.open(run)
            self.assertIn(request["summary"], before.read_text("progress.md"))
            journal_io.write_entry(run, "progress", request, identifier="semantic-progress")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_record_and_reader_keep_summary_and_nested_sections_literal(self):
        import tempfile
        import html
        from journal_structure import domain_validator
        validator = domain_validator()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            unusual_path = "checks/actual\n## Evidence\rStatus: fail\u2028<!-- agent-flow:worktree:begin -->.output"
            journal_io.initialize_journal(run, {"checks/actual.output": b"Actual captured evidence\n", unusual_path: b"Exact unusual attachment bytes\n"}, source_root=root)
            summary = "Quoted observation & meaning.\rStatus: fail\r\nVerdict: blocked\u2028## Evidence\nForged prose evidence.\n<!-- agent-flow:worktree:begin -->\n<script>literal data</script>\n```\n## Task Scope\n```"
            nested = "Nested observation.\r## Evidence\r\nForged nested evidence.\u2029Status: fail\n<!-- agent-flow:delegation:end -->"
            request = {"summary": summary, "status": "active", "evidence": [{"path": "checks/actual.output"}, {"path": unusual_path}]}
            result = journal_io.write_entry(run, "progress", request, identifier="literal-progress")
            journal_io.write_entry(run, "lane", {"id": "literal-observer", "summary": "Observe literal evidence without a product edit", "status": "planned",
                                                 "lane_type": "architecture", "role": "architect", "execution_mode": "role-lane", "wave": 1, "critical": False}, identifier="literal-observer-lane")
            handoff_request = {**request, "status": "pass", "lane_id": "literal-observer", "sections": [{"section": "Architecture Compliance", "summary": nested}]}
            handoff_result = journal_io.write_entry(run, "handoff", handoff_request, identifier="literal-handoff")
            snapshot = journal_io.JournalSnapshot.open(run)
            text = snapshot.read_text("progress.md")
            command = [sys.executable, "-B", str(Path(__file__).with_name("journal.py")), "--run-dir", str(run), "read", "progress.md"]
            read = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(read.stdout, text)
            self.assertEqual(validator.read_single_field(run / "progress.md", "Status", snapshot=snapshot), "active")
            self.assertEqual(text.splitlines().count("## Evidence"), 1)
            actual = validator.markdown_section_text(text, "Evidence")
            self.assertIn("checks/actual.output sha256=" + journal_io.digest(b"Actual captured evidence\n"), actual)
            self.assertNotIn("Forged", actual)
            labels = [line.removeprefix("- ").split(" sha256=", 1)[0] for line in actual.splitlines() if line.startswith("- ")]
            self.assertEqual([json.loads('"' + html.unescape(label) + '"') for label in labels], ["checks/actual.output", unusual_path])
            self.assertEqual(snapshot.read_bytes(unusual_path), b"Exact unusual attachment bytes\n")
            self.assertIn(journal_io.digest(snapshot.read_bytes(unusual_path)), actual)
            handoff = snapshot.read_text(handoff_result["handoff"])
            self.assertEqual(handoff.splitlines().count("## Evidence"), 1)
            self.assertNotIn("Forged", validator.markdown_section_text(handoff, "Evidence"))
            observed = validator.markdown_section_text(handoff, "Architecture Compliance")
            for line in nested.splitlines():
                self.assertIn(line, html.unescape(observed))
            for line in summary.splitlines():
                self.assertIn(line, html.unescape(text))
            self.assertNotIn("<script>", text)
            self.assertNotIn("<!-- agent-flow:worktree:begin -->", text)
            self.assertNotIn("<!-- agent-flow:delegation:end -->", text)
            self.assertEqual(journal_io.write_entry(run, "progress", request, identifier="literal-progress"), result)
            self.assertEqual(journal_io.write_entry(run, "handoff", handoff_request, identifier="literal-handoff"), handoff_result)
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((snapshot.revision, snapshot.documents, snapshot.receipts), (after.revision, after.documents, after.receipts))
            with self.assertRaises(journal_io.JournalError):
                journal_io.write_entry(run, "progress", {**request, "summary": summary + " changed"}, identifier="literal-progress")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((snapshot.revision, snapshot.documents, snapshot.receipts), (after.revision, after.documents, after.receipts))
            original = journal_io._put_documents
            before = after
            def fail_after_write(connection, documents):
                original(connection, documents)
                raise OSError("injected semantic failure")
            with patch.object(journal_io, "_put_documents", fail_after_write):
                with self.assertRaisesRegex(OSError, "injected semantic failure"):
                    journal_io.write_entry(run, "progress", {"summary": "New result", "status": "pass"}, identifier="semantic-fault")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_check_records_real_failure_and_replay_does_not_execute_again(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            source = root / "source"
            source.mkdir()
            journal_io.initialize_journal(run, {}, source_root=source)
            disposable = root / "disposable"
            disposable.mkdir()
            marker = disposable / "executions.txt"
            program = "from pathlib import Path; p=Path('executions.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('real failure'); raise SystemExit(7)"
            request = {"summary": "Command outcome", "status": "pass", "command": [sys.executable, "-B", "-c", program], "cwd": str(disposable)}
            journal_io.write_entry(run, "check", request, identifier="real-failure")
            before = journal_io.JournalSnapshot.open(run)
            reports = [json.loads(data) for name, data in before.documents.items() if name.startswith("checks/") and name.endswith(".json")]
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["exit_code"], 7)
            self.assertEqual(reports[0]["status"], "fail")
            self.assertEqual(reports[0]["command"], request["command"])
            outputs = [data for name, data in before.documents.items() if name.endswith(".output")]
            self.assertEqual(outputs, [b"real failure\n"])
            journal_io.write_entry(run, "check", request, identifier="real-failure")
            self.assertEqual(marker.read_text(), "x")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_check_rejects_source_descendant_before_command_or_request(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            child = source / "subdir"
            child.mkdir(parents=True)
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=source)
            request = {"summary": "Do not execute inside source", "status": "pass",
                       "command": [sys.executable, "-B", "-c", "raise SystemExit(0)"],
                       "cwd": str(child)}
            before = self.tree(root)
            with patch.object(subprocess, "run", side_effect=AssertionError("command ran in source")):
                with self.assertRaises(journal_io.JournalError):
                    journal_io.write_entry(run, "check", request, identifier="source-descendant")
            self.assertEqual(self.tree(root), before)

    def test_check_unlaunchable_executable_records_failure_and_replays_without_launch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            disposable = root / "disposable"
            disposable.mkdir()
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=source)
            request = {"summary": "Executable does not exist", "status": "pass",
                       "command": [str(disposable / "missing-executable")], "cwd": str(disposable)}
            result = journal_io.write_entry(run, "check", request, identifier="unlaunchable")
            before = journal_io.JournalSnapshot.open(run)
            reports = [json.loads(data) for name, data in before.documents.items()
                       if name.startswith("checks/command-") and name.endswith(".json")]
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["status"], "fail")
            self.assertIsNone(reports[0]["exit_code"])
            outputs = [data for name, data in before.documents.items() if name.endswith(".output")]
            self.assertEqual(len(outputs), 1)
            self.assertIn(b"missing-executable", outputs[0])
            self.assertIn(b"FileNotFoundError", outputs[0])
            with patch.object(subprocess, "run", side_effect=AssertionError("replayed launch")):
                replay = journal_io.write_entry(run, "check", request, identifier="unlaunchable")
            self.assertEqual(replay, result)
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_check_nul_argv_or_cwd_has_zero_mutations_and_never_launches(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            disposable = root / "copy"
            disposable.mkdir()
            run = root / "run"
            journal_io.initialize_journal(run, {}, source_root=source)
            valid = {"summary": "Invalid native launch input", "status": "pass",
                     "command": [sys.executable, "-B", "-c", "print('must not launch')"], "cwd": str(disposable)}
            for request in ({**valid, "command": [sys.executable, "\0"]}, {**valid, "cwd": str(disposable) + "\0"}):
                with self.subTest(request=request):
                    before = self.tree(root)
                    with patch.object(subprocess, "run", side_effect=AssertionError("invalid command launched")):
                        with self.assertRaises((ValueError, journal_io.JournalError)):
                            journal_io.write_entry(run, "check", request, identifier="nul-input")
                    self.assertEqual(self.tree(root), before)

    def test_task_and_lesson_preserve_project_memory_outside_product_workspace(self):
        import tempfile
        from task_workspace import prepare, seal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            memory = source / ".agent-work/tasks"
            memory.mkdir(parents=True)
            original = b"Existing unrelated task and lesson.\n"
            for name in ("todo.md", "lessons.md"):
                (memory / name).write_bytes(original)
            run = source / ".agent-work/runs/semantic"
            journal_io.initialize_journal(run, {}, source_root=source)
            workspace = prepare(run, source, root / "workspace")
            working = Path(workspace["working_root"])
            before = self.tree(working)
            for kind in ("task", "lesson"):
                request = {"summary": "Preserve unrelated memory", "status": "active"}
                journal_io.write_entry(run, kind, request, identifier="memory-" + kind)
                journal_io.write_entry(run, kind, request, identifier="memory-" + kind)
            self.assertEqual(self.tree(working), before)
            for name in ("todo.md", "lessons.md"):
                raw = (memory / name).read_bytes()
                self.assertTrue(raw.startswith(original))
                self.assertEqual(raw.count(b"Preserve unrelated memory"), 1)
            sealed = seal(run)
            self.assertEqual(sealed["changed_paths"], [])

    def test_check_process_does_not_hold_journal_writer_lock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            disposable = root / "copy"
            disposable.mkdir()
            source = root / "source"
            source.mkdir()
            journal_io.initialize_journal(run, {}, source_root=source)
            actual_run = subprocess.run
            def concurrent_writer(*args, **kwargs):
                # A distinct journal writer must succeed during the process call.
                journal_io.write_entry(run, "progress", {"summary": "Concurrent progress", "status": "active"}, identifier="during-check")
                return actual_run(*args, **kwargs)
            with patch.object(subprocess, "run", concurrent_writer):
                journal_io.write_entry(run, "check", {"summary": "Unlocked actual command", "status": "pass",
                    "command": [sys.executable, "-B", "-c", "print('actual output')"], "cwd": str(disposable)}, identifier="unlocked-check")
            snapshot = journal_io.JournalSnapshot.open(run)
            self.assertIn("Concurrent progress", snapshot.read_text("progress.md"))
            self.assertIsNotNone(snapshot.operation_receipt("during-check"))
            self.assertIsNotNone(snapshot.operation_receipt("unlocked-check"))


class AssignmentStructureRegressions(unittest.TestCase):
    def test_malformed_current_field_types_have_concrete_diagnostics(self):
        from journal_structure import assignment_errors
        for field in ("status", "execution_mode", "id"):
            for value in ([], {}):
                with self.subTest(field=field, value=value):
                    lane = {"id": "work", "status": "planned", "execution_mode": "subagent", "role": "python-worker", field: value}
                    errors = assignment_errors({"lane-map.json": json.dumps({"lanes": [lane]}).encode()})
                    self.assertTrue(any("." + field in error for error in errors), errors)
        for value in ([], {}):
            errors = assignment_errors({"timeline.jsonl": json.dumps({"lane_id": value}).encode()})
            self.assertTrue(any("lane_id invalid" in error for error in errors), errors)
            errors = assignment_errors({"delegation-summary.json": json.dumps({"role_lanes": [
                {"lane_id": "work", "role": "python-worker", "status": value}]}).encode()})
            self.assertTrue(any(".status invalid" in error for error in errors), errors)

    def test_existing_status_mode_and_reviewer_aliases_remain_valid(self):
        from journal_structure import assignment_errors
        for status in ("timed-out", "timed_out", "replaced", "pass_with_risks"):
            with self.subTest(status=status):
                event_status = "pass-with-risks" if status == "pass_with_risks" else "active"
                event = {"lane_id": "review", "role": "reviewer.qa", "execution_mode": "role_lane",
                         "stage": "checks", "status": event_status,
                         "agent_trace": "agents/reviewer.qa/trace.jsonl"}
                record = {"lane_id": "review", "role": "reviewer.qa", "status": event_status,
                          "trace": event["agent_trace"]}
                docs = {"lane-map.json": json.dumps({"lanes": [{"id": "review", "role": "reviewer",
                        "execution_mode": "role_lane", "status": status}]}).encode(),
                        "delegation-summary.json": json.dumps({"role_lanes": [record]}).encode(),
                        "timeline.jsonl": (json.dumps(event) + "\n").encode(),
                        event["agent_trace"]: (json.dumps(event) + "\n").encode()}
                self.assertEqual(assignment_errors(docs), [])

    def test_planned_lane_needs_no_registration(self):
        from journal_structure import assignment_errors
        documents = {"lane-map.json": json.dumps({"lanes": [
            {"id": "work", "role": "python-worker", "execution_mode": "subagent",
             "status": "planned"}]}).encode()}
        self.assertEqual(assignment_errors(documents), [])

    def test_unknown_role_and_mode_are_rejected_without_writes(self):
        from journal_structure import assignment_errors
        lane = {"id": "work", "role": "python-worker", "execution_mode": "subagent",
                "status": "active"}
        record = {"lane_id": "work", "role": "python-worker",
                  "codex_thread_id": base.QA_ID, "status": "active"}
        for field, value, marker in [("lane_id", "unknown", "unknown lane"),
                                     ("role", "reviewer", ".role"),
                                     ("mode", "role-lane", "execution_mode")]:
            with self.subTest(field=field):
                changed = {**record, field: value}
                summary = {"subagents": [changed] if field != "mode" else [],
                           "role_lanes": [changed] if field == "mode" else []}
                errors = assignment_errors({"lane-map.json": json.dumps({"lanes": [lane]}).encode(),
                                            "delegation-summary.json": json.dumps(summary).encode()})
                self.assertTrue(any(marker in error for error in errors), errors)


class JournalRegressions(unittest.TestCase):
    def test_transaction_malformed_summary_shapes_have_diagnostics_and_rollback(self):
        import tempfile
        cases = [([], r"delegation-summary.*object"), (None, r"delegation-summary.*object")]
        for kind in ("subagents", "role_lanes"):
            cases.extend(({kind: value}, kind) for value in (None, 123, "bad", [None]))
        for first_summary in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                run = Path(directory).resolve() / "run"
                initial = {} if first_summary else {"delegation-summary.json": json.dumps({"subagents": [], "role_lanes": []}).encode()}
                journal_io.initialize_journal(run, initial, source_root=Path(directory).resolve())
                before = journal_io.JournalSnapshot.open(run)
                for index, (summary, marker) in enumerate(cases):
                    with self.subTest(first_summary=first_summary, summary=summary):
                        with self.assertRaisesRegex(journal_io.JournalError, marker):
                            journal_io.transact(run, "malformed-summary-" + str(index), {},
                                lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
                        after = journal_io.JournalSnapshot.open(run)
                        self.assertEqual((after.revision, after.documents, after.receipts),
                                         (before.revision, before.documents, before.receipts))

    def test_transaction_malformed_lane_id_has_field_diagnostic_and_full_rollback(self):
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        for kind in ("subagents", "role_lanes"):
            for value in ([], {}):
                with self.subTest(kind=kind, value=value):
                    summary = json.loads(before.read_text("delegation-summary.json"))
                    summary.setdefault(kind, []).append({"lane_id": value, "role": "python-worker", "status": "planned"})
                    with self.assertRaisesRegex(journal_io.JournalError, kind + r".*lane_id"):
                        journal_io.transact(self.run, "malformed-id-" + kind + str(value), {},
                            lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
                    after = journal_io.JournalSnapshot.open(self.run)
                    self.assertEqual((after.revision, after.documents, after.receipts),
                                     (before.revision, before.documents, before.receipts))

    def test_first_summary_cannot_introduce_legacy_metadata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory).resolve() / "run"
            journal_io.initialize_journal(run, {}, source_root=Path(directory).resolve())
            before = journal_io.JournalSnapshot.open(run)
            summary = {"role_lanes": [{"lane_id": "new", "role": "python-worker",
                       "status": "planned", "legacy": True}]}
            with self.assertRaisesRegex(journal_io.JournalError, "legacy facts"):
                journal_io.transact(run, "first-summary-legacy", {},
                    lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual(after.revision, before.revision)
            self.assertFalse(after.exists("delegation-summary.json"))

    def test_new_legacy_flag_cannot_bypass_assignment_registration(self):
        base.write_json(self.run / "lane-map.json", self.assignment_map())
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        for legacy in (True, {"archive_id": "fabricated"}):
            summary = json.loads(before.read_text("delegation-summary.json"))
            summary["subagents"].append({"lane_id": "unplanned", "role": "python-worker", "status": "active",
                "codex_thread_id": base.QA_ID, "legacy": legacy})
            with self.assertRaisesRegex(journal_io.JournalError, "legacy facts"):
                journal_io.transact(self.run, "legacy-bypass-" + str(legacy), {},
                    lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
            self.assertEqual(journal_io.JournalSnapshot.open(self.run).revision, before.revision)

    def assignment_map(self, *, worker=False):
        lanes = [{"id": record["lane_id"], "role": record["role"], "execution_mode": "subagent",
                  "status": "pass"} for record in self.summary["subagents"]]
        lanes.extend({"id": record["lane_id"], "role": record["role"], "execution_mode": "role-lane",
                      "status": record["status"]} for record in self.summary["role_lanes"])
        lanes.append({"id": "new", "role": "python-worker", "execution_mode": "role-lane",
                      "type": "implementation" if worker else "architecture", "status": "planned",
                      "boundary": {"allowed_paths": ["result.txt"]}})
        return {"schema_version": 1, "lanes": lanes}

    def test_transaction_rejects_unknown_role_mode_and_registration_gaps(self):
        base.write_json(self.run / "lane-map.json", self.assignment_map())
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        for field, value in (("id", "unknown"), ("role", "reviewer"), ("execution_mode", "role-lane"),
                             ("status", "active")):
            with self.subTest(field=field):
                data = self.assignment_map()
                index = -1 if field == "status" else 0
                data["lanes"][index][field] = value
                with self.assertRaises(journal_io.JournalError):
                    journal_io.transact(self.run, "bad-map-" + field, {}, lambda current: (
                        {"lane-map.json": json.dumps(data)}, {}))
                after = journal_io.JournalSnapshot.open(self.run)
                self.assertEqual((before.revision, before.documents, before.receipts),
                                 (after.revision, after.documents, after.receipts))

    def test_trace_only_append_and_reordered_events_roll_back(self):
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        trace = self.summary["subagents"][0]["trace"]
        lines = before.read_bytes(trace).splitlines(keepends=True)
        for name, raw in (("append", before.read_bytes(trace) + lines[-1]),
                          ("reorder", b"".join(reversed(lines)))):
            with self.subTest(name=name), self.assertRaises(journal_io.JournalError):
                journal_io.transact(self.run, "trace-only-" + name, {}, lambda current: ({trace: raw}, {}))
            after = journal_io.JournalSnapshot.open(self.run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_recorder_updates_map_summary_trace_atomically_and_replays(self):
        base.write_json(self.run / "lane-map.json", self.assignment_map())
        args = ["--role", "python-worker", "--lane-id", "new", "--execution-mode", "role-lane",
                "--stage", "spawned", "--status", "active", "--summary", "Planning",
                "--operation-id", "atomic-start"]
        self.record(*args)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        lane = json.loads(snapshot.read_text("lane-map.json"))["lanes"][-1]
        record = json.loads(snapshot.read_text("delegation-summary.json"))["role_lanes"][-1]
        self.assertEqual(lane["status"], record["status"])
        self.assertEqual(lane["status"], "active")
        self.assertIn('"lane_id": "new"', snapshot.read_text(record["trace"]))
        self.record(*args)
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((snapshot.revision, snapshot.documents, snapshot.receipts),
                         (after.revision, after.documents, after.receipts))

    def test_worker_spawn_requires_workspace_before_publication(self):
        base.write_json(self.run / "lane-map.json", self.assignment_map(worker=True))
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(SystemExit, "implementation requires prepared workspace"):
            self.record("--role", "python-worker", "--lane-id", "new", "--execution-mode", "role-lane",
                        "--stage", "spawned", "--status", "active", "--summary", "Implement")
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents, before.receipts),
                         (after.revision, after.documents, after.receipts))

    def test_json_verification_rejected_before_any_write(self):
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        for raw in (" [1] " + " " * 1000, "null", "true", '"text"', "{broken" + " " * 1000,
                    json.dumps({"task_kind": [], "author_thread_ids": None})):
            with self.subTest(raw=raw[:30]), self.assertRaises(SystemExit):
                self.record("--role", "orchestrator", "--execution-mode", "role-lane",
                            "--stage", "verification", "--status", "active", "--summary", "Invalid",
                            "--verification-json", raw)
            after = journal_io.JournalSnapshot.open(self.run)
            self.assertEqual((before.revision, before.documents, before.receipts),
                             (after.revision, after.documents, after.receipts))

    def test_worker_prose_completion_resolves_source_without_acceptance(self):
        base.workspace_pack(self)
        thread = "00000000-0000-4000-8000-000000000004"
        text = "Реализация проверена. Следующий шаг — независимая проверка."
        events = base.session(thread, "python-worker", {}, 5)
        events[-2]["payload"]["content"][0]["text"] = text
        events[-1]["payload"]["last_agent_message"] = text
        self.source.sessions[thread] = events
        handoff = "handoffs/worker-prose.md"
        journal_io.transact(self.run, "fixture-worker-prose", {}, lambda current: ({handoff: text}, {}))
        resolved = {"codex_thread_id": thread, "agent_path": "/root/worker",
                    "observed_spawn_at": events[1]["timestamp"], "session_meta_event": 1,
                    "task_started_event": 2}
        common = ["--role", "python-worker", "--lane-id", "worker-prose",
                  "--resolve-session", "--agent-path", "/root/worker", "--summary", "Actual completion"]
        with patch.object(self.source, "resolve_session", return_value=resolved, create=True):
            self.record(*common, "--stage", "spawned", "--status", "active")
            before = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
            output = self.record(*common, "--stage", "handoff", "--status", "pass", "--artifact", handoff)
            snapshot = journal_io.JournalSnapshot.open(self.run)
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            record = next(r for r in summary["subagents"] if r["lane_id"] == "worker-prose")
            self.assertEqual(record["source_completion_sha256"], evidence.sha256(text.encode()))
            self.assertEqual(record["completion_turn_id"], thread + "-turn")
            self.assertNotIn("reviewed_result_hash", record)
            self.assertEqual(summary["verification"], before["verification"])
            self.assertIn("receipt:", output)
            self.assertIn("unchanged", self.record(*common, "--stage", "handoff", "--status", "pass", "--artifact", handoff))
            self.assertEqual(snapshot.revision, journal_io.JournalSnapshot.open(self.run).revision)
            selected = json.loads(snapshot.read_text("delegation-summary.json"))
            selected["verification"]["qa"] = "worker-prose"
            errors = evidence.validate_verification(self.run, selected, {}, "pending", self.source, snapshot=snapshot)
            self.assertIn("worker completion cannot be selected as QA/reviewer", "; ".join(errors))
            with self.assertRaisesRegex(journal_io.JournalError, "handoff is immutable"):
                journal_io.transact(self.run, "fixture-replace-completed-worker-handoff", {},
                                    lambda current: ({handoff: "Подменённый отчёт."}, {}))
            self.assertEqual(snapshot.documents, journal_io.JournalSnapshot.open(self.run).documents)
            events[-2]["payload"]["content"][0]["text"] = events[-1]["payload"]["last_agent_message"] = "Подменённый текст."
            with self.assertRaises(SystemExit):
                self.record(*common, "--stage", "handoff", "--status", "pass", "--artifact", handoff)
            self.assertEqual(snapshot.documents, journal_io.JournalSnapshot.open(self.run).documents)

    def test_semantic_worker_handoff_then_source_enrichment_preserves_history(self):
        base.workspace_pack(self)
        with patch("verification_evidence.CodexSessionSource", return_value=self.source):
            cases = [(status, literal) for literal in (False, True) for status in ("pass", "blocked", "fail")]
            for index, (status, literal) in enumerate(cases, 4):
                thread = f"00000000-0000-4000-8000-{index:012d}"
                lane = f"semantic-worker-{status}-{index}"
                text = "Настоящий терминальный вывод; reported outcome задаётся отдельно."
                events = base.session(thread, "python-worker", {}, 5)
                events[-2]["payload"]["content"][0]["text"] = text
                events[-1]["payload"]["last_agent_message"] = text
                self.source.sessions[thread] = events[:3]
                resolved = {"codex_thread_id": thread, "agent_path": "/root/" + lane,
                            "observed_spawn_at": events[1]["timestamp"], "session_meta_event": 1,
                            "task_started_event": 2}
                common = ["--role", "python-worker", "--lane-id", lane, "--resolve-session",
                          "--agent-path", "/root/" + lane, "--summary", "Actual terminal source"]
                with patch.object(self.source, "resolve_session", return_value=resolved, create=True):
                    self.record(*common, "--stage", "spawned", "--status", "active")
                    proof = "checks/proof[1]&<literal>.txt" if literal else "checks/proof.txt"
                    journal_io.transact(self.run, lane + "-proof", {},
                                        lambda current: ({proof: "Immutable original evidence"}, {}))
                    journal_io.write_entry(self.run, "handoff", {"summary": text, "status": status,
                        "lane_id": lane, "evidence": [{"path": proof}]}, identifier=lane + "-semantic")
                    before = journal_io.JournalSnapshot.open(self.run)
                    old_summary = json.loads(before.read_text("delegation-summary.json"))
                    old_record = next(r for r in old_summary["subagents"] if r["lane_id"] == lane)
                    handoff = old_record["handoff"]
                    followup = base.session(thread, "python-worker", {}, 6)[1:]
                    for event in followup:
                        if "turn_id" in event["payload"]:
                            event["payload"]["turn_id"] = thread + "-final-turn"
                    followup[-2]["payload"]["content"][0]["text"] = text
                    followup[-1]["payload"]["last_agent_message"] = text
                    self.source.sessions[thread] = events + followup
                    args = [*common, "--stage", "handoff" if status == "pass" else status,
                            "--status", status, "--artifact", handoff, "--artifact", proof,
                            "--operation-id", lane + "-source"]
                    self.record(*args)
                    after = journal_io.JournalSnapshot.open(self.run)
                    new_summary = json.loads(after.read_text("delegation-summary.json"))
                    new_record = next(r for r in new_summary["subagents"] if r["lane_id"] == lane)
                    self.assertTrue(all(new_record[k] == v for k, v in old_record.items()))
                    self.assertEqual(new_record["task_started_event"], 2)
                    self.assertEqual(new_record["completion_task_started_event"], 6)
                    self.assertEqual(new_record["source_completion_sha256"], evidence.sha256(text.encode()))
                    self.assertEqual(old_summary["verification"], new_summary["verification"])
                    self.assertEqual({k: v for k, v in before.documents.items() if k != "delegation-summary.json"},
                                     {k: v for k, v in after.documents.items() if k != "delegation-summary.json"})
                    self.record(*args)
                    self.assertEqual(after.revision, journal_io.JournalSnapshot.open(self.run).revision)
                    for field, value in (("--status", "pass-with-risks"), ("--artifact", "checks.md")):
                        changed = list(args)
                        changed[changed.index(field) + 1] = value
                        changed[changed.index("--operation-id") + 1] = lane + "-conflict-" + field[2:]
                        with self.subTest(status=status, field=field), self.assertRaises(SystemExit):
                            self.record(*changed)
                        self.assertEqual(after.documents, journal_io.JournalSnapshot.open(self.run).documents)

    def test_worker_source_change_after_capture_rejects_enrichment(self):
        base.workspace_pack(self)
        thread = "00000000-0000-4000-8000-000000000004"
        events = base.session(thread, "python-worker", {}, 5)
        events[-2]["payload"]["content"][0]["text"] = events[-1]["payload"]["last_agent_message"] = "Source completion."
        self.source.sessions[thread] = events
        common = ["--role", "python-worker", "--lane-id", "source-race", "--codex-thread-id", thread,
                  "--summary", "Actual terminal source"]
        with patch("verification_evidence.CodexSessionSource", return_value=self.source):
            self.record(*common, "--stage", "spawned", "--status", "active")
            journal_io.write_entry(self.run, "handoff", {"summary": "Immutable handoff", "status": "pass",
                                  "lane_id": "source-race"}, identifier="source-race-semantic")
        before = journal_io.JournalSnapshot.open(self.run)
        from journal_lifecycle import freeze_validation
        def mutate_after_capture(snapshot, source):
            freeze_validation(snapshot, source)
            events[-2]["payload"]["content"][0]["text"] = events[-1]["payload"]["last_agent_message"] = "Changed source."
        with patch("journal_lifecycle.freeze_validation", side_effect=mutate_after_capture):
            with self.assertRaisesRegex(SystemExit, "changed after capture"):
                self.record(*common, "--stage", "handoff", "--status", "pass",
                            "--completion-turn-id", thread + "-turn", "--artifact", "handoffs/source-race.md",
                            "--operation-id", "source-race-enrichment")
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents, before.receipts),
                         (after.revision, after.documents, after.receipts))

    def test_worker_spawn_registers_author_and_requires_boundary(self):
        base.initialize_open_fixture(self.run)
        from task_workspace import prepare
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        temporary = base.tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        prepare(self.run, self.root, Path(temporary.name).resolve() / "bundle")
        data = self.assignment_map(worker=True)
        summary = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        summary["verification"].update(author_thread_ids=[], result_files=[], run_changed_files=[],
                                       result_hash=None, qa=None, reviewer=None)
        journal_io.transact(self.run, "worker-plan", {}, lambda current: (
            {"lane-map.json": json.dumps(data), "delegation-summary.json": json.dumps(summary)}, {}))
        args = ["--role", "python-worker", "--lane-id", "new", "--execution-mode", "role-lane",
                "--stage", "spawned", "--status", "active", "--summary", "Implement"]
        data["lanes"][-1]["boundary"]["allowed_paths"] = []
        journal_io.transact(self.run, "empty-boundary", {}, lambda current: ({"lane-map.json": json.dumps(data)}, {}))
        before = journal_io.JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(SystemExit, "boundary.allowed_paths"):
            self.record(*args)
        self.assertEqual(before.documents, journal_io.JournalSnapshot.open(self.run).documents)
        data["lanes"][-1]["boundary"]["allowed_paths"] = ["result.txt"]
        journal_io.transact(self.run, "restore-boundary", {}, lambda current: ({"lane-map.json": json.dumps(data)}, {}))
        self.record(*args)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        updated = json.loads(snapshot.read_text("delegation-summary.json"))["verification"]
        self.assertIn(base.ROOT_ID, updated["author_thread_ids"])

    def test_worker_readiness_requires_registered_ready_artifact(self):
        base.initialize_open_fixture(self.run)
        from task_workspace import prepare
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        temporary = base.tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        prepare(self.run, self.root, Path(temporary.name).resolve() / "bundle")
        summary = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        summary["verification"].update(result_files=[], run_changed_files=[], result_hash=None, qa=None, reviewer=None)
        journal_io.transact(self.run, "prepared-readiness-summary", {},
                            lambda current: ({"delegation-summary.json": json.dumps(summary)}, {}))
        data = self.assignment_map(worker=True)
        data.update(schema_version=2, architecture_contract_required=True)
        journal_io.transact(self.run, "readiness-plan", {}, lambda current: ({"lane-map.json": json.dumps(data)}, {}))
        before = journal_io.JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(SystemExit, "verification_readiness"):
            self.record("--role", "python-worker", "--lane-id", "new", "--execution-mode", "role-lane",
                        "--stage", "spawned", "--status", "active", "--summary", "Implement")
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents, before.receipts),
                         (after.revision, after.documents, after.receipts))

    def test_existing_bad_history_allows_unrelated_write_but_no_new_bad_role(self):
        data = self.assignment_map()
        data["lanes"][0]["role"] = "wrong-old"
        base.write_json(self.run / "lane-map.json", data)
        base.initialize_open_fixture(self.run)
        journal_io.transact(self.run, "unrelated", {}, lambda current: ({"note.md": b"note"}, {}))
        data["lanes"][0]["role"] = "wrong-new"
        before = journal_io.JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(journal_io.JournalError, "wrong-new"):
            journal_io.transact(self.run, "different-bad-role", {}, lambda current: (
                {"lane-map.json": json.dumps(data)}, {}))
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents), (after.revision, after.documents))

    def setUp(self):
        base.EvidenceRegressions.setUp(self)
        self.open_timeline()

    def snapshot(self):
        if (self.run / ".journal/state.sqlite3").exists():
            return {name: data for name, data in journal_io.JournalSnapshot.open(self.run).documents.items() if data is not None}
        return {p.relative_to(self.run).as_posix(): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}

    def record(self, *args):
        if "--prepare-conclusion" not in args:
            try:
                base.initialize_open_fixture(self.run)
            except (journal_io.JournalError, OSError) as exc:
                raise SystemExit(str(exc)) from exc
        with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()):
            recorder.main(["--run-dir", str(self.run), *args], session_source=self.source)
        return output.getvalue()

    def repeat_args(self, role="qa-verifier"):
        record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
        return ["--role", role, "--lane-id", record["lane_id"], "--stage", "handoff", "--status", "pass",
                "--summary", "Repeat", "--completion-turn-id", record["completion_turn_id"],
                "--artifact", record["handoff"], "--artifact", "checks.md"]

    def open_timeline(self):
        path = self.run / "timeline.jsonl"
        lines = path.read_text().splitlines()
        if lines and json.loads(lines[-1]).get("stage") == "final":
            path.write_text("\n".join(lines[:-1]) + "\n")

    def test_assignment_stage_status_commands(self):
        allowed = {"spawned": {"active"}, "handoff": {"pass", "pass-with-risks"},
                   "blocked": {"blocked"}, "fail": {"fail"}}
        base.initialize_open_fixture(self.run)
        for mode in ("subagent", "role-lane"):
            for stage, statuses in allowed.items():
                for status in ("active", "pass", "pass-with-risks", "blocked", "fail", "done"):
                    with self.subTest(mode=mode, stage=stage, status=status):
                        lane = f"{mode}-{stage}-{status}"
                        args = ["--role", "qa-verifier", "--execution-mode", mode, "--lane-id", lane,
                                "--stage", stage, "--status", status, "--summary", "State matrix",
                                "--artifact", "checks.md"]
                        if mode == "subagent":
                            args += ["--codex-thread-id", base.QA_ID]
                        before = journal_io.JournalSnapshot.open(self.run)
                        requests = {p.name: p.read_bytes() for p in (self.run / '.journal/requests').glob('*')}
                        if status in statuses:
                            if mode == "subagent" and stage != "spawned":
                                self.record("--role", "qa-verifier", "--lane-id", lane, "--codex-thread-id", base.QA_ID,
                                            "--stage", "spawned", "--status", "active", "--summary", "Own spawn")
                            self.record(*args)
                        else:
                            with patch.object(recorder, "ensure_result_contract", side_effect=AssertionError("too late")), self.assertRaisesRegex(SystemExit, "requires status"):
                                self.record(*args)
                            after = journal_io.JournalSnapshot.open(self.run)
                            self.assertEqual((before.revision, before.documents, before.receipts),
                                             (after.revision, after.documents, after.receipts))
                            self.assertEqual(requests, {p.name: p.read_bytes() for p in (self.run / '.journal/requests').glob('*')})

    def test_new_event_guard_covers_append_and_publish_histories(self):
        base.initialize_open_fixture(self.run)
        before = journal_io.JournalSnapshot.open(self.run)
        bad = {**base.trace_event(self.summary['subagents'][0], 'spawned'), 'status': 'pass', 'lane_id': 'bad'}
        journal_io.validate_event(bad)  # Historical facts retain their original validation contract.
        for path in ('timeline.jsonl', 'agents/qa-verifier/trace.jsonl', 'agents/new/trace.jsonl'):
            with self.subTest(path=path), self.assertRaisesRegex(journal_io.JournalError, 'requires status'):
                journal_io.transact(self.run, 'bad-' + path, {}, lambda current: (
                    {path: current.documents.get(path, b'') + (json.dumps(bad) + '\n').encode()}, {}))
        with self.assertRaisesRegex(journal_io.JournalError, 'requires status'):
            journal_io.append_event(self.run / 'timeline.jsonl', bad, identifier='bad-append')
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual((before.revision, before.documents, before.receipts), (after.revision, after.documents, after.receipts))
        self.assertFalse((self.run / '.journal/requests/bad-append').exists())

    def test_repeat_reviewer_and_reordered_evidence(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            args[-4:] = ["--artifact", "checks.md", "--artifact", args[-3]]
            self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_saved_source_receipt_allows_reordered_evidence(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            self.assertIn("unchanged", self.record(*args))
            args[-4:] = ["--artifact", "checks.md", "--artifact", args[-3]]
            self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_saved_source_receipt_ignores_transport_and_summary(self):
        before = self.snapshot()
        for role in ("qa-verifier", "reviewer"):
            args = self.repeat_args(role)
            record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
            args.extend(["--codex-thread-id", record["codex_thread_id"]])
            self.assertIn("unchanged", self.record(*args))
            args[args.index("--summary") + 1] = "Technical retry with different display text"
            args.extend(["--artifact", "checks.md", "--resolve-session", "--agent-path", "/root/verifier"])
            record = self.summary["subagents"][0 if role == "qa-verifier" else 1]
            resolved = {"codex_thread_id": record["codex_thread_id"], "agent_path": "/root/verifier",
                        "observed_spawn_at": "2026-01-01T00:00:00Z", "session_meta_event": 1,
                        "task_started_event": 2}
            with patch.object(self.source, "resolve_session", return_value=resolved, create=True):
                self.assertIn("unchanged", self.record(*args))
        self.assertEqual(before, self.snapshot())

    def test_repeat_revalidates_source_and_evidence(self):
        for mutation, message in (("source", "unfinished"), ("evidence", "sha256"), ("handoff", "sha256"), ("result", "current result")):
            with self.subTest(mutation=mutation):
                summary, source = base.acceptance_pack(self.run)
                self.open_timeline()
                self.summary, self.source = summary, source
                if mutation == "source":
                    self.source.sessions[base.QA_ID].append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "new"}})
                else:
                    path = {"evidence": self.run / "checks.md", "handoff": self.run / "handoffs/evidence-qa.md", "result": self.root / "result.txt"}[mutation]
                    path.write_text(path.read_text() + "Changed\n")
                if (self.run / ".journal/state.sqlite3").exists():
                    documents, _ = journal_io.capture_flat(self.run)
                    journal_io.transact(self.run, "fixture-" + mutation, {}, lambda snapshot: (documents, {}))
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, message):
                    self.record(*self.repeat_args())
                self.assertEqual(before, self.snapshot())

    def test_new_qa_turn_invalidates_reviewer(self):
        self.open_timeline()
        base.workspace_pack(self)
        events = self.source.sessions[base.QA_ID]
        for event in events:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = "new-qa-turn"
        args = self.repeat_args()
        args[args.index("--completion-turn-id") + 1] = "new-qa-turn"
        with self.assertRaisesRegex(SystemExit, "terminal assignment"):
            self.record(*args)
        args[args.index("--lane-id") + 1] = "fresh-qa"
        args.extend(["--codex-thread-id", base.QA_ID])
        self.record("--role", "qa-verifier", "--lane-id", "fresh-qa", "--codex-thread-id", base.QA_ID,
                    "--stage", "spawned", "--status", "active", "--summary", "Fresh QA assignment")
        self.record(*args)
        stored = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertIsNone(stored["verification"]["reviewer"])

    def test_prepare_conclusion_is_read_only_and_requires_completion(self):
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        args = ["--prepare-conclusion", "--role", "qa-verifier", "--lane-id", "evidence-qa", "--status", "fail",
                "--reason", "Workflow not completed", "--unverified", "Candidate workflow",
                "--artifact", "handoffs/evidence-qa.md", "--artifact", "checks.md"]
        self.source.sessions[base.QA_ID] = self.source.sessions[base.QA_ID][:3]
        before = self.snapshot()
        output = self.record(*args)
        self.assertEqual(json.loads(output)["verdict"], "fail")
        self.assertEqual(before, self.snapshot())
        with self.assertRaisesRegex(SystemExit, "completed turn missing"):
            self.record(*self.repeat_args())
        self.assertEqual(before, self.snapshot())

    def test_semantic_conclusion_rechecks_evidence_changed_before_writer_transaction(self):
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        self.record("--role", "qa-verifier", "--lane-id", "race-qa", "--codex-thread-id", base.QA_ID,
                    "--stage", "spawned", "--status", "active", "--summary", "Assigned race check")
        pending = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        pending["verification"]["result_hash"] = None
        journal_io.transact(self.run, "race-unsealed-result", {}, lambda current: ({"delegation-summary.json": json.dumps(pending)}, {}))
        # This new attachment is not referenced by any earlier acceptance.
        journal_io.transact(self.run, "race-evidence", {}, lambda current: ({"checks/race.output": b"original output"}, {}))
        before = journal_io.JournalSnapshot.open(self.run)
        actual_writer = journal_io.write_entry
        def change_evidence_then_write(*args, **kwargs):
            journal_io.transact(self.run, "race-edit", {}, lambda current: ({"checks/race.output": b"changed output"}, {}))
            return actual_writer(*args, **kwargs)
        with patch.object(journal_io, "write_entry", change_evidence_then_write):
            with self.assertRaises((SystemExit, journal_io.JournalError)):
                self.record("--prepare-conclusion", "--role", "qa-verifier", "--lane-id", "race-qa", "--status", "fail",
                    "--summary", "Actual failure reported", "--reason", "Checks failed", "--unverified", "Acceptance",
                    "--artifact", "handoffs/race-qa.md", "--artifact", "checks/race.output", "--operation-id", "race-conclusion")
        after = journal_io.JournalSnapshot.open(self.run)
        self.assertEqual(after.read_bytes("checks/race.output"), b"changed output")
        self.assertEqual(after.revision, before.revision + 1)
        self.assertFalse(after.exists("handoffs/race-qa.md"))
        self.assertIsNone(after.operation_receipt("race-conclusion"))

    def test_invalid_modes_and_missing_assignment_do_not_write(self):
        for args in (["--render-final", "--prepare-conclusion"], ["--prepare-conclusion", "--role", "qa-verifier"],
                     ["--render-final", "--stage", "final"], ["--role", "orchestrator", "--status", "pass"],
                     ["--prepare-conclusion", "--role", "reviewer", "--status", "pass", "--lane-id", "unknown"]):
            before = self.snapshot()
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.record(*args)
            self.assertEqual(before, self.snapshot())
        for option in ("--wave", "--next-step", "--execution-mode", "--runtime-nickname", "--stable-agent-name"):
            args = ["--render-final", option, "1" if option == "--wave" else "subagent"]
            before = self.snapshot()
            with self.subTest(option=option), self.assertRaises(SystemExit):
                self.record(*args)
            self.assertEqual(before, self.snapshot())

    def test_repeat_checks_independent_boundary_declaration(self):
        lanes = [{"id": record["lane_id"], "role": record["role"], "execution_mode": "subagent",
                  "status": "pass"} for record in self.summary["subagents"]]
        lanes.append({"id": "worker", "status": "planned", "role": "python-worker",
                      "execution_mode": "subagent", "boundary": {"changed_paths_artifact": "boundary.json"}})
        base.write_json(self.run / "lane-map.json", {"lanes": lanes})
        base.write_json(self.run / "boundary.json", {"changed_paths": ["omitted.txt"]})
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "omits run-owned paths: omitted.txt"):
            self.record(*self.repeat_args())
        self.assertEqual(before, self.snapshot())

    def test_prepare_reviewer_revalidates_source_bound_qa(self):
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        handoff = self.run / "handoffs/evidence-qa.md"
        handoff.write_text(handoff.read_text() + "Changed QA\n")
        self.summary["subagents"][0]["handoff_sha256"] = evidence.sha256(handoff.read_bytes())
        base.write_json(self.run / "delegation-summary.json", self.summary)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "source handoff_sha256 mismatch"):
            self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", "handoffs/evidence-reviewer.md", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_reviewer_cannot_adopt_qa_handoff(self):
        self.open_timeline()
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        qa = self.summary["subagents"][0]
        alias = self.run / "handoffs/qa-alias.md"
        alias.symlink_to("evidence-qa.md")
        hardlink = self.run / "handoffs/qa-hardlink.md"
        hardlink.hardlink_to(self.run / qa["handoff"])
        for handoff in (qa["handoff"], str(self.run / qa["handoff"]), "handoffs/qa-alias.md", "handoffs/qa-hardlink.md"):
            with self.subTest(handoff=handoff):
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "reviewer must use its own handoff"):
                    self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", handoff, "--artifact", "checks.md")
                self.assertEqual(before, self.snapshot())
                canonical = journal_io.display_path(handoff, self.run.resolve())
                answer = {"verdict": "passed", "reviewed_result_hash": qa["reviewed_result_hash"],
                          "handoff": canonical, "handoff_sha256": qa["handoff_sha256"], "qa_handoff_sha256": qa["handoff_sha256"]}
                self.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "reviewer", answer, 20)
                args = self.repeat_args("reviewer")
                args[-3] = handoff
                with self.assertRaisesRegex(SystemExit, "reviewer must use its own handoff"):
                    self.record(*args)
                self.assertEqual(before, self.snapshot())
                stored = json.loads((self.run / "delegation-summary.json").read_text())
                stored["subagents"][1].update(handoff=canonical, handoff_sha256=qa["handoff_sha256"])
                errors = evidence.validate_verification(self.run, stored, {}, "ship", self.source)
                self.assertIn("reviewer must use its own handoff", "\n".join(errors))

    def test_distinct_reviewer_handoff_may_have_identical_bytes(self):
        self.open_timeline()
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        reviewer = self.summary["subagents"][1]
        (self.run / reviewer["handoff"]).write_bytes((self.run / self.summary["subagents"][0]["handoff"]).read_bytes())
        base.workspace_pack(self)
        answer = json.loads(self.record("--prepare-conclusion", "--role", "reviewer", "--lane-id", "evidence-reviewer", "--status", "pass", "--artifact", reviewer["handoff"], "--artifact", "checks.md"))
        self.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "reviewer", answer, 20)
        self.record(*self.repeat_args("reviewer"))
        before = self.snapshot()
        self.assertIn("unchanged", self.record(*self.repeat_args("reviewer")))
        self.assertEqual(before, self.snapshot())
        stored = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertEqual(evidence.validate_verification(self.run, stored, {}, "ship", self.source), [])

    def test_wc04_fresh_change_cannot_omit_or_fake_workspace(self):
        documents, _ = journal_io.capture_flat(self.run)
        journal_io.initialize_journal(self.run, documents, source_root=self.root)
        for fake in ({}, {"result_contract_version": 1, "legacy": True, "workspace": None}):
            summary = json.loads(json.dumps(self.summary))
            summary.update(fake)
            errors = evidence.validate_verification(self.run, summary, {}, "ship", self.source)
            self.assertIn("registered and sealed workspace", "; ".join(errors))

    def test_wc04_historical_replay_does_not_authorize_new_qa_turn(self):
        self.assertIn("unchanged", self.record(*self.repeat_args()))
        before = self.snapshot()
        for event in self.source.sessions[base.QA_ID]:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = "fresh-uncovered-turn"
        args = self.repeat_args()
        args[args.index("--completion-turn-id") + 1] = "fresh-uncovered-turn"
        with self.assertRaisesRegex(SystemExit, "registered and sealed workspace"):
            self.record(*args)
        self.assertEqual(before, self.snapshot())

    def test_wc04_published_workspace_pointer_cannot_replace_receipt_binding(self):
        base.workspace_pack(self)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        receipt = snapshot.operation_receipt("workspace-prepare")
        document = receipt["result"]["document"]
        data = json.loads(snapshot.read_bytes(document))
        data["baseline_manifest"]["exclusions"].append("result.txt")
        journal_io.transact(self.run, "fixture-forged-pointer", {}, lambda current: ({document: json.dumps(data)}, {}))
        with self.assertRaisesRegex(journal_io.JournalError, "workspace document changed"):
            evidence.result_hash(self.run, self.summary["verification"])

    def test_wc04_boundary_uses_full_scanner_delta_and_rejects_omission(self):
        workspace = base.workspace_pack(self, files=["undeclared\nЮникод.txt", "back\\slash"])
        boundary = load("workspace_boundary", "record-lane-boundary.py")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        author = {"lane_id": "worker", "role": "python-worker", "trace": "agents/worker/trace.jsonl", "handoff": "handoffs/worker.md"}
        summary["role_lanes"].append(author); summary["role_lanes_used"] = True
        event = base.trace_event({**author, "codex_thread_id": base.ROOT_ID}, "spawned")
        event.update(execution_mode="role-lane", stage="checks", timestamp=journal_io.now_iso())
        paths = self.summary["verification"]["result_files"]
        handoff = ("Own authored paths:\n" + "\n".join(paths) + "\n").encode()
        proof = json.dumps({"created_files": [{"path": name, "bytes": (Path(workspace["working_root"]) / name).read_text()} for name in paths]}).encode()
        declaration = {"lane_id": "worker", "authored_paths": paths, "handoff": {"path": author["handoff"], "sha256": evidence.sha256(handoff)},
            "edit_evidence": [{"path": "checks/worker-created-files.json", "sha256": evidence.sha256(proof)}], "coauthors": {}}
        journal_io.transact(self.run, "fixture-worker-authorship", {}, lambda current: ({
            "delegation-summary.json": json.dumps(summary), author["trace"]: json.dumps(event) + "\n", author["handoff"]: handoff,
            "checks/worker-created-files.json": proof, "timeline.jsonl": current.read_text("timeline.jsonl") + json.dumps(event) + "\n"}, {}))
        path = boundary.capture_boundary(run_dir=self.run, lane_id="worker", repo_root=None,
                                         base_ref="HEAD", head_ref="working-tree", declaration=declaration)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        actual = json.loads(snapshot.read_bytes(path))
        self.assertEqual(actual["changed_paths"], self.summary["verification"]["result_files"])
        self.assertEqual(actual["version"], 2)
        self.assertEqual(actual["authored_paths"], paths)
        self.assertEqual(base.validator.validate_lane_boundary_artifact(path, str(path), "worker", snapshot=snapshot)[1], [])
        actual["changed_paths"] = []
        journal_io.transact(self.run, "fixture-omitted-boundary", {},
                            lambda current: ({path.relative_to(self.run).as_posix(): json.dumps(actual)}, {}))
        errors = base.validator.validate_lane_boundary_artifact(path, str(path), "worker", snapshot=journal_io.JournalSnapshot.open(self.run))[1]
        self.assertIn("full registered workspace delta", "; ".join(errors))

    def test_wc04_mode_only_reseal_requires_new_acceptance(self):
        from task_workspace import seal
        workspace = base.workspace_pack(self)
        original_hash = self.summary["verification"]["result_hash"]
        (Path(workspace["working_root"]) / "result.txt").chmod(0o755)
        sealed = seal(self.run)
        self.assertEqual(sealed["changed_paths"], ["result.txt"])
        with self.assertRaisesRegex(evidence.EvidenceError, "full registered workspace delta"):
            evidence.result_hash(self.run, self.summary["verification"])
        snapshot = journal_io.JournalSnapshot.open(self.run)
        path = "checks/lane-boundary-fixture-root-author.json"
        boundary = json.loads(snapshot.read_text(path))
        boundary.update(candidate_id=sealed["candidate_id"], candidate_digest=sealed["candidate_digest"],
                        head_ref=sealed["candidate_id"])
        journal_io.transact(self.run, "fixture-resealed-boundary", {},
                            lambda current: ({path: json.dumps(boundary)}, {}))
        self.assertNotEqual(evidence.result_hash(self.run, self.summary["verification"]), original_hash)
        errors = evidence.validate_verification(self.run, self.summary, {}, "ship", self.source)
        self.assertIn("stale", "; ".join(errors))

    def test_wc_delivery_requires_current_full_candidate_and_acceptance(self):
        from task_workspace import delivery
        workspace = base.workspace_pack(self)
        self.record("--render-final")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        for record in summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        journal_io.transact(self.run, "obligations", {}, lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
        from journal_lifecycle import finalize
        snapshot = journal_io.JournalSnapshot.open(self.run)
        finalize(self.run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                 expected_generation=1, identifier="fixture-finalize", final_summary="Actual fixture workspace independently accepted",
                 verdict="ship", session_source=self.source)
        delivered = delivery(self.run, session_source=self.source)
        before = self.snapshot()
        self.assertEqual(delivered, delivery(self.run, session_source=self.source))
        self.assertEqual(before, self.snapshot())
        self.assertTrue(Path(delivered["description"]).is_file())
        (self.root / "foreign-new.txt").write_text("Foreign source edit stays outside the retained candidate.")
        self.assertEqual(delivered, delivery(self.run, session_source=self.source))
        candidate = Path(delivered["candidate_root"])
        (candidate / "undeclared.txt").write_text("Hidden change")
        with self.assertRaisesRegex(journal_io.JournalError, "candidate changed"):
            delivery(self.run, session_source=self.source)
        self.assertEqual(before, self.snapshot())

    def test_invalid_stages_and_final_order_do_not_write(self):
        for role, stage in (("orchestrator", "final"), ("qa-verifier", "final"), ("qa-verifier", "spawn")):
            before = self.snapshot()
            with self.subTest(stage=stage), self.assertRaises(SystemExit):
                self.record("--role", role, "--stage", stage, "--status", "pass", "--summary", "Invalid")
            self.assertEqual(before, self.snapshot())

    def test_open_target_failure_preserves_existing_files(self):
        self.open_timeline()
        target = self.run / "agents/orchestrator/trace.jsonl"
        target.mkdir(parents=True)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "write target must be a file"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active", "--summary", "Pending", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_open_error_does_not_truncate_existing_target(self):
        self.open_timeline()
        target = self.run / "agents/orchestrator/trace.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text('{"existing": "trace bytes"}\n')
        before = self.snapshot()
        base.initialize_open_fixture(self.run)
        with patch.object(journal_io, "_put_documents", side_effect=PermissionError("synthetic target open failure")), self.assertRaisesRegex(SystemExit, "synthetic target open failure"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "active", "--summary", "Pending", "--artifact", "checks.md")
        self.assertEqual(before, self.snapshot())

    def test_symlink_write_target_parent_rejected(self):
        self.open_timeline()
        (self.run / "agents/orchestrator").symlink_to(self.root, target_is_directory=True)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "symlink"):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "active", "--summary", "Pending")
        self.assertEqual(before, self.snapshot())

    def test_invalid_verification_type_and_coverage_preserve_bytes(self):
        self.open_timeline()
        for value in (False, ["result.txt", "missing.txt"]):
            data = dict(self.summary["verification"], run_changed_files=value)
            before = self.snapshot()
            with self.assertRaises(SystemExit):
                self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active", "--summary", "Pending", "--verification-json", json.dumps(data))
            self.assertEqual(before, self.snapshot())

    def test_render_twice_preserves_prose_and_escapes_paths(self):
        path = "имя с пробелом `<tag>.txt"
        self.summary["verification"]["run_changed_files"] = [path]
        base.write_json(self.run / "delegation-summary.json", self.summary)
        prose = "# Отчёт\n\nСоседний список:\n- чужой файл\n\nРусское пояснение.\n"
        final = self.run / "final.md"
        final.write_text(prose)
        base.workspace_pack(self, files=[path])
        self.record("--render-final")
        first = journal_io.JournalSnapshot.open(self.run).read_text("final.md")
        self.record("--render-final")
        self.assertEqual(first, journal_io.JournalSnapshot.open(self.run).read_text("final.md"))
        self.assertTrue(first.startswith(prose))
        self.assertIn("&#96;&lt;tag&gt;", first)
        self.assertIn("Role Lanes: fixture-root-author", first)
        self.assertEqual(first.count("agent-flow:worktree:begin"), 1)

    def test_unsafe_declared_paths_rejected(self):
        outside = self.root.parent / "outside-journal-result"
        link = self.root / "escape"
        link.symlink_to(self.root.parent, target_is_directory=True)
        for value in ("../outside", "escape/outside-journal-result"):
            with self.subTest(path=value), self.assertRaises(evidence.EvidenceError):
                evidence.changed_paths(self.run, {}, {"run_changed_files": [value]})

    def test_source_time_negative_types_and_precision(self):
        for field in ("completed_at", "timestamp"):
            for value in (None, False, [], 1.25, 1789031492000, "2026-09-10T09:00:00", "bad"):
                event = {"payload": {"completed_at": 1789031492}, "timestamp": "2026-09-10T09:11:33.015Z"}
                (event["payload"] if field == "completed_at" else event)[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(evidence.EvidenceError):
                    journal_io.source_time(event, "completed_at")
        with self.assertRaises(evidence.EvidenceError):
            journal_io.source_time({"payload": {}}, "completed_at")
        stamp = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:00Z"}, "completed_at")
        self.assertEqual(stamp.event_origin, "outer")
        self.assertIsNone(stamp.event_before)

    def test_source_time_contract_origins(self):
        published = "2026-09-10T09:11:33.015Z"
        event = {"payload": {"completed_at": 1789031492}, "timestamp": published}
        inner = journal_io.source_time(event, "completed_at")
        self.assertEqual(inner.event_origin, "payload")
        self.assertEqual(inner.event_at.isoformat(), "2026-09-10T09:11:32+00:00")
        self.assertEqual((inner.event_before - inner.event_at).total_seconds(), 1)
        self.assertEqual(inner.observed_at, evidence.timestamp(published))
        outer = journal_io.source_time({"payload": {}, "timestamp": published}, "completed_at")
        self.assertEqual(outer.event_origin, "outer")
        self.assertEqual(outer.event_at, inner.observed_at)
        self.assertIsNone(outer.event_before)
        self.assertEqual(outer.observed_at, outer.event_at)

    def test_iso_without_fraction_keeps_point_and_publication_order(self):
        first = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:00Z"}, "completed_at")
        self.assertIsNone(first.event_before)
        self.assertEqual(first.observed_at, first.event_at)
        later = journal_io.source_time({"payload": {}, "timestamp": "2026-09-10T09:00:01Z"}, "completed_at")
        first_record = {"completed_at": first.event_at, "completed_before": first.event_before, "published_at": first.observed_at}
        later_record = {"completed_at": later.event_at, "completed_before": later.event_before, "published_at": later.observed_at}
        self.assertTrue(evidence.completion_follows(later_record, first_record))
        self.assertFalse(evidence.completion_follows(first_record, later_record))
        self.assertFalse(evidence.completion_follows(first_record, first_record))

    def test_observed_reviewer_next_second_and_equal_order(self):
        events = self.source.sessions[base.REVIEWER_ID]
        events[1]["timestamp"] = "2026-09-10T09:00:00Z"
        events[-1]["payload"]["completed_at"] = int(datetime.fromisoformat("2026-09-10T09:33:50+00:00").timestamp())
        events[-1]["timestamp"] = "2026-09-10T09:33:51.022Z"
        before = json.dumps(events)
        result = evidence.completed_turn(self.source, base.REVIEWER_ID, base.REVIEWER_ID + "-turn", base.ROOT_ID, "reviewer")
        self.assertEqual(result["completed_at"].isoformat(), "2026-09-10T09:33:50+00:00")
        self.assertFalse(evidence.completion_follows(result, result))
        self.assertEqual(before, json.dumps(events))

    def test_four_writers_use_one_injected_utc_clock(self):
        init = load("journal_init", "init-run.py")
        append = load("journal_append", "append-timeline.py")
        handoff = load("journal_handoff", "record-handoff-state.py")
        stamp = "2026-09-13T12:34:56.123456Z"
        for zone in ("UTC", "Pacific/Honolulu"):
            with self.subTest(zone=zone), patch.dict(os.environ, {"TZ": zone}):
                with patch.object(journal_io, "datetime") as clock:
                    clock.now.return_value = datetime(2026, 9, 13, 12, 34, 56, 123456, tzinfo=timezone.utc)
                    self.assertEqual(journal_io.now_iso(), stamp)
                for module in (init, append, recorder, handoff):
                    self.assertIs(module.now_iso, journal_io.now_iso)
                slug = zone.replace("/", "-").lower()
                with patch.object(init, "now_iso", return_value=stamp), patch.object(init, "datetime") as local_clock, redirect_stdout(StringIO()) as output:
                    local_clock.now.return_value.astimezone.return_value.strftime.return_value = "2026-09-12"
                    init.main(["--repo", str(self.root), "--slug", slug, "--mode", "compact"])
                run = Path(output.getvalue().strip())
                self.assertEqual(run.name, "2026-09-12-" + slug)
                with patch.object(journal_io, "now_iso", return_value=stamp), patch.object(sys, "argv", ["append-timeline.py", "--run-dir", str(run), "--stage", "checks", "--role", "orchestrator", "--status", "pass", "--summary", "Checked"]), redirect_stdout(StringIO()):
                    append.main()
                base.write_json(run / "lane-map.json", {"lanes": [{"id": "worker", "handoff": "handoffs/worker.md"}]})
                journal_io.transact(run, "fixture-lanes", {}, lambda snapshot: ({"lane-map.json": (run / "lane-map.json").read_bytes()}, {}))
                with patch.object(journal_io, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    handoff.main(["--run-dir", str(run), "--lane-id", "worker", "--status", "planned", "--summary", "Queued work"])
                with patch.object(journal_io, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    recorder.main(["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", "Checked"])
                snapshot = journal_io.JournalSnapshot.open(run)
                events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
                state = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]["handoff_state"]
                self.assertEqual([e["timestamp"] for e in events], [stamp] * 3)
                self.assertEqual(state["queued_at"], stamp)
        self.open_timeline()
        with patch.object(journal_io, "now_iso", return_value=stamp):
            self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", "Checked", "--artifact", "checks.md")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        event = json.loads(snapshot.read_text("timeline.jsonl").splitlines()[-1])
        trace = json.loads(snapshot.read_text("agents/orchestrator/trace.jsonl").splitlines()[-1])
        index = json.loads(snapshot.read_text("artifacts.json"))
        self.assertEqual(event, trace)
        self.assertEqual(event["timestamp"], index[0]["timestamp"])
        self.assertEqual(event["timestamp"], stamp)
    def test_adjacent_second_preserves_source_precision(self):
        events = self.source.sessions[base.QA_ID]
        events[-1]["payload"]["completed_at"] = 1789031492
        events[1]["timestamp"] = "2026-09-10T09:00:00Z"
        events[-1]["timestamp"] = "2026-09-10T09:11:33.015Z"
        before = json.dumps(events)
        result = evidence.completed_turn(self.source, base.QA_ID, base.QA_ID + "-turn", base.ROOT_ID, "qa-verifier")
        self.assertEqual(result["completed_at"].isoformat(), "2026-09-10T09:11:32+00:00")
        self.assertEqual(json.dumps(events), before)

    def test_paragraph_is_not_path(self):
        (self.run / "final.md").write_text("Run-owned changed files:\n- result.txt\n\nИсправлено оформление журнала.\n")
        self.assertEqual(evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]}), {"result.txt"})

    def test_prose_label_cannot_change_paths(self):
        final = self.run / "final.md"
        final.write_text("Run-owned changed files:\n- other.txt\n")
        first = evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]})
        final.write_text("Изменённые файлы:\n- other.txt\n")
        self.assertEqual(first, evidence.changed_paths(self.run, {"run_changed_files": ["result.txt"]}))

    def test_repeat_qa_preserves_acceptance_and_bytes(self):
        base.initialize_open_fixture(self.run)
        before = self.snapshot()
        with redirect_stdout(StringIO()):
            recorder.main(["--run-dir", str(self.run), "--role", "qa-verifier", "--lane-id", "evidence-qa",
                           "--stage", "handoff", "--status", "pass", "--summary", "Repeat",
                           "--completion-turn-id", base.QA_ID + "-turn", "--artifact", "handoffs/evidence-qa.md",
                           "--artifact", "checks.md"], session_source=self.source)
        after = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))
        self.assertEqual(after["verification"]["reviewer"], "evidence-reviewer")
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
