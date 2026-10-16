#!/usr/bin/env python3
"""Crash and snapshot regressions for the authoritative journal."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import signal
import sqlite3
import shutil
import importlib.util
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

import journal_io as journal

SCRIPTS = Path(__file__).resolve().parent


def external_corruption(run, documents):
    """Unsupported direct SQL writes test pinned readers, not lifecycle permissions."""
    import hashlib
    with sqlite3.connect(Path(run) / ".journal/state.sqlite3") as connection:
        for name, data in documents.items():
            connection.execute("INSERT OR REPLACE INTO documents VALUES (?, 'file', ?, ?)",
                               (name, data, hashlib.sha256(data).hexdigest()))
        connection.execute("UPDATE run_state SET revision=revision+1")


def child(code, *args):
    return subprocess.run([sys.executable, "-B", "-c", code, *map(str, args)],
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SCRIPTS)},
                          capture_output=True, text=True, timeout=25)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class StorageTests(unittest.TestCase):
    def test_public_publish_denies_writer_owned_paths_before_request_and_preserves_raw_evidence(self):
        journal.transact(self.run, "owned-lane-fixture", {}, lambda snapshot: (
            {"lane-map.json": json.dumps({"schema_version": 2, "lanes": [
                {"id": "worker", "role": "python-worker", "execution_mode": "role-lane", "status": "planned"}]})}, {}))
        capture = self.run.parent / "raw-evidence"
        raw = b"\x00original\xff\r\nbytes\n"
        capture.write_bytes(raw)
        before = journal.JournalSnapshot.open(self.run)
        before_tree = {str(path.relative_to(self.run)): path.read_bytes()
                       for path in self.run.rglob("*") if path.is_file()}
        owned_paths = (
            "timeline.jsonl", "delegation-summary.json", "lane-map.json", "final.md", "task.md", "run.md",
            "context.md", "manifest.md", "plan.md", "progress.md", "lesson.md", "artifacts.json",
            "route.md", "decisions.md", "definition-of-done.md", "checks.md",
            "claim-evidence.json", "acceptance-traceability.json", "risk-mitigations.json", "risk-resolutions.json",
            "verification-readiness.json", "continuation-summary.json", "harness-evaluation.json",
            "checks/coverage-matrix.md", "checks/engineering-simplicity-scope.md", "checks/worker.md",
            "checks/lane-boundary-worker.json", "checks/command-owned.json", "checks/command-owned.output",
            "handoffs/worker.md", "agents/worker/trace.jsonl", "artifacts/source-references.json",
            "artifacts/memory/owned.md", "artifacts/workspaces/owned.json",
            "artifacts/lifecycle/owned.json", "artifacts/checkpoints/owned.json",
        )
        for name in owned_paths:
            with self.subTest(name=name):
                result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                         "publish", "--file", name, str(capture)], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("writer-owned", result.stderr)
                after = journal.JournalSnapshot.open(self.run)
                self.assertEqual((before.revision, before.documents, before.receipts),
                                 (after.revision, after.documents, after.receipts))
                self.assertEqual(before_tree, {str(path.relative_to(self.run)): path.read_bytes()
                                               for path in self.run.rglob("*") if path.is_file()})
        for name in ("checks/raw.output", "artifacts/screenshots/raw.png", "artifacts/specs/raw.md", "checks/unassigned-evidence.md"):
            result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                     "publish", "--file", name, str(capture)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes(name), raw)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / "run"
        journal.initialize_journal(self.run, {"first": b"old", "second": b"old", "empty": None},
                                   source_root=self.run.parent)

    def unchanged(self, before):
        self.assertEqual(dict(journal.JournalSnapshot.open(self.run).documents), before)

    def test_jd01_atomic_failure_between_documents(self):
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        original = journal._put_documents
        injected = []
        def failed(connection, documents):
            original(connection, {"first": documents["first"]})
            injected.append(True)
            raise OSError("failure after first document")
        with patch.object(journal, "_put_documents", failed), self.assertRaisesRegex(OSError, "after first"):
            journal.write_files({self.run / "first": b"new", self.run / "second": b"new"}, run_dir=self.run)
        self.assertEqual(injected, [True])
        self.unchanged(before)

    def test_jd01_old_flat_writer_negative_control(self):
        first, second = self.run / "control-first", self.run / "control-second"
        first.write_text("old-first")
        second.write_text("old-second")
        with first.open("a+") as one, second.open("a+") as two:
            one.seek(0)
            one.truncate()
            one.write("new-first")
            two.seek(0)
            two.truncate()  # Original second write failed after this truncation.
        observed = [first.read_text(), second.read_text()]
        self.assertEqual(observed, ["new-first", ""])
        with self.assertRaises(AssertionError):
            self.assertIn(observed, [["old-first", "old-second"], ["new-first", "new-second"]])

    def test_jd01_sigkill_each_write_and_after_commit(self):
        code = '''
import os, signal, sys
from pathlib import Path
import journal_io as j
run, point = Path(sys.argv[1]), sys.argv[2]
if point == 'before': os.kill(os.getpid(), signal.SIGKILL)
original = j._put_documents
def put(connection, documents):
    for index, item in enumerate(documents.items()):
        original(connection, dict([item]))
        if point == str(index): os.kill(os.getpid(), signal.SIGKILL)
j._put_documents = put
j.transact(run, 'kill', {}, lambda snapshot: ({str(i): bytes([65+i])*1048576 for i in range(3)}, {}))
os.kill(os.getpid(), signal.SIGKILL)
'''
        for point in ("before", "0", "1", "2", "after"):
            with self.subTest(point=point):
                run = self.run.parent / ("kill-" + point)
                old = {str(i): b"old" for i in range(3)}
                journal.initialize_journal(run, old, source_root=self.run.parent)
                result = child(code, run, point)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                expected = {str(i): bytes([65+i])*1048576 for i in range(3)} if point == "after" else old
                self.assertEqual(dict(journal.JournalSnapshot.open(run).documents), expected)

    def test_jd02_parallel_cli_append_trace_handoff_and_final(self):
        lanes = {"lanes": [{"id": f"worker-{i}", "role": "python-worker", "execution_mode": "role-lane",
                            "handoff": f"handoffs/{i}.md"} for i in range(4)]}
        summary = {"version": 1, "subagents_used": False, "role_lanes_used": True, "notes": "jd02 parallel fixture",
                   "subagents": [], "role_lanes": [{"lane_id": f"worker-{i}", "role": "python-worker",
                    "status": "planned", "trace": "agents/python-worker/trace.jsonl"} for i in range(4)],
                   "verification": {"task_kind": None, "root_thread_id": None, "author_thread_ids": [],
                                    "result_files": [], "initial_snapshot": None, "task_scope": None,
                                    "qa": None, "reviewer": None, "behavioral_checks": []}}
        journal.transact(self.run, "lanes", {}, lambda snapshot: (
            {"lane-map.json": json.dumps(lanes), "delegation-summary.json": json.dumps(summary)}, {}))
        event_request = self.run.parent / "event-request.json"
        event_request.write_text(json.dumps({"summary": "Identical independent event", "status": "pass",
                                             "stage": "checks", "role": "orchestrator"}))
        commands = []
        for i in range(4):
            commands.append(["journal.py", "record", "--kind", "event", "--request-file", str(event_request)])
            commands.append(["record-agent-trace.py", "--role", "python-worker", "--execution-mode", "role-lane",
                             "--lane-id", f"worker-{i}", "--stage", "checks", "--status", "planned", "--summary", "Queued work"])
            commands.append(["record-agent-trace.py", "--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", str(i)])
        processes = [subprocess.Popen([sys.executable, "-B", str(SCRIPTS / command[0]), "--run-dir", str(self.run), *command[1:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
        for process in processes:
            output, error = process.communicate(timeout=25)
            self.assertEqual(process.returncode, 0, output + error)
        snapshot = journal.JournalSnapshot.open(self.run)
        events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
        self.assertEqual(len(events), 12)
        self.assertEqual([event["timestamp"] for event in events], sorted(event["timestamp"] for event in events))
        self.assertTrue(all(lane["handoff_state"]["status"] == "queued" for lane in json.loads(snapshot.read_text("lane-map.json"))["lanes"]))
        final_request = self.run.parent / "final-event-request.json"
        final_request.write_text(json.dumps({"summary": "Done", "status": "pass", "stage": "final", "role": "orchestrator"}))
        final = [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                 "record", "--kind", "event", "--request-file", str(final_request)]
        result = subprocess.run(final, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("finalize", result.stderr)
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        final_request.write_text(json.dumps({"summary": "Late", "status": "pass", "stage": "final", "role": "orchestrator"}))
        late = subprocess.run(final, capture_output=True, text=True)
        self.assertNotEqual(late.returncode, 0)
        self.assertIn("final", late.stderr)
        self.unchanged(before)

    def test_jd02_busy_writer_has_no_partial_state(self):
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        lock = journal.connect_database(self.run)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(journal.JournalError, "locked"):
                journal.transact(self.run, "busy", {}, lambda snapshot: ({"first": b"new"}, {}), timeout=0)
        finally:
            lock.rollback()
            lock.close()
        self.unchanged(before)

    def test_jd03_snapshot_is_pinned_and_immutable(self):
        old = journal.JournalSnapshot.open(self.run)
        journal.transact(self.run, "replace", {}, lambda snapshot: ({"first": b"new", "second": b"new"}, {}))
        self.assertEqual([old.read_bytes(p) for p in ("first", "second")], [b"old", b"old"])
        new = journal.JournalSnapshot.open(self.run)
        self.assertEqual([new.read_bytes(p) for p in ("first", "second")], [b"new", b"new"])
        with self.assertRaises(TypeError):
            old.documents["first"] = b"changed"

    def test_jd04_receipt_replay_and_conflict(self):
        operation = lambda snapshot: ({"first": b"accepted"}, {"acceptance": "kept"})
        first = journal.transact(self.run, "accept", {"payload": 1}, operation)
        second = journal.transact(self.run, "accept", {"payload": 1}, operation)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(journal.JournalError, "payload"):
            journal.transact(self.run, "accept", {"payload": 2}, operation)
        self.assertEqual(journal.JournalSnapshot.open(self.run).revision, first["revision"])

    def test_jd04_ambiguous_commit_looks_up_receipt(self):
        original = journal.connect_database
        connections, injected = [], []
        class LostReply:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                self.connection.commit()
                injected.append(True)
                raise sqlite3.OperationalError("lost commit reply")
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            connections.append(connection)
            return LostReply(connection) if len(connections) == 2 else connection
        with patch.object(journal, "connect_database", connect):
            receipt = journal.transact(self.run, "ambiguous", {}, lambda snapshot: ({"first": b"accepted"}, {"acceptance": True}))
        self.assertEqual(injected, [True])
        self.assertGreaterEqual(len(connections), 3)
        repeat = journal.transact(self.run, "ambiguous", {}, lambda snapshot: self.fail("replayed mutation"))
        self.assertEqual(receipt, repeat)

    def test_jd04_cli_lost_stdout_and_saved_request_retry(self):
        capture = self.run.parent / "capture"
        capture.write_bytes(b"accepted capture")
        code = '''
import sys
import journal
class LostStdout:
    def write(self, text): raise BrokenPipeError('lost stdout after commit')
    def flush(self): pass
sys.stdout = LostStdout()
journal.main(['--run-dir', sys.argv[1], 'publish', '--file', 'accepted', sys.argv[2], '--operation-id', 'saved-request'])
'''
        failed = child(code, self.run, capture)
        self.assertNotEqual(failed.returncode, 0)
        snapshot = journal.JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.read_bytes("accepted"), b"accepted capture")
        args = [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run), "publish", "--file", "accepted", str(capture), "--operation-id", "saved-request"]
        retry = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(journal.JournalSnapshot.open(self.run).revision, snapshot.revision)
        capture.write_bytes(b"conflict")
        conflict = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("payload", conflict.stderr)
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes("accepted"), b"accepted capture")

    def test_jd05_export_is_disposable_and_flat_is_not_authoritative(self):
        (self.run / "first").write_bytes(b"flat spoof")
        snapshot = journal.JournalSnapshot.open(self.run)
        view = journal.export_snapshot(snapshot)
        self.assertEqual((view / "first").read_bytes(), b"old")
        (view / "first").write_bytes(b"damaged")
        repaired = journal.export_snapshot(snapshot)
        self.assertEqual((repaired / "first").read_bytes(), b"old")
        self.assertTrue(snapshot.is_dir("empty"))

    def test_jd05_sqlite_full_and_write_io_failure(self):
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        original = journal.connect_database
        def limited(*args, **kwargs):
            connection = original(*args, **kwargs)
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={pages}")
            return connection
        with patch.object(journal, "connect_database", limited), self.assertRaisesRegex(journal.JournalError, "full"):
            journal.transact(self.run, "full", {}, lambda snapshot: ({"first": b"x" * 4194304, "second": b"new"}, {}))
        self.unchanged(before)
        for error in (PermissionError("denied"), OSError("I/O failure")):
            with patch.object(journal, "_put_documents", side_effect=error), self.assertRaises(OSError):
                journal.transact(self.run, str(error), {}, lambda snapshot: ({"first": b"new"}, {}))
            self.unchanged(before)

    def test_jd05_sync_failure_and_interrupted_export_repair(self):
        snapshot = journal.JournalSnapshot.open(self.run)
        for function in ("sync_file", "sync_directory"):
            with self.subTest(function=function), patch.object(journal, function, side_effect=OSError("sync failed")), self.assertRaises(OSError):
                journal.export_snapshot(snapshot)
            self.assertEqual(journal.JournalSnapshot.open(self.run).revision, snapshot.revision)
        view = journal.export_snapshot(snapshot)
        (view / "first").unlink()
        self.assertEqual((journal.export_snapshot(snapshot) / "first").read_bytes(), b"old")

    def test_jd05_refuse_structural_and_canonical_corruption(self):
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        with self.assertRaisesRegex(journal.JournalError, "collision"):
            journal.transact(self.run, "collision", {}, lambda snapshot: ({"first/child": b"bad"}, {}))
        self.unchanged(before)
        connection = journal.connect_database(self.run)
        connection.execute("UPDATE documents SET content=X'00' WHERE path='empty'")
        connection.close()
        with self.assertRaisesRegex(journal.JournalError, "corrupt"):
            journal.JournalSnapshot.open(self.run)

    def test_jd05_publish_rejects_fifo_symlinks_and_symlink_parent(self):
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        regular = self.run.parent / "regular"
        regular.write_bytes(b"outside")
        link = self.run.parent / "link"
        link.symlink_to(regular)
        fifo = self.run.parent / "fifo"
        os.mkfifo(fifo)
        parent = self.run.parent / "parent"
        parent.symlink_to(self.run.parent, target_is_directory=True)
        for path in (link, fifo, parent / "regular"):
            args = [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run), "publish", "--file", "bad", str(path)]
            result = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0, result.stdout)
        self.unchanged(before)

    def test_jd07_domain_capture_absolute_reference_and_capture_directories(self):
        source = self.run.parent / "scope.md"
        source.write_bytes(b"original scope bytes")
        def publish_summary(path, identifier):
            current = journal.JournalSnapshot.open(self.run)
            documents = {"delegation-summary.json": json.dumps({"verification": {"task_scope": {"path": str(path)}}}).encode()}
            documents = journal.capture_external_references(self.run, documents, current.source_root, previous=current.documents)
            return journal.transact(self.run, identifier, {"path": str(path)}, lambda snapshot: (documents, {}))
        publish_summary(source, "first-capture")
        snapshot = journal.JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.read_bytes(source), b"original scope bytes")
        self.assertTrue(snapshot.is_dir("artifacts/source-captures"))
        other_source = self.run.parent / "other.md"
        other_source.write_bytes(b"other reference")
        publish_summary(other_source, "other-capture")
        replay = publish_summary(source, "first-capture")
        self.assertEqual(replay["revision"], snapshot.revision)
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes(other_source), b"other reference")


class VersionGateTests(unittest.TestCase):
    """Storage version 3 is the only journal format; pre-0.3 journals are archival."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "run"
        journal.initialize_journal(self.run, {"context.md": b"exact\r\nbytes\n", "data.json": b'{ "preserve" : true }\n'},
                                   source_root=self.root)

    def init_flat(self, run):
        documents, identities = journal.capture_flat(run)
        return journal.initialize_journal(run, documents, source_root=self.root, identities=identities)

    def downgrade(self, run, version):
        # Construct the pre-0.3 schema fixture directly; production writers never receive this seam.
        with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
            connection.execute("ALTER TABLE run_state RENAME TO old_state")
            connection.execute("CREATE TABLE run_state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, run_uuid TEXT NOT NULL, revision INTEGER NOT NULL, logical_root TEXT NOT NULL, source_root TEXT NOT NULL, identities TEXT NOT NULL)")
            connection.execute("INSERT INTO run_state SELECT id, ?, run_uuid, revision, logical_root, source_root, identities FROM old_state", (version,))
            connection.execute("DROP TABLE old_state")

    def test_current_version_and_package_stamp(self):
        snapshot = journal.JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.storage_version, 3)
        self.assertEqual(snapshot.package_version, journal.current_package_version())
        receipt = snapshot.operation_receipt("initialize")
        self.assertEqual(receipt["result"]["package_version"], snapshot.package_version)

    def test_validate_run_warns_on_package_version_mismatch(self):
        init = [sys.executable, "-B", str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                "--date", "2026-09-13", "--slug", "stamp", "--mode", "compact"]
        result = subprocess.run(init, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.root / ".agent-work/runs/2026-09-13-stamp"
        validate = [sys.executable, "-B", str(SCRIPTS / "validate-run.py"), "--run-dir", str(run), "--allow-pending"]
        clean = subprocess.run(validate, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertNotIn("WARNING", clean.stderr)
        with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
            connection.execute("UPDATE run_state SET package_version='0.0.0' WHERE id=1")
        mismatched = subprocess.run(validate, capture_output=True, text=True)
        self.assertEqual(mismatched.returncode, 0, mismatched.stderr)
        self.assertIn("WARNING journal created by agent-flow 0.0.0", mismatched.stderr)
        self.assertNotIn("unsupported journal storage version", mismatched.stderr)

    def test_pre_0_3_storage_refused_at_every_entry_point(self):
        for version in (1, 2):
            with self.subTest(version=version):
                run = self.root / f"v{version}"
                shutil.copytree(self.run, run)
                self.downgrade(run, version)
                with self.assertRaisesRegex(journal.JournalError, "unsupported journal storage version"):
                    journal.JournalSnapshot.open(run)
                with self.assertRaisesRegex(journal.JournalError, "unsupported journal storage version"):
                    journal.transact(run, "write", {}, lambda snapshot: ({"new.md": b"new"}, {}))
                request = run.parent / "late-event-request.json"
                request.write_text(json.dumps({"summary": "late", "status": "pass", "stage": "checks", "role": "orchestrator"}))
                commands = [
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run), "read", "context.md"],
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run), "diagnose-recovery"],
                    [sys.executable, "-B", str(SCRIPTS / "validate-run.py"), "--run-dir", str(run), "--allow-pending"],
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run),
                     "record", "--kind", "event", "--request-file", str(request)],
                ]
                for command in commands:
                    with self.subTest(command=Path(command[2]).name):
                        result = subprocess.run(command, capture_output=True, text=True)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(journal.UNSUPPORTED_STORAGE_VERSION, result.stderr)
                        self.assertNotIn("Traceback", result.stderr)

    def test_missing_database_with_started_lifecycle_refused_everywhere(self):
        init = [sys.executable, "-B", str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                "--date", "2026-09-13", "--slug", "loss", "--mode", "compact"]
        result = subprocess.run(init, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.root / ".agent-work/runs/2026-09-13-loss"
        database = run / ".journal/state.sqlite3"
        database.unlink()
        with self.assertRaisesRegex(journal.JournalError, "unsupported journal storage version"):
            journal.JournalSnapshot.open(run)
        request = self.root / "late-event-request.json"
        request.write_text(json.dumps({"summary": "late", "status": "pass", "stage": "checks", "role": "orchestrator"}))
        commands = [init + ["--reuse"],
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run), "read", "context.md"],
                    [sys.executable, "-B", str(SCRIPTS / "validate-run.py"), "--run-dir", str(run), "--allow-pending"],
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run),
                     "record", "--kind", "event", "--request-file", str(request)]]
        for command in commands:
            with self.subTest(command=Path(command[2]).name):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported journal storage version", result.stderr)
                self.assertFalse(database.exists())

    def test_missing_committed_state_never_falls_back(self):
        connection = journal.connect_database(self.run)
        connection.execute("DELETE FROM run_state")
        connection.close()
        with self.assertRaisesRegex(journal.JournalError, "fallback forbidden"):
            journal.JournalSnapshot.open(self.run)

    def test_flat_directory_reads_without_journal_and_never_writes(self):
        flat = self.root / "flat"
        flat.mkdir()
        (flat / "context.md").write_bytes(b"exact\r\nbytes\n")
        snapshot = journal.JournalSnapshot.open(flat)
        self.assertFalse(snapshot.durable)
        self.assertEqual(snapshot.read_bytes("context.md"), b"exact\r\nbytes\n")
        before = {str(p.relative_to(flat)): p.read_bytes() for p in flat.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(journal.JournalError, "unsupported journal storage version"):
            journal.transact(flat, "write", {}, lambda current: ({"new.md": b"new"}, {}))
        with self.assertRaisesRegex(journal.JournalError, "unsupported journal storage version"):
            journal.export_snapshot(snapshot)
        self.assertEqual({str(p.relative_to(flat)): p.read_bytes() for p in flat.rglob("*") if p.is_file()}, before)
        self.assertFalse((flat / ".journal").exists())

    def test_lifecycle_rejects_zero_foreign_and_missing_race(self):
        for damage in ("zero", "foreign", "race"):
            with self.subTest(damage=damage):
                run = self.root / ("loss-" + damage)
                shutil.copytree(self.run, run)
                database = run / ".journal/state.sqlite3"
                if damage == "zero":
                    database.write_bytes(b"")
                elif damage == "foreign":
                    connection = journal.connect_database(run)
                    connection.execute("DROP TABLE operations")
                    connection.close()
                if damage == "race":
                    original = journal.connect_database
                    def lose(*args, **kwargs):
                        database.unlink()
                        return original(*args, **kwargs)
                    with patch.object(journal, "connect_database", lose):
                        with self.assertRaises(journal.JournalError):
                            journal.JournalSnapshot.open(run)
                    self.assertFalse(database.exists())
                else:
                    with self.assertRaises(journal.JournalError):
                        journal.JournalSnapshot.open(run)

    def test_lifecycle_crash_windows_resume_exact_bootstrap(self):
        flat = self.root / "flat-source"
        flat.mkdir()
        (flat / "context.md").write_bytes(b"exact\r\nbytes\n")
        (flat / "data.json").write_bytes(b'{ "preserve" : true }\n')
        for point in ("schema", "database-sync", "marker-before-sync", "marker-after-sync"):
            with self.subTest(point=point):
                run = self.root / ("crash-" + point)
                shutil.copytree(flat, run)
                code = r'''
import os, signal, sys
from pathlib import Path
import journal_io as j
run, point = Path(sys.argv[1]), sys.argv[2]
original_connect, original_sync = j.connect_database, j.sync_file
class Barrier:
    def __init__(self, connection): self.connection = connection
    def __getattr__(self, name): return getattr(self.connection, name)
    def execute(self, sql, *args):
        result = self.connection.execute(sql, *args)
        if point == 'schema' and sql.startswith('CREATE TABLE documents'):
            os.kill(os.getpid(), signal.SIGKILL)
        return result
def connect(*args, **kwargs): return Barrier(original_connect(*args, **kwargs))
def sync(fd):
    identity = os.fstat(fd).st_ino
    database, marker = run / '.journal/state.sqlite3', run / '.journal/storage-required'
    is_database = database.exists() and database.stat().st_ino == identity
    is_marker = marker.exists() and marker.stat().st_ino == identity
    if (point == 'database-sync' and is_database) or (point == 'marker-before-sync' and is_marker):
        os.kill(os.getpid(), signal.SIGKILL)
    original_sync(fd)
    if point == 'marker-after-sync' and is_marker: os.kill(os.getpid(), signal.SIGKILL)
j.connect_database, j.sync_file = connect, sync
documents, identities = j.capture_flat(run)
j.initialize_journal(run, documents, source_root=run.parent, identities=identities)
'''
                result = child(code, run, point)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                snapshot = journal.JournalSnapshot.open(run)
                self.assertFalse(snapshot.durable)
                self.assertEqual(snapshot.read_bytes("context.md"), b"exact\r\nbytes\n")
                self.assertTrue(self.init_flat(run).durable)
                self.assertTrue(journal.storage_required(run))

    def test_sigkill_init_before_and_after_run_state(self):
        flat = self.root / "flat-kill"
        flat.mkdir()
        (flat / "context.md").write_bytes(b"exact\r\nbytes\n")
        for point in ("documents", "committed"):
            with self.subTest(point=point):
                run = self.root / ("kill-" + point)
                shutil.copytree(flat, run)
                code = '''
import sys, os, signal
from pathlib import Path
import journal_io as j
original = j._put_documents
def put(connection, documents):
    original(connection, documents)
    if sys.argv[2] == 'documents': os.kill(os.getpid(), signal.SIGKILL)
j._put_documents = put
run = Path(sys.argv[1])
documents, identities = j.capture_flat(run)
j.initialize_journal(run, documents, source_root=run.parent, identities=identities)
os.kill(os.getpid(), signal.SIGKILL)
'''
                result = child(code, run, point)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                snapshot = journal.JournalSnapshot.open(run)
                self.assertEqual(snapshot.durable, point == "committed")
                self.assertEqual(snapshot.read_bytes("context.md"), b"exact\r\nbytes\n")
                if not snapshot.durable:
                    self.assertTrue(self.init_flat(run).durable)

    def test_marker_sync_failure_precedes_domain_commit(self):
        flat = self.root / "marker-failure"
        flat.mkdir()
        (flat / "context.md").write_bytes(b"exact\r\nbytes\n")
        original = journal.sync_file
        fired = []
        def fail_marker(fd):
            marker = flat / ".journal/storage-required"
            if marker.exists() and marker.stat().st_ino == os.fstat(fd).st_ino:
                fired.append(True)
                raise OSError("marker sync failure")
            original(fd)
        with patch.object(journal, "sync_file", fail_marker):
            with self.assertRaisesRegex(OSError, "marker sync failure"):
                self.init_flat(flat)
        self.assertEqual(fired, [True])
        connection = journal.connect_database(flat)
        self.assertEqual(connection.execute("SELECT count(*) FROM run_state").fetchone()[0], 0)
        journal.validate_bootstrap(connection)
        connection.close()
        self.assertFalse(journal.JournalSnapshot.open(flat).durable)
        self.assertTrue(self.init_flat(flat).durable)

    def test_marker_directory_sync_order_and_failure(self):
        flat = self.root / "marker-order"
        flat.mkdir()
        (flat / "context.md").write_bytes(b"exact\r\nbytes\n")
        original_file, original_directory, original_put = journal.sync_file, journal.sync_directory, journal._put_documents
        observed = []
        def file_sync(fd):
            original_file(fd)
            marker = flat / ".journal/storage-required"
            if marker.exists() and marker.stat().st_ino == os.fstat(fd).st_ino:
                observed.append("marker-file-synced")
        def directory_sync(path):
            if "marker-file-synced" in observed and Path(path) == flat / ".journal":
                observed.append("marker-directory-sync-failed")
                raise OSError("marker directory sync failure")
            original_directory(path)
        with patch.object(journal, "sync_file", file_sync), patch.object(journal, "sync_directory", directory_sync):
            with self.assertRaisesRegex(OSError, "marker directory sync failure"):
                self.init_flat(flat)
        self.assertEqual(observed, ["marker-file-synced", "marker-directory-sync-failed"])
        connection = journal.connect_database(flat)
        journal.validate_bootstrap(connection)
        connection.close()
        observed.clear()
        def successful_directory_sync(path):
            original_directory(path)
            if "marker-file-synced" in observed and Path(path) == flat / ".journal":
                observed.append("marker-directory-synced")
        def put(connection, documents):
            self.assertIn("marker-directory-synced", observed)
            return original_put(connection, documents)
        with patch.object(journal, "sync_file", file_sync), patch.object(journal, "sync_directory", successful_directory_sync), patch.object(journal, "_put_documents", put):
            self.assertTrue(self.init_flat(flat).durable)

    def test_adopt_existing_committed_storage_without_document_change(self):
        before = journal.JournalSnapshot.open(self.run)
        (self.run / ".journal/storage-required").unlink()
        after = journal.initialize_journal(self.run, {}, source_root=self.root)
        self.assertTrue(journal.storage_required(self.run))
        self.assertEqual((before.run_uuid, before.revision, dict(before.documents)),
                         (after.run_uuid, after.revision, dict(after.documents)))

    def test_file_alias_identity_survives_initialization(self):
        flat = self.root / "aliases"
        flat.mkdir()
        target = self.root / "outside-run-inside-source"
        target.write_bytes(b"source handoff")
        (flat / "qa.md").symlink_to(target)
        (flat / "alias.md").hardlink_to(target)
        (flat / "review.md").write_bytes(b"source handoff")
        from verification_evidence import require_own_reviewer_handoff
        old = journal.JournalSnapshot.open(flat)
        documents, identities = journal.capture_flat(flat, allow_file_links=True, source_root=self.root)
        migrated = journal.initialize_journal(flat, documents, source_root=self.root, identities=identities)
        self.assertEqual(old.read_bytes("qa.md"), migrated.read_bytes("qa.md"))
        for snapshot in (old, migrated):
            with self.assertRaisesRegex(journal.JournalError, "own handoff"):
                require_own_reviewer_handoff(flat, "alias.md", "qa.md", snapshot=snapshot)
            require_own_reviewer_handoff(flat, "review.md", "qa.md", snapshot=snapshot)
        view = journal.export_snapshot(migrated)
        with self.assertRaises(journal.JournalError):
            require_own_reviewer_handoff(flat, str(view / "qa.md"), "qa.md", snapshot=migrated)


class ConsumerTests(unittest.TestCase):
    """All completion records in this class are explicitly synthetic."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.fixtures = load("storage_verification_fixtures", "test-verification-evidence.py")
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "run.md").write_text("# Synthetic compact change\n")
        (self.run / "checks.md").write_text("# Synthetic checks\n")
        self.summary, self.source = self.fixtures.acceptance_pack(self.run)

    def initialize_closed(self, run, *, allow_file_links=False, source_root=None):
        from journal_lifecycle import finalize
        summary = json.loads((run / "delegation-summary.json").read_text())
        for record in summary["subagents"]:
            record["status"] = "pass"
            record["obligation"] = {"id": record["role"], "required": True, "state": "current"}
        (run / "delegation-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        timeline = run / "timeline.jsonl"
        lines = timeline.read_bytes().splitlines(keepends=True)
        timeline.write_bytes(b"".join(line for line in lines if json.loads(line).get("stage") != "final"))
        (run / "final.md").unlink()
        source_root = source_root or journal._source_root(run)
        documents, identities = journal.capture_flat(run, allow_file_links=allow_file_links, source_root=source_root)
        snapshot = journal.initialize_journal(run, documents, source_root=source_root,
                                              identities=identities, result_contract_version=1)
        finalize(run, expected_run_uuid=snapshot.run_uuid, expected_revision=snapshot.revision,
                 expected_generation=snapshot.generation, identifier="fixture-finalize",
                 final_summary="Synthetic compact acceptance", verdict="ship", session_source=self.source)
        return journal.JournalSnapshot.open(run)

    def test_jd03_validator_modes_and_mixed_reader_negative_control(self):
        for mode in ("compact", "full", "auto"):
            for mixed in (False, True):
                with self.subTest(mode=mode, mixed=mixed):
                    run = self.root / (mode + str(mixed))
                    shutil.copytree(self.run, run)
                    if mode == "full":
                        for name in ("manifest.md", "route.md", "definition-of-done.md", "decisions.md"):
                            (run / name).write_text("# Full fixture\n\nVerdict: ship\n")
                        (run / "artifacts").mkdir()
                        (run / "checks").mkdir()
                        (run / "checks/smoke.md").write_text("# Synthetic checks\n")
                        (run / "artifacts.json").write_text("[]\n")
                    snapshot = self.initialize_closed(run)
                    self.assertEqual(self.fixtures.validator.validate_run(run, mode=mode, session_source=self.source, snapshot=snapshot), [])
                    original = journal.JournalSnapshot.read_text
                    fired = []
                    def interleaved(current, path, encoding="utf-8"):
                        if mixed and fired:
                            return original(journal.JournalSnapshot.open(run), path, encoding)
                        text = original(current, path, encoding)
                        if not fired:
                            fired.append(True)
                            code = "from pathlib import Path; import sys, runpy; m=runpy.run_path(str(Path(__import__('journal_io').__file__).with_name('test-journal-storage.py'))); m['external_corruption'](Path(sys.argv[1]), {'checks.md': b'new', 'final.md': b'Verdict: blocked\\n', 'timeline.jsonl': b'invalid JSON\\n'})"
                            result = child(code, run)
                            self.assertEqual(result.returncode, 0, result.stderr)
                        return text
                    with patch.object(journal.JournalSnapshot, "read_text", interleaved):
                        errors = self.fixtures.validator.validate_run(run, mode=mode, session_source=self.source, snapshot=snapshot)
                    self.assertEqual(fired, [True])
                    self.assertEqual(bool(errors), mixed)
                    self.assertTrue(self.fixtures.validator.validate_run(run, mode=mode, session_source=self.source))

    def test_jd03_main_output_and_harness_use_same_snapshot(self):
        import verification_evidence
        import harness_promotion
        snapshot = self.initialize_closed(self.run)
        validator = self.fixtures.validator
        original_read = journal.JournalSnapshot.read_text
        fired = []
        def interleaved(current, path, encoding="utf-8"):
            text = original_read(current, path, encoding)
            if not fired:
                fired.append(True)
                external_corruption(self.run, {"final.md": b"Verdict: blocked\n"})
            return text
        with patch.object(journal.JournalSnapshot, "read_text", interleaved), patch.object(verification_evidence, "CodexSessionSource", return_value=self.source), patch.object(sys, "argv", ["validate-run.py", "--run-dir", str(self.run)]), redirect_stdout(StringIO()) as output:
            self.assertEqual(validator.main(), 0)
        self.assertEqual(fired, [True])
        self.assertTrue(output.getvalue().startswith("PASS "))
        external_corruption(self.run, {"final.md": snapshot.read_bytes("final.md"), "harness-evaluation.json": b'{"findings": [], "notes": "old"}'})
        observed = []
        def validate(run, *, snapshot):
            errors = validator.validate_run(run, session_source=self.source, snapshot=snapshot)
            self.assertEqual(errors, [])
            external_corruption(run, {"harness-evaluation.json": b'{"findings": [], "notes": "new"}'})
            return "synthetic pinned validator", "PASS"
        original_load = harness_promotion.load_harness_evaluation
        def read(run, *, snapshot):
            data = original_load(run, snapshot=snapshot)
            observed.append(data["notes"])
            return data
        with patch.object(harness_promotion, "validate_run", validate), patch.object(harness_promotion, "load_harness_evaluation", read):
            result = harness_promotion.promote_harness_evaluation(self.run, self.root / "notes.md")
        self.assertEqual(observed, ["old"])
        self.assertEqual(result.promoted, 0)
        self.assertEqual(harness_promotion.load_harness_evaluation(self.run)["notes"], "new")

    def test_jd07_eligible_file_links_and_relocation_preserve_acceptance(self):
        from verification_evidence import result_hash, validate_verification
        targets = self.root / "link-targets"
        targets.mkdir()
        for index, name in enumerate(("checks.md", "handoffs/evidence-qa.md", "handoffs/evidence-reviewer.md")):
            path = self.run / name
            target = targets / str(index)
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
        self.assertEqual(validate_verification(self.run, self.summary, {}, "ship", self.source), [])
        before = result_hash(self.run, self.summary["verification"])
        relocated = self.root / "other-project" / "relocated-run"
        shutil.copytree(self.run, relocated, symlinks=True)
        snapshot = self.initialize_closed(relocated, allow_file_links=True, source_root=self.root)
        self.assertEqual(result_hash(relocated, self.summary["verification"], snapshot=snapshot), before)
        self.assertEqual(validate_verification(relocated, self.summary, {}, "ship", self.source, snapshot=snapshot), [])
        journal.export_snapshot(snapshot)
        self.assertEqual(validate_verification(relocated, self.summary, {}, "ship", self.source), [])
        again = journal.initialize_journal(relocated, {}, source_root=self.root)
        self.assertEqual((again.run_uuid, again.revision), (snapshot.run_uuid, snapshot.revision))


if __name__ == "__main__":
    unittest.main()
