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

    def test_public_noncontract_acceptance_retains_surface_proofs(self):
        import tempfile
        journal = load("noncontract_acceptance_public", "journal.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            journal_io.initialize_journal(run, {"run.md": b"# Ordinary service function\n"}, source_root=root)
            proof = root / "proof.output"
            proof.write_text("VALUES_OK\nEMPTY_REJECTED\n")
            with redirect_stdout(StringIO()):
                self.assertEqual(journal.main(["--run-dir", str(run), "publish", "--file", "checks/service.output", str(proof)]), 0)
            request_path = root / "request.json"
            requests = []
            for polarity, marker in (("positive", "VALUES_OK"), ("negative", "EMPTY_REJECTED")):
                request = {"id": "service-" + polarity, "summary": "Ordinary service function " + polarity,
                    "status": "pass", "source": "run.md", "subjects": ["service.go"], "contract_types": [],
                    "surface_expectations": [{"surface": "service", "polarity": polarity, "proof_kinds": ["unit-test"]}],
                    "evidence": [{"path": "checks/service.output", "markers": [marker],
                                  "surface": "service", "polarity": polarity, "proof_kind": "unit-test"}]}
                requests.append(request)
                request_path.write_text(json.dumps(request))
                with redirect_stdout(StringIO()):
                    self.assertEqual(journal.main(["--run-dir", str(run), "record", "--kind", "acceptance",
                        "--request-file", str(request_path), "--operation-id", request["id"]]), 0)
            snapshot = journal_io.JournalSnapshot.open(run)
            self.assertEqual(base.validator.validate_acceptance_traceability_records(run,
                required_acceptance_ids=[request["id"] for request in requests], required=True,
                final_verdict="ship", snapshot=snapshot), [])
            for index, value in enumerate(("api", ["unknown-contract"], None, [""], [False], {})):
                request_path.write_text(json.dumps({**requests[0], "contract_types": value}))
                with self.subTest(value=value), redirect_stderr(StringIO()), self.assertRaises((ValueError, journal_io.JournalError, SystemExit)):
                    journal.main(["--run-dir", str(run), "record", "--kind", "acceptance",
                        "--request-file", str(request_path), "--operation-id", "invalid-contract-" + str(index)])
                current = journal_io.JournalSnapshot.open(run)
                self.assertEqual((current.revision, current.documents, current.receipts),
                                 (snapshot.revision, snapshot.documents, snapshot.receipts))
            from dataclasses import replace
            for missing in (b"VALUES_OK\n", b"EMPTY_REJECTED\n"):
                altered = replace(snapshot, documents={**snapshot.documents, "checks/service.output": missing})
                self.assertTrue(base.validator.validate_acceptance_traceability_records(run,
                    required_acceptance_ids=[request["id"] for request in requests], required=True,
                    final_verdict="ship", snapshot=altered))

    def test_architecture_outcome_public_contract(self):
        journal = load("architecture_public_journal", "journal.py")
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(journal.main(["contract"]), 0)
        contract = json.loads(output.getvalue())
        self.assertIn("architecture-outcome", contract["fields"])
        example = contract["examples"]["architecture-outcome"]
        self.assertEqual(len(example["sections"]), 20)
        self.assertEqual(contract["semantic_choices"]["architecture-outcome"]["decision"],
                         ["approved", "needs-revision", "rejected"])

    def test_covers_render_receipt_readback_and_domain_obligation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            journal_io.initialize_journal(run, {"checks.md": b"# Checks\n"}, source_root=root)
            self.register_synthetic_root(run)
            journal_io.write_entry(run, "lane", {"id": "worker", "summary": "Planned worker", "status": "planned",
                "lane_type": "implementation", "role": "python-worker", "execution_mode": "role-lane",
                "wave": 1, "critical": False}, identifier="covers-lane")
            request = {"lane_id": "worker", "summary": "Reviewed the declared scope", "status": "pass",
                       "sections": [{"section": "Architecture Compliance", "summary": "Compliant with the contract",
                                     "covers": ["backend-service", "monolith"]}]}
            result = journal_io.write_entry(run, "handoff", request, identifier="covers-handoff")
            self.assertEqual(result["covers"], {"Architecture Compliance": ["backend-service", "monolith"]})
            snapshot = journal_io.JournalSnapshot.open(run)
            text = snapshot.read_text("handoffs/worker.md")
            self.assertIn("- Covers: `backend-service`", text)
            self.assertIn("- Covers: `monolith`", text)
            self.assertEqual(journal_io.handoff_covers(snapshot, "worker"),
                             {"Architecture Compliance": ["backend-service", "monolith"]})
            before = (snapshot.revision, dict(snapshot.documents))
            for bad_sections in (
                [{"section": "Contract Drift", "summary": "Missing covers declaration"}],
                [{"section": "Architecture Invariants", "summary": "Declared", "covers": ["Not-Kebab"]}],
                [{"section": "Architecture Invariants", "summary": "Declared", "covers": ["worker", "worker"]}],
            ):
                with self.subTest(sections=bad_sections):
                    with self.assertRaises((ValueError, journal_io.JournalError)):
                        journal_io.write_entry(run, "handoff", {**request, "sections": bad_sections}, identifier="bad-covers")
            after = journal_io.JournalSnapshot.open(run)
            self.assertEqual(before, (after.revision, dict(after.documents)))
            # Legacy handoff without receipts or marker lines stays readable as "no covers data".
            legacy = root / "legacy"
            journal_io.initialize_journal(legacy, {
                "lane-map.json": json.dumps({"lanes": [{"id": "worker", "handoff": "handoffs/worker.md"}]}).encode(),
                "handoffs/worker.md": b"# worker handoff\n\n## Contract Drift\n\nLegacy prose.\n",
            }, source_root=root)
            self.assertIsNone(journal_io.handoff_covers(journal_io.JournalSnapshot.open(legacy), "worker"))
            # Strict writer marker lines remain readable for flat/legacy documents.
            journal_io.transact(legacy, "marker-handoff", {}, lambda current: (
                {"handoffs/worker.md": b"# worker handoff\n\n## Contract Drift\n\n- Covers: `worker-a`\n\nLegacy prose.\n"}, {}))
            self.assertEqual(journal_io.handoff_covers(journal_io.JournalSnapshot.open(legacy), "worker"),
                             {"Contract Drift": ["worker-a"]})

    def test_covers_and_receipt_evidence_are_bound_to_current_bytes(self):
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            journal_io.initialize_journal(run, {"checks.md": b"# Checks\n",
                                                "checks/ev-a.md": b"proof a\n", "checks/ev-b.md": b"proof b\n"},
                                          source_root=root)
            self.register_synthetic_root(run)
            for lane_id in ("worker-a", "worker-b"):
                journal_io.write_entry(run, "lane", {"id": lane_id, "summary": "Planned worker", "status": "planned",
                    "lane_type": "implementation", "role": "python-worker", "execution_mode": "role-lane",
                    "wave": 1, "critical": False}, identifier="lane-" + lane_id)
            # Receipt evidence follows only the exact handoff path; a foreign handoff receipt never matches.
            journal_io.write_entry(run, "handoff", {"lane_id": "worker-a", "summary": "A done", "status": "pass",
                "evidence": [{"path": "checks/ev-a.md"}]}, identifier="handoff-a")
            journal_io.write_entry(run, "handoff", {"lane_id": "worker-b", "summary": "B done", "status": "pass",
                "evidence": [{"path": "checks/ev-b.md"}]}, identifier="handoff-b")
            snapshot = journal_io.JournalSnapshot.open(run)
            sha_a = journal_io.digest(snapshot.read_bytes("checks/ev-a.md"))
            self.assertEqual(journal_io.handoff_receipt_evidence(snapshot, "handoffs/worker-a.md"),
                             {("checks/ev-a.md", sha_a)})
            # A rewritten lane report supersedes stale covers receipts.
            report_request = {"lane_id": "worker-a", "summary": "Worker report", "status": "pass",
                              "sections": [{"section": "Contract Drift", "summary": "drift", "covers": ["api-service"]}]}
            journal_io.write_entry(run, "lane-report", report_request, identifier="report-1")
            snapshot = journal_io.JournalSnapshot.open(run)
            self.assertEqual(journal_io.handoff_covers(snapshot, "worker-a").get("Contract Drift"), ["api-service"])
            journal_io.write_entry(run, "lane-report",
                                   {"lane_id": "worker-a", "summary": "Worker report rewritten", "status": "pass",
                                    "sections": [{"section": "Architecture Invariants", "summary": "ok",
                                                  "covers": ["monolith"]}]}, identifier="report-2")
            snapshot = journal_io.JournalSnapshot.open(run)
            covers = journal_io.handoff_covers(snapshot, "worker-a")
            self.assertNotIn("Contract Drift", covers)
            self.assertEqual(covers.get("Architecture Invariants"), ["monolith"])
            # Legacy receipts without a hash are ignored; document markers remain the fallback.
            connection = sqlite3.connect(run / ".journal/state.sqlite3")
            try:
                rows = connection.execute("SELECT operation_id, result FROM operations").fetchall()
                for operation_id, raw in rows:
                    result = json.loads(raw)
                    if result.pop("handoff_sha256", None) is not None:
                        connection.execute("UPDATE operations SET result=? WHERE operation_id=?",
                                           (json.dumps(result), operation_id))
                connection.commit()
            finally:
                connection.close()
            snapshot = journal_io.JournalSnapshot.open(run)
            self.assertEqual(journal_io.handoff_covers(snapshot, "worker-a"), covers)
            # Legacy receipts without a hash still match strictly by handoff path.
            self.assertEqual(journal_io.handoff_receipt_evidence(snapshot, "handoffs/worker-a.md"),
                             {("checks/ev-a.md", sha_a)})

    def test_writer_generated_final_sections_cover_audit_required_ids(self):
        import tempfile
        from journal_structure import render_document
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run = root / "run"
            lane_map = {"schema_version": 2, "architecture_contract_required": True,
                "engineering_simplicity_scope": {"primary_surfaces": ["service-cli", "storage-layer"],
                    "secondary_surfaces": ["docs-site"], "evidence": ["checks.md"],
                    "notes": "Primary surfaces verified by actual command evidence"},
                "lanes": [
                    {"id": "worker-a", "type": "implementation", "role": "python-worker", "execution_mode": "role-lane",
                     "status": "pass", "wave": 1, "handoff": "handoffs/worker-a.md",
                     "boundary": {"allowed_paths": ["src/module.py"], "forbidden_paths": [],
                                  "changed_paths_artifact": "checks/lane-boundary-worker-a.json"},
                     "architecture_compliance": {"engineering_simplicity": {"status": "pass", "checks": ["no-simpler-interface"]}},
                     "evidence": ["checks/lane-boundary-worker-a.json"]},
                    {"id": "worker-b", "type": "integration", "role": "python-worker", "execution_mode": "role-lane",
                     "status": "pass", "wave": 2, "handoff": "handoffs/worker-b.md",
                     "boundary": {"allowed_paths": ["src/bridge.py"], "forbidden_paths": [],
                                  "changed_paths_artifact": "checks/lane-boundary-worker-b.json"},
                     "evidence": ["checks/lane-boundary-worker-b.json"]},
                ]}
            continuation = {"version": 1, "status": "resumed-ready",
                "resolved_blockers": [{"id": "blocked-import", "resolution": "Retried with the corrected check", "evidence": ["checks.md"]}],
                "readiness_lane": None, "qa_recheck_lane": None, "reviewer_recheck_lane": None,
                "historical_worker_lanes": ["worker-a"], "new_worker_lanes": ["worker-b"], "revalidated_lanes": ["worker-a"],
                "notes": "Continuation recorded after the blocked checkpoint"}
            journal_io.initialize_journal(run, {
                "checks.md": b"# Checks\n\nActual command evidence.\n",
                "lane-map.json": json.dumps(lane_map).encode(),
                "continuation-summary.json": json.dumps(continuation).encode(),
                "handoffs/worker-a.md": b"Worker A own handoff\n",
                "handoffs/worker-b.md": b"Worker B own handoff\n",
            }, source_root=root)
            snapshot = journal_io.JournalSnapshot.open(run)
            # The exact render pipeline journal_lifecycle.finalize applies to caller text.
            summary = {"subagents_used": False, "role_lanes_used": False, "subagents": [], "role_lanes": [], "verification": {}}
            text = render_document("final", "Architecture-gated result accepted by independent QA and reviewer", "pass")
            text = journal_io.render_final(text, summary, ["src/module.py", "src/bridge.py"], declared=True)
            text += journal_io.render_service_sections(snapshot)
            journal_io.transact(run, "writer-final", {}, lambda current: ({"final.md": text}, {}))
            snapshot = journal_io.JournalSnapshot.open(run)
            for heading, ids in (("Engineering Simplicity", ["service-cli", "storage-layer"]),
                                 ("Boundary Evidence", ["worker-a", "worker-b"]),
                                 ("Continuation Summary", ["blocked-import", "worker-a", "worker-b"])):
                self.assertTrue(base.validator.has_markdown_heading(text, heading), heading)
                section = base.validator.markdown_section_text(text, heading)
                for required in ids:
                    self.assertTrue(base.validator.contains_facet_id(section, required), (heading, required))
            # The full audit no longer reports final.md-caused errors for a
            # writer-generated report; these were the latent-bug failures.
            lane_errors = base.validator.validate_lane_map(run, snapshot=snapshot)
            self.assertFalse([error for error in lane_errors if "final.md" in error], lane_errors)
            continuation_errors = base.validator.validate_continuation_summary(run,
                final_verdict="ship", lane_by_id={lane["id"]: lane for lane in lane_map["lanes"]},
                normalized_status_by_id={lane["id"]: lane["status"] for lane in lane_map["lanes"]},
                successful_worker_lanes=[(lane["id"], lane["wave"], lane["handoff"]) for lane in lane_map["lanes"]],
                verification_readiness_data=None, snapshot=snapshot)
            self.assertFalse([error for error in continuation_errors if "final.md" in error], continuation_errors)
            # Without the corresponding facts no such sections are rendered.
            empty_run = root / "empty"
            journal_io.initialize_journal(empty_run, {"checks.md": b"# Checks\n"}, source_root=root)
            empty_text = journal_io.render_service_sections(journal_io.JournalSnapshot.open(empty_run))
            self.assertNotIn("Engineering Simplicity", empty_text)
            self.assertNotIn("Boundary Evidence", empty_text)
            self.assertNotIn("Continuation Summary", empty_text)

    def tree(self, root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}

    def register_synthetic_root(self, run):
        source = base.SyntheticSource()
        source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        self.enterContext(patch.object(evidence, "CodexSessionSource", return_value=source))
        with redirect_stdout(StringIO()):
            recorder.main(["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane",
                           "--stage", "verification", "--status", "active", "--summary", "Synthetic root metadata fixture",
                           "--verification-json", json.dumps({**evidence.empty_verification(), "root_thread_id": base.ROOT_ID})], session_source=source)

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
            "lane-gate", "simplicity-scope", "route", "decision", "dod", "checks", "lane-report",
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
                disposable = root.parent / (root.name + "-approved-command")
                disposable.mkdir()
                self.addCleanup(__import__("shutil").rmtree, disposable)
                command = [sys.executable, "-B", "-c", "from pathlib import Path; Path('executed').write_text('actual execution')"]
                scoped = {**request, "id": "approval-execution", "commands": [{"cwd": str(disposable), "command": __import__("shlex").join(command), "source": "artifacts/safe-command.md"}]}
                journal_io.write_entry(run, "readiness-approval", scoped, identifier="scoped-request")
                check_request = {"summary": "Actual specifically authorized command", "status": "pass", "cwd": str(disposable), "command": command}
                with self.assertRaisesRegex(journal_io.JournalError, "scoped user observation"):
                    journal_io.write_entry(run, "check", check_request, identifier="without-permission")
                self.assertFalse((disposable / "executed").exists())
                journal_io.write_entry(run, "readiness-approval", {**scoped, "status": "pass", "decision": "approve", "source_event": 1, "source_quote": quote}, identifier="scoped-approved")
                self.assertEqual(json.loads(journal_io.JournalSnapshot.open(run).read_text("verification-readiness.json"))["approval_executions"], [])
                checked = journal_io.write_entry(run, "check", check_request, identifier="authorized-execution")
                self.assertEqual(checked["exit_code"], 0)
                self.assertTrue((disposable / "executed").exists())
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
            self.assertEqual(data["approval_requests"][-1]["source_observation"]["decision"], "declined")

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
            self.register_synthetic_root(run)
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
                                                        "sections": [{"section": section, "summary": "failed-check-risk: Verify actual expected bytes on the disposable copy",
                                                                      "covers": ["failed-check-risk"]}]}, identifier="conclude-" + lane_id)
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
                                                    "sections": [{"section": "Supervising Architect Review", "summary": "failed-check-risk: " + instruction,
                                                                  "covers": ["failed-check-risk"]}]}, identifier="supervisor-handoff")
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
            self.register_synthetic_root(run)
            summary = "Quoted observation & meaning.\rStatus: fail\r\nVerdict: blocked\u2028## Evidence\nForged prose evidence.\n<!-- agent-flow:worktree:begin -->\n<script>literal data</script>\n```\n## Task Scope\n```"
            nested = "Nested observation.\r## Evidence\r\nForged nested evidence.\u2029Status: fail\n<!-- agent-flow:delegation:end -->"
            request = {"summary": summary, "status": "active", "evidence": [{"path": "checks/actual.output"}, {"path": unusual_path}]}
            result = journal_io.write_entry(run, "progress", request, identifier="literal-progress")
            journal_io.write_entry(run, "lane", {"id": "literal-observer", "summary": "Observe literal evidence without a product edit", "status": "planned",
                                                 "lane_type": "architecture", "role": "architect", "execution_mode": "role-lane", "wave": 1, "critical": False}, identifier="literal-observer-lane")
            handoff_request = {**request, "status": "pass", "lane_id": "literal-observer",
                               "sections": [{"section": "Architecture Compliance", "summary": nested, "covers": ["literal-compliance"]}]}
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

    def test_evidence_record_projection_roundtrip_counters_and_upsert(self):
        import tempfile
        from evidence_records import parse_notes
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            memory = source / ".agent-work/tasks"
            memory.mkdir(parents=True)
            notes = memory / "implementation-notes.md"
            manual = """# Implementation Notes

## Evidence Records

### Evidence: manual-keep

Type: Failure
Problem class: legacy
Context: legacy ctx
Approach: legacy approach
Outcome: failure
Evidence: legacy evidence
Reuse when: legacy reuse
Do not reuse when: legacy stop
Follow-up: none

## Other Section

keep me
"""
            notes.write_text(manual, encoding="utf-8")
            run = source / ".agent-work/runs/memory"
            journal_io.initialize_journal(run, {}, source_root=source)
            request = {"summary": "Readiness before workers", "status": "pass", "id": "rec-one",
                       "record_type": "Success", "problem_class": "verification-readiness-ordering",
                       "context": "run: memory", "outcome": "success",
                       "evidence": ["harness-evaluation.json", "checks/smoke.md"],
                       "reuse_when": "same gates", "do_not_reuse_when": "different gates",
                       "follow_up": "none", "section": "harness", "keywords": ["readiness", "ordering"],
                       "provenance": "run=memory", "verification": "python3 scripts/validate-run.py"}
            receipt = journal_io.write_entry(run, "evidence-record", request, identifier="record-one")
            self.assertEqual(receipt["record_id"], "rec-one")
            snapshot = journal_io.JournalSnapshot.open(run)
            store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual([row["id"] for row in store["records"]], ["rec-one"])
            self.assertEqual(store["records"][0]["outcomes"], [])
            text = notes.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# Implementation Notes"))
            self.assertIn("<!-- agent-flow:" + snapshot.run_uuid + ":begin -->", text)
            self.assertIn("### Evidence: manual-keep", text)
            self.assertTrue(text.endswith("keep me\n"))
            self.assertLess(text.index("### Evidence: rec-one"), text.index("### Evidence: manual-keep"))
            parsed = parse_notes(text)
            self.assertEqual([record.record_id for record in parsed.records], ["rec-one", "manual-keep"])
            record = parsed.records[0]
            self.assertEqual(record.fields["Helpful"], "1")
            self.assertEqual(record.fields["Harmful"], "0")
            self.assertEqual(record.fields["Neutral"], "0")
            self.assertEqual(record.fields["Active"], "true")
            self.assertEqual(record.fields["Verification"], "python3 scripts/validate-run.py")
            row = store["records"][0]
            self.assertEqual(record.fields, journal_io.evidence_record_fields(row))
            outcome = {"summary": "Reused successfully", "status": "pass", "id": "rec-one",
                       "counter": "helpful", "evidence": ["checks/reuse.md"]}
            journal_io.write_entry(run, "evidence-record-outcome", outcome, identifier="outcome-one")
            journal_io.write_entry(run, "evidence-record-outcome", outcome, identifier="outcome-one")
            journal_io.write_entry(run, "evidence-record-outcome",
                                   {"summary": "Stopped reuse", "status": "fail", "id": "rec-one",
                                    "counter": "harmful", "evidence": ["checks/harm.md"], "active": False},
                                   identifier="outcome-two")
            snapshot = journal_io.JournalSnapshot.open(run)
            store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual(len(store["records"][0]["outcomes"]), 2)
            record = parse_notes(notes.read_text(encoding="utf-8")).records[0]
            self.assertEqual((record.fields["Helpful"], record.fields["Harmful"], record.fields["Neutral"]),
                             ("2", "1", "0"))
            self.assertEqual(record.fields["Active"], "false")
            updated = dict(request, context="run: memory updated")
            journal_io.write_entry(run, "evidence-record", updated, identifier="record-one-update")
            snapshot = journal_io.JournalSnapshot.open(run)
            store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual(len(store["records"]), 1)
            self.assertEqual(store["records"][0]["context"], "run: memory updated")
            self.assertEqual(len(store["records"][0]["outcomes"]), 2)
            self.assertEqual(parse_notes(notes.read_text(encoding="utf-8")).records[0].fields["Context"],
                             "run: memory updated")

    def test_evidence_record_validation_rejections(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            memory = source / ".agent-work/tasks"
            memory.mkdir(parents=True)
            notes = memory / "implementation-notes.md"
            notes.write_text("# Implementation Notes\n\n## Evidence Records\n\n### Evidence: manual-dup\n\n"
                             "Type: Success\nProblem class: p\nContext: c\nApproach: a\nOutcome: success\n"
                             "Evidence: e\nReuse when: r\nDo not reuse when: s\nFollow-up: f\n", encoding="utf-8")
            run = source / ".agent-work/runs/memory"
            journal_io.initialize_journal(run, {}, source_root=source)
            valid = {"summary": "Approach text", "status": "pass", "id": "rec-two",
                     "record_type": "Success", "problem_class": "p", "context": "c", "outcome": "success",
                     "evidence": ["e"], "reuse_when": "r", "do_not_reuse_when": "s", "follow_up": "f"}
            before = journal_io.JournalSnapshot.open(run).documents.copy()
            rejects = [
                (dict(valid, id="Bad_ID"), "id must be kebab-case"),
                (dict(valid, record_type="Bogus"), "record_type has an unknown semantic choice"),
                (dict(valid, outcome="bogused"), "outcome has an unknown semantic choice"),
                (dict(valid, section="other"), "section has an unknown semantic choice"),
                (dict(valid, keywords=["Bad Token"]), "keywords must be comma-separated lowercase/kebab tokens"),
                (dict(valid, context="first\nEvil: injected"), "continuation lines must be non-blank text without field syntax"),
                (dict(valid, id="manual-dup"), "duplicate record id"),
            ]
            for index, (request, needle) in enumerate(rejects):
                with self.subTest(rejection=index):
                    with self.assertRaisesRegex(journal_io.JournalError, needle):
                        journal_io.write_entry(run, "evidence-record", request, identifier=f"reject-{index}")
            with self.assertRaisesRegex(journal_io.JournalError, "requires an existing record"):
                journal_io.write_entry(run, "evidence-record-outcome",
                                       {"summary": "orphan", "status": "pass", "id": "missing",
                                        "counter": "helpful", "evidence": ["e"]}, identifier="orphan-outcome")
            with self.assertRaisesRegex(journal_io.JournalError, "counter has an unknown semantic choice"):
                journal_io.write_entry(run, "evidence-record-outcome",
                                       {"summary": "orphan", "status": "pass", "id": "missing",
                                        "counter": "bogus", "evidence": ["e"]}, identifier="bogus-counter")
            self.assertNotIn("artifacts/memory/evidence-records.json",
                             journal_io.JournalSnapshot.open(run).documents)
            self.assertEqual(journal_io.JournalSnapshot.open(run).documents, before)
            self.assertEqual(notes.read_text(encoding="utf-8").count("### Evidence:"), 1)

    def test_evidence_record_closed_journal_and_import(self):
        import sqlite3
        import tempfile
        from evidence_records import parse_notes
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            memory = source / ".agent-work/tasks"
            memory.mkdir(parents=True)
            notes = memory / "implementation-notes.md"
            notes.write_text("""# Implementation Notes

## Evidence Records

### Evidence: legacy-a

Type: Success
Problem class: p1
Context: ctx a
Approach: approach a
Outcome: success
Evidence: ea
Reuse when: ra
Do not reuse when: sa
Follow-up: none
Helpful: 3
Active: false

### Evidence: legacy-b

Type: Failure
Problem class: p2
Context: ctx b
Approach: approach b
Outcome: failure
Evidence: eb
Reuse when: rb
Do not reuse when: sb
Follow-up: check later
Custom Field: keep me

## Other Section

keep me
""", encoding="utf-8")
            run = source / ".agent-work/runs/memory"
            journal_io.initialize_journal(run, {}, source_root=source)
            result = journal_io.import_evidence_records(run, today="2026-10-17")
            self.assertEqual(result["imported"], ["legacy-a", "legacy-b"])
            self.assertEqual(result["skipped"], [])
            text = notes.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("keep me\n"))
            self.assertNotIn("### Evidence: legacy-a\n\nType: Success\nProblem class: p1\nContext: ctx a",
                             text.split(":end -->")[-1])
            parsed = {record.record_id: record for record in parse_notes(text).records}
            self.assertEqual(set(parsed), {"legacy-a", "legacy-b"})
            self.assertEqual(parsed["legacy-a"].fields["Helpful"], "3")
            self.assertEqual(parsed["legacy-a"].fields["Active"], "false")
            self.assertEqual(parsed["legacy-a"].fields["Provenance"], "import:2026-10-17")
            self.assertEqual(parsed["legacy-b"].fields["Custom Field"], "keep me")
            snapshot = journal_io.JournalSnapshot.open(run)
            store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
            rows = {row["id"]: row for row in store["records"]}
            self.assertEqual(rows["legacy-a"]["active"], False)
            self.assertEqual([event["counter"] for event in rows["legacy-a"]["outcomes"]],
                             ["helpful", "helpful"])
            self.assertEqual(rows["legacy-b"]["extra"], {"Custom Field": "keep me"})
            again = journal_io.import_evidence_records(run, today="2026-10-18")
            self.assertEqual(again["imported"], [])
            self.assertEqual(again["skipped"], [])
            self.assertEqual(notes.read_text(encoding="utf-8"), text)
            event = dict(timestamp="2026-01-01T00:00:00Z", stage="final", role="orchestrator",
                         stable_agent_name="Orchestrator", stable_agent_slug="orchestrator", status="blocked",
                         summary="Synthetic blocked acceptance", artifacts=["final.md"], next_step="")
            raw = (journal_io.encode_json(event) + "\n").encode()
            with sqlite3.connect(run / ".journal/state.sqlite3") as db:
                journal_io._put_documents(db, {"timeline.jsonl": raw})
            self.assertTrue(journal_io.JournalSnapshot.open(run).closed)
            journal_io.write_entry(run, "evidence-record-outcome",
                                   {"summary": "Observed after close", "status": "pass", "id": "legacy-b",
                                    "counter": "neutral", "evidence": ["notes"]}, identifier="late-outcome")
            record = {record.record_id: record for record in parse_notes(notes.read_text(encoding="utf-8")).records}["legacy-b"]
            self.assertEqual(record.fields["Neutral"], "1")
            with self.assertRaisesRegex(journal_io.JournalError, "semantic writes require an open durable journal"):
                journal_io.write_entry(run, "lesson", {"summary": "late lesson", "status": "pass"},
                                       identifier="late-lesson")

    def test_import_evidence_records_cli(self):
        import tempfile
        journal_cli = load("journal_cli_import", "journal.py")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            memory = source / ".agent-work/tasks"
            memory.mkdir(parents=True)
            notes = memory / "implementation-notes.md"
            notes.write_text("# Implementation Notes\n\n## Evidence Records\n\n### Evidence: cli-record\n\n"
                             "Type: Attempt\nProblem class: p\nContext: c\nApproach: a\nOutcome: unknown\n"
                             "Evidence: e\nReuse when: r\nDo not reuse when: s\nFollow-up: f\n", encoding="utf-8")
            run = source / ".agent-work/runs/memory"
            journal_io.initialize_journal(run, {}, source_root=source)
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = journal_cli.main(["--run-dir", str(run), "import-evidence-records"])
            self.assertEqual(code, 0)
            answer = json.loads(stdout.getvalue())
            self.assertEqual(answer["imported"], ["cli-record"])
            snapshot = journal_io.JournalSnapshot.open(run)
            store = json.loads(snapshot.read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual([row["id"] for row in store["records"]], ["cli-record"])
            self.assertTrue(store["records"][0]["provenance"].startswith("import:"))
            stderr = StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    journal_cli.main(["--run-dir", str(run), "import-evidence-records",
                                      "--notes", str(root / "missing.md")])
            self.assertEqual(caught.exception.code, 1)
            self.assertIn("notes file not found", stderr.getvalue())

    def test_import_evidence_records_manual_zone_only_and_crash_recovery(self):
        import tempfile
        from evidence_records import parse_notes
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            notes = source / ".agent-work/tasks/implementation-notes.md"
            notes.parent.mkdir(parents=True)
            notes.write_text("# Implementation Notes\n\n## Evidence Records\n\n### Evidence: manual-one\n\n"
                             "Type: Success\nProblem class: p\nContext: c\nApproach: a\nOutcome: success\n"
                             "Evidence: e\nReuse when: r\nDo not reuse when: s\nFollow-up: f\n", encoding="utf-8")
            first = source / ".agent-work/runs/first"
            journal_io.initialize_journal(first, {}, source_root=source)
            journal_io.write_entry(first, "evidence-record",
                                   {"summary": "First run record", "status": "pass", "id": "journaled-one",
                                    "record_type": "Success", "problem_class": "p", "context": "c",
                                    "outcome": "success", "evidence": ["e"], "reuse_when": "r",
                                    "do_not_reuse_when": "s", "follow_up": "f"}, identifier="first-record")
            text = notes.read_text(encoding="utf-8")
            self.assertIn("journaled-one", text)
            # A second run imports only the manual zone; the generated block is not re-journaled.
            second = source / ".agent-work/runs/second"
            journal_io.initialize_journal(second, {}, source_root=source)
            result = journal_io.import_evidence_records(second, today="2026-10-18")
            self.assertEqual(result["imported"], ["manual-one"])
            store = json.loads(journal_io.JournalSnapshot.open(second).read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual([row["id"] for row in store["records"]], ["manual-one"])
            text = notes.read_text(encoding="utf-8")
            parsed = parse_notes(text)
            self.assertEqual({record.record_id for record in parsed.records}, {"journaled-one", "manual-one"})
            self.assertEqual(text.count("### Evidence: journaled-one"), 1)
            self.assertEqual(text.count("### Evidence: manual-one"), 1)
            # A manual record whose id is already journaled by another run is rejected atomically.
            third = source / ".agent-work/runs/third"
            journal_io.initialize_journal(third, {}, source_root=source)
            conflicted = source / ".agent-work/tasks/conflicted-notes.md"
            conflicted.write_text("# Implementation Notes\n\n## Evidence Records\n\n### Evidence: journaled-one\n\n"
                                  "Type: Success\nProblem class: p\nContext: c\nApproach: a\nOutcome: success\n"
                                  "Evidence: e\nReuse when: r\nDo not reuse when: s\nFollow-up: f\n"
                                  + text[text.index("<!-- agent-flow:"):], encoding="utf-8")
            with self.assertRaisesRegex(journal_io.JournalError, "already journaled by another run"):
                journal_io.import_evidence_records(third, notes=conflicted)
            self.assertNotIn("artifacts/memory/evidence-records.json",
                             journal_io.JournalSnapshot.open(third).documents)
            # A crash after the store write still lets a rerun strip and project.
            fourth = source / ".agent-work/runs/fourth"
            journal_io.initialize_journal(fourth, {}, source_root=source)
            crash_notes = root / "crash-notes.md"
            crash_notes.write_text("# Implementation Notes\n\n## Evidence Records\n\n### Evidence: manual-two\n\n"
                                   "Type: Success\nProblem class: p\nContext: c\nApproach: a\nOutcome: success\n"
                                   "Evidence: e\nReuse when: r\nDo not reuse when: s\nFollow-up: f\n", encoding="utf-8")
            original_project = journal_io.project_memory
            journal_io.project_memory = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash"))
            try:
                with self.assertRaises(RuntimeError):
                    journal_io.import_evidence_records(fourth, notes=crash_notes, today="2026-10-18")
            finally:
                journal_io.project_memory = original_project
            store = json.loads(journal_io.JournalSnapshot.open(fourth).read_text("artifacts/memory/evidence-records.json"))
            self.assertEqual([row["id"] for row in store["records"]], ["manual-two"])
            self.assertNotIn("manual-two", crash_notes.read_text(encoding="utf-8"))
            rerun = journal_io.import_evidence_records(fourth, notes=crash_notes, today="2026-10-19")
            self.assertEqual(rerun["imported"], [])
            projected = journal_io.memory_path(journal_io.JournalSnapshot.open(fourth), "evidence-record")
            self.assertIn("### Evidence: manual-two", projected.read_text(encoding="utf-8"))

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


class ReviewInputRegressions(unittest.TestCase):
    def context_pin(self, fixture, sources, identifier="external-context-pin"):
        args = ["--run-dir", str(fixture.run), "--prepare-review-input", "--lane-id", fixture.lane_id,
                "--review-subject", "requirements-plan", "--operation-id", identifier]
        args += [item for name in sources for item in ("--review-source", name)]
        output = StringIO()
        with patch.object(journal_io, "now_iso", return_value="2026-09-10T09:59:00+00:00"), redirect_stdout(output):
            self.assertEqual(recorder.main(args, session_source=fixture.source), 0)
        return json.loads(output.getvalue())
    def declared_external_context(self):
        import tempfile
        from task_workspace import prepare
        fixture = self.fixture()
        external = tempfile.TemporaryDirectory(prefix="declared-context-")
        self.addCleanup(external.cleanup)
        root = Path(external.name).resolve()
        for name in ("one", "two", "rules/nested"):
            (root / name).mkdir(parents=True)
        first, second, directory = root / "one/AGENTS.md", root / "two/AGENTS.md", root / "rules"
        first.write_text("Global declared instructions\n")
        second.write_text("Separate declared instructions with same basename\n")
        (directory / "policy.txt").write_text("Explicit directory policy\n")
        (directory / "nested/worker.md").write_text("Explicit nested member\n")
        product = fixture.repo / "product"
        product.mkdir()
        subprocess.run(["git", "init", "-q", str(product)], check=True)
        (product / "result.txt").write_text("Product baseline\n")
        workspace = prepare(fixture.run, [product], fixture.repo / "owned-context-workspace",
                            context_roots=[first, second, directory], identifier="context-scope")
        sources = ["run.md", str(first), str(second), str(directory / "policy.txt"), str(directory / "nested/worker.md")]
        return fixture, root, workspace, sources

    def test_external_declared_context_public_prepare_bind_review(self):
        fixture, root, workspace, sources = self.declared_external_context()
        pin = self.context_pin(fixture, sources)
        keys = {row["key"] for row in pin["review_subject"]["sources"]}
        self.assertEqual(keys, {"journal:run.md", "context:0/AGENTS.md", "context:1/AGENTS.md",
                               "context:2/rules/policy.txt", "context:2/rules/nested/worker.md"})
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        for capture in pin["captures"]:
            self.assertEqual(evidence.current_review_source(snapshot, capture["key"]), snapshot.read_bytes(capture["path"]))
        self.start(fixture, pin)
        answer, handoff = self.conclude(fixture)
        self.accept(fixture, answer, handoff)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        record = next(row for row in json.loads(snapshot.read_text("delegation-summary.json"))["subagents"]
                      if row["lane_id"] == fixture.lane_id)
        evidence.source_bound_record(fixture.run, record, json.loads(snapshot.read_text("delegation-summary.json"))["verification"],
                                     "qa-verifier", fixture.source, snapshot=snapshot, current=True)

    def test_context_pin_omission_keys_aliases_and_duplicate_reject_atomically(self):
        fixture, root, workspace, sources = self.declared_external_context()
        before = fixture.snapshot()
        malformed = ["context:00/AGENTS.md", "context:-1/AGENTS.md", "context:+1/AGENTS.md", "context:x/AGENTS.md",
                     "context:999/AGENTS.md", "context:0/wrong.md", "context:0/AGENTS.md/child", "context:2/rules/missing",
                     "context:2/rules/../escape", "context:2//rules/policy.txt", "context:2/rules//policy.txt"]
        for index, key in enumerate(malformed):
            with self.subTest(key=key), self.assertRaises(journal_io.JournalError):
                self.context_pin(fixture, [*sources, key], identifier="bad-context-key-" + str(index))
            self.assertEqual(fixture.snapshot(), before)
        for index, omitted in enumerate((sources[2], sources[3])):
            with self.subTest(omitted=omitted), self.assertRaisesRegex(journal_io.JournalError, "review omits declared inputs: context:"):
                self.context_pin(fixture, [name for name in sources if name != omitted], identifier="omitted-context-" + str(index))
            self.assertEqual(fixture.snapshot(), before)
        (root / "sibling.txt").write_text("Not declared\n")
        (root / "alias").symlink_to(root / "one/AGENTS.md")
        (root / "parent-alias").symlink_to(root / "rules", target_is_directory=True)
        (root / "rules/member-alias").symlink_to(root / "sibling.txt")
        (root / "rules/new-member.txt").write_text("Not in captured manifest\n")
        bad_paths = [root / "sibling.txt", root / "alias", root / "parent-alias/policy.txt",
                     root / "rules/member-alias", root / "rules/new-member.txt", root / "rules"]
        for index, path in enumerate(bad_paths):
            with self.subTest(path=str(path)), self.assertRaises(journal_io.JournalError):
                self.context_pin(fixture, [*sources, str(path)], identifier="bad-context-path-" + str(index))
            self.assertEqual(fixture.snapshot(), before)
        with self.assertRaisesRegex(journal_io.JournalError, "duplicate logical review sources"):
            self.context_pin(fixture, [*sources, "context:0/AGENTS.md"], identifier="duplicate-context")
        self.assertEqual(fixture.snapshot(), before)

    def test_context_current_directory_set_drift_history_and_single_scope_scan(self):
        import workspace_scope
        fixture, root, workspace, sources = self.declared_external_context()
        with patch.object(workspace_scope, "context_capture", wraps=workspace_scope.context_capture) as capture:
            pin = self.context_pin(fixture, sources)
        self.assertEqual(capture.call_count, 3)  # Once per declared root, not per member.
        row = {"lane_id": fixture.lane_id, "review_subject": pin["review_subject"], "review_input_operation_id": pin["operation_id"]}
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        with patch.object(workspace_scope, "context_capture", wraps=workspace_scope.context_capture) as capture:
            evidence.review_input_receipt(snapshot, row, current=True)
        self.assertEqual(capture.call_count, 3)
        extra = root / "rules/new-member.txt"
        extra.write_text("Directory set changed without changing old members\n")
        with self.assertRaisesRegex(journal_io.JournalError, "canonical context changed"):
            evidence.review_input_receipt(snapshot, row, current=True)
        evidence.review_input_receipt(snapshot, row, current=False)
        extra.unlink()
        original = (root / "one/AGENTS.md").read_bytes()
        (root / "one/AGENTS.md").write_text("Changed declared bytes\n")
        with self.assertRaisesRegex(journal_io.JournalError, "declared context bytes changed"):
            evidence.review_input_receipt(snapshot, row, current=True)
        evidence.review_input_receipt(snapshot, row, current=False)
        (root / "one/AGENTS.md").write_bytes(original)
        # A frozen incomplete dependency capture must fail, never fall back live.
        from dataclasses import replace
        from validation_inputs import ValidationInputs
        inputs = ValidationInputs()
        inputs.freeze()
        with self.assertRaisesRegex(journal_io.JournalError, "validation dependency changed"):
            evidence.review_input_receipt(replace(snapshot, external_inputs=inputs), row, current=True)

    def test_context_missing_kind_alias_io_and_capture_race_are_typed(self):
        fixture, root, workspace, sources = self.declared_external_context()
        pin = self.context_pin(fixture, sources)
        row = {"lane_id": fixture.lane_id, "review_subject": pin["review_subject"], "review_input_operation_id": pin["operation_id"]}
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        target = root / "one/AGENTS.md"
        original = target.read_bytes()
        for defect in ("missing", "directory", "symlink", "parent-symlink"):
            with self.subTest(defect=defect):
                target.unlink()
                if defect == "directory": target.mkdir()
                if defect == "symlink": target.symlink_to(root / "two/AGENTS.md")
                if defect == "parent-symlink":
                    target.parent.rmdir()
                    target.parent.symlink_to(root / "two", target_is_directory=True)
                with self.assertRaises(journal_io.JournalError):
                    evidence.review_input_receipt(snapshot, row, current=True)
                evidence.review_input_receipt(snapshot, row, current=False)
                if defect == "directory": target.rmdir()
                if defect == "symlink": target.unlink()
                if defect == "parent-symlink":
                    target.parent.unlink()
                    target.parent.mkdir()
                target.write_bytes(original)
        with patch.object(journal_io, "capture_file", side_effect=PermissionError("context I/O denial")), self.assertRaisesRegex(journal_io.JournalError, "declared context source unavailable"):
            evidence.review_input_receipt(snapshot, row, current=True)
        # Deterministic environment race between member capture and final
        # existing whole-scope verification; real file bytes are changed.
        capture = journal_io.capture_file
        def changed_after_read(path):
            raw = capture(path)
            if Path(path) == target:
                target.write_text("Changed after first captured read\n")
            return raw
        before = fixture.snapshot()
        with patch.object(journal_io, "capture_file", side_effect=changed_after_read), self.assertRaisesRegex(journal_io.JournalError, "canonical context changed"):
            self.context_pin(fixture, sources, identifier="context-capture-race")
        self.assertEqual(fixture.snapshot(), before)

    def test_context_real_relocation_current_keys_reorder_and_new_set(self):
        import shutil, tempfile
        from copy import deepcopy
        from task_workspace import manifest_digest
        from workspace_scope import context_capture
        fixture, root, workspace, sources = self.declared_external_context()
        pin = self.context_pin(fixture, sources)
        row = {"lane_id": fixture.lane_id, "review_subject": pin["review_subject"], "review_input_operation_id": pin["operation_id"]}
        moved = tempfile.TemporaryDirectory(prefix="relocated-declared-context-")
        self.addCleanup(moved.cleanup)
        destination = Path(moved.name).resolve() / "external"
        shutil.copytree(root, destination)
        roots = [destination / "one/AGENTS.md", destination / "two/AGENTS.md", destination / "rules"]
        # Existing workspace updates preserve physical context roots add-only.
        # This is an explicitly approved current-scope input double, not a
        # claimed public repack; actual old paths are removed below.
        moved_scope = deepcopy(workspace)
        for declaration, path in zip(moved_scope["task_scope"]["context_roots"], roots):
            declaration["canonical_path"] = str(path)
        moved_scope["task_scope"]["digest"] = manifest_digest({key: value for key, value in moved_scope["task_scope"].items() if key != "digest"})
        shutil.rmtree(root)
        self.assertFalse(root.exists())
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        with patch.object(evidence, "registered_workspace", return_value=moved_scope):
            evidence.review_input_receipt(snapshot, row, current=True)
            current_rows = [{"key": entry["key"], "sha256": evidence.sha256(evidence.current_review_source(snapshot, entry["key"]))}
                            for entry in pin["review_subject"]["sources"]]
            self.assertEqual(evidence.requirements_digest(current_rows), pin["review_subject"]["digest"])
        reordered = deepcopy(moved_scope)
        reordered["task_scope"]["context_roots"][:2] = reversed(reordered["task_scope"]["context_roots"][:2])
        reordered["task_scope"]["digest"] = manifest_digest({key: value for key, value in reordered["task_scope"].items() if key != "digest"})
        with patch.object(evidence, "registered_workspace", return_value=reordered), self.assertRaisesRegex(journal_io.JournalError, "current review input drift"):
            evidence.review_input_receipt(snapshot, row, current=True)
        (roots[2] / "new-member.txt").write_text("Newly declared member\n")
        changed_set = deepcopy(moved_scope)
        changed_set["task_scope"]["context_roots"][2] = context_capture(roots[2])
        changed_set["task_scope"]["digest"] = manifest_digest({key: value for key, value in changed_set["task_scope"].items() if key != "digest"})
        with patch.object(evidence, "registered_workspace", return_value=changed_set), self.assertRaisesRegex(journal_io.JournalError, "review omits declared inputs"):
            evidence.review_input_receipt(snapshot, row, current=True)
        evidence.review_input_receipt(snapshot, row, current=False)

    def test_context_overlap_explicit_identity_and_product_namespace(self):
        from task_workspace import prepare
        fixture, root, workspace, sources = self.declared_external_context()
        prepare(fixture.run, [fixture.repo / "product"], fixture.repo / "owned-context-workspace",
                context_roots=[root / "one/AGENTS.md", root / "two/AGENTS.md", root / "rules", root / "rules/policy.txt"],
                identifier="overlapping-context-scope")
        before = fixture.snapshot()
        with self.assertRaisesRegex(journal_io.JournalError, "ambiguous declared context source"):
            self.context_pin(fixture, sources, identifier="ambiguous-context-pin")
        self.assertEqual(fixture.snapshot(), before)
        explicit = [name if name != str(root / "rules/policy.txt") else "context:2/rules/policy.txt" for name in sources]
        explicit += ["context:3/policy.txt"]
        current = evidence.registered_workspace(journal_io.JournalSnapshot.open(fixture.run))
        product_file = Path(current["working_root"]) / "context/AGENTS.md"
        product_file.parent.mkdir()
        product_file.write_text("Scoped product file, distinct from external context\n")
        pin = self.context_pin(fixture, [*explicit, str(product_file)])
        self.assertIn("source:context/AGENTS.md", {row["key"] for row in pin["review_subject"]["sources"]})
        self.assertIn("context:0/AGENTS.md", {row["key"] for row in pin["review_subject"]["sources"]})
    """Public prepare-first subject transitions with explicitly synthetic source."""

    def test_new_native_review_defaults_product_subject_and_conclusion_inherits(self):
        for role, thread in (("qa-verifier", base.QA_ID), ("reviewer", base.REVIEWER_ID)):
            with self.subTest(role=role):
                fixture = FirstBindingRegressions()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.initialize(schema=0)
                fixture.source.sessions[thread] = base.session(thread, role, {}, 1)
                lane_id = "default-" + role
                journal_io.write_entry(fixture.run, "lane", {"id": lane_id, "summary": "Planned independent review",
                    "status": "planned", "lane_type": "qa", "role": role, "execution_mode": "subagent",
                    "wave": 1, "critical": True}, identifier="default-lane")
                args = ["--role", role, "--lane-id", lane_id, "--codex-thread-id", thread,
                        "--stage", "spawned", "--operation-id", "default-start"]
                before = fixture.snapshot()
                for assertion in ("unknown", "null", "requirements-plan"):
                    with self.subTest(assertion=assertion), self.assertRaises(SystemExit):
                        fixture.record(*args, "--review-subject", assertion)
                    self.assertEqual(fixture.snapshot(), before)
                fixture.record(*args)
                snapshot = journal_io.JournalSnapshot.open(fixture.run)
                row = next(row for row in json.loads(snapshot.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == lane_id)
                self.assertEqual(row["review_subject"], {"kind": "product-result"})
                fixture.record(*args)
                self.assertEqual(fixture.snapshot(), (snapshot.revision, snapshot.documents, snapshot.receipts))
                fixture.record("--role", role, "--lane-id", lane_id, "--codex-thread-id", thread, "--stage", "checks")
                capture = fixture.repo / "evidence.output"
                capture.write_text("Actual fixture limitation observed\n")
                journal = load("default_subject_journal", "journal.py")
                with redirect_stdout(StringIO()):
                    self.assertEqual(journal.main(["--run-dir", str(fixture.run), "publish", "--file", "checks/limitation.output", str(capture)]), 0)
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--prepare-conclusion", "--role", role,
                        "--lane-id", lane_id, "--status", "fail", "--reason", "Fixture limitation", "--unverified", "Product acceptance",
                        "--summary", "Own negative observation", "--artifact", "handoffs/" + lane_id + ".md",
                        "--artifact", "checks/limitation.output"], session_source=fixture.source), 0)
                self.assertEqual(json.loads(output.getvalue())["review_subject"], {"kind": "product-result"})

    def test_started_native_legacy_omission_is_not_retagged(self):
        fixture = FirstBindingRegressions()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.run = fixture.repo / "legacy-started"
        fixture.source.sessions[base.QA_ID] = base.session(base.QA_ID, "qa-verifier", {}, 1)
        row = {"lane_id": "legacy-qa", "role": "qa-verifier", "codex_thread_id": base.QA_ID,
               "status": "active", "trace": "agents/qa-verifier/trace.jsonl"}
        event = base.trace_event({**row, "handoff": "handoffs/legacy-qa.md"}, "spawned")
        summary = {"version": 1, "notes": "Explicit synthetic prior untagged start", "subagents_used": True,
            "role_lanes_used": False, "subagents": [row], "role_lanes": [],
            "verification": {**evidence.empty_verification(), "task_kind": "analysis", "root_thread_id": base.ROOT_ID}}
        journal_io.initialize_journal(fixture.run, {"delegation-summary.json": json.dumps(summary).encode(),
            "timeline.jsonl": (json.dumps(event) + "\n").encode(), row["trace"]: (json.dumps(event) + "\n").encode()}, source_root=fixture.repo)
        args = ["--role", "qa-verifier", "--lane-id", row["lane_id"], "--codex-thread-id", row["codex_thread_id"],
                "--stage", "checks", "--status", "active", "--summary", "Continue legacy started review", "--operation-id", "legacy-review-continuation"]
        fixture.record(*args)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        stored = next(record for record in json.loads(snapshot.read_text("delegation-summary.json"))["subagents"] if record["lane_id"] == row["lane_id"])
        self.assertNotIn("review_subject", stored)
        fixture.record(*args)
        current = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual((current.revision, current.documents, current.receipts), (snapshot.revision, snapshot.documents, snapshot.receipts))

    def test_full_sealed_handoff_precedes_required_author_boundary(self):
        from task_workspace import registered_workspace, seal
        fixture = self.fixture(schema=1)
        self.change_workspace(fixture)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        working = Path(registered_workspace(snapshot)["working_root"])
        (working / "module.py").write_text("value = 2\n")
        seal(fixture.run)
        journal_io.write_entry(fixture.run, "lane", {"id": "author", "summary": "Own scoped implementation",
            "status": "planned", "lane_type": "implementation", "role": "python-worker",
            "execution_mode": "role-lane", "wave": 2, "critical": True,
            "allowed_paths": ["module.py"]}, identifier="author-lane")
        journal = load("public_sealed_handoff", "journal.py")
        request = fixture.repo / "handoff-input.json"
        request.write_text(json.dumps({"lane_id": "author", "summary": "Changed module.py within own boundary", "status": "pass"}))
        with redirect_stdout(StringIO()), patch.object(evidence, "CodexSessionSource", return_value=fixture.source):
            self.assertEqual(journal.main(["--run-dir", str(fixture.run), "record", "--kind", "handoff",
                "--request-file", str(request), "--operation-id", "author-handoff"]), 0)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        verification = json.loads(snapshot.read_text("delegation-summary.json"))["verification"]
        self.assertIsNone(verification["result_hash"])
        self.assertIn("module.py", snapshot.read_text("handoffs/author.md"))
        with self.assertRaisesRegex(evidence.EvidenceError, "author boundary artifact missing"):
            evidence.result_hash(fixture.run, verification, snapshot=snapshot)
        before = snapshot.revision
        with redirect_stdout(StringIO()), patch.object(evidence, "CodexSessionSource", return_value=fixture.source):
            self.assertEqual(journal.main(["--run-dir", str(fixture.run), "record", "--kind", "handoff",
                "--request-file", str(request), "--operation-id", "author-handoff"]), 0)
        self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before)
        proof = fixture.repo / "edit-proof.json"
        proof.write_text(json.dumps({"author": base.ROOT_ID, "written_paths": ["module.py"]}))
        with redirect_stdout(StringIO()):
            self.assertEqual(journal.main(["--run-dir", str(fixture.run), "publish", "--file", "checks/author-edit.json", str(proof)]), 0)
        recorder = load("public_sealed_boundary_recorder", "record-agent-trace.py")
        with redirect_stdout(StringIO()):
            self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--prepare-conclusion", "--role", "python-worker",
                "--lane-id", "author", "--status", "pass",
                "--artifact", "handoffs/author.md", "--artifact", "checks/author-edit.json",
                "--authored-path", "module.py", "--operation-id", "author-boundary"], session_source=fixture.source), 0)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        digest = evidence.result_hash(fixture.run, verification, snapshot=snapshot)
        self.assertEqual(len(digest), 64)
        self.assertEqual(evidence.result_hash(fixture.run, verification, snapshot=snapshot, require_ready=False), digest)

    def test_no_map_phase_admission_and_explicit_product_stages(self):
        for mode in ("compact", "full"):
            fixture = FirstBindingRegressions()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.initialize(schema=0, mode=mode)
            fixture.source.sessions[base.QA_ID] = base.session(base.QA_ID, "python-worker", {}, 1)
            verification = {**evidence.empty_verification(), "root_thread_id": base.ROOT_ID}
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification))
            before = journal_io.JournalSnapshot.open(fixture.run)
            for stage in ("spawned", "active"):
                with self.subTest(mode=mode, stage=stage), self.assertRaisesRegex(SystemExit, "task_kind"):
                    fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", stage,
                                   "--execution-mode", "role-lane" if stage == "active" else "subagent", "--codex-thread-id", base.ROOT_ID if stage == "active" else base.QA_ID)
                self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)
            verification["task_kind"] = "analysis"
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification))
            fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "spawned", "--codex-thread-id", base.QA_ID, "--operation-id", "readonly-start")
            fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "active", "--codex-thread-id", base.QA_ID)
            before = journal_io.JournalSnapshot.open(fixture.run)
            for stage in ("implementation", "integration", "fix"):
                with self.subTest(mode=mode, stage=stage), self.assertRaisesRegex(SystemExit, "task_kind=change"):
                    fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", stage, "--codex-thread-id", base.QA_ID)
                self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)
            from types import SimpleNamespace
            def locked_admission(current):
                journal_io.worker_start_preflight(current, None, None, verification, SimpleNamespace(stage="fix", status="active", lane_id="readonly-worker", role="python-worker", execution_mode="subagent", codex_thread_id=base.QA_ID))
                return {}, {}
            with self.assertRaisesRegex(journal_io.JournalError, "task_kind=change"):
                journal_io.transact(fixture.run, "locked-phase-guard", {}, locked_admission)
            request = json.loads((fixture.run / ".journal/requests/readonly-start").read_text())["payload"]
            request.pop("artifact")
            root_action = SimpleNamespace(**request)
            root_action.stage = "fix"
            root_action.lane_id = None
            root_action.role = "orchestrator"
            root_action.execution_mode = "role-lane"
            root_action.codex_thread_id = base.ROOT_ID
            with self.assertRaisesRegex((journal_io.JournalError, SystemExit), "task_kind=change"):
                journal_io.transact(fixture.run, "locked-root-product-guard", {}, lambda current: journal_io.record_event(fixture.run, root_action, [], fixture.source, current))
            self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)
            self.assertFalse(before.exists("lane-map.json"))
            verification["task_kind"] = None
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification))
            before = journal_io.JournalSnapshot.open(fixture.run)
            fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "spawned", "--codex-thread-id", base.QA_ID, "--operation-id", "readonly-start")
            self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)
            with self.assertRaisesRegex(SystemExit, "payload conflict"):
                fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "spawned", "--codex-thread-id", base.QA_ID, "--operation-id", "readonly-start", "--summary", "Changed retry payload")
            verification.update(task_kind="change", blocker="Workspace not yet prepared", unverified=["scoped product action"])
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "blocked", "--status", "blocked", "--verification-json", json.dumps(verification))
            with self.assertRaisesRegex(SystemExit, "prepared workspace"):
                fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "implementation", "--codex-thread-id", base.QA_ID)
            self.change_workspace(fixture)
            fixture.record("--role", "python-worker", "--lane-id", "readonly-worker", "--stage", "implementation", "--codex-thread-id", base.QA_ID)
            summary = json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("delegation-summary.json"))
            self.assertIn(base.QA_ID, summary["verification"]["author_thread_ids"])
            self.assertFalse(journal_io.JournalSnapshot.open(fixture.run).exists("lane-map.json"))

    def test_single_source_and_parent_symlink_reject_before_pin(self):
        fixture = self.fixture()
        (fixture.repo / "a.md").write_bytes(b"original source\n")
        (fixture.repo / "b.md").write_bytes(b"other source\n")
        link = fixture.repo / "link.md"
        link.symlink_to("a.md")
        directory = fixture.repo / "alias-directory"
        directory.symlink_to(fixture.repo, target_is_directory=True)
        before = journal_io.JournalSnapshot.open(fixture.run)
        for name in ("link.md", str(link), str(directory / "a.md")):
            with self.subTest(name=name), self.assertRaisesRegex((evidence.EvidenceError, journal_io.JournalError), "symlink"):
                journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", name], identifier="alias-" + str(len(name)))
            self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)

    def test_public_prepare_input_freezes_without_assignment(self):
        fixture = FirstBindingRegressions()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize(schema=0)
        (fixture.repo / "requirements.md").write_text("Actual full requirements\n")
        before = json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("delegation-summary.json"))
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--prepare-review-input",
                "--lane-id", "qa-requirements", "--review-subject", "requirements-plan",
                "--review-source", str(fixture.repo / "requirements.md"), "--review-source", "run.md", "--operation-id", "pin-one"], session_source=fixture.source), 0)
        receipt = json.loads(output.getvalue())
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual(json.loads(snapshot.read_text("delegation-summary.json")), before)
        self.assertEqual(receipt["review_subject"]["kind"], "requirements-plan")
        self.assertIn({"key": "source:requirements.md", "sha256": journal_io.digest(b"Actual full requirements\n")}, receipt["review_subject"]["sources"])
        self.assertIn("frozen_at", receipt)

    def fixture(self, schema=0):
        fixture = FirstBindingRegressions()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize(schema=schema)
        fixture.lane_id = "qa-requirements" if schema == 0 else "verification-readiness-1"
        fixture.source.sessions[base.QA_ID] = base.session(base.QA_ID, "qa-verifier", {}, 1)
        if schema == 0:
            journal_io.write_entry(fixture.run, "lane", {"id": fixture.lane_id, "summary": "Preliminary QA", "status": "planned",
                "lane_type": "qa", "role": "qa-verifier", "execution_mode": "subagent", "wave": 1, "critical": True}, identifier="declare-preliminary")
        return fixture

    def pin(self, fixture, identifier="pin-one"):
        sources = ["run.md"]
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        if snapshot.exists("plan.md"):
            sources.append("plan.md")
        mapping = json.loads(snapshot.documents.get("lane-map.json", b"{}"))
        verification = json.loads(snapshot.read_text("delegation-summary.json"))["verification"]
        sources += [ref["path"] for field in ("initial_snapshot", "task_scope") if isinstance(ref := verification.get(field), dict)]
        sources += list({lane[field] for lane in mapping.get("lanes", []) if lane.get("type") == "architecture"
                        for field in ("handoff", "architecture_design_brief") if lane.get(field) and snapshot.exists(lane[field])})
        with patch.object(journal_io, "now_iso", return_value="2026-09-10T09:59:00+00:00"):
            return journal_io.prepare_review_input(fixture.run, fixture.lane_id, list(dict.fromkeys(sources)), identifier=identifier)

    def start(self, fixture, pin):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return recorder.main(["--run-dir", str(fixture.run), "--role", "qa-verifier", "--lane-id", fixture.lane_id,
                "--stage", "spawned", "--status", "active", "--summary", "Synthetic preliminary",
                "--codex-thread-id", base.QA_ID, "--review-input-operation-id", pin["operation_id"],
                *(["--obligation-id", fixture.obligation_id] if hasattr(fixture, "obligation_id") else [])], session_source=fixture.source)

    def conclude(self, fixture, status="pass", gates=None, identifier=None):
        capture = fixture.repo / "observation.txt"
        capture.write_text("Full observed requirements and scoped environment evidence\n")
        journal = load("preliminary_public_journal", "journal.py")
        with redirect_stdout(StringIO()):
            journal.main(["--run-dir", str(fixture.run), "publish", "--file", "artifacts/observation.txt", str(capture), "--operation-id", "preliminary-observation"])
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        lane = next(row for row in json.loads(snapshot.read_text("lane-map.json"))["lanes"] if row["id"] == fixture.lane_id)
        handoff = lane.get("handoff") or "handoffs/" + fixture.lane_id + ".md"
        args = ["--run-dir", str(fixture.run), "--prepare-conclusion", "--role", "qa-verifier", "--lane-id", fixture.lane_id,
                "--status", status, "--summary", "Substantive preliminary review", "--artifact", handoff, "--artifact", "artifacts/observation.txt"]
        if status in {"blocked", "fail"}:
            args += ["--reason", "Missing required observation", "--unverified", "dependent check"]
        if gates is not None:
            path = fixture.repo / "gates.json"
            path.write_text(json.dumps(gates))
            args += ["--readiness-gates-file", str(path)]
            args += [argument for gate in gates for ref in gate["evidence"] for argument in ("--artifact", ref["path"])]
        if identifier:
            args += ["--operation-id", identifier]
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(StringIO()):
            self.assertEqual(recorder.main(args, session_source=fixture.source), 0)
        return json.loads(output.getvalue()), handoff

    def accept(self, fixture, answer, handoff):
        events = base.session(base.QA_ID, "qa-verifier", answer, 1)
        turn = getattr(fixture, "review_turn", base.QA_ID + "-turn")
        for event in events:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = turn
        events[3:3] = getattr(fixture, "tool_outputs", [])
        fixture.source.sessions[base.QA_ID] = fixture.prior_events + events[1:] if hasattr(fixture, "prior_events") else events
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return recorder.main(["--run-dir", str(fixture.run), "--role", "qa-verifier", "--lane-id", fixture.lane_id,
                "--stage", answer["verdict"] if answer["verdict"] in {"blocked", "fail"} else "handoff",
                "--status", "pass" if answer["verdict"] == "passed" else answer["verdict"], "--summary", "Synthetic completed preliminary",
                "--codex-thread-id", base.QA_ID, "--completion-turn-id", turn, "--artifact", handoff,
                "--artifact", "artifacts/observation.txt", *[argument for gate in answer.get("readiness_gates", []) for ref in gate["evidence"] for argument in ("--artifact", ref["path"])]], session_source=fixture.source)

    def mcp_readiness_fixture(self):
        fixture = self.fixture(schema=2)
        self.change_workspace(fixture)
        self.start(fixture, self.pin(fixture))
        raw = json.dumps({"content": [{"type": "text", "text": "Observed scoped MCP result"}]}, indent=2)
        fixture.tool_outputs = [{"type": "response_item", "payload": {"type": "custom_tool_call_output",
            "call_id": "own-mcp", "output": [{"type": "output_text", "text": "unrelated part"},
                {"type": "output_text", "text": raw}]}}]
        fixture.source.sessions[base.QA_ID][3:3] = fixture.tool_outputs
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        pairs = __import__("journal_structure").domain_validator().selected_verification_gate_pairs(json.loads(snapshot.read_text("lane-map.json"))["architecture_context"])
        gates = [{"axis": axis, "facet": facet, "check": "Scoped tool observation", "readiness": "ready",
            "notes": "Synthetic independent QA applicability", "evidence": [{"path": "artifacts/own-mcp.output",
                "source_event": 4, "source_call_id": "own-mcp", "source_part": 1}]} for axis, facet in pairs]
        return fixture, gates, raw

    def test_public_mcp_readiness_output_preparation_acceptance_and_admission(self):
        fixture, gates, raw = self.mcp_readiness_fixture()
        answer, handoff = self.conclude(fixture, gates=gates)
        self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).read_bytes("artifacts/own-mcp.output"), raw.encode())
        self.assertEqual(answer["readiness_gates"][0]["evidence"][0]["sha256"], evidence.sha256(raw.encode()))
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        result = journal_io.automatic_readiness(journal_io.JournalSnapshot.open(fixture.run), fixture.source)
        self.assertEqual(json.loads(result["verification-readiness.json"])["status"], "ready")

    def test_mcp_readiness_finite_output_and_locator_atomic_refusals(self):
        import copy
        fixture, original, raw = self.mcp_readiness_fixture()
        # Supplemental narrative is published before the operation under test.
        observation = fixture.repo / "observation.txt"
        observation.write_text("Full observed requirements and scoped environment evidence\n")
        journal = load("mcp_negative_public_journal", "journal.py")
        with redirect_stdout(StringIO()):
            journal.main(["--run-dir", str(fixture.run), "publish", "--file", "artifacts/observation.txt", str(observation), "--operation-id", "preliminary-observation"])
        source_output = fixture.source.sessions[base.QA_ID][3]["payload"]
        cases = [({"source_event": True}, None), ({"source_event": 1}, None), ({"source_event": 99}, None),
            ({"source_call_id": "foreign"}, None), ({"source_call_id": ""}, None),
            ({"source_part": True}, None), ({"source_part": -1}, None), ({"source_part": 9}, None),
            ({"source_part": 0}, None), ({"sha256": "0" * 64}, None), ({"extra": "unknown"}, None),
            ({}, {"content": [{"type": "text", "text": "failed"}], "isError": True}),
            ({}, {"content": [{"type": "text", "text": "unknown"}], "isError": "false"}),
            ({}, {"status": "pass", "value": "narrative"}), ({}, {"content": []}),
            ({}, {"content": [{"type": "image", "data": "unsupported"}]}),
            ({}, {"content": [{"type": "text", "text": ""}]}),
            ({}, {"content": [{"type": "text", "text": "ok"}], "structuredContent": []})]
        for mutation, result in cases:
            with self.subTest(mutation=mutation, result=result):
                gates = copy.deepcopy(original)
                for gate in gates:
                    gate["evidence"][0].update(mutation)
                source_output["output"][1]["text"] = raw if result is None else json.dumps(result)
                before = journal_io.JournalSnapshot.open(fixture.run)
                with self.assertRaises(SystemExit):
                    self.conclude(fixture, gates=gates)
                after = journal_io.JournalSnapshot.open(fixture.run)
                self.assertEqual(before.documents, after.documents)
                self.assertEqual(before.receipts, after.receipts)
                self.assertEqual(before.revision, after.revision)
        source_output["output"][1]["text"] = raw
        source_output["output"].append({"type": "image", "data": "unavailable part"})
        with self.assertRaisesRegex(SystemExit, "unavailable"):
            self.conclude(fixture, gates=original)

    def test_mcp_readiness_raw_string_optional_assertions_replay_and_tamper(self):
        fixture, gates, raw = self.mcp_readiness_fixture()
        fixture.tool_outputs[0]["payload"].update(type="function_call_output", output=raw)
        for gate in gates:
            del gate["evidence"][0]["source_part"]
            del gate["evidence"][0]["source_call_id"]
        answer, handoff = self.conclude(fixture, gates=gates, identifier="own-tool-conclusion")
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        repeated, _ = self.conclude(fixture, gates=gates, identifier="own-tool-conclusion")
        self.assertEqual(repeated, answer)
        self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, snapshot.revision)
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        accepted = journal_io.JournalSnapshot.open(fixture.run)
        record = next(row for row in json.loads(accepted.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == fixture.lane_id)
        verification = json.loads(accepted.read_text("delegation-summary.json"))["verification"]
        fixture.source.sessions[base.QA_ID][3]["payload"]["output"] = raw + " "
        with self.assertRaisesRegex(journal_io.JournalError, "hash assertion mismatch"):
            evidence.source_bound_record(fixture.run, record, verification, "qa-verifier", fixture.source, snapshot=accepted)

    def test_mcp_readiness_same_duty_public_replacement_preserves_attempts(self):
        import copy
        fixture, gates, raw = self.mcp_readiness_fixture()
        answer, handoff = self.conclude(fixture, gates=gates)
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        old_lane = fixture.lane_id
        old_snapshot = journal_io.JournalSnapshot.open(fixture.run)
        old_record = next(row for row in json.loads(old_snapshot.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == old_lane)
        old_trace = old_snapshot.read_bytes(old_record["trace"])
        old_handoff = old_snapshot.read_bytes(handoff)
        # First public worker start materializes the old endorsed attempt.
        fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "python-worker", {}, 2)
        for worker in ("worker-before", "worker-after"):
            journal_io.write_entry(fixture.run, "lane", {"id": worker, "summary": "Scoped dependent worker", "status": "planned",
                "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 2,
                "critical": True, "allowed_paths": ["module.py"]}, identifier="declare-" + worker)
        def worker_start(lane):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                return recorder.main(["--run-dir", str(fixture.run), "--role", "python-worker", "--lane-id", lane,
                    "--stage", "spawned", "--status", "active", "--summary", "Actual synthetic dependent start",
                    "--codex-thread-id", base.REVIEWER_ID], session_source=fixture.source)
        self.assertEqual(worker_start("worker-before"), 0)
        fixture.prior_events = copy.deepcopy(fixture.source.sessions[base.QA_ID])
        fixture.review_turn = base.QA_ID + "-replacement-turn"
        fixture.obligation_id = old_record["obligation"]["id"]
        fixture.lane_id = "readiness-replacement"
        journal_io.write_entry(fixture.run, "lane", {"id": fixture.lane_id, "summary": "Fresh same-duty QA", "status": "planned",
            "lane_type": "qa", "role": "qa-verifier", "execution_mode": "subagent", "wave": 1, "critical": True}, identifier="declare-replacement")
        pin = self.pin(fixture, identifier="replacement-pin")
        events = base.session(base.QA_ID, "qa-verifier", {}, 3)
        for event in events:
            if "turn_id" in event["payload"]:
                event["payload"]["turn_id"] = fixture.review_turn
        fixture.tool_outputs = copy.deepcopy(fixture.tool_outputs)
        fixture.tool_outputs[0]["payload"]["call_id"] = "fresh-own-mcp"
        events[3:3] = fixture.tool_outputs
        fixture.source.sessions[base.QA_ID] = fixture.prior_events + events[1:]
        self.assertEqual(self.start(fixture, pin), 0)
        fresh = copy.deepcopy(gates)
        for gate in fresh:
            gate["evidence"][0].update(path="artifacts/fresh-mcp.output", source_event=len(fixture.prior_events) + 3, source_call_id="fresh-own-mcp")
        answer, fresh_handoff = self.conclude(fixture, gates=fresh)
        self.assertEqual(self.accept(fixture, answer, fresh_handoff), 0)
        with self.assertRaisesRegex(journal_io.JournalError, "stale"):
            journal_io.automatic_readiness(journal_io.JournalSnapshot.open(fixture.run), fixture.source)
        journal = load("mcp_replace_public_journal", "journal.py")
        with redirect_stdout(StringIO()):
            self.assertEqual(journal.main(["--run-dir", str(fixture.run), "resolve-obligation", "--lane-id", old_lane,
                "--replacement", fixture.lane_id, "--reason", "Fresh same-duty source-confirmed check",
                "--expected-revision", str(journal_io.JournalSnapshot.open(fixture.run).revision), "--operation-id", "mcp-duty-resolution"]), 0)
        self.assertEqual(worker_start("worker-after"), 0)
        current = journal_io.JournalSnapshot.open(fixture.run)
        data = json.loads(current.read_text("verification-readiness.json"))
        self.assertEqual({row["lane"] for row in data["attempts"]}, {old_lane, fixture.lane_id})
        self.assertEqual(json.loads(current.read_text("lane-map.json"))["verification_readiness"]["lanes"], [old_lane])
        # Existing resolve-obligation appends its own resolution event; original bytes remain.
        self.assertTrue(current.read_bytes(old_record["trace"]).startswith(old_trace))
        old_current = next(row for row in json.loads(current.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == old_lane)
        self.assertEqual({key: value for key, value in old_current.items() if key != "obligation"},
            {key: value for key, value in old_record.items() if key != "obligation"})
        self.assertEqual(current.read_bytes(handoff), old_handoff)
        self.assertEqual(self.readiness_schema_errors(fixture, current), [])
        verification = json.loads(current.read_text("delegation-summary.json"))["verification"]
        evidence.source_bound_record(fixture.run, old_current, verification, "qa-verifier", fixture.source, snapshot=current, current=False)
        from dataclasses import replace
        from types import MappingProxyType
        saved_summary = json.loads(current.read_text("delegation-summary.json"))
        for variant in ("missing", "cycle", "wrong-duty", "negative", "exempt"):
            with self.subTest(variant=variant):
                mutated = copy.deepcopy(saved_summary)
                old = next(row for row in mutated["subagents"] if row["lane_id"] == old_lane)
                endpoint = next(row for row in mutated["subagents"] if row["lane_id"] == fixture.lane_id)
                if variant == "missing":
                    old["obligation"]["resolution"]["replacement"] = "absent"
                elif variant == "wrong-duty":
                    endpoint["obligation"]["id"] = "unrelated-duty"
                elif variant == "negative":
                    endpoint["status"] = "fail"
                else:
                    endpoint["obligation"].update(state="resolved" if variant == "cycle" else "exempt",
                        resolution={"original_lane": fixture.lane_id, "replacement": old_lane, "reason": "Synthetic invalid endpoint",
                            "evidence": [{"path": fresh_handoff, "sha256": evidence.sha256(current.read_bytes(fresh_handoff))}]})
                    if variant == "exempt":
                        endpoint["obligation"]["required"] = False
                        endpoint["purpose"] = "consultation"
                # Explicit malformed snapshot double; committed writer state stays unchanged.
                altered = replace(current, documents=MappingProxyType({**current.documents,
                    "delegation-summary.json": json.dumps(mutated).encode()}))
                with self.assertRaises(journal_io.JournalError):
                    journal_io.automatic_readiness(altered, fixture.source)

    def test_mcp_readiness_mixed_failure_and_multi_capture_atomicity(self):
        import copy, tempfile
        fixture, gates, raw = self.mcp_readiness_fixture()
        # First valid capture must not publish when a second source locator is invalid.
        invalid = copy.deepcopy(gates)
        for gate in invalid:
            gate["evidence"].append({"path": "artifacts/second-mcp.output", "source_event": 4,
                "source_call_id": "wrong-call", "source_part": 1})
        with self.assertRaisesRegex(SystemExit, "call_id mismatch"):
            self.conclude(fixture, gates=invalid)
        self.assertFalse(journal_io.JournalSnapshot.open(fixture.run).exists("artifacts/own-mcp.output"))
        with tempfile.TemporaryDirectory() as directory:
            failed = journal_io.write_entry(fixture.run, "check", {"summary": "Actual failed native command",
                "status": "pass", "command": [sys.executable, "-B", "-c", "print('Actual failing execution'); raise SystemExit(7)"], "cwd": directory}, identifier="mcp-mixed-command-fail")
            mixed = copy.deepcopy(gates)
            for gate in mixed:
                gate["evidence"] += [{"path": ref["path"]} for ref in failed["evidence"]]
            with self.assertRaisesRegex(SystemExit, "successful actual command"):
                self.conclude(fixture, gates=mixed)
            self.assertFalse(journal_io.JournalSnapshot.open(fixture.run).exists("artifacts/own-mcp.output"))
        journal = load("mcp_occupied_public_journal", "journal.py")
        occupied = fixture.repo / "occupied.output"
        occupied.write_text("Previously published unrelated immutable bytes")
        with redirect_stdout(StringIO()):
            journal.main(["--run-dir", str(fixture.run), "publish", "--file", "artifacts/own-mcp.output", str(occupied), "--operation-id", "mcp-occupied"])
        before = journal_io.JournalSnapshot.open(fixture.run)
        with self.assertRaisesRegex(SystemExit, "already differs"):
            self.conclude(fixture, gates=gates)
        after = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual(before.documents, after.documents)
        self.assertEqual(before.receipts, after.receipts)

    def test_mcp_readiness_outputs_outside_turn_and_false_error_flag(self):
        import copy
        fixture, gates, raw = self.mcp_readiness_fixture()
        events = fixture.source.sessions[base.QA_ID]
        output = copy.deepcopy(events[3])
        events.append(output)
        for gate in gates:
            gate["evidence"][0]["source_event"] = len(events)
        with self.assertRaisesRegex(SystemExit, "reviewing turn"):
            self.conclude(fixture, gates=gates)
        events.pop()
        for gate in gates:
            gate["evidence"][0]["source_event"] = 4
        false_result = {"content": [{"type": "text", "text": "Observed MCP response"}], "isError": False,
            "structuredContent": {}, "_meta": {}}
        events[3]["payload"]["output"][1]["text"] = json.dumps(false_result, indent=1)
        answer, _ = self.conclude(fixture, gates=gates)
        self.assertEqual(answer["readiness_gates"][0]["evidence"][0]["sha256"], evidence.sha256(json.dumps(false_result, indent=1).encode()))

    def test_mcp_readiness_missing_configured_assignment_is_atomic_refusal(self):
        fixture = self.fixture(schema=2)
        self.change_workspace(fixture)
        journal_io.write_entry(fixture.run, "lane", {"id": "worker-unready", "summary": "Unready dependent worker", "status": "planned",
            "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 2,
            "critical": True, "allowed_paths": ["module.py"]}, identifier="declare-unready-worker")
        fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "python-worker", {}, 2)
        before = journal_io.JournalSnapshot.open(fixture.run)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaisesRegex(SystemExit, "readiness"):
            recorder.main(["--run-dir", str(fixture.run), "--role", "python-worker", "--lane-id", "worker-unready",
                "--stage", "spawned", "--status", "active", "--summary", "Attempt without QA assignment",
                "--codex-thread-id", base.REVIEWER_ID], session_source=fixture.source)
        after = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual(before.documents, after.documents)
        self.assertEqual(before.receipts, after.receipts)
        self.assertEqual(before.revision, after.revision)

    def test_mcp_readiness_newer_endpoint_blocker_before_and_after_materialization(self):
        import tempfile
        for materialized in (False, True):
            with self.subTest(materialized=materialized):
                class EndpointSetup(ReviewInputRegressions):
                    def fixture(self, *args, **kwargs):
                        self.saved = super().fixture(*args, **kwargs)
                        return self.saved
                helper = EndpointSetup()
                self.addCleanup(helper.doCleanups)
                class EndpointReady(Exception):
                    pass
                original_main = recorder.main
                def stop_at_endpoint(args, *pos, **kwargs):
                    if not materialized and "worker-after" in args and "spawned" in args:
                        raise EndpointReady()
                    return original_main(args, *pos, **kwargs)
                # Reuse the existing public same-duty setup; do not repair private state.
                with patch.object(recorder, "main", side_effect=stop_at_endpoint):
                    try:
                        helper.test_mcp_readiness_same_duty_public_replacement_preserves_attempts()
                    except EndpointReady:
                        pass
                fixture = helper.saved
                initial = journal_io.JournalSnapshot.open(fixture.run)
                summary = json.loads(initial.read_text("delegation-summary.json"))
                endpoint = next(row for row in summary["subagents"] if row["lane_id"] == fixture.lane_id)
                with tempfile.TemporaryDirectory() as directory:
                    failed = journal_io.write_entry(fixture.run, "check", {"summary": "Real required check failed after endpoint endorsement",
                        "status": "pass", "cwd": directory, "command": [sys.executable, "-B", "-c",
                            "print('Actual endpoint diagnostic failure'); raise SystemExit(7)"]}, identifier="late-endpoint-command")
                    self.assertEqual(failed["exit_code"], 7)
                    refs = [{"path": ref["path"]} for ref in failed["evidence"]]
                    journal_io.write_entry(fixture.run, "readiness-attempt", {"id": "late-endpoint-blocker", "lane_id": fixture.lane_id,
                        "summary": "Actual exit7 after accepted endpoint needs fresh review", "status": "blocked",
                        "blockers": ["Required check failed after current QA endorsement"], "evidence": refs}, identifier="late-endpoint-attempt")
                    pairs = __import__("journal_structure").domain_validator().selected_verification_gate_pairs(json.loads(initial.read_text("lane-map.json"))["architecture_context"])
                    for index, (axis, facet) in enumerate(pairs):
                        journal_io.write_entry(fixture.run, "readiness-gate", {"attempt_id": "late-endpoint-blocker", "axis": axis,
                            "facet": facet, "check": "Actual newer endpoint failure", "summary": "Fresh applicable readiness required",
                            "status": "blocked", "evidence": refs}, identifier="late-endpoint-gate-" + str(index))
                    journal_io.write_entry(fixture.run, "lane", {"id": "worker-late-endpoint", "summary": "Cannot revive older endpoint readiness",
                        "status": "planned", "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent",
                        "wave": 2, "critical": True, "allowed_paths": ["module.py"]}, identifier="declare-late-endpoint-worker")
                    before = journal_io.JournalSnapshot.open(fixture.run)
                    with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as refusal:
                        recorder.main(["--run-dir", str(fixture.run), "--role", "python-worker", "--lane-id", "worker-late-endpoint",
                            "--stage", "spawned", "--status", "active", "--summary", "Expect newer endpoint blocker refusal",
                            "--codex-thread-id", base.REVIEWER_ID], session_source=fixture.source)
                    if not materialized:
                        self.assertIn("newer environment/check blocker", str(refusal.exception))
                    after = journal_io.JournalSnapshot.open(fixture.run)
                    self.assertEqual(before.revision, after.revision)
                    self.assertEqual(before.documents, after.documents)
                    self.assertEqual(before.receipts, after.receipts)
                    retained = next(row for row in json.loads(after.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == fixture.lane_id)
                    self.assertEqual(retained, endpoint)
                    self.assertEqual(after.operation_receipt("late-endpoint-command")["result"]["exit_code"], 7)

    def test_mcp_readiness_historical_plain_gate_uses_preserved_bytes(self):
        fixture, gates, raw = self.mcp_readiness_fixture()
        for gate in gates:
            gate["evidence"].append({"path": "run.md"})
        answer, handoff = self.conclude(fixture, gates=gates)
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        old = journal_io.JournalSnapshot.open(fixture.run)
        record = next(row for row in json.loads(old.read_text("delegation-summary.json"))["subagents"] if row["lane_id"] == fixture.lane_id)
        journal_io.write_entry(fixture.run, "task", {"summary": "Lawful meaningful scope update requires fresh review", "status": "active"}, identifier="historical-gate-task-update")
        current = journal_io.JournalSnapshot.open(fixture.run)
        verification = json.loads(current.read_text("delegation-summary.json"))["verification"]
        ref = next(ref for ref in record["evidence"] if ref["path"] == "run.md")
        self.assertNotEqual(current.read_bytes("run.md"), old.read_bytes("run.md"))
        self.assertEqual(evidence.historical_evidence_bytes(current, record, ref), old.read_bytes("run.md"))
        evidence.source_bound_record(fixture.run, record, verification, "qa-verifier", fixture.source, snapshot=current, current=False)
        with self.assertRaises(journal_io.JournalError):
            evidence.source_bound_record(fixture.run, record, verification, "qa-verifier", fixture.source, snapshot=current, current=True)

    def readiness_schema_errors(self, fixture, snapshot):
        validator = __import__("journal_structure").domain_validator()
        mapping = json.loads(snapshot.read_text("lane-map.json"))
        by_id = {row["id"]: row for row in mapping["lanes"]}
        _, errors = validator.validate_verification_readiness(fixture.run, mapping, required=True,
            architecture_context_by_axis=mapping["architecture_context"], lane_by_id=by_id,
            normalized_status_by_id={key: validator.normalize_lane_status(row.get("status")) for key, row in by_id.items()},
            final_verdict=None, snapshot=snapshot)
        return errors

    def test_mcp_readiness_unrelated_duty_history_never_expands_selector(self):
        import copy
        fixture, gates, raw = self.mcp_readiness_fixture()
        answer, handoff = self.conclude(fixture, gates=gates)
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        original = fixture.lane_id
        foreign = "unrelated-readiness"
        thread = "44444444-4444-4444-8444-444444444444"
        journal_io.write_entry(fixture.run, "lane", {"id": foreign, "summary": "Separate unrelated QA duty", "status": "planned",
            "lane_type": "qa", "role": "qa-verifier", "execution_mode": "subagent", "wave": 1, "critical": True}, identifier="declare-unrelated-qa")
        journal_io.write_entry(fixture.run, "readiness-attempt", {"id": "foreign-attempt", "lane_id": foreign,
            "summary": "Honest unrelated pending review", "status": "blocked", "blockers": ["Separate duty pending"]}, identifier="foreign-attempt")
        for index, gate in enumerate(gates):
            journal_io.write_entry(fixture.run, "readiness-gate", {"attempt_id": "foreign-attempt", "axis": gate["axis"],
                "facet": gate["facet"], "check": "Unrelated pending observation", "summary": "Separate duty not ready",
                "status": "blocked", "evidence": [{"path": "artifacts/observation.txt"}]}, identifier="foreign-gate-" + str(index))
        fixture.lane_id = foreign
        pin = self.pin(fixture, identifier="foreign-pin")
        events = base.session(thread, "qa-verifier", {}, 3)
        foreign_output = copy.deepcopy(fixture.tool_outputs[0])
        foreign_output["payload"]["call_id"] = "foreign-mcp"
        events[3:3] = [foreign_output]
        fixture.source.sessions[thread] = events
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--role", "qa-verifier", "--lane-id", foreign,
                "--stage", "spawned", "--status", "active", "--summary", "Separate unconfigured QA",
                "--codex-thread-id", thread, "--review-input-operation-id", pin["operation_id"]], session_source=fixture.source), 0)
        other = copy.deepcopy(gates)
        for gate in other:
            gate["evidence"][0].update(path="artifacts/foreign-mcp.output", source_call_id="foreign-mcp")
        answer, foreign_handoff = self.conclude(fixture, gates=other)
        events = base.session(thread, "qa-verifier", answer, 3)
        events[3:3] = [foreign_output]
        fixture.source.sessions[thread] = events
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--role", "qa-verifier", "--lane-id", foreign,
                "--stage", "handoff", "--status", "pass", "--summary", "Separate own completed duty", "--codex-thread-id", thread,
                "--completion-turn-id", thread + "-turn", "--artifact", foreign_handoff, "--artifact", "artifacts/observation.txt",
                "--artifact", "artifacts/foreign-mcp.output"], session_source=fixture.source), 0)
        fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "python-worker", {}, 5)
        for worker in ("configured-first", "configured-stale"):
            journal_io.write_entry(fixture.run, "lane", {"id": worker, "summary": "Scoped dependent worker", "status": "planned",
                "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 2,
                "critical": True, "allowed_paths": ["module.py"]}, identifier="declare-" + worker)
        def start_worker(lane):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                return recorder.main(["--run-dir", str(fixture.run), "--role", "python-worker", "--lane-id", lane,
                    "--stage", "spawned", "--status", "active", "--summary", "Dependent public admission",
                    "--codex-thread-id", base.REVIEWER_ID], session_source=fixture.source)
        self.assertEqual(start_worker("configured-first"), 0)
        current = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual(json.loads(current.read_text("lane-map.json"))["verification_readiness"]["lanes"], [original])
        self.assertEqual({row["lane"] for row in json.loads(current.read_text("verification-readiness.json"))["attempts"]}, {original, foreign})
        self.assertEqual(self.readiness_schema_errors(fixture, current), [])
        from dataclasses import replace
        from types import MappingProxyType
        data = json.loads(current.read_text("verification-readiness.json"))
        historical = next(row for row in data["attempts"] if row["lane"] == foreign)
        data["attempts"] = [row for row in data["attempts"] if row["lane"] != foreign] + [historical]
        # Explicit aggregate snapshot double: unrelated latest blocked display cannot block configured ready.
        display = replace(current, documents=MappingProxyType({**current.documents,
            "verification-readiness.json": json.dumps(data).encode()}))
        self.assertEqual(self.readiness_schema_errors(fixture, display), [])
        primary = next(row for row in data["attempts"] if row["lane"] == original)
        primary.update(status="blocked", blockers=["Configured duty blocked"])
        historical.update(status="ready", blockers=[])
        for gate in historical["gates"]:
            gate["readiness"] = "ready"
        display = replace(current, documents=MappingProxyType({**current.documents,
            "verification-readiness.json": json.dumps(data).encode()}))
        self.assertTrue(any("latest ready attempt" in error for error in self.readiness_schema_errors(fixture, display)))
        # A later own turn makes only the configured QA stale; fresh unrelated PASS cannot replace it.
        fixture.source.sessions[base.QA_ID].append({"type": "event_msg", "timestamp": "2026-09-10T10:00:06+00:00",
            "payload": {"type": "task_started", "turn_id": base.QA_ID + "-unfinished"}})
        before = journal_io.JournalSnapshot.open(fixture.run)
        with self.assertRaisesRegex(SystemExit, "stale"):
            start_worker("configured-stale")
        after = journal_io.JournalSnapshot.open(fixture.run)
        self.assertEqual(before.documents, after.documents)
        self.assertEqual(before.receipts, after.receipts)

    def test_public_preliminary_completion_and_historical_drift(self):
        fixture = self.fixture()
        pin = self.pin(fixture)
        self.assertEqual(self.start(fixture, pin), 0)
        answer, handoff = self.conclude(fixture)
        self.assertEqual(answer["reviewed_result_hash"], None)
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        row = summary["subagents"][0]
        self.assertEqual((summary["verification"]["qa"], summary["verification"]["reviewer"]), (None, None))
        with self.assertRaises(evidence.EvidenceError):
            evidence.accepted_record(fixture.run, row, summary["verification"], "qa-verifier", fixture.source, snapshot=snapshot)
        journal_io.write_entry(fixture.run, "progress", {"summary": "Unrelated allowed work", "status": "active"}, identifier="unrelated")
        journal_io.write_entry(fixture.run, "task", {"summary": "Changed requirements", "status": "active"}, identifier="drift")
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        evidence.source_bound_record(fixture.run, row, summary["verification"], "qa-verifier", fixture.source, snapshot=snapshot, current=False)
        with self.assertRaises(evidence.EvidenceError):
            evidence.source_bound_record(fixture.run, row, summary["verification"], "qa-verifier", fixture.source, snapshot=snapshot, current=True)

    def test_public_negative_preliminary_does_not_set_product_blocker(self):
        fixture = self.fixture()
        self.start(fixture, self.pin(fixture))
        answer, handoff = self.conclude(fixture, status="blocked")
        self.assertEqual(self.accept(fixture, answer, handoff), 0)
        verification = json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("delegation-summary.json"))["verification"]
        self.assertIsNone(verification["qa"])
        self.assertFalse(verification.get("blocker"))

    def test_prepare_missing_declared_source_and_duplicate_are_atomic(self):
        fixture = self.fixture()
        before = SemanticRequestRegressions().tree(fixture.run)
        for sources in ([], ["run.md", "run.md"], ["artifacts/observation.txt"]):
            with self.assertRaises((ValueError, OSError)):
                journal_io.prepare_review_input(fixture.run, fixture.lane_id, sources, identifier="bad-input")
            after = SemanticRequestRegressions().tree(fixture.run)
            self.assertEqual({key: val for key, val in after.items() if not key.startswith(".journal/requests/")}, {key: val for key, val in before.items() if not key.startswith(".journal/requests/")})

    def test_causal_pin_and_source_echo_reject_atomic(self):
        fixture = self.fixture()
        pin = self.pin(fixture)
        self.start(fixture, pin)
        answer, handoff = self.conclude(fixture)
        before = SemanticRequestRegressions().tree(fixture.run)
        for change in ({"review_subject": None}, {"review_input_operation_id": "unknown-pin"}, {"reviewed_result_hash": "a" * 64}):
            with self.subTest(change=change):
                with self.assertRaises(SystemExit):
                    self.accept(fixture, {**answer, **change}, handoff)
                self.assertEqual(SemanticRequestRegressions().tree(fixture.run), before)
        events = base.session(base.QA_ID, "qa-verifier", answer, 1)
        events[1]["payload"]["started_at"] = int(datetime.fromisoformat(pin["frozen_at"]).timestamp())
        fixture.source.sessions[base.QA_ID] = events
        with self.assertRaises(evidence.EvidenceError):
            completion = evidence.completed_turn(fixture.source, base.QA_ID, base.QA_ID + "-turn", base.ROOT_ID, "qa-verifier")
            snapshot = journal_io.JournalSnapshot.open(fixture.run)
            row = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
            evidence.require_review_completion(snapshot, row, completion, fixture.source, current=True)
        self.assertEqual(SemanticRequestRegressions().tree(fixture.run), before)

    def test_real_external_root_move_uses_current_scope_and_keeps_pin(self):
        from dataclasses import replace
        fixture = self.fixture()
        external = fixture.repo / "declared-contract.txt"
        external.write_bytes(b"Full contract\n")
        pin = journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", str(external)], identifier="external-pin")
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        row = {"lane_id": fixture.lane_id, "review_subject": pin["review_subject"], "review_input_operation_id": "external-pin"}
        # Explicit approved-context double, with real moved bytes and old root gone.
        moved = fixture.repo.parent / (fixture.repo.name + "-relocated")
        fixture.repo.rename(moved)
        self.addCleanup(__import__("shutil").rmtree, moved)
        current = replace(snapshot, source_root=moved)
        evidence.review_input_receipt(current, row, current=True)
        (moved / "declared-contract.txt").write_bytes(b"Full contract\nchanged")
        evidence.review_input_receipt(current, row, current=False)
        with self.assertRaises(evidence.EvidenceError):
            evidence.review_input_receipt(current, row, current=True)

    def test_capture_finishes_before_frozen_time_and_earlier_review_rejects(self):
        fixture = self.fixture()
        external = fixture.repo / "requirements.txt"
        external.write_bytes(b"Complete delayed capture\n")
        captured = []
        original = evidence.review_source_bytes
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("capture", True):
                captured.append(result[0])
            return result
        def frozen_clock():
            self.assertEqual(set(captured), {"journal:run.md", "source:requirements.txt"})
            return "2026-09-10T10:01:00+00:00"
        with patch.object(evidence, "review_source_bytes", side_effect=capture), patch.object(journal_io, "now_iso", side_effect=frozen_clock):
            pin = journal_io.prepare_review_input(fixture.run, fixture.lane_id, [str(external), "run.md"], identifier="delayed-pin")
        self.start(fixture, pin)
        answer, handoff = self.conclude(fixture)
        before = SemanticRequestRegressions().tree(fixture.run)
        with self.assertRaisesRegex(SystemExit, "strictly after input freeze"):
            self.accept(fixture, answer, handoff)
        self.assertEqual(SemanticRequestRegressions().tree(fixture.run), before)
        self.assertEqual(journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", str(external)], identifier="delayed-pin"), pin)

    def test_own_followup_review_turn_after_pin_preserves_old_session_start(self):
        fixture = self.fixture()
        old = base.session(base.QA_ID, "qa-verifier", {}, 1)
        old[1]["timestamp"] = "2026-09-10T09:00:00+00:00"
        old[4]["timestamp"] = "2026-09-10T09:00:01+00:00"
        old[1]["payload"]["turn_id"] = old[2]["payload"]["turn_id"] = old[4]["payload"]["turn_id"] = "unrelated-old-turn"
        fixture.source.sessions[base.QA_ID] = old
        self.start(fixture, self.pin(fixture))
        answer, handoff = self.conclude(fixture)
        followup = base.session(base.QA_ID, "qa-verifier", answer, 2)
        fixture.source.sessions[base.QA_ID] = old + followup[1:]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--role", "qa-verifier", "--lane-id", fixture.lane_id,
                "--stage", "handoff", "--status", "pass", "--summary", "Own reviewing followup", "--codex-thread-id", base.QA_ID,
                "--completion-turn-id", base.QA_ID + "-turn", "--artifact", handoff, "--artifact", "artifacts/observation.txt"], session_source=fixture.source), 0)

    def test_closed_subject_product_tag_and_pin_immutable(self):
        self.assertEqual(evidence.review_subject_kind({"review_subject": {"kind": "product-result"}}), "product-result")
        for subject in (None, {}, {"kind": "invented"}, {"kind": "product-result", "digest": "a" * 64}):
            with self.assertRaises(evidence.EvidenceError):
                evidence.review_subject_kind({"review_subject": subject})
        fixture = self.fixture()
        self.start(fixture, self.pin(fixture))
        snapshot = journal_io.JournalSnapshot.open(fixture.run)
        before = snapshot.revision
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        del summary["subagents"][0]["review_subject"]
        identifier = journal_io.operation_id(fixture.run, "fault-omission", {}, identifier="fault-omission")
        with self.assertRaises(journal_io.JournalError):
            journal_io.transact(fixture.run, identifier, {}, lambda current: ({"delegation-summary.json": journal_io.encode_json(summary)}, {}))
        self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before)

    def test_declared_context_only_pin_has_current_resolver_and_unknown_scope_rejects(self):
        fixture = self.fixture()
        product = fixture.repo / "product"
        product.mkdir()
        working = fixture.repo / "working"
        working.mkdir()
        context = fixture.repo / "requirements.txt"
        context.write_bytes(b"Declared context-only requirements\n")
        from workspace_scope import context_capture
        from task_workspace import manifest_digest
        scoped = {"version": 2, "working_root": str(working), "task_scope": {"product_roots": [{"source": str(product), "relative_path": "product"}],
                  "context_roots": [context_capture(context)], "context_exclusions": []}}
        scoped["task_scope"]["digest"] = manifest_digest(scoped["task_scope"])
        # Approved scope/source double; real context exists only outside working tree.
        with patch.object(evidence, "registered_workspace", return_value=scoped):
            pin = journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", str(context)], identifier="context-only-pin")
            snapshot = journal_io.JournalSnapshot.open(fixture.run)
            row = {"lane_id": fixture.lane_id, "review_subject": pin["review_subject"], "review_input_operation_id": "context-only-pin"}
            evidence.review_input_receipt(snapshot, row, current=True)
            self.assertFalse((working / "requirements.txt").exists())
            context.write_bytes(b"Known declared context drift\n")
            with self.assertRaises(evidence.EvidenceError):
                evidence.review_input_receipt(snapshot, row, current=True)
            evidence.review_input_receipt(snapshot, row, current=False)
        before = journal_io.JournalSnapshot.open(fixture.run)
        scoped["task_scope"]["context_roots"] = []
        with patch.object(evidence, "registered_workspace", return_value=scoped), self.assertRaisesRegex(evidence.EvidenceError, "declared product/context scope"):
            journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", str(context)], identifier="undeclared-context-pin")
        (working / "unknown-context.txt").write_bytes(b"Undeclared outside selected product root\n")
        with patch.object(evidence, "registered_workspace", return_value=scoped), self.assertRaisesRegex(evidence.EvidenceError, "declared product/context scope"):
            journal_io.prepare_review_input(fixture.run, fixture.lane_id, ["run.md", str(working / "unknown-context.txt")], identifier="undeclared-working-context-pin")
        self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)

    def test_malformed_gate_input_rejects_before_publication(self):
        fixture = self.fixture(schema=2)
        self.start(fixture, self.pin(fixture))
        with __import__("tempfile").TemporaryDirectory() as disposable:
            receipt = journal_io.write_entry(fixture.run, "check", {"summary": "Real scoped process", "status": "pass", "cwd": disposable,
                "command": [sys.executable, "-B", "-c", "print('actual command executed')"]}, identifier="gate-input-command")
        pairs = __import__("journal_structure").domain_validator().selected_verification_gate_pairs(json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("lane-map.json"))["architecture_context"])
        gates = [{"axis": axis, "facet": facet, "check": "Actual scoped execution", "readiness": "ready", "notes": "Synthetic independent QA applicability", "evidence": [{"path": ref["path"]} for ref in receipt["evidence"]]} for axis, facet in pairs]
        _, handoff = self.conclude(fixture, gates=gates)
        before = SemanticRequestRegressions().tree(fixture.run)
        bad = [gates[:-1], gates + [gates[0]], [{**gates[0], "readiness": "passed"}, *gates[1:]],
               [{**gates[0], "extra": True}, *gates[1:]], [{**gates[0], "axis": "stack_runtime"}, *gates[1:]],
               [{**gates[0], "evidence": [{"path": receipt["evidence"][0]["path"], "extra": True}]}, *gates[1:]]]
        for rows in bad:
            gate_file = fixture.repo / "bad-gates.json"
            gate_file.write_text(json.dumps(rows))
            with self.assertRaises(SystemExit), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                recorder.main(["--run-dir", str(fixture.run), "--prepare-conclusion", "--role", "qa-verifier", "--lane-id", fixture.lane_id,
                    "--status", "pass", "--summary", "Malformed applicability input", "--artifact", handoff, "--artifact", "artifacts/observation.txt",
                    "--readiness-gates-file", str(gate_file), *[argument for ref in receipt["evidence"] for argument in ("--artifact", ref["path"])]], session_source=fixture.source)
            self.assertEqual(SemanticRequestRegressions().tree(fixture.run), before)

    def change_workspace(self, fixture):
        (fixture.repo / "module.py").write_text("value = 1\n")
        for command in (["git", "init", "--quiet"], ["git", "config", "user.email", "fixture@example.invalid"],
                        ["git", "config", "user.name", "Fixture"], ["git", "add", "module.py"], ["git", "commit", "--quiet", "-m", "Fixture baseline"]):
            subprocess.run(command, cwd=fixture.repo, check=True, capture_output=True)
        (fixture.repo / ".git/info/exclude").write_text(".agent-work/\n")
        workspace = load("review_workspace_public", "task-workspace.py")
        with redirect_stdout(StringIO()):
            self.assertEqual(workspace.main(["--run-dir", str(fixture.run), "prepare", "--source", str(fixture.repo),
                "--destination", str(fixture.repo / ".agent-work/workspace")]), 0)
        verification = {**evidence.empty_verification(), "task_kind": "change", "root_thread_id": base.ROOT_ID,
            "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"}, "task_scope": {"path": "run.md", "section": "Task Scope"}}
        self.assertEqual(fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification)), 0)

    def test_initial_change_requires_prepare_with_phase_specific_reason(self):
        fixture = self.fixture()
        verification = {**evidence.empty_verification(), "task_kind": "change", "root_thread_id": base.ROOT_ID}
        with self.assertRaisesRegex(SystemExit, "prepare workspace before initial change verification"):
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification))
        fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "blocked", "--status", "blocked", "--verification-json",
                       json.dumps({**verification, "blocker": "Workspace not yet prepared", "unverified": ["initial change verification"]}))
        with self.assertRaisesRegex(SystemExit, "prepare workspace before initial change verification"):
            fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--verification-json", json.dumps(verification))
        self.change_workspace(fixture)
        current = json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("delegation-summary.json"))["verification"]
        self.assertIsNone(current["result_hash"])

    def test_automatic_readiness_uses_latest_applicable_native_check(self):
        import tempfile
        fixture = self.fixture(schema=2)
        self.change_workspace(fixture)
        journal_io.write_entry(fixture.run, "lane", {"id": "worker-implementation", "summary": "Scoped worker",
            "status": "planned", "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent",
            "wave": 2, "critical": True, "allowed_paths": ["module.py"]}, identifier="current-worker")
        self.start(fixture, self.pin(fixture))
        with tempfile.TemporaryDirectory() as disposable, tempfile.TemporaryDirectory() as other:
            command = [sys.executable, "-B", "-c", "from pathlib import Path; print('selected check'); raise SystemExit(7 if Path('fail-now').exists() else 0)"]
            request = {"summary": "Selected scoped check", "status": "pass", "command": command, "cwd": disposable}
            passed = journal_io.write_entry(fixture.run, "check", request, identifier="endorsed-check")
            self.assertEqual(passed["exit_code"], 0)
            snapshot = journal_io.JournalSnapshot.open(fixture.run)
            from journal_structure import domain_validator
            pairs = domain_validator().selected_verification_gate_pairs(json.loads(snapshot.read_text("lane-map.json"))["architecture_context"])
            gates = [{"axis": axis, "facet": facet, "check": "Selected environment check", "readiness": "ready",
                      "evidence": [{"path": ref["path"]} for ref in passed["evidence"]], "notes": "Actual QA applicability to pinned scope"} for axis, facet in pairs]
            answer, handoff = self.conclude(fixture, gates=gates)
            self.accept(fixture, answer, handoff)
            for identifier, changed in (("unrelated-command", {"command": [sys.executable, "-B", "-c", "raise SystemExit(7)"]}),
                                        ("unrelated-cwd", {"cwd": other})):
                (Path(other) / "fail-now").touch()
                failed = journal_io.write_entry(fixture.run, "check", {**request, **changed}, identifier=identifier)
                self.assertEqual(failed["exit_code"], 7)
                observations = journal_io.automatic_readiness(journal_io.JournalSnapshot.open(fixture.run), fixture.source)
                self.assertEqual(json.loads(observations["verification-readiness.json"])["status"], "ready")
            (Path(disposable) / "fail-now").touch()
            failed = journal_io.write_entry(fixture.run, "check", request, identifier="same-check-failure")
            self.assertEqual(failed["exit_code"], 7)
            snapshot = journal_io.JournalSnapshot.open(fixture.run)
            with self.assertRaises(journal_io.JournalError):
                journal_io.automatic_readiness(snapshot, fixture.source)
            fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "python-worker", {}, 2)
            with self.assertRaises(SystemExit):
                fixture.record("--role", "python-worker", "--lane-id", "worker-implementation", "--stage", "spawned", "--codex-thread-id", base.REVIEWER_ID)
            self.assertEqual(fixture.snapshot(), (snapshot.revision, snapshot.documents, snapshot.receipts))
            # Exact replay is the prior execution, not a fresh success.
            (Path(disposable) / "fail-now").unlink()
            self.assertEqual(journal_io.write_entry(fixture.run, "check", request, identifier="same-check-failure")["exit_code"], 7)
            with self.assertRaises(journal_io.JournalError):
                journal_io.automatic_readiness(journal_io.JournalSnapshot.open(fixture.run), fixture.source)
            retried = journal_io.write_entry(fixture.run, "check", request, identifier="same-check-real-retry")
            self.assertEqual(retried["exit_code"], 0)
            observations = journal_io.automatic_readiness(journal_io.JournalSnapshot.open(fixture.run), fixture.source)
            self.assertEqual(json.loads(observations["verification-readiness.json"])["status"], "ready")

    def test_source_facts_automatically_materialize_readiness_without_rerun(self):
        import tempfile
        fixture = self.fixture(schema=2)
        self.change_workspace(fixture)
        journal_io.write_entry(fixture.run, "lane", {"id": "worker-implementation", "summary": "Scoped worker", "status": "planned",
            "lane_type": "implementation", "role": "python-worker", "execution_mode": "subagent", "wave": 2, "critical": True, "allowed_paths": ["module.py"]}, identifier="declare-worker")
        self.start(fixture, self.pin(fixture))
        with tempfile.TemporaryDirectory() as disposable:
            counter = Path(disposable) / "executions"
            command = [sys.executable, "-B", "-c", "from pathlib import Path; p=Path('executions'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('declared Python config observation')"]
            receipt = journal_io.write_entry(fixture.run, "check", {"summary": "Native declared environment check", "status": "pass", "command": command, "cwd": disposable}, identifier="native-env")
            paths = [ref["path"] for ref in receipt["evidence"]]
            mapping = json.loads(journal_io.JournalSnapshot.open(fixture.run).read_text("lane-map.json"))
            from journal_structure import domain_validator
            pairs = domain_validator().selected_verification_gate_pairs(mapping["architecture_context"])
            gates = [{"axis": axis, "facet": facet, "check": "Declared pinned contract Python observation", "readiness": "ready", "evidence": [{"path": path} for path in paths], "notes": "Independent synthetic QA endorses applicability to declared module/config"} for axis, facet in pairs]
            # Native exit0 plus a manual mapping cannot grant readiness before source QA.
            journal_io.write_entry(fixture.run, "readiness-attempt", {"id": fixture.lane_id, "lane_id": fixture.lane_id, "summary": "Pending actual QA", "status": "blocked", "blockers": ["missing source mapping"], "evidence": []}, identifier="pending-readiness")
            before = journal_io.JournalSnapshot.open(fixture.run)
            with self.assertRaises(journal_io.JournalError):
                journal_io.write_entry(fixture.run, "readiness-gate", {"attempt_id": fixture.lane_id, "axis": pairs[0][0], "facet": pairs[0][1], "check": gates[0]["check"], "summary": gates[0]["notes"], "status": "pass", "evidence": [{"path": path} for path in paths]}, identifier="unendorsed-gate")
            self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, before.revision)
            answer, handoff = self.conclude(fixture, gates=gates)
            self.assertEqual(self.accept(fixture, answer, handoff), 0)
            fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "python-worker", {}, 2)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(recorder.main(["--run-dir", str(fixture.run), "--role", "python-worker", "--lane-id", "worker-implementation", "--stage", "spawned", "--status", "active", "--summary", "Start with proven scoped facts", "--codex-thread-id", base.REVIEWER_ID], session_source=fixture.source), 0)
            snapshot = journal_io.JournalSnapshot.open(fixture.run)
            self.assertEqual(json.loads(snapshot.read_text("verification-readiness.json"))["status"], "ready")
            lane_map = json.loads(snapshot.read_text("lane-map.json"))
            readiness_lane = next(row for row in lane_map["lanes"] if row["id"] == fixture.lane_id)
            self.assertEqual(readiness_lane["verification_results"]["status"], "pass")
            self.assertEqual(lane_map["verification_readiness"]["lanes"], [fixture.lane_id])
            self.assertEqual(counter.read_text(), "1")
            self.assertEqual(snapshot.operation_receipt("native-env")["result"], {key: value for key, value in receipt.items() if key != "operation_id"})
            failed = journal_io.write_entry(fixture.run, "check", {"summary": "Known environment drift", "status": "pass", "command": [sys.executable, "-B", "-c", "raise SystemExit(7)"], "cwd": disposable}, identifier="later-failed-environment")
            journal_io.write_entry(fixture.run, "readiness-gate", {"attempt_id": fixture.lane_id, "axis": pairs[0][0], "facet": pairs[0][1], "check": "Current environment failed", "summary": "Known current check blocker", "status": "blocked", "evidence": [{"path": ref["path"]} for ref in failed["evidence"]]}, identifier="later-failed-gate")
            blocked = journal_io.JournalSnapshot.open(fixture.run)
            with self.assertRaisesRegex(journal_io.JournalError, "newer environment/check blocker"):
                journal_io.automatic_readiness(blocked, fixture.source)
            self.assertEqual(journal_io.JournalSnapshot.open(fixture.run).revision, blocked.revision)


