#!/usr/bin/env python3
"""Lifecycle regressions use temporary SQLite journals and synthetic sessions."""
import importlib.util
import json
import marshal
import struct
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace

from journal_io import JournalSnapshot, JournalError, initialize_journal, transact, encode_json
from journal_lifecycle import finalize, load_validator

SCRIPTS = Path(__file__).resolve().parent
CONTROLS = json.loads((SCRIPTS.parent / "testdata/golden-traces/lifecycle-obligation-controls.json").read_text())
spec = importlib.util.spec_from_file_location("lifecycle_fixtures", SCRIPTS / "test-verification-evidence.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / "run"
        self.run.mkdir()
        (self.run / "run.md").write_text("# Synthetic compact run\n")
        (self.run / "checks.md").write_text("# Checks\n\nSynthetic assertions.\n")
        self.summary, self.source = fixtures.acceptance_pack(self.run)
        self.final = (self.run / "final.md").read_bytes()
        for record in self.summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        fixtures.write_json(self.run / "delegation-summary.json", self.summary)
        timeline = (self.run / "timeline.jsonl").read_bytes().splitlines(keepends=True)
        (self.run / "timeline.jsonl").write_bytes(b"".join(timeline[:-1]))
        documents = {p.relative_to(self.run).as_posix(): p.read_bytes() if p.is_file() else None
                     for p in self.run.rglob("*")}
        initialize_journal(self.run, documents, source_root=self.run.parent, result_contract_version=1)

    def close(self, **kw):
        snapshot = JournalSnapshot.open(self.run)
        args = dict(expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                    expected_generation=1, identifier="finish", final_bytes=self.final,
                    verdict="ship", session_source=self.source)
        args.update(kw)
        return finalize(self.run, **args)

    def assert_unchanged(self, before):
        current = JournalSnapshot.open(self.run)
        self.assertEqual(current.revision, before.revision)
        self.assertEqual(dict(current.documents), dict(before.documents))
        self.assertEqual(dict(current.receipts), dict(before.receipts))


    def test_flat_domain_and_cli_refusal_preserves_complete_tree(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as temporary:
            root = Path(temporary).resolve()
            self.assertEqual(root, Path(temporary))
            def tree(run):
                return {p.relative_to(run).as_posix():
                        ('link', str(p.readlink())) if p.is_symlink() else
                        ('directory', None) if p.is_dir() else ('file', p.read_bytes())
                        for p in run.rglob('*')}
            for route in ('finalize', 'resolve-obligation'):
                with self.subTest(route=route):
                    run = root / route
                    run.mkdir()
                    (run / 'run.md').write_text('# Flat legacy run\n')
                    (run / 'empty').mkdir()
                    (run / 'binary').write_bytes(b'\x00\xff')
                    (run / 'timeline.jsonl').write_text('')
                    (run / 'final.md').write_text('Verdict: blocked\n')
                    snapshot = JournalSnapshot.open(run)
                    self.assertFalse(snapshot.durable)
                    before = tree(run)
                    identifier = 'flat-' + route
                    common = dict(expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                                  expected_generation=snapshot.generation, identifier=identifier)
                    command = [sys.executable, '-B', str(SCRIPTS / 'journal.py'), '--run-dir', str(run), route]
                    flags = ['--expected-revision', str(snapshot.revision), '--operation-id', identifier]
                    from journal_lifecycle import resolve_obligation
                    if route == 'finalize':
                        capture = root / 'final-input.md'
                        capture.write_bytes(b'Verdict: blocked\n')
                        flags += ['--expected-run-uuid', snapshot.run_uuid, '--expected-generation',
                                  str(snapshot.generation), '--final-file', str(capture), '--verdict', 'blocked']
                        invoke = lambda: finalize(run, **common, final_bytes=capture.read_bytes(), verdict='blocked')
                        message = 'flat legacy requires explicit import before finalization'
                    else:
                        flags += ['--lane-id', 'old', '--replacement', 'new', '--reason', 'Same duty replacement']
                        invoke = lambda: resolve_obligation(run, lane_id='old', replacement='new',
                            reason='Same duty replacement', expected_revision=snapshot.revision, identifier=identifier)
                        message = 'flat legacy requires explicit import before obligation resolution' 
                    with self.assertRaises(JournalError) as failure:
                        invoke()
                    self.assertEqual(tree(run), before)
                    self.assertFalse((run / '.journal').exists())
                    self.assertIn(message, str(failure.exception))
                    result = subprocess.run(command + flags, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertEqual(tree(run), before)
                    self.assertFalse((run / '.journal').exists())

    def test_failed_full_validation_leaves_open_then_repair_closes(self):
        before = JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(JournalError, "Delegation Trace"):
            self.close(identifier="invalid-final", final_bytes=self.final.split(b"## Delegation Trace")[0])
        self.assert_unchanged(before)
        self.close()
        self.assertTrue(JournalSnapshot.open(self.run).closed)

    def test_validator_loads_full_captured_source_and_rejects_invalid_bytes(self):
        raw = (SCRIPTS / "validate-run.py").read_bytes()
        module = load_validator(raw)
        self.assertEqual(module.validate_run.__code__.co_filename, str(SCRIPTS / "validate-run.py"))
        self.assertEqual(module.__file__, str(SCRIPTS / "validate-run.py"))
        self.assertEqual(module.POSITIVE_FINAL_VERDICTS, {"ship", "pass-with-risks"})
        with self.assertRaises(SyntaxError):
            load_validator(b"invalid captured source ?")

    def test_validator_ignores_matching_timestamp_bytecode_cache(self):
        import journal_lifecycle
        runtime = Path(self.temp.name).resolve() / "cached-runtime"
        runtime.mkdir()
        entrypoint = runtime / "validate-run.py"
        raw = (SCRIPTS / "validate-run.py").read_bytes()
        entrypoint.write_bytes(raw)
        cache = Path(importlib.util.cache_from_source(str(entrypoint)))
        cache.parent.mkdir()
        header = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, int(entrypoint.stat().st_mtime), len(raw))
        cached = header + marshal.dumps(compile("BYTECODE_ONLY = True", str(entrypoint), "exec"))
        cache.write_bytes(cached)
        with patch.object(journal_lifecycle, "__file__", str(runtime / "journal_lifecycle.py")):
            module = load_validator(raw)
        self.assertTrue(callable(module.validate_run))
        self.assertFalse(hasattr(module, "BYTECODE_ONLY"))
        self.assertEqual(cache.read_bytes(), cached)

    def test_finalize_executes_captured_source_and_receipt_hash_after_file_change(self):
        import journal_lifecycle
        from journal_io import digest
        scripts = Path(self.temp.name).resolve() / "runtime"
        scripts.mkdir()
        for name in ("validate-run.py", "verification_evidence.py", "journal_io.py",
                     "journal_lifecycle.py", "validation_inputs.py", "task_workspace.py",
                     "architecture_capabilities.py", "agent_config.py"):
            shutil.copyfile(SCRIPTS / name, scripts / name)
        entrypoint = scripts / "validate-run.py"
        raw = entrypoint.read_bytes()
        capture = journal_lifecycle.capture_validation
        def capture_then_change(*args, **kwargs):
            result = capture(*args, **kwargs)
            # Invalid text cannot execute: successful validation must use the captured original.
            entrypoint.write_bytes(b"invalid later source ?")
            return result
        with patch.object(journal_lifecycle, "__file__", str(scripts / "journal_lifecycle.py")), \
                patch.object(journal_lifecycle, "capture_validation", side_effect=capture_then_change):
            before = JournalSnapshot.open(self.run)
            with self.assertRaisesRegex(JournalError, "Delegation Trace"):
                self.close(identifier="invalid-final", final_bytes=self.final.split(b"## Delegation Trace")[0])
            self.assert_unchanged(before)
            entrypoint.write_bytes(raw)
            result = self.close()
        self.assertTrue(JournalSnapshot.open(self.run).closed)
        self.assertEqual(result["validator_sha256"], digest(raw))
        self.assertNotEqual(result["validator_sha256"], digest(entrypoint.read_bytes()))
        self.assertEqual(result["validator_version"], 2)
        self.assertFalse((scripts / "__pycache__").exists())

    def test_successful_render_does_not_authorize_invalid_delegation(self):
        from contextlib import redirect_stdout
        from io import StringIO
        recorder = fixtures.module("lifecycle_recorder", "record-agent-trace.py")
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(recorder.main(["--run-dir", str(self.run), "--render-final"],
                                           session_source=self.source), 0)
        self.assertIn("rendered revision", output.getvalue())
        self.reject_required_assignment("python-worker", "fail")

    def test_current_negative_and_done_cannot_ship(self):
        for status in CONTROLS["current_statuses"]:
            with self.subTest(status=status):
                summary = json.loads(json.dumps(self.summary))
                summary["subagents"][0]["status"] = status
                before = JournalSnapshot.open(self.run)
                proposed = replace(before, documents={**before.documents,
                    "delegation-summary.json": json.dumps(summary).encode()})
                from verification_evidence import evaluate_obligations
                self.assertTrue(evaluate_obligations(proposed, summary)[1])

    def reject_required_assignment(self, role, status):
        record = {"lane_id": "required-negative", "role": role, "status": status,
                  "codex_thread_id": "00000000-0000-4000-8000-000000000005",
                  "trace": "agents/" + role + "/trace.jsonl", "handoff": "handoffs/required-negative.md",
                  "obligation": {"id": "required-negative", "required": True, "state": "current"}}
        self.summary["subagents"].append(record)
        events = [fixtures.trace_event(record, "spawned"),
                  {**fixtures.trace_event(record, "handoff"), "status": status}]
        trace = "".join(json.dumps(e) + "\n" for e in events).encode()
        transact(self.run, "required-negative", {}, lambda s: ({
            "delegation-summary.json": json.dumps(self.summary).encode(),
            record["trace"]: s.documents.get(record["trace"], b"") + trace,
            "timeline.jsonl": s.read_bytes("timeline.jsonl") + trace,
            record["handoff"]: b"Unresolved required assignment.\n"}, {}))
        self.final = self.final.split(b"## Delegation Trace")[0] + fixtures.delegation_section(self.summary).encode()
        before = JournalSnapshot.open(self.run)
        with self.assertRaisesRegex(JournalError, "current obligation not accepted"):
            self.close()
        self.assert_unchanged(before)

    def test_required_worker_fail_blocks_finalize(self):
        self.reject_required_assignment("python-worker", "fail")

    def test_required_qa_blocked_blocks_finalize(self):
        self.reject_required_assignment("qa-verifier", "blocked")

    def test_required_reviewer_done_blocks_finalize(self):
        self.reject_required_assignment("reviewer", "done")

    def test_required_worker_active_blocks_finalize(self):
        self.reject_required_assignment("python-worker", "active")

    def test_uncovered_noncritical_planned_lane_blocks_acceptance(self):
        snapshot = JournalSnapshot.open(self.run)
        lane = {"id": "uncovered", "critical": False, "status": "planned", "role": "python-worker",
                "execution_mode": "role-lane"}
        _, errors = load_validator((SCRIPTS / "validate-run.py").read_bytes()).validate_delegation_summary(
            self.run, [lane], {"uncovered": lane}, "ship", required=True, snapshot=snapshot)
        self.assertTrue(any("uncovered lane obligation" in error for error in errors), errors)

    def test_terminal_handoff_state_cannot_turn_failure_into_completion(self):
        lane = {"id": "failed-worker", "status": "fail", "handoff": "handoffs/failed.md"}
        transact(self.run, "failed-lane", {}, lambda s: ({"lane-map.json": json.dumps({"lanes": [lane]})}, {}))
        before = JournalSnapshot.open(self.run)
        command = [sys.executable, "-B", str(SCRIPTS / "record-handoff-state.py"), "--run-dir", str(self.run),
                   "--lane-id", lane["id"], "--status", "completed"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires handoff state failed", result.stderr)
        self.assert_unchanged(before)

    def test_new_role_lane_records_obligation_and_cannot_hide_active_work(self):
        command = [sys.executable, "-B", str(SCRIPTS / "record-agent-trace.py"), "--run-dir", str(self.run),
                   "--role", "python-worker", "--execution-mode", "role-lane", "--lane-id", "local-worker",
                   "--obligation-id", "implementation", "--stage", "implementation", "--status", "active",
                   "--summary", "Local implementation assignment"]
        # Use an analysis-only journal so this public recorder needs no provider fixture injection.
        run = self.run.parent / "analysis-run"
        summary = {"version": 1, "notes": "Synthetic analysis", "subagents_used": False, "role_lanes_used": False,
                   "subagents": [], "role_lanes": [], "verification": {**fixtures.empty_verification(),
                       "task_kind": "analysis", "root_thread_id": fixtures.ROOT_ID}}
        initialize_journal(run, {"delegation-summary.json": json.dumps(summary).encode()}, source_root=run.parent)
        command[command.index("--run-dir") + 1] = str(run)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = JournalSnapshot.open(run)
        summary = json.loads(snapshot.read_bytes("delegation-summary.json"))
        self.assertTrue(summary["role_lanes_used"])
        self.assertEqual(summary["role_lanes"][0]["obligation"],
                         {"id": "implementation", "required": True, "state": "current"})
        from verification_evidence import evaluate_obligations
        self.assertTrue(any("not accepted" in e for e in evaluate_obligations(snapshot, summary)[1]))

    def test_missing_cyclic_and_wrong_obligation_resolution(self):
        from verification_evidence import evaluate_obligations
        for replacement, expected in CONTROLS["replacement_controls"]:
            summary = json.loads(json.dumps(self.summary))
            old = summary["subagents"][0]
            old["obligation"].update(state="resolved", resolution={
                "original_lane": old["lane_id"], "replacement": replacement, "reason": "Synthetic control",
                "evidence": [{"path": old["handoff"], "sha256": old["handoff_sha256"]}]})
            _, errors = evaluate_obligations(JournalSnapshot.open(self.run), summary)
            self.assertTrue(any(expected in error for error in errors), errors)

    def test_cannot_relabel_an_existing_obligation_or_make_it_optional(self):
        before = JournalSnapshot.open(self.run)
        for field, value in (("id", "different-responsibility"), ("required", False)):
            summary = json.loads(json.dumps(self.summary))
            summary["subagents"][0]["obligation"][field] = value
            with self.assertRaisesRegex(JournalError, "obligation identity"):
                transact(self.run, "relabel-" + field, {}, lambda s: (
                    {"delegation-summary.json": json.dumps(summary)}, {}))
            self.assert_unchanged(before)

    def test_full_mode_preserves_resolved_history(self):
        documents = {name: b"# Full fixture\n\nVerdict: ship\n" for name in
                     ("manifest.md", "route.md", "definition-of-done.md", "decisions.md")}
        documents.update({"artifacts": None, "checks": None, "artifacts.json": b"[]\n",
                          "checks/smoke.md": b"# Synthetic smoke\n"})
        transact(self.run, "full-mode", {}, lambda s: (documents, {}))
        self.test_explicit_resolution_preserves_historical_failure()

    def test_locked_validator_performs_no_external_reads(self):
        locked = [False]
        observed = []
        old_profile = sys.getprofile()
        def profile(frame, event, arg):
            if event == "call" and frame.f_globals.get("__name__") == "pathlib" and frame.f_code.co_name in {
                    "resolve", "stat", "lstat", "exists", "is_file", "is_dir", "is_symlink"}:
                observed.append(("pathlib", frame.f_code.co_name))
            if event == "c_call" and getattr(arg, "__module__", "") == "posix" and getattr(arg, "__name__", "") in {
                    "stat", "lstat", "readlink", "open", "listdir", "scandir"}:
                observed.append(("posix", arg.__name__))
        def audit(event, args):
            if locked[0] and event in {"open", "os.listdir", "os.scandir", "subprocess.Popen"}:
                observed.append((event, repr(args)))
                raise AssertionError("external read inside locked validator: " + event)
        sys.addaudithook(audit)
        def barrier(point):
            locked[0] = point == "locked"
            sys.setprofile(profile if locked[0] else old_profile)
        try:
            self.close(barrier=barrier)
        finally:
            locked[0] = False
            sys.setprofile(old_profile)
        self.assertEqual(observed, [])

    def test_candidate_change_after_capture_rejects_live_validation(self):
        def change(point):
            if point == "captured":
                (self.run.parent / "result.txt").write_bytes(b"Changed after capture")
        self.close(barrier=change)
        errors = load_validator((SCRIPTS / "validate-run.py").read_bytes()).validate_run(self.run, session_source=self.source)
        self.assertTrue(any("current result" in error or "reviewed_result_hash is stale" in error for error in errors), errors)

    def crash_finalize(self, phase):
        child = os.fork()
        if child == 0:
            def kill(point):
                if point == phase:
                    os.kill(os.getpid(), signal.SIGKILL)
            try:
                self.close(barrier=kill)
            except BaseException:
                os._exit(9)
            os._exit(8)
        _, status = os.waitpid(child, 0)
        self.assertTrue(os.WIFSIGNALED(status))
        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)

    def test_sigkill_before_commit_preserves_open_journal(self):
        before = JournalSnapshot.open(self.run)
        self.crash_finalize("before-commit")
        self.assert_unchanged(before)
        self.close()

    def test_sigkill_after_commit_replays_receipt(self):
        before = JournalSnapshot.open(self.run)
        self.crash_finalize("after-commit")
        closed = JournalSnapshot.open(self.run)
        self.assertTrue(closed.closed)
        self.close(expected_revision=before.revision)
        self.assert_unchanged(closed)

    def test_public_resolve_rejects_missing_replacement_without_writes(self):
        before = JournalSnapshot.open(self.run)
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
            "resolve-obligation", "--lane-id", "evidence-qa", "--replacement", "missing",
            "--reason", "Synthetic missing replacement", "--expected-revision", str(before.revision),
            "--operation-id", "resolution"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assignment missing", result.stderr)
        self.assert_unchanged(before)

    def test_workspace_capture_and_late_candidate_change(self):
        from types import SimpleNamespace
        from task_workspace import delivery
        root = self.run.parent / "workspace-case"
        root.mkdir()
        run = root / "run"
        run.mkdir()
        (run / "run.md").write_text("# Synthetic compact run\n")
        (run / "checks.md").write_text("# Synthetic checks\n")
        summary, source = fixtures.acceptance_pack(run)
        lines = (run / "timeline.jsonl").read_bytes().splitlines(keepends=True)
        (run / "timeline.jsonl").write_bytes(b"".join(lines[:-1]))
        case = SimpleNamespace(root=root, run=run, summary=summary, source=source, addCleanup=self.addCleanup)
        workspace = fixtures.workspace_pack(case)
        for record in summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        transact(run, "obligations", {}, lambda s: ({"delegation-summary.json": json.dumps(summary)}, {}))
        snapshot = JournalSnapshot.open(run)
        from task_workspace import registered_workspace
        sealed = registered_workspace(snapshot)["seal"]
        locked = [False]
        filesystem_calls = []
        old_profile = sys.getprofile()
        def profile(frame, event, arg):
            if event == "call" and frame.f_globals.get("__name__") == "pathlib" and frame.f_code.co_name in {
                    "resolve", "stat", "lstat", "exists", "is_file", "is_dir", "is_symlink"}:
                filesystem_calls.append(("pathlib", frame.f_code.co_name))
            if event == "c_call" and getattr(arg, "__module__", "") == "posix" and getattr(arg, "__name__", "") in {
                    "stat", "lstat", "readlink", "open", "listdir", "scandir"}:
                filesystem_calls.append(("posix", arg.__name__))
        def audit(event, args):
            if locked[0] and event in {"open", "os.listdir", "os.scandir", "subprocess.Popen"}:
                raise AssertionError("workspace external I/O under SQL: " + event)
        sys.addaudithook(audit)
        def barrier(point):
            locked[0] = point == "locked"
            sys.setprofile(profile if locked[0] else old_profile)
            if point == "captured":
                (Path(sealed["candidate_root"]) / "unexpected").write_bytes(b"late candidate change")
        try:
            result = finalize(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                expected_generation=1, identifier="workspace-final", final_bytes=snapshot.read_bytes("final.md"),
                verdict="ship", session_source=source, barrier=barrier)
        finally:
            locked[0] = False
            sys.setprofile(old_profile)
        self.assertEqual(filesystem_calls, [])
        keys = result["input_hashes"]
        self.assertIn("tree:" + workspace["baseline_root"], keys)
        self.assertIn("tree:" + sealed["candidate_root"], keys)
        self.assertIn("owned:" + workspace["bundle"] + "/owner.json", keys)
        with self.assertRaisesRegex(JournalError, "candidate changed"):
            delivery(run, session_source=source)
        (Path(sealed["candidate_root"]) / "unexpected").unlink()
        import journal_lifecycle
        import task_workspace
        from journal_io import digest
        runtime = root / "runtime"
        runtime.mkdir()
        entrypoint = runtime / "validate-run.py"
        raw = (SCRIPTS / "validate-run.py").read_bytes()
        entrypoint.write_bytes(raw)
        capture = journal_lifecycle.capture_validation
        def capture_then_change(*args, **kwargs):
            captured = capture(*args, **kwargs)
            entrypoint.write_bytes(b"invalid later source ?")
            return captured
        with patch.object(journal_lifecycle, "__file__", str(runtime / "journal_lifecycle.py")), \
                patch.object(task_workspace, "__file__", str(runtime / "task_workspace.py")), \
                patch.object(journal_lifecycle, "capture_validation", side_effect=capture_then_change):
            delivered = delivery(run, session_source=source)
        self.assertEqual(delivered["validator_sha256"], digest(raw))
        self.assertNotEqual(delivered["validator_sha256"], digest(entrypoint.read_bytes()))
        self.assertFalse((runtime / "__pycache__").exists())

    def test_public_resolution_and_finalize(self):
        self.test_explicit_resolution_preserves_historical_failure(via_cli=True)

    def test_explicit_resolution_preserves_historical_failure(self, via_cli=False):
        old = {"lane_id": "old-qa", "role": "qa-verifier", "codex_thread_id": "00000000-0000-4000-8000-000000000004",
               "trace": "agents/qa-verifier/trace.jsonl", "handoff": "handoffs/old.md", "status": "blocked",
               "obligation": {"id": "qa-verifier", "required": True, "state": "resolved",
                   "resolution": {"original_lane": "old-qa", "replacement": "evidence-qa",
                       "reason": "A new QA assignment checked the corrected result.",
                       "evidence": [{"path": self.summary["subagents"][0]["handoff"],
                                     "sha256": self.summary["subagents"][0]["handoff_sha256"]}]}}}
        if via_cli:
            old["obligation"].pop("resolution")
            old["obligation"]["state"] = "current"
        self.summary["subagents"].append(old)
        before = JournalSnapshot.open(self.run)
        events = [fixtures.trace_event(old, "spawned"), {**fixtures.trace_event(old, "handoff"), "status": "blocked"}]
        appended = "".join(json.dumps(e) + "\n" for e in events).encode()
        self.final = self.final.split(b"## Delegation Trace")[0] + fixtures.delegation_section(self.summary).encode()
        transact(self.run, "history", {}, lambda s: ({
            "delegation-summary.json": json.dumps(self.summary).encode(),
            old["trace"]: s.read_bytes(old["trace"]) + appended,
            "timeline.jsonl": s.read_bytes("timeline.jsonl") + appended,
            old["handoff"]: b"Original blocked conclusion.\n"}, {}))
        historical = JournalSnapshot.open(self.run)
        if via_cli:
            command = [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                       "resolve-obligation", "--lane-id", old["lane_id"], "--replacement", "evidence-qa",
                       "--reason", "New QA covers the original responsibility",
                       "--expected-revision", str(historical.revision), "--operation-id", "resolve-old-qa"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.close()
        closed = JournalSnapshot.open(self.run)
        self.assertEqual(closed.read_bytes(old["trace"]), historical.read_bytes(old["trace"]))
        self.assertEqual(closed.read_bytes(old["handoff"]), historical.read_bytes(old["handoff"]))
        self.assertIn(b'"status": "blocked"', closed.read_bytes("delegation-summary.json"))

    def test_finalize_request_precedes_sql_and_survives_lost_response(self):
        import journal_lifecycle
        initial=JournalSnapshot.open(self.run);captured={};original=journal_lifecycle._transact
        with self.assertRaisesRegex(JournalError,'invalid operation ID'):
            self.close(identifier='../invalid')
        def lost_response(run_dir,identifier,payload,mutation,**kwargs):
            path=Path(run_dir)/'.journal/requests'/identifier
            self.assertEqual(json.loads(path.read_bytes()),{'run_uuid':initial.run_uuid,'operation_id':identifier,
                'command':'finalize','payload':payload})
            self.assertIsNone(JournalSnapshot.open(run_dir).operation_receipt(identifier))
            captured['request']=path.read_bytes()
            if not captured.get('precommit_seen'):
                captured['precommit_seen']=True
                raise OSError('synthetic before SQL')
            captured['result']=original(run_dir,identifier,payload,mutation,**kwargs)
            raise OSError('synthetic lost response')
        with patch.object(journal_lifecycle,'_transact',side_effect=lost_response),self.assertRaisesRegex(OSError,'before SQL'):
            self.close()
        self.assert_unchanged(initial)
        self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt('finish'))
        with patch.object(journal_lifecycle,'_transact',side_effect=lost_response),self.assertRaisesRegex(OSError,'lost response'):
            self.close()
        committed=JournalSnapshot.open(self.run)
        self.assertEqual(self.close(expected_revision=initial.revision),captured['result'])
        final_file=Path(self.temp.name).resolve()/'retry-final.md';final_file.write_bytes(self.final)
        cli=subprocess.run([sys.executable,'-B',str(SCRIPTS/'journal.py'),'--run-dir',str(self.run),'finalize',
            '--expected-run-uuid',initial.run_uuid,'--expected-revision',str(initial.revision),'--expected-generation','1',
            '--operation-id','finish','--final-file',str(final_file),'--verdict','ship'],capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual({k:v for k,v in json.loads(cli.stdout).items() if k!='operation_id'},captured['result'])
        self.assert_unchanged(committed)
        self.assertEqual((self.run/'.journal/requests/finish').read_bytes(),captured['request'])
        with self.assertRaisesRegex(JournalError,'payload conflict'):
            self.close(expected_revision=initial.revision,final_bytes=self.final+b'\n')
        self.assert_unchanged(committed)

    def test_replay_and_payload_conflict(self):
        initial = JournalSnapshot.open(self.run)
        first = self.close()
        closed = JournalSnapshot.open(self.run)
        second = self.close(expected_revision=initial.revision)
        self.assertEqual(first, second)
        self.assert_unchanged(closed)
        with self.assertRaisesRegex(JournalError, "payload conflict"):
            self.close(expected_revision=initial.revision, final_bytes=self.final + b"\n")

    def test_stale_revision_generation_and_concurrent_publish(self):
        for values in ({"expected_revision": 999}, {"expected_generation": 2}):
            with self.assertRaisesRegex(JournalError, "preconditions"):
                self.close(identifier="stale-" + next(iter(values)), **values)
            identifier="stale-" + next(iter(values))
            self.assertTrue((self.run/".journal/requests"/identifier).is_file())
            self.assertIsNone(JournalSnapshot.open(self.run).operation_receipt(identifier))
        def change(point):
            if point == "captured":
                transact(self.run, "concurrent", {}, lambda s: ({"checks-extra.md": b"Another operation"}, {}))
        with self.assertRaisesRegex(JournalError, "preconditions"):
            self.close(barrier=change)
        self.assertFalse(JournalSnapshot.open(self.run).closed)

    def test_closed_guard_public_commands_and_direct_transaction(self):
        self.close()
        before = JournalSnapshot.open(self.run)
        capture = self.run.parent / "capture.txt"
        capture.write_text("replacement")
        for path in ("final.md", "delegation-summary.json", "handoffs/evidence-qa.md", "agents/qa-verifier/trace.jsonl"):
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                        "publish", "--file", path, str(capture)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("closed", result.stderr)
            with self.assertRaisesRegex(JournalError, "closed"):
                transact(self.run, path, {}, lambda s: ({path: b"changed"}, {}))
            self.assert_unchanged(before)
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                 "read", "final.md"], capture_output=True)
        self.assertEqual(result.stdout, self.final)

    def test_late_source_and_candidate_changes_reject_live_validation(self):
        def change(point):
            if point == "captured":
                later = fixtures.session(fixtures.QA_ID, "qa-verifier", {"verdict": "fail"}, 40)[1:]
                for event in later:
                    if "turn_id" in event["payload"]:
                        event["payload"]["turn_id"] = "later-turn"
                self.source.sessions[fixtures.QA_ID].extend(later)
        self.close(barrier=change)
        errors = load_validator((SCRIPTS / "validate-run.py").read_bytes()).validate_run(self.run, session_source=self.source)
        self.assertTrue(any("stale" in error or "unfinished" in error for error in errors), errors)

    def test_precommit_exception_and_lost_response_recovery(self):
        before = JournalSnapshot.open(self.run)
        def fail(point):
            if point == "before-commit":
                raise OSError("simulated crash before commit")
        with self.assertRaises(OSError):
            self.close(barrier=fail)
        self.assert_unchanged(before)
        def lost(point):
            if point == "after-commit":
                raise OSError("lost stdout")
        with self.assertRaises(OSError):
            self.close(barrier=lost)
        closed = JournalSnapshot.open(self.run)
        self.close(expected_revision=before.revision)
        self.assert_unchanged(closed)

    def test_version_one_is_read_only(self):
        import sqlite3
        database = self.run / ".journal/state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE run_state SET version=1")
        before = JournalSnapshot.open(self.run)
        self.assertEqual(before.storage_version, 1)
        with self.assertRaisesRegex(JournalError, "read-only"):
            transact(self.run, "v1", {}, lambda s: ({"final.md": b"changed"}, {}))
        self.assert_unchanged(before)


if __name__ == "__main__":
    unittest.main()
