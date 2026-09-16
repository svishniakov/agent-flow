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
        lanes = {"lanes": [{"id": f"worker-{i}", "handoff": f"handoffs/{i}.md"} for i in range(4)]}
        journal.transact(self.run, "lanes", {}, lambda snapshot: ({"lane-map.json": json.dumps(lanes)}, {}))
        commands = []
        for i in range(4):
            commands.append(["append-timeline.py", "--role", "orchestrator", "--stage", "checks", "--status", "pass", "--summary", "Identical independent event"])
            commands.append(["record-handoff-state.py", "--lane-id", f"worker-{i}", "--status", "queued"])
            commands.append(["record-agent-trace.py", "--role", "orchestrator", "--execution-mode", "role-lane", "--stage", "checks", "--status", "pass", "--summary", str(i)])
        processes = [subprocess.Popen([sys.executable, "-B", str(SCRIPTS / command[0]), "--run-dir", str(self.run), *command[1:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
        for process in processes:
            output, error = process.communicate(timeout=25)
            self.assertEqual(process.returncode, 0, output + error)
        snapshot = journal.JournalSnapshot.open(self.run)
        events = [json.loads(line) for line in snapshot.read_text("timeline.jsonl").splitlines()]
        self.assertEqual(len(events), 8)
        self.assertEqual([event["timestamp"] for event in events], sorted(event["timestamp"] for event in events))
        self.assertTrue(all(lane["handoff_state"]["status"] == "queued" for lane in json.loads(snapshot.read_text("lane-map.json"))["lanes"]))
        final = [sys.executable, "-B", str(SCRIPTS / "append-timeline.py"), "--run-dir", str(self.run), "--role", "orchestrator", "--stage", "final", "--status", "pass", "--summary", "Done"]
        result = subprocess.run(final, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("finalize", result.stderr)
        before = dict(journal.JournalSnapshot.open(self.run).documents)
        late = subprocess.run([*final[:-3], "pass", "--summary", "Late"], capture_output=True, text=True)
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

    def test_jd07_cli_publish_absolute_reference_and_capture_directories(self):
        source = self.run.parent / "scope.md"
        source.write_bytes(b"original scope bytes")
        capture = self.run.parent / "summary-capture.json"
        capture.write_text(json.dumps({"verification": {"task_scope": {"path": str(source)}}}))
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                 "publish", "--file", "delegation-summary.json", str(capture)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = journal.JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.read_bytes(source), b"original scope bytes")
        self.assertTrue(snapshot.is_dir("artifacts/source-captures"))
        identifier = json.loads(result.stdout)["operation_id"]
        other_source = self.run.parent / "other.md"
        other_source.write_bytes(b"other reference")
        other_capture = self.run.parent / "other-summary.json"
        other_capture.write_text(json.dumps({"verification": {"task_scope": {"path": str(other_source)}}}))
        other = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                "publish", "--file", "delegation-summary.json", str(other_capture)],
                               capture_output=True, text=True)
        self.assertEqual(other.returncode, 0, other.stderr)
        repeat = subprocess.run([sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(self.run),
                                 "publish", "--file", "delegation-summary.json", str(capture), "--operation-id", identifier],
                                capture_output=True, text=True)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout)["revision"], snapshot.revision)
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes(other_source), b"other reference")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / "legacy"
        self.run.mkdir()
        (self.run / "context.md").write_bytes(b"exact\r\nbytes\n")
        (self.run / "data.json").write_bytes(b'{ "preserve" : true }\n')

    def test_jd06_read_does_not_import_and_reuse_preserves_bytes(self):
        old = journal.JournalSnapshot.open(self.run)
        self.assertFalse(old.durable)
        self.assertFalse((self.run / ".journal").exists())
        migrated = journal.import_legacy(self.run)
        self.assertEqual(dict(old.documents), dict(migrated.documents))
        again = journal.import_legacy(self.run)
        self.assertEqual((again.run_uuid, again.revision), (migrated.run_uuid, migrated.revision))
        self.assertEqual((self.run / "context.md").read_bytes(), b"exact\r\nbytes\n")

    def test_jd07_absolute_declared_refs_ignore_diagnostic_paths(self):
        target = self.run.parent / "scope.md"
        target.write_bytes(b"original scope")
        summary = {"verification": {"task_scope": {"path": str(target)}}}
        (self.run / "delegation-summary.json").write_text(json.dumps(summary))
        (self.run / "diagnostics.json").write_text(json.dumps({"path": "/missing/unrelated"}))
        old = journal.JournalSnapshot.open(self.run)
        self.assertEqual(old.read_bytes(target), b"original scope")
        self.assertFalse((self.run / ".journal").exists())
        migrated = journal.import_legacy(self.run)
        self.assertEqual(dict(old.documents), dict(migrated.documents))
        target.write_bytes(b"later change")
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes(target), b"original scope")

    def test_jd06_malformed_and_concurrent_import_refuse(self):
        (self.run / "data.json").write_bytes(b"{broken")
        with self.assertRaises(json.JSONDecodeError):
            journal.import_legacy(self.run)
        self.assertFalse((self.run / ".journal/state.sqlite3").exists())
        (self.run / "data.json").write_bytes(b"{}")
        original = journal._put_documents
        def change(connection, documents):
            original(connection, documents)
            (self.run / "context.md").write_bytes(b"changed")
        with patch.object(journal, "_put_documents", change), self.assertRaisesRegex(journal.JournalError, "changed"):
            journal.import_legacy(self.run)
        self.assertFalse(journal.JournalSnapshot.open(self.run).durable)
        self.assertTrue(journal.import_legacy(self.run).durable)

    def test_jd06_sigkill_import_before_and_after_run_state(self):
        for point in ("documents", "committed"):
            with self.subTest(point=point):
                run = self.root / point
                shutil.copytree(self.run, run)
                code = '''
import sys, os, signal
from pathlib import Path
import journal_io as j
original = j._put_documents
def put(connection, documents):
    original(connection, documents)
    if sys.argv[2] == 'documents': os.kill(os.getpid(), signal.SIGKILL)
j._put_documents = put
j.import_legacy(Path(sys.argv[1]))
os.kill(os.getpid(), signal.SIGKILL)
'''
                result = child(code, run, point)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                snapshot = journal.JournalSnapshot.open(run)
                self.assertEqual(snapshot.durable, point == "committed")
                self.assertEqual(snapshot.read_bytes("context.md"), b"exact\r\nbytes\n")
                self.assertTrue(journal.import_legacy(run).durable)

    def test_jd06_missing_committed_state_never_falls_back(self):
        journal.import_legacy(self.run)
        connection = journal.connect_database(self.run)
        connection.execute("DELETE FROM run_state")
        connection.close()
        with self.assertRaisesRegex(journal.JournalError, "fallback forbidden"):
            journal.JournalSnapshot.open(self.run)

    def test_jd06_deleted_committed_database_refuses_legacy(self):
        journal.import_legacy(self.run)
        (self.run / ".journal/state.sqlite3").unlink()
        for reader in (journal.JournalSnapshot.open, journal.import_legacy):
            with self.subTest(reader=reader.__name__):
                with self.assertRaisesRegex(journal.JournalError, "missing|unavailable"):
                    reader(self.run)

    def test_jd06_deleted_database_all_cli_consumers_fail_closed(self):
        init = [sys.executable, "-B", str(SCRIPTS / "init-run.py"), "--repo", str(self.root),
                "--date", "2026-09-13", "--slug", "loss", "--mode", "compact"]
        result = subprocess.run(init, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.root / ".agent-work/runs/2026-09-13-loss"
        before = journal.JournalSnapshot.open(run)
        for name, data in before.documents.items():
            if data is not None:
                target = run / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        journal.transact(run, "new", {}, lambda snapshot: ({"context.md": b"new committed context"}, {}))
        database = run / ".journal/state.sqlite3"
        database.unlink()
        commands = [init + ["--reuse"],
                    [sys.executable, "-B", str(SCRIPTS / "journal.py"), "--run-dir", str(run), "read", "context.md"],
                    [sys.executable, "-B", str(SCRIPTS / "validate-run.py"), "--run-dir", str(run), "--allow-pending"],
                    [sys.executable, "-B", str(SCRIPTS / "append-timeline.py"), "--run-dir", str(run),
                     "--role", "orchestrator", "--stage", "checks", "--status", "pass", "--summary", "late"]]
        for command in commands:
            with self.subTest(command=Path(command[2]).name):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing", result.stderr)
                self.assertFalse(database.exists())

    def test_jd06_lifecycle_rejects_zero_foreign_and_missing_race(self):
        for damage in ("zero", "foreign", "race"):
            with self.subTest(damage=damage):
                run = self.root / ("loss-" + damage)
                shutil.copytree(self.run, run)
                journal.import_legacy(run)
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
                    with self.assertRaises(journal.JournalError):
                        journal.import_legacy(run)

    def test_jd06_lifecycle_crash_windows_resume_exact_bootstrap(self):
        for point in ("schema", "database-sync", "marker-before-sync", "marker-after-sync"):
            with self.subTest(point=point):
                run = self.root / ("crash-" + point)
                shutil.copytree(self.run, run)
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
j.import_legacy(run)
'''
                result = child(code, run, point)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                snapshot = journal.JournalSnapshot.open(run)
                self.assertFalse(snapshot.durable)
                self.assertEqual(snapshot.read_bytes("context.md"), b"exact\r\nbytes\n")
                self.assertTrue(journal.import_legacy(run).durable)
                self.assertTrue(journal.storage_required(run))

    def test_jd05_marker_sync_failure_precedes_domain_commit(self):
        original = journal.sync_file
        fired = []
        def fail_marker(fd):
            marker = self.run / ".journal/storage-required"
            if marker.exists() and marker.stat().st_ino == os.fstat(fd).st_ino:
                fired.append(True)
                raise OSError("marker sync failure")
            original(fd)
        with patch.object(journal, "sync_file", fail_marker):
            with self.assertRaisesRegex(OSError, "marker sync failure"):
                journal.import_legacy(self.run)
        self.assertEqual(fired, [True])
        connection = journal.connect_database(self.run)
        self.assertEqual(connection.execute("SELECT count(*) FROM run_state").fetchone()[0], 0)
        journal.validate_bootstrap(connection)
        connection.close()
        self.assertFalse(journal.JournalSnapshot.open(self.run).durable)
        self.assertTrue(journal.import_legacy(self.run).durable)

    def test_jd05_marker_directory_sync_order_and_failure(self):
        original_file, original_directory, original_put = journal.sync_file, journal.sync_directory, journal._put_documents
        observed = []
        def file_sync(fd):
            original_file(fd)
            marker = self.run / ".journal/storage-required"
            if marker.exists() and marker.stat().st_ino == os.fstat(fd).st_ino:
                observed.append("marker-file-synced")
        def directory_sync(path):
            if "marker-file-synced" in observed and Path(path) == self.run / ".journal":
                observed.append("marker-directory-sync-failed")
                raise OSError("marker directory sync failure")
            original_directory(path)
        with patch.object(journal, "sync_file", file_sync), patch.object(journal, "sync_directory", directory_sync):
            with self.assertRaisesRegex(OSError, "marker directory sync failure"):
                journal.import_legacy(self.run)
        self.assertEqual(observed, ["marker-file-synced", "marker-directory-sync-failed"])
        connection = journal.connect_database(self.run)
        journal.validate_bootstrap(connection)
        connection.close()
        observed.clear()
        def successful_directory_sync(path):
            original_directory(path)
            if "marker-file-synced" in observed and Path(path) == self.run / ".journal":
                observed.append("marker-directory-synced")
        def put(connection, documents):
            self.assertIn("marker-directory-synced", observed)
            return original_put(connection, documents)
        with patch.object(journal, "sync_file", file_sync), patch.object(journal, "sync_directory", successful_directory_sync), patch.object(journal, "_put_documents", put):
            self.assertTrue(journal.import_legacy(self.run).durable)

    def test_jd06_adopt_existing_committed_storage_without_document_change(self):
        before = journal.import_legacy(self.run)
        (self.run / ".journal/storage-required").unlink()
        after = journal.import_legacy(self.run)
        self.assertTrue(journal.storage_required(self.run))
        self.assertEqual((before.run_uuid, before.revision, dict(before.documents)),
                         (after.run_uuid, after.revision, dict(after.documents)))

    def test_jd07_legacy_file_alias_identity_survives_import(self):
        target = self.root / "outside-run-inside-source"
        target.write_bytes(b"source handoff")
        (self.run / "qa.md").symlink_to(target)
        (self.run / "alias.md").hardlink_to(target)
        (self.run / "review.md").write_bytes(b"source handoff")
        from verification_evidence import require_own_reviewer_handoff
        old = journal.JournalSnapshot.open(self.run)
        migrated = journal.import_legacy(self.run)
        self.assertEqual(old.read_bytes("qa.md"), migrated.read_bytes("qa.md"))
        for snapshot in (old, migrated):
            with self.assertRaisesRegex(journal.JournalError, "own handoff"):
                require_own_reviewer_handoff(self.run, "alias.md", "qa.md", snapshot=snapshot)
            require_own_reviewer_handoff(self.run, "review.md", "qa.md", snapshot=snapshot)
        view = journal.export_snapshot(migrated)
        with self.assertRaises(journal.JournalError):
            require_own_reviewer_handoff(self.run, str(view / "qa.md"), "qa.md", snapshot=migrated)


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
                    snapshot = journal.import_legacy(run)
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
        snapshot = journal.import_legacy(self.run)
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
        targets = self.root / "legacy-targets"
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
        snapshot = journal.import_legacy(relocated, source_root=self.root)
        self.assertEqual(result_hash(relocated, self.summary["verification"], snapshot=snapshot), before)
        self.assertEqual(validate_verification(relocated, self.summary, {}, "ship", self.source, snapshot=snapshot), [])
        journal.export_snapshot(snapshot)
        self.assertEqual(validate_verification(relocated, self.summary, {}, "ship", self.source), [])
        with self.assertRaisesRegex(journal.JournalError, "cannot be overridden"):
            journal.import_legacy(relocated, source_root=relocated.parent)


if __name__ == "__main__":
    unittest.main()