class ArchitectureOutcomeRegressions(unittest.TestCase):
    """Public writer transitions; Codex session completion is a synthetic double."""

    def setUp(self):
        self.fixture = FirstBindingRegressions()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize()
        self.fixture.start()
        from journal_structure import request_contract
        self.request = request_contract()["examples"]["architecture-outcome"]
        self.request.update(lane_id=self.fixture.lane_id, claim_evidence_ids=["actual-proof"],
                            acceptance_criteria_ids=["public-transition"])
        self.journal = load("outcome_public_journal", "journal.py")

    def write(self, request=None, identifier="outcome", source_request=None, mutate_source=None):
        request = self.request if request is None else request
        self.fixture.source.sessions[base.QA_ID] = base.session(base.QA_ID, "architect",
            request if source_request is None else source_request, 1)
        if mutate_source:
            mutate_source(self.fixture.source.sessions[base.QA_ID])
        request_file = self.fixture.repo / "outcome-request.json"
        request_file.write_text(json.dumps(request))
        with patch.object(evidence, "CodexSessionSource", return_value=self.fixture.source), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return self.journal.main(["--run-dir", str(self.fixture.run), "record", "--kind", "architecture-outcome",
                                      "--request-file", str(request_file), "--operation-id", identifier])

    def tree(self):
        return SemanticRequestRegressions().tree(self.fixture.run)

    def update_input(self, kind, summary, identifier, **extra):
        request_file = self.fixture.repo / "input-update.json"
        request_file.write_text(json.dumps({"status": "active", "summary": summary, **extra}))
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return self.journal.main(["--run-dir", str(self.fixture.run), "record", "--kind", kind,
                "--request-file", str(request_file), "--operation-id", identifier])

    def test_mutable_scope_preserved_metadata_registers_and_drift_blocks(self):
        from verification_evidence import historical_evidence_bytes, require_current_architecture_inputs
        self.request["evidence"] = [{"path": "run.md"}]
        self.assertEqual(self.write(), 0)
        original = journal_io.JournalSnapshot.open(self.fixture.run)
        record = json.loads(original.read_text("delegation-summary.json"))["subagents"][0]
        before_bytes = original.read_bytes("run.md")
        before_source = json.dumps(self.fixture.source.sessions)
        self.update_input("task", "A substantively changed scope requiring another review.", "scope-change")
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        stored = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
        self.assertEqual(stored, record)
        self.assertEqual(json.dumps(self.fixture.source.sessions), before_source)
        self.assertEqual(historical_evidence_bytes(snapshot, record, record["evidence"][0]), before_bytes)
        receipt = snapshot.operation_receipt("scope-change")
        self.assertEqual(len(receipt["result"]["preserved_evidence"]), 1)
        state = self.fixture.snapshot()
        self.update_input("task", "A substantively changed scope requiring another review.", "scope-change")
        self.assertEqual(self.fixture.snapshot(), state)
        with self.assertRaises(SystemExit):
            self.update_input("task", "Conflicting retry", "scope-change")
        self.assertEqual(self.fixture.snapshot(), state)
        verification = json.loads(snapshot.read_text("delegation-summary.json"))["verification"]
        verification["task_scope"] = {"path": "run.md", "section": "Task Scope"}
        self.fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification-prepared",
            "--verification-json", json.dumps(verification), "--operation-id", "new-scope-registration")
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        with self.assertRaisesRegex(journal_io.JournalError, "architecture reviewed input is stale"):
            require_current_architecture_inputs(snapshot)
        # Admission checks this same predicate before workspace/readiness checks.
        from types import SimpleNamespace
        args = SimpleNamespace(stage="implementation", status="active", lane_id="worker", codex_thread_id=base.REVIEWER_ID,
                               execution_mode="subagent")
        with self.assertRaisesRegex(journal_io.JournalError, "architecture reviewed input is stale"):
            journal_io.worker_start_preflight(snapshot, {}, None, verification, args)
        with self.assertRaisesRegex(journal_io.JournalError, "architecture reviewed input is stale"):
            journal_io.automatic_readiness(snapshot, self.fixture.source)
        state = self.fixture.snapshot()
        with self.assertRaisesRegex(journal_io.JournalError, "architecture reviewed input is stale"):
            journal_io.write_entry(self.fixture.run, "readiness-attempt", {"id": "stale-ready", "lane_id": "verification-readiness-1",
                "summary": "Attempt readiness against changed scope", "status": "pass", "blockers": []}, identifier="stale-readiness")
        self.assertEqual(self.fixture.snapshot(), state)
        self.assertIn("architecture reviewed input is stale", " ".join(base.validator.validate_product_acceptance(
            self.fixture.run, snapshot=snapshot, verdict="ship", session_source=self.fixture.source)))
        self.update_input("task", "A second legitimate scope update.", "second-scope-change")
        latest = journal_io.JournalSnapshot.open(self.fixture.run)
        self.assertNotIn("preserved_evidence", latest.operation_receipt("second-scope-change")["result"])
        self.assertEqual(historical_evidence_bytes(latest, record, record["evidence"][0]), before_bytes)
        verification["task_scope"] = {"path": "run.md", "section": "Task Scope"}
        self.fixture.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification-prepared",
            "--verification-json", json.dumps(verification), "--operation-id", "second-scope-registration")
        # New public same-duty architect outcome restores applicability without
        # changing old records, source completion, or evidence hashes.
        old_lane = self.fixture.lane_id
        journal_io.write_entry(self.fixture.run, "lane", {"id": "architecture-current", "summary": "Review updated scope",
            "status": "planned", "lane_type": "architecture", "role": "architect", "execution_mode": "subagent",
            "wave": 1, "critical": True}, identifier="current-architecture-lane")
        self.fixture.lane_id = "architecture-current"
        self.fixture.start(base.REVIEWER_ID, "--obligation-id", record["obligation"]["id"])
        current_request = {**self.request, "lane_id": self.fixture.lane_id}
        self.fixture.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "architect", current_request, 2)
        with patch.object(evidence, "CodexSessionSource", return_value=self.fixture.source):
            journal_io.write_entry(self.fixture.run, "architecture-outcome", current_request, identifier="current-architecture-outcome")
        from journal_lifecycle import resolve_obligation
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        resolve_obligation(self.fixture.run, lane_id=old_lane, replacement=self.fixture.lane_id,
            reason="Updated architect review covers the same duty with current scope", expected_revision=snapshot.revision,
            identifier="resolve-old-architecture")
        require_current_architecture_inputs(journal_io.JournalSnapshot.open(self.fixture.run))

    def test_presentation_only_scope_update_and_plan_input(self):
        from verification_evidence import require_current_architecture_inputs, reference_bytes
        # Use a native plan projection with its existing Execution Plan section.
        self.update_input("plan", "Review the scoped implementation and its checks.", "plan-input")
        scope = "Reviewed semantic scope."
        self.update_input("task", scope, "task-input")
        self.request["evidence"] = [{"path": "run.md"}, {"path": "plan.md"}]
        self.write()
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        self.update_input("task", scope, "task-presentation", evidence=[{"path": "plan.md"}])
        require_current_architecture_inputs(journal_io.JournalSnapshot.open(self.fixture.run))
        self.update_input("task", "    " + scope, "task-indentation")
        with self.assertRaisesRegex(journal_io.JournalError, "stale: run.md"):
            require_current_architecture_inputs(journal_io.JournalSnapshot.open(self.fixture.run))
        self.update_input("task", scope, "restore-reviewed-task")
        self.update_input("plan", "A changed execution plan.", "changed-plan")
        with self.assertRaisesRegex(journal_io.JournalError, "stale: plan.md"):
            require_current_architecture_inputs(journal_io.JournalSnapshot.open(self.fixture.run))

    def test_preserved_input_capture_and_binding_tamper_are_strict(self):
        from dataclasses import replace
        from types import MappingProxyType
        from copy import deepcopy
        from verification_evidence import historical_evidence_bytes
        self.request["evidence"] = [{"path": "run.md"}]
        self.write()
        self.update_input("task", "A current changed task scope.", "capture-input")
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        record = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
        capture = snapshot.operation_receipt("capture-input")["result"]["preserved_evidence"][0]["capture"]["path"]
        before = self.fixture.snapshot()
        with self.assertRaisesRegex(journal_io.JournalError, "capture is immutable"):
            journal_io.transact(self.fixture.run, "overwrite-capture", {}, lambda _: ({capture: b"tampered"}, {}))
        self.assertEqual(self.fixture.snapshot(), before)
        with self.assertRaisesRegex(journal_io.JournalError, "terminal handoff is immutable"):
            journal_io.transact(self.fixture.run, "overwrite-handoff", {}, lambda _: ({record["handoff"]: b"tampered"}, {}))
        self.assertEqual(self.fixture.snapshot(), before)
        for defect in ("missing", "corrupt", "path", "lane", "assignment", "receipt", "legacy-lost"):
            with self.subTest(defect=defect):
                docs, receipts = dict(snapshot.documents), dict(snapshot.receipts)
                if defect == "missing":
                    del docs[capture]
                elif defect == "corrupt":
                    docs[capture] = b"corrupt capture"
                elif defect == "legacy-lost":
                    del receipts["capture-input"]
                else:
                    saved = deepcopy(json.loads(receipts["capture-input"]))
                    row = saved["result"]["preserved_evidence"][0]
                    if defect == "path": row["reference"]["path"] = "plan.md"
                    if defect == "lane": row["lane_id"] = "foreign-lane"
                    if defect == "assignment": row["assignment"]["lane_id"] = "foreign-lane"
                    if defect == "receipt": row["capture"]["sha256"] = "f" * 64
                    receipts["capture-input"] = json.dumps(saved).encode()
                candidate = replace(snapshot, documents=MappingProxyType(docs), receipts=MappingProxyType(receipts))
                with self.assertRaises((journal_io.JournalError, FileNotFoundError)):
                    historical_evidence_bytes(candidate, record, record["evidence"][0])

    def test_compact_and_full_historical_completion_reaccept_preserves_old_input(self):
        from verification_evidence import historical_evidence_bytes
        for schema in (0, 1):
            with self.subTest(schema=schema):
                f = FirstBindingRegressions()
                f.setUp()
                try:
                    f.initialize(schema)
                    f.start()
                    text = "Scope analysis completed against the declared original input."
                    events = base.session(base.QA_ID, "architect", {}, 1)
                    events[-2]["payload"]["content"][0]["text"] = text
                    events[-1]["payload"]["last_agent_message"] = text
                    f.source.sessions[base.QA_ID] = events
                    with patch.object(evidence, "CodexSessionSource", return_value=f.source):
                        journal_io.write_entry(f.run, "handoff", {"lane_id": f.lane_id, "status": "pass", "summary": text,
                            "evidence": [{"path": "run.md"}]}, identifier="compact-original-handoff")
                    snapshot = journal_io.JournalSnapshot.open(f.run)
                    old = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
                    raw = snapshot.read_bytes("run.md")
                    # Enrich the accepted handoff before mutation through the genuine
                    # public source adapter; source itself is an explicit double.
                    args = ["--role", "architect", "--lane-id", f.lane_id, "--stage", "handoff", "--status", "pass",
                            "--codex-thread-id", base.QA_ID, "--completion-turn-id", base.QA_ID + "-turn",
                            "--artifact", old["handoff"], "--artifact", "run.md", "--operation-id", "compact-source-completion"]
                    f.record(*args)
                    completed = journal_io.JournalSnapshot.open(f.run)
                    record = json.loads(completed.read_text("delegation-summary.json"))["subagents"][0]
                    journal_io.write_entry(f.run, "task", {"status": "active", "summary": "Updated native task scope."},
                                           identifier="compact-task-update")
                    journal_io.write_entry(f.run, "task", {"status": "active", "summary": "Second lawful task update."},
                                           identifier="compact-second-task-update")
                    completed = journal_io.JournalSnapshot.open(f.run)
                    self.assertEqual(historical_evidence_bytes(completed, record, record["evidence"][0]), raw)
                    state = f.snapshot()
                    f.record(*args)
                    self.assertEqual(f.snapshot(), state)
                    args[-1] = "compact-source-reaccept"
                    f.record(*args)
                    self.assertEqual(journal_io.JournalSnapshot.open(f.run).revision, completed.revision)
                    state = f.snapshot()
                    with self.assertRaises(SystemExit):
                        f.record(*args, "--artifact", "foreign-proof.md")
                    self.assertEqual(f.snapshot(), state)
                    # Adapters compute current hashes; an arbitrary asserted
                    # third hash must never be silently normalized by writer.
                    bad_payload = {"artifacts": {old["handoff"]: evidence.sha256(completed.read_bytes(old["handoff"])),
                                                  "run.md": "f" * 64}}
                    with self.assertRaisesRegex(journal_io.JournalError, "hash assertion differs"):
                        journal_io.write_entry(f.run, "event", {"role": "architect", "stage": "handoff", "status": "pass",
                            "summary": text}, identifier="bad-hash", payload=bad_payload,
                            completion={"lane_id": f.lane_id}, mutation=lambda current: ({}, {}))
                    self.assertEqual(f.snapshot(), state)
                    f.source.sessions[base.QA_ID][-1]["payload"]["last_agent_message"] = "Different own source completion"
                    with self.assertRaises(SystemExit):
                        f.record(*args)
                    self.assertEqual(f.snapshot(), state)
                finally:
                    f.doCleanups()

    def test_final_consumer_summary_defects_return_structured_refusal(self):
        from dataclasses import replace
        from types import MappingProxyType
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        state = self.fixture.snapshot()
        for defect, content in (("missing", None), ("malformed", b"{invalid"), ("null", b"null"), ("list", b"[]")):
            with self.subTest(defect=defect):
                docs = dict(snapshot.documents)
                if content is None:
                    del docs["delegation-summary.json"]
                else:
                    docs["delegation-summary.json"] = content
                candidate = replace(snapshot, documents=MappingProxyType(docs))
                errors = base.validator.validate_product_acceptance(self.fixture.run, snapshot=candidate,
                    verdict="ship", session_source=self.fixture.source)
                self.assertIsInstance(errors, list)
                self.assertTrue(errors)
                self.assertTrue(all(isinstance(error, str) for error in errors))
        self.assertEqual(self.fixture.snapshot(), state)

    def test_final_consumer_missing_and_mixed_captures_return_typed_refusal(self):
        from dataclasses import replace
        from types import MappingProxyType
        self.update_input("plan", "Original reviewed execution plan.", "typed-plan-input")
        self.update_input("task", "Reviewed current scope.", "typed-task-input")
        self.request["evidence"] = [{"path": "run.md"}, {"path": "plan.md"}]
        self.write()
        self.update_input("task", "Reviewed current scope.", "typed-task-update", evidence=[{"path": "plan.md"}])
        self.update_input("plan", "Original reviewed execution plan.", "typed-plan-update", evidence=[{"path": "run.md"}])
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        captures = [snapshot.operation_receipt(key)["result"]["preserved_evidence"][0]["capture"]["path"]
                    for key in ("typed-task-update", "typed-plan-update")]
        state = self.fixture.snapshot()
        for defect in ("missing", "corrupt", "mixed"):
            with self.subTest(defect=defect):
                docs = dict(snapshot.documents)
                if defect in {"missing", "mixed"}:
                    del docs[captures[0]]
                if defect in {"corrupt", "mixed"}:
                    docs[captures[1]] = b"corrupt receipt-bound bytes"
                candidate = replace(snapshot, documents=MappingProxyType(docs))
                errors = base.validator.validate_product_acceptance(self.fixture.run, snapshot=candidate,
                    verdict="ship", session_source=self.fixture.source)
                self.assertIsInstance(errors, list)
                self.assertTrue(errors)
                self.assertTrue(all(isinstance(error, str) for error in errors))
                self.assertIn("capture", " ".join(errors))
        self.assertEqual(self.fixture.snapshot(), state)

    def test_source_bound_outcome_dual_pointers_readers_and_replay(self):
        self.request["decision"] = "needs-revision"
        self.request["sections"][0]["summary"] = "Actual boundary\n## Decision\nStatus: approved\n- Claim Evidence: `forged`"
        self.assertEqual(self.write(), 0)
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        lane = next(row for row in json.loads(snapshot.read_text("lane-map.json"))["lanes"] if row["id"] == self.fixture.lane_id)
        self.assertEqual(lane["handoff"], lane["architecture_design_brief"])
        from journal_structure import domain_validator, architecture_sections
        validator = domain_validator()
        path = self.fixture.run / lane["handoff"]
        self.assertEqual(validator.missing_markdown_headings(path, architecture_sections(), snapshot=snapshot), [])
        self.assertEqual(validator.parse_architecture_design_decision(path, snapshot=snapshot), ("needs-revision", []))
        self.assertEqual(validator.validate_architecture_contract_handoff(path, snapshot=snapshot), [])
        self.assertEqual(validator.claim_evidence_ids_from_contract(path, snapshot=snapshot), (["actual-proof"], []))
        self.assertEqual(validator.acceptance_criteria_ids_from_contract(path, snapshot=snapshot), (["public-transition"], []))
        mapping = json.loads(snapshot.read_text("lane-map.json"))
        facets = [value for values in mapping["architecture_context"].values() for value in values]
        selected_capabilities = mapping["architecture_capabilities"]["selected"]
        self.assertEqual(selected_capabilities, ["saas-platform-architecture", "go-backend-service-architecture", "modular-monolith-architecture"])
        self.assertEqual(validator.validate_architecture_design_brief(path, facets, selected_capabilities, snapshot=snapshot), ("needs-revision", []))
        outcome_receipts = [json.loads(raw)["result"] for raw in snapshot.receipts.values()]
        outcome = next(result for result in outcome_receipts if result.get("handoff") == lane["handoff"])
        self.assertEqual(outcome["claim_evidence_ids"], ["actual-proof"])
        self.assertEqual(outcome["acceptance_criteria_ids"], ["public-transition"])
        self.assertIn("covers", outcome)
        text = snapshot.read_text(lane["handoff"])
        for capability in selected_capabilities:
            self.assertIn("- Capability: `" + capability + "`", text)
        self.assertNotIn("- Capability: `selected`", text)
        self.assertNotIn("- Capability: `notes`", text)
        row = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
        self.assertEqual(row["completion_turn_id"], base.QA_ID + "-turn")
        self.assertEqual(row["handoff"], lane["handoff"])
        output = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("journal.py")),
            "--run-dir", str(self.fixture.run), "read", lane["handoff"]], capture_output=True, text=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertIn("Status: needs-revision", output.stdout)
        before = self.tree()
        self.assertEqual(self.write(), 0)
        self.assertEqual(self.tree(), before)

    def test_source_identity_model_and_current_completion_reject_atomically(self):
        def wrong_parent(events):
            events[0]["payload"]["parent_thread_id"] = base.REVIEWER_ID
            events[0]["payload"]["source"]["subagent"]["thread_spawn"]["parent_thread_id"] = base.REVIEWER_ID
        def wrong_role(events):
            events[0]["payload"]["agent_role"] = "python-worker"
            events[0]["payload"]["source"]["subagent"]["thread_spawn"]["agent_role"] = "python-worker"
        def wrong_model(events):
            events[2]["payload"]["model"] = "invented-model"
        def stale_completion(events):
            events.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "later-turn"}})
        for mutate in (wrong_parent, wrong_role, wrong_model, stale_completion, lambda events: events.clear()):
            with self.subTest(source=mutate.__name__):
                before = self.tree()
                with self.assertRaises(SystemExit):
                    self.write(mutate_source=mutate)
                self.assertEqual(self.tree(), before)

    def test_negative_terminal_decision_and_explicit_selector(self):
        self.request.update(status="blocked", decision="rejected", completion_turn_id=base.QA_ID + "-turn")
        self.assertEqual(self.write(), 0)
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        event = snapshot.timeline()[-1]
        self.assertEqual((event["stage"], event["status"]), ("blocked", "blocked"))
        self.assertEqual(event["completion_turn_id"], self.request["completion_turn_id"])
        before = self.tree()
        with self.assertRaises(SystemExit):
            self.write(identifier="terminal-rewrite")
        self.assertEqual(self.tree(), before)

    def test_concurrent_terminal_writers_publish_one_outcome(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.fixture.source.sessions[base.QA_ID] = base.session(base.QA_ID, "architect", self.request, 1)
        barrier = Barrier(2)
        def write(identifier):
            barrier.wait()
            try:
                journal_io.write_entry(self.fixture.run, "architecture-outcome", self.request, identifier=identifier)
                return "committed"
            except journal_io.JournalError:
                return "rejected"
        with patch.object(evidence, "CodexSessionSource", return_value=self.fixture.source), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["race-one", "race-two"]))
        self.assertEqual(sorted(results), ["committed", "rejected"])
        snapshot = journal_io.JournalSnapshot.open(self.fixture.run)
        self.assertEqual(len([row for row in snapshot.timeline() if row.get("stage") == "handoff"]), 1)

    def test_schema1_outcome_without_optional_gate_ids(self):
        fixture = FirstBindingRegressions()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize(schema=1)
        fixture.start()
        self.fixture = fixture
        del self.request["claim_evidence_ids"]
        del self.request["acceptance_criteria_ids"]
        self.request["decision"] = "rejected"
        self.assertEqual(self.write(), 0)

    def test_root_assignment_cannot_supply_architect_authority(self):
        fixture = FirstBindingRegressions()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize(schema=1)
        fixture.start(base.ROOT_ID, "--execution-mode", "role-lane")
        self.fixture = fixture
        before = self.tree()
        with self.assertRaises(SystemExit):
            self.write()
        self.assertEqual(self.tree(), before)

    def test_malformed_and_source_mismatch_are_atomic(self):
        import copy
        bad = []
        for field in ("sections", "decision", "lane_id"):
            value = copy.deepcopy(self.request)
            del value[field]
            bad.append(value)
        for sections in (self.request["sections"][:-1], self.request["sections"] + [self.request["sections"][0]],
                         [{**row, "summary": " "} for row in self.request["sections"]]):
            bad.append({**self.request, "sections": sections})
        for extra in ({"decision": "invented"}, {"decision": "approved", "status": "blocked"},
                      {"path": "handoffs/fake.md"}, {"lane_id": "unrelated-lane"}, {"completion_turn_id": "unrelated-turn"},
                      {"claim_evidence_ids": ["duplicate", "duplicate"]},
                      {"acceptance_criteria_ids": ["not an id"]}, {"claim_evidence_ids": []}):
            bad.append({**self.request, **extra})
        for index, request in enumerate(bad):
            with self.subTest(index=index):
                before = self.tree()
                with self.assertRaises(SystemExit):
                    self.write(request, identifier="bad-" + str(index))
                self.assertEqual(self.tree(), before)
        before = self.tree()
        with self.assertRaises(SystemExit):
            self.write(source_request={**self.request, "decision": "rejected"})
        self.assertEqual(self.tree(), before)
        for change in ({"summary": "Different source boundary"}, {"claim_evidence_ids": ["different-proof"]},
                       {"evidence": [{"path": "artifacts/source-only.txt"}]}):
            with self.subTest(source_change=change):
                with self.assertRaises(SystemExit):
                    self.write(source_request={**self.request, **change})
                self.assertEqual(self.tree(), before)


