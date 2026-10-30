#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import journal_io as journal


SCRIPTS = Path(__file__).resolve().parent


def initialize_current(run, documents, **kwargs):
    documents.setdefault('task.json', journal.encode_json({'contract_version': 2}).encode())
    return journal.initialize_journal(run, documents, **kwargs)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-flow-storage-test-")
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve() / "run"
        self.initial = {"scope.json": b"old", "nested/second": b"old"}
        initialize_current(self.run, self.initial, source_root=self.run.parent,
                                   identities={"root_agent_id": "root"}, identifier="init-task")

    def test_document_updates_deletion_and_receipt_are_atomic(self):
        receipt = journal.transact(self.run, "update", {"request": 1}, lambda snapshot: (
            {"scope.json": b"new", "nested/third": b"new", "report.json": b'{"status":"completed"}'},
            {"report": "report.json"}))
        current = journal.JournalSnapshot.open(self.run)
        self.assertEqual(current.read_bytes("scope.json"), b"new")
        self.assertEqual(current.read_bytes("nested/second"), b"old")
        self.assertEqual(current.json("report.json"), {"status": "completed"})
        self.assertEqual(current.operation_receipt("update"), receipt)
        self.assertEqual(receipt["result"], {"report": "report.json"})
        self.assertEqual(current.revision, receipt["revision"])

    def test_legacy_database_reads_without_conversion_and_refuses_writes(self):
        run = self.run.parent / 'legacy'
        journal.initialize_journal(run, {'task.json': b'{"summary":"Legacy task"}'}, source_root=self.run.parent)
        database = run / '.journal/state.sqlite3'
        original = database.read_bytes()
        snapshot = journal.JournalSnapshot.open(run)
        self.assertEqual(snapshot.json('task.json'), {'summary': 'Legacy task'})
        with self.assertRaisesRegex(journal.JournalError, 'read-only'):
            journal.transact(run, 'legacy-write', {}, lambda current: ({'artifacts/new': b'new'}, {}))
        self.assertEqual(database.read_bytes(), original)

    def test_failure_between_documents_rolls_back_all_state(self):
        old = journal.JournalSnapshot.open(self.run)
        original = journal._put_documents
        def failed(connection, documents):
            original(connection, {"scope.json": documents["scope.json"]})
            raise OSError("failure after first document")
        with patch.object(journal, "_put_documents", failed), self.assertRaisesRegex(OSError, "after first"):
            journal.transact(self.run, "partial", {}, lambda snapshot: (
                {"scope.json": b"new", "nested/second": b"new"}, {}))
        current = journal.JournalSnapshot.open(self.run)
        self.assertEqual((old.documents, old.revision, old.operations),
                         (current.documents, current.revision, current.operations))

    def test_sigkill_before_during_and_after_commit(self):
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
j.transact(run, 'kill', {}, lambda snapshot: ({'new-'+str(i): bytes([65+i])*1048576 for i in range(2)}, {}))
os.kill(os.getpid(), signal.SIGKILL)
'''
        for point in ("before", "0", "1", "after"):
            with self.subTest(point=point):
                run = self.run.parent / ("kill-" + point)
                old = {str(i): b"old" for i in range(2)}
                initialize_current(run, old, source_root=self.run.parent)
                result = subprocess.run([sys.executable, "-B", "-c", code, str(run), point],
                                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SCRIPTS)},
                                        capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                current = journal.JournalSnapshot.open(run)
                expected = {**old, **{'new-'+str(i): bytes([65+i])*1048576 for i in range(2)}} if point == "after" else old
                self.assertEqual(dict(current.documents), expected)
                self.assertEqual(current.operation_receipt("kill") is not None, point == "after")

    def test_busy_is_explicit_and_fresh_retry_succeeds(self):
        lock = journal.connect_database(self.run)
        lock.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaisesRegex(journal.JournalError, "locked"):
                journal.transact(self.run, "busy", {}, lambda snapshot: self.fail("publisher called"), timeout=0)
        finally:
            lock.rollback()
            lock.close()
        current = journal.JournalSnapshot.open(self.run)
        self.assertEqual(dict(current.documents), self.initial)
        self.assertIsNone(current.operation_receipt("busy"))
        receipt = journal.transact(self.run, "busy", {}, lambda snapshot: ({"scope.json": b"retry"}, {}))
        self.assertEqual(receipt["revision"], current.revision + 1)

    def test_parallel_writers_read_the_latest_committed_state(self):
        journal.transact(self.run, "scope.json", {}, lambda snapshot: ({"scope.json": b"0"}, {}))
        code = '''
import sys
import journal_io as j
def increment(snapshot):
    value = int(snapshot.read_bytes('scope.json')) + 1
    return {'scope.json': str(value).encode()}, {'scope.json': value}
j.transact(sys.argv[1], sys.argv[2], {}, increment)
'''
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code, str(self.run), f"writer-{index}"],
                                      env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SCRIPTS)},
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for index in range(3)]
        try:
            for process in processes:
                output, error = process.communicate(timeout=25)
                self.assertEqual(process.returncode, 0, output + error)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        snapshot = journal.JournalSnapshot.open(self.run)
        self.assertEqual(snapshot.read_bytes("scope.json"), b"3")
        self.assertEqual(sorted(snapshot.operation_receipt(f"writer-{index}")["result"]["scope.json"]
                                for index in range(3)), [1, 2, 3])

    def test_snapshot_is_pinned(self):
        old = journal.JournalSnapshot.open(self.run)
        journal.transact(self.run, "replace", {}, lambda snapshot: ({"scope.json": b"new"}, {}))
        self.assertEqual(old.read_bytes("scope.json"), b"old")
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes("scope.json"), b"new")
        with self.assertRaises(TypeError):
            old.documents["scope.json"] = b"changed"

    def test_replay_precedes_publication_and_closed_guard(self):
        receipt = journal.transact(self.run, "accept", {"input": 1}, lambda snapshot: (
            {"scope.json": b"accepted"}, {"report": "scope.json"}))
        journal.transact(self.run, "close", {}, lambda snapshot: (
            {"final.json": b'{"status":"failed","reason":"verification unavailable"}'}, {}))
        self.assertEqual(journal.transact(self.run, "accept", {"input": 1},
                                         lambda snapshot: self.fail("replayed publisher")), receipt)
        with self.assertRaisesRegex(journal.JournalError, "payload conflict"):
            journal.transact(self.run, "accept", {"input": 2}, lambda snapshot: self.fail("conflict publisher"))
        with self.assertRaisesRegex(journal.JournalError, "closed"):
            journal.transact(self.run, "late", {}, lambda snapshot: self.fail("late publisher"))

    def test_ambiguous_commit_recovers_saved_receipt(self):
        original = journal.connect_database
        calls = []
        class LostReply:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                self.connection.commit()
                raise sqlite3.OperationalError("lost commit reply")
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            calls.append(connection)
            return LostReply(connection) if len(calls) == 1 else connection
        with patch.object(journal, "connect_database", connect):
            receipt = journal.transact(self.run, "ambiguous", {}, lambda snapshot: (
                {"scope.json": b"accepted"}, {"accepted": True}))
        self.assertEqual(len(calls), 2)
        self.assertEqual(journal.JournalSnapshot.open(self.run).operation_receipt("ambiguous"), receipt)

    def test_failed_commit_does_not_claim_success(self):
        original = journal.connect_database
        calls = []
        class FailedCommit:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def commit(self):
                raise sqlite3.OperationalError("write failed")
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            calls.append(connection)
            return FailedCommit(connection) if len(calls) == 1 else connection
        with patch.object(journal, "connect_database", connect), self.assertRaisesRegex(journal.JournalError, "write failed"):
            journal.transact(self.run, "failed", {}, lambda snapshot: ({"scope.json": b"not committed"}, {}))
        self.assertEqual(journal.JournalSnapshot.open(self.run).read_bytes("scope.json"), b"old")
        self.assertIsNone(journal.JournalSnapshot.open(self.run).operation_receipt("failed"))

    def test_full_database_and_io_error_preserve_state(self):
        original = journal.connect_database
        def limited(*args, **kwargs):
            connection = original(*args, **kwargs)
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={pages}")
            return connection
        with patch.object(journal, "connect_database", limited), self.assertRaisesRegex(journal.JournalError, "full"):
            journal.transact(self.run, "full", {}, lambda snapshot: ({"scope.json": b"x" * 4194304}, {}))
        with patch.object(journal, "_put_documents", side_effect=PermissionError("denied")), self.assertRaises(PermissionError):
            journal.transact(self.run, "denied", {}, lambda snapshot: ({"scope.json": b"new"}, {}))
        self.assertEqual(dict(journal.JournalSnapshot.open(self.run).documents), self.initial)

    def test_sqlite_settings_and_no_side_documents(self):
        connection = journal.connect_database(self.run)
        try:
            self.assertEqual(dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")), journal.STORAGE_SCHEMA)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 3)
            self.assertEqual(connection.execute("PRAGMA fullfsync").fetchone()[0], int(sys.platform == "darwin"))
        finally:
            connection.close()
        self.assertEqual(list(self.run.iterdir()), [self.run / ".journal"])
        self.assertEqual(list((self.run / ".journal").iterdir()), [self.run / ".journal/state.sqlite3"])

    def test_initialize_retry_conflict_and_no_flat_fallback(self):
        initial = journal.JournalSnapshot.open(self.run)
        repeated = initialize_current(self.run, self.initial, source_root=self.run.parent,
                                              identities={"root_agent_id": "root"}, identifier="init-task")
        self.assertEqual(initial.run_uuid, repeated.run_uuid)
        with self.assertRaisesRegex(journal.JournalError, "initialization payload conflict"):
            initialize_current(self.run, {"scope.json": b"changed"}, source_root=self.run.parent, identifier="init-task")
        missing = self.run.parent / "missing"
        missing.mkdir()
        (missing / "report.json").write_text('{"status":"completed"}')
        with self.assertRaises(journal.JournalError):
            journal.JournalSnapshot.open(missing)

    def test_corrupt_document_refuses_read(self):
        with sqlite3.connect(self.run / ".journal/state.sqlite3") as connection:
            connection.execute("UPDATE documents SET content=? WHERE path='scope.json'", (b"tampered",))
        with self.assertRaisesRegex(journal.JournalError, "corrupt journal document"):
            journal.JournalSnapshot.open(self.run)

    def test_initialize_semantic_retry_preserves_current_documents(self):
        run = self.run.parent / "semantic-init"
        request = {"kind": "initialize", "request": {"summary": "Edit selected source"}}
        first = initialize_current(run, {"scope.json": b'{"initial":"old"}'},
                                           source_root=self.run.parent, identifier="semantic-init", payload=request)
        journal.transact(run, "expanded", {}, lambda snapshot: (
            {"scope.json": b'{"initial":"old","additional":"new"}', "reports/author.json": b'{}'}, {}))
        before = journal.JournalSnapshot.open(run)
        repeated = initialize_current(run, {"scope.json": b'{"initial":"recomputed"}'},
                                              source_root=self.run.parent, identifier="semantic-init", payload=request)
        self.assertEqual(first.run_uuid, repeated.run_uuid)
        self.assertEqual((before.revision, before.documents, before.operations),
                         (repeated.revision, repeated.documents, repeated.operations))
        with self.assertRaisesRegex(journal.JournalError, "initialization payload conflict"):
            initialize_current(run, {}, source_root=self.run.parent, identifier="semantic-init",
                                       payload={"kind": "initialize", "request": {"summary": "Different task"}})

    def test_capture_refuses_symlink_and_symlink_parent(self):
        target = self.run.parent / "original"
        target.write_bytes(b"original")
        alias = self.run.parent / "alias"
        alias.symlink_to(target)
        with self.assertRaises(journal.JournalError):
            journal.capture_file(alias)
        folder = self.run.parent / "folder-alias"
        folder.symlink_to(self.run.parent, target_is_directory=True)
        with self.assertRaises(journal.JournalError):
            journal.capture_file(folder / "original")
        self.assertEqual(journal.capture_file(target), b"original")

    def test_storage_refuses_link_aliases_without_modifying_target(self):
        target = self.run.parent / "original"
        target.write_bytes(b"outside bytes")
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                run = self.run.parent / kind
                (run / ".journal").mkdir(parents=True)
                path = run / ".journal/state.sqlite3"
                path.symlink_to(target) if kind == "symlink" else os.link(target, path)
                with self.assertRaises(journal.JournalError):
                    initialize_current(run, {}, source_root=self.run.parent)
                self.assertEqual(target.read_bytes(), b"outside bytes")

    def test_unsafe_logical_path_rolls_back(self):
        for name in ("../escape", "/absolute", ".journal/state.sqlite3", "nested/../escape", "nested//file"):
            with self.subTest(name=name), self.assertRaises(journal.JournalError):
                journal.transact(self.run, name, {}, lambda snapshot: ({name: b"bad"}, {}))
        self.assertEqual(dict(journal.JournalSnapshot.open(self.run).documents), self.initial)


if __name__ == "__main__":
    unittest.main()