class FirstBindingRegressions(unittest.TestCase):
    """Public initializer/recorder transitions with explicitly synthetic sources."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.source = base.SyntheticSource()
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        self.source.sessions[base.QA_ID] = base.session(base.QA_ID, "architect", {}, 1)
        self.source.sessions[base.REVIEWER_ID] = base.session(base.REVIEWER_ID, "architect", {}, 2)

    def initialize(self, schema=2, mode="compact"):
        initializer = load("first_binding_initializer", "init-run.py")
        args = ["--repo", str(self.repo), "--slug", "binding", "--date", "2026-10-13", "--summary", "Binding regression"]
        if schema == 0:
            args += ["--mode", mode]
        else:
            args += ["--with-lanes"]
        if schema == 2:
            args += ["--architecture-gate", "--budget", "standard", "--architecture-context-json",
                     json.dumps({"product_context": ["saas-service"], "application_surface": ["backend-service"],
                                 "architecture_pattern": ["monolith"], "stack_runtime": ["go"],
                                 "risk_gates": ["migrations"], "verification_gates": ["unit", "integration"]}),
                     "--architecture-capabilities", "saas-platform-architecture,go-backend-service-architecture,modular-monolith-architecture"]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(initializer.main(args), 0)
        self.run = self.repo / ".agent-work/runs/2026-10-13-binding"
        self.lane_id = "architecture-contract"
        if schema == 1:
            self.declare("role-lane")
        self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification",
                    "--verification-json", json.dumps({**evidence.empty_verification(), "task_kind": "analysis", "root_thread_id": base.ROOT_ID}))

    def declare(self, mode, **extra):
        return journal_io.write_entry(self.run, "lane", {"id": self.lane_id, "summary": "Planned architecture",
            "status": "planned", "lane_type": "architecture", "role": "architect", "execution_mode": mode,
            "wave": 1, "critical": True, **extra}, identifier="declare-" + mode)

    def record(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return recorder.main(["--run-dir", str(self.run), "--status", "active", "--summary", "Binding regression", *args], session_source=self.source)

    def start(self, thread=base.QA_ID, *extra):
        return self.record("--role", "architect", "--lane-id", self.lane_id, "--stage", "spawned", "--codex-thread-id", thread, *extra)

    def snapshot(self):
        snapshot = journal_io.JournalSnapshot.open(self.run)
        return snapshot.revision, snapshot.documents, snapshot.receipts

    def test_generated_plan_binds_actual_child_and_replays(self):
        self.initialize()
        self.start(base.QA_ID, "--operation-id", "first-binding")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        lane = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]
        record = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
        self.assertEqual((lane["execution_mode"], lane["codex_thread_id"], lane["status"]), ("subagent", base.QA_ID, "active"))
        self.assertEqual(record["codex_thread_id"], base.QA_ID)
        self.assertIn('"execution_mode": "subagent"', snapshot.read_text(record["trace"]))
        before = self.snapshot()
        self.start(base.QA_ID, "--operation-id", "first-binding")
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(SystemExit):
            self.start(base.REVIEWER_ID, "--operation-id", "first-binding")
        self.assertEqual(self.snapshot(), before)

    def test_schema_one_and_compact_bind_child(self):
        for schema in (1, 0):
            with self.subTest(schema=schema):
                # Each public journey owns a fresh repository and journal.
                self.repo = Path(self.temp.name).resolve() / str(schema)
                self.repo.mkdir()
                self.initialize(schema)
                self.start()
                snapshot = journal_io.JournalSnapshot.open(self.run)
                self.assertEqual(snapshot.exists("lane-map.json"), schema == 1)
                self.assertEqual(json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]["codex_thread_id"], base.QA_ID)

    def test_generated_nonworker_roles_bind_without_configure(self):
        self.initialize()
        for index, (lane_id, role) in enumerate((("architecture-contract", "architect"),
                    ("verification-readiness-1", "qa-verifier"), ("qa-behavior", "qa-verifier"),
                    ("review-contract", "reviewer")), 1):
            with self.subTest(lane_id=lane_id):
                thread = f"00000000-0000-4000-8000-{index + 10:012d}"
                self.source.sessions[thread] = base.session(thread, role, {}, index)
                self.record("--role", role, "--lane-id", lane_id, "--stage", "spawned", "--codex-thread-id", thread)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        records = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(row["status"] == "active" for row in records))

    def test_planned_wave_critical_and_handoff_project_without_cli_flags(self):
        self.initialize()
        self.start(base.QA_ID, "--artifact", "context.md", "--operation-id", "planned-metadata")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        record = json.loads(snapshot.read_text("delegation-summary.json"))["subagents"][0]
        self.assertEqual((record["wave"], record["critical"], record["handoff"]),
                         (1, True, "handoffs/architecture-contract.md"))
        event = next(e for e in snapshot.timeline() if e.get("lane_id") == self.lane_id)
        index = next(e for e in json.loads(snapshot.read_text("artifacts.json")) if e.get("lane_id") == self.lane_id)
        for row in (event, index):
            self.assertEqual((row["wave"], row["critical"]), (1, True))
        before = self.snapshot()
        self.start(base.QA_ID, "--artifact", "context.md", "--operation-id", "planned-metadata")
        self.assertEqual(self.snapshot(), before)

    def test_coverage_matrix_is_writer_regenerated_from_lane_map(self):
        self.initialize()
        snapshot = journal_io.JournalSnapshot.open(self.run)
        self.assertFalse(snapshot.exists("checks/coverage-matrix.md"))
        before = self.snapshot()
        with self.assertRaisesRegex(journal_io.JournalError, "unknown journal request kind"):
            journal_io.write_entry(self.run, "coverage", {"summary": "Manual coverage table", "status": "active"},
                                   identifier="manual-coverage")
        self.assertEqual(self.snapshot(), before)
        self.start(base.QA_ID)
        snapshot = journal_io.JournalSnapshot.open(self.run)
        lane_map = json.loads(snapshot.read_text("lane-map.json"))
        matrix = snapshot.read_text("checks/coverage-matrix.md")
        self.assertIn("Generated from `lane-map.json`", matrix)
        for lane in lane_map["lanes"]:
            self.assertIn("| " + lane["id"] + " |", matrix)
        started = next(lane for lane in lane_map["lanes"] if lane["id"] == self.lane_id)
        self.assertEqual(started["status"], "active")
        row = next(line for line in matrix.splitlines() if line.startswith("| " + self.lane_id + " |"))
        self.assertIn("active", row)
        self.assertIn("handoffs/" + self.lane_id + ".md", row)

    def test_event_commit_hash_is_recorded_and_validated(self):
        self.initialize()
        commit_hash = "0123456789abcdef0123456789abcdef01234567"
        journal_io.write_entry(self.run, "event", {"summary": "Recorded the actual product commit", "status": "pass",
                                                   "stage": "commit", "role": "orchestrator", "commit_hash": commit_hash},
                               identifier="commit-event")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        event = json.loads(snapshot.read_text("timeline.jsonl").splitlines()[-1])
        self.assertEqual((event["stage"], event["commit_hash"]), ("commit", commit_hash))
        before = self.snapshot()
        for bad in ("abc123", "g" * 40, commit_hash + "0", commit_hash[:-1]):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(journal_io.JournalError, "commit_hash"):
                    journal_io.write_entry(self.run, "event", {"summary": "Invalid hash declaration", "status": "pass",
                                                               "stage": "commit", "role": "orchestrator", "commit_hash": bad},
                                           identifier="bad-commit-" + str(len(bad)))
        self.assertEqual(self.snapshot(), before)

    def test_plan_metadata_flags_are_assertions(self):
        self.initialize(1)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "wave"):
            self.start(base.QA_ID, "--wave", "99")
        self.assertEqual(self.snapshot(), before)
        self.start(base.QA_ID, "--wave", "1", "--critical", "--artifact", "context.md")
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "wave"):
            self.start(base.QA_ID, "--wave", "2")
        self.assertEqual(self.snapshot(), before)
        journal_io.write_entry(self.run, "lane", {"id": "noncritical", "summary": "Noncritical source assignment", "status": "planned",
            "lane_type": "architecture", "role": "architect", "execution_mode": "role-lane", "wave": 2, "critical": False}, identifier="noncritical-declaration")
        self.lane_id = "noncritical"
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "critical"):
            self.start(base.REVIEWER_ID, "--critical")
        self.assertEqual(self.snapshot(), before)
        self.start(base.REVIEWER_ID, "--artifact", "context.md")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        event = next(e for e in snapshot.timeline() if e.get("lane_id") == "noncritical")
        self.assertEqual((event["wave"], event["critical"]), (2, False))
        index = next(e for e in json.loads(snapshot.read_text("artifacts.json")) if e.get("path") == "context.md")
        self.assertEqual((index["lane_id"], index["wave"], index["critical"]), ("noncritical", 2, False))

    def test_schema_one_subagent_plan_binds_allowed_root(self):
        self.initialize(1)
        self.declare("subagent")
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        self.start(base.ROOT_ID)
        lane = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("lane-map.json"))["lanes"][0]
        self.assertEqual(lane["execution_mode"], "role-lane")

    def test_root_first_binding_without_uuid_still_requires_source(self):
        self.initialize(1)
        before = self.snapshot()
        del self.source.sessions[base.ROOT_ID]
        with self.assertRaisesRegex(SystemExit, "session missing"):
            self.record("--role", "architect", "--lane-id", self.lane_id,
                        "--execution-mode", "role-lane", "--stage", "spawned")
        self.assertEqual(self.snapshot(), before)
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        self.record("--role", "architect", "--lane-id", self.lane_id,
                    "--execution-mode", "role-lane", "--stage", "spawned", "--operation-id", "root-first-binding")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        record = json.loads(snapshot.read_text("delegation-summary.json"))["role_lanes"][0]
        self.assertEqual(record["codex_thread_id"], base.ROOT_ID)
        lane = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]
        self.assertEqual(lane["codex_thread_id"], base.ROOT_ID)
        before = self.snapshot()
        self.record("--role", "architect", "--lane-id", self.lane_id,
                    "--execution-mode", "role-lane", "--stage", "spawned", "--operation-id", "root-first-binding")
        self.assertEqual(self.snapshot(), before)

    def test_planned_declaration_refines_mode_without_spawn(self):
        self.initialize()
        self.declare("subagent")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        self.assertFalse(json.loads(snapshot.read_text("delegation-summary.json"))["subagents_used"])
        self.assertFalse(any(e.get("lane_id") == self.lane_id for e in snapshot.timeline()))
        self.start()

    def test_registered_mode_identity_and_scope_are_immutable(self):
        self.initialize()
        self.start(base.QA_ID, "--stable-agent-name", "Architect", "--stable-agent-slug", "architecture-owner")
        self.record("--role", "architect", "--lane-id", self.lane_id, "--stage", "checks")
        before = self.snapshot()
        for extra in (("--execution-mode", "role-lane"), ("--role", "reviewer"),
                      ("--stable-agent-slug", "replacement"), ("--stable-agent-name", "Replacement"),
                      ("--codex-thread-id", base.REVIEWER_ID)):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.start(base.QA_ID, *extra)
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises(journal_io.JournalError):
            self.declare("subagent", allowed_paths=["new/**"])
        self.assertEqual(self.snapshot(), before)

    def test_source_negatives_leave_no_partial_publication(self):
        from copy import deepcopy
        self.initialize()
        before = self.snapshot()
        for invalid in ("missing", "parent", "role", "uuid", "child-as-root"):
            with self.subTest(invalid=invalid):
                self.source.sessions[base.QA_ID] = base.session(base.QA_ID, "architect", {}, 1)
                thread = base.QA_ID
                extra = []
                if invalid == "missing":
                    del self.source.sessions[thread]
                elif invalid in {"parent", "role"}:
                    meta = self.source.sessions[thread][0]["payload"]
                    field = "parent_thread_id" if invalid == "parent" else "agent_role"
                    value = base.REVIEWER_ID if invalid == "parent" else "reviewer"
                    meta[field] = value
                    meta["source"]["subagent"]["thread_spawn"][field] = value
                elif invalid == "uuid":
                    thread = "not-a-uuid"
                    self.source.sessions[thread] = deepcopy(self.source.sessions[base.QA_ID])
                else:
                    extra = ["--execution-mode", "role-lane"]
                with self.assertRaises(SystemExit):
                    self.start(thread, *extra)
                self.assertEqual(self.snapshot(), before)

    def test_root_mode_is_source_inferred_and_registered_mode_is_reused(self):
        self.initialize()
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        self.start(base.ROOT_ID)
        self.record("--role", "architect", "--lane-id", self.lane_id, "--stage", "checks")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        lane = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]
        self.assertEqual(lane["execution_mode"], "role-lane")
        self.assertEqual(len(json.loads(snapshot.read_text("delegation-summary.json"))["role_lanes"]), 1)

    def test_independent_architecture_policy_rejects_root(self):
        self.initialize()
        snapshot = journal_io.JournalSnapshot.open(self.run)
        lanes = json.loads(snapshot.read_text("lane-map.json"))
        lanes["architecture_contract_independent"] = True
        journal_io.transact(self.run, "independent-policy", {}, lambda current: ({"lane-map.json": json.dumps(lanes)}, {}))
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "requires subagent"):
            self.start(base.ROOT_ID)
        self.assertEqual(self.snapshot(), before)

    def test_orphan_history_and_planned_registration_are_not_virgin(self):
        from dataclasses import replace
        from types import MappingProxyType
        self.initialize()
        snapshot = journal_io.JournalSnapshot.open(self.run)
        event = {"lane_id": self.lane_id, "role": "architect", "execution_mode": "subagent",
                 "stage": "spawned", "status": "active", "codex_thread_id": base.QA_ID}
        # Malformed/history snapshots are input doubles, never published repairs.
        for path in ("timeline.jsonl", "agents/architect/trace.jsonl"):
            with self.subTest(path=path):
                candidate = replace(snapshot, documents=MappingProxyType({**snapshot.documents,
                    path: snapshot.documents.get(path, b"") + (json.dumps(event) + "\n").encode()}))
                with self.assertRaisesRegex(journal_io.JournalError, "integrity"):
                    journal_io.first_binding_allowed(candidate, self.lane_id)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        summary["role_lanes"].append({"lane_id": self.lane_id, "role": "architect", "status": "planned"})
        candidate = replace(snapshot, documents=MappingProxyType({**snapshot.documents, "delegation-summary.json": json.dumps(summary).encode()}))
        self.assertFalse(journal_io.first_binding_allowed(candidate, self.lane_id))
        archived = candidate
        candidate = replace(snapshot, archives=MappingProxyType({"old-binding": {}}))
        with patch.object(journal_io.JournalSnapshot, "archive", return_value=archived):
            self.assertFalse(journal_io.first_binding_allowed(candidate, self.lane_id))

    def test_two_first_binding_writers_commit_one_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        self.initialize()
        barrier = Barrier(2)
        writer = journal_io.write_entry
        def simultaneous(*args, **kwargs):
            if args[1] == "event":
                barrier.wait(timeout=10)
            return writer(*args, **kwargs)
        def attempt(thread):
            try:
                self.start(thread, "--operation-id", "race-" + thread)
                return True
            except SystemExit:
                return False
        with patch.object(journal_io, "write_entry", simultaneous), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [base.QA_ID, base.REVIEWER_ID]))
        self.assertEqual(sorted(outcomes), [False, True])
        snapshot = journal_io.JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        lane = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]
        self.assertEqual(len(summary["subagents"]), 1)
        winner = summary["subagents"][0]["codex_thread_id"]
        self.assertEqual(lane["codex_thread_id"], winner)
        events = [e for e in snapshot.timeline() if e.get("lane_id") == self.lane_id]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["codex_thread_id"], winner)
        self.assertEqual(len([key for key in snapshot.receipts if key.startswith("race-")]), 1)


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
        base.initialize_open_fixture(self.run)
        from task_workspace import prepare
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        temporary = base.tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        prepare(self.run, self.root, Path(temporary.name).resolve() / "bundle")
        verification = json.loads(journal_io.JournalSnapshot.open(self.run).read_text("delegation-summary.json"))["verification"]
        verification.update(result_files=[], result_hash=None, qa=None, reviewer=None)
        self.record("--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "verification", "--status", "active",
                    "--summary", "Prepared fresh change admission", "--verification-json", json.dumps(verification))
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
        self.source.sessions[base.ROOT_ID] = [{"type": "session_meta", "payload": {"id": base.ROOT_ID}}]
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
        # Intentional oracle copy of journal_structure.EVENT_STATUS_RULES: the test
        # must fail if the canonical contract changes, not import it blindly.
        allowed = {"spawned": {"active"}, "handoff": {"pass", "pass-with-risks"},
                   "blocked": {"blocked"}, "fail": {"fail"}, "timed-out": {"timed-out"}}
        base.workspace_pack(self)
        for mode in ("subagent", "role-lane"):
            for stage, statuses in allowed.items():
                for status in ("active", "pass", "pass-with-risks", "blocked", "fail", "done", "timed-out"):
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
        # The fresh native assignment defaults to a typed product review;
        # preserve the untagged terminal fixture used by the rejection above.
        answer = json.loads(events[-1]["payload"]["last_agent_message"])
        answer["review_subject"] = {"kind": "product-result"}
        events[-1]["payload"]["last_agent_message"] = json.dumps(answer)
        events[-2]["payload"]["content"][0]["text"] = json.dumps(answer)
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
        base.workspace_pack(self)
        self.summary["verification"]["result_hash"] = evidence.result_hash(self.run, self.summary["verification"])
        base.write_json(self.run / "delegation-summary.json", self.summary)
        self.record("--role", "qa-verifier", "--lane-id", "race-qa", "--codex-thread-id", base.QA_ID,
                    "--stage", "spawned", "--status", "active", "--summary", "Assigned race check")
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
        boundary = load("workspace_boundary_recorder", "record-agent-trace.py")
        snapshot = journal_io.JournalSnapshot.open(self.run)
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        author = {"lane_id": "worker", "role": "python-worker", "trace": "agents/worker/trace.jsonl", "handoff": "handoffs/worker.md"}
        summary["role_lanes"].append(author); summary["role_lanes_used"] = True
        event = base.trace_event({**author, "codex_thread_id": base.ROOT_ID}, "spawned")
        event.update(execution_mode="role-lane", stage="checks", timestamp=journal_io.now_iso())
        paths = self.summary["verification"]["result_files"]
        handoff = ("Own authored paths:\n" + "\n".join(paths) + "\n").encode()
        proof = json.dumps({"created_files": [{"path": name, "bytes": (Path(workspace["working_root"]) / name).read_text()} for name in paths]}).encode()
        journal_io.transact(self.run, "fixture-worker-authorship", {}, lambda current: ({
            "delegation-summary.json": json.dumps(summary), author["trace"]: json.dumps(event) + "\n", author["handoff"]: handoff,
            "checks/worker-created-files.json": proof, "timeline.jsonl": current.read_text("timeline.jsonl") + json.dumps(event) + "\n"}, {}))
        from types import SimpleNamespace
        args = SimpleNamespace(sections_file=None, summary=None, status="pass", lane_id="worker", role="python-worker",
                               reason=None, unverified=[], context_classification_json=None, readiness_gates_file=None,
                               authored_path=list(paths), coauthor=[], operation_id="wc04-boundary")
        answer = boundary.prepare_conclusion(self.run, args, [author["handoff"], "checks/worker-created-files.json"],
                                             evidence.CodexSessionSource())
        path = self.run / answer["boundary"]
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

    def test_writers_use_one_injected_utc_clock(self):
        init = load("journal_init", "init-run.py")
        stamp = "2026-09-13T12:34:56.123456Z"
        for zone in ("UTC", "Pacific/Honolulu"):
            with self.subTest(zone=zone), patch.dict(os.environ, {"TZ": zone}):
                with patch.object(journal_io, "datetime") as clock:
                    clock.now.return_value = datetime(2026, 9, 13, 12, 34, 56, 123456, tzinfo=timezone.utc)
                    self.assertEqual(journal_io.now_iso(), stamp)
                for module in (init, recorder):
                    self.assertIs(module.now_iso, journal_io.now_iso)
                slug = zone.replace("/", "-").lower()
                with patch.object(init, "now_iso", return_value=stamp), patch.object(init, "datetime") as local_clock, redirect_stdout(StringIO()) as output:
                    local_clock.now.return_value.astimezone.return_value.strftime.return_value = "2026-09-12"
                    init.main(["--repo", str(self.root), "--slug", slug, "--mode", "compact"])
                run = Path(output.getvalue().strip())
                self.assertEqual(run.name, "2026-09-12-" + slug)
                with patch.object(journal_io, "now_iso", return_value=stamp):
                    journal_io.write_entry(run, "event", {"stage": "checks", "role": "orchestrator", "status": "pass",
                                                          "summary": "Checked"}, identifier="clock-event-" + slug)
                summary = json.loads(journal_io.JournalSnapshot.open(run).read_text("delegation-summary.json"))
                summary["role_lanes"] = [{"lane_id": "worker", "role": "python-worker", "status": "planned",
                                          "trace": "agents/python-worker/trace.jsonl"}]
                summary["role_lanes_used"] = True
                lane_map = {"lanes": [{"id": "worker", "role": "python-worker", "execution_mode": "role-lane",
                                       "handoff": "handoffs/worker.md"}]}
                journal_io.transact(run, "fixture-lanes", {}, lambda snapshot: (
                    {"lane-map.json": json.dumps(lane_map).encode(),
                     "delegation-summary.json": json.dumps(summary).encode()}, {}))
                with patch.object(journal_io, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    recorder.main(["--run-dir", str(run), "--role", "python-worker", "--execution-mode", "role-lane",
                                   "--lane-id", "worker", "--stage", "checks", "--status", "planned", "--summary", "Queued work"])
                with patch.object(journal_io, "now_iso", return_value=stamp), redirect_stdout(StringIO()):
                    recorder.main(["--run-dir", str(run), "--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", "Checked"])
                snapshot = journal_io.JournalSnapshot.open(run)
                events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
                state = json.loads(snapshot.read_text("lane-map.json"))["lanes"][0]["handoff_state"]
                self.assertEqual([e["timestamp"] for e in events], [stamp] * 4)
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
