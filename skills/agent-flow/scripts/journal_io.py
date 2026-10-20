from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
import uuid


class JournalError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise JournalError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def timestamp(value):
    if type(value) is int:
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            raise JournalError("invalid source Unix timestamp (seconds required)") from exc
    require(isinstance(value, str), "source timestamp missing or invalid type")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JournalError("invalid source timestamp") from exc
    require(parsed.tzinfo is not None, "source timestamp requires timezone")
    return parsed


@dataclass(frozen=True)
class SourceTime:
    event_at: datetime
    event_before: datetime | None
    event_origin: str
    observed_at: datetime | None


def source_time(event, field):
    payload = event.get("payload", {})
    require(isinstance(payload, dict), "source payload must be an object")
    outer = timestamp(event["timestamp"]) if "timestamp" in event else None
    origin = "payload" if field in payload else "outer"
    value = payload[field] if field in payload else event.get("timestamp")
    start = timestamp(value)
    try:
        before = start + timedelta(seconds=1) if type(value) is int else None
    except OverflowError as exc:
        raise JournalError("invalid source timestamp interval") from exc
    return SourceTime(start, before, origin, outer if isinstance(event.get("timestamp"), str) else None)


def event_time(event, field):
    value = source_time(event, field)
    return value.event_at, value.event_before


def logical_path(value):
    require(isinstance(value, str) and bool(value), "journal path must be nonempty text")
    path = Path(value)
    require(value != "." and not path.is_absolute() and path.as_posix() == value
            and ".." not in path.parts and "\\" not in value
            and path.parts[0] != ".journal", f"unsafe journal path: {value}")
    return value


def validate_directory(path):
    path = Path(path)
    require(not path.exists() or path.is_dir(), f"directory unavailable: {path}")
    require(not path.is_symlink() and all(not parent.is_symlink() for parent in path.parents),
            f"directory must not contain symlinks: {path}")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_file(fd):
    os.fsync(fd)
    if sys.platform == "darwin":
        fcntl.fcntl(fd, getattr(fcntl, "F_FULLFSYNC", 51))


def durable_mkdir(path):
    path = Path(path)
    validate_directory(path)
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        validate_directory(directory)
        sync_directory(directory)
        sync_directory(directory.parent)


def capture_file(path):
    path = Path(path).absolute()
    validate_directory(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), f"capture must be a regular file: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode) and (before.st_dev, before.st_ino) ==
                (opened.st_dev, opened.st_ino), f"capture changed before read: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read()
        after = os.fstat(fd)
        current = path.lstat()
        def identity(item):
            return (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                    item.st_mtime_ns, item.st_ctime_ns)
        require(identity(before) == identity(after) == identity(current),
                f"capture changed during read: {path}")
        return data
    finally:
        os.close(fd)


STORAGE_SCHEMA = {
    "run_state": "CREATE TABLE run_state (id INTEGER PRIMARY KEY CHECK(id=1), run_uuid TEXT NOT NULL, revision INTEGER NOT NULL, logical_root TEXT NOT NULL, source_root TEXT NOT NULL, identities TEXT NOT NULL)",
    "documents": "CREATE TABLE documents (path TEXT PRIMARY KEY, kind TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL)",
    "operations": "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, revision INTEGER NOT NULL, result TEXT NOT NULL)",
}


def validate_storage_schema(connection):
    schema = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
    require(schema == STORAGE_SCHEMA, "journal schema is not the current contract; start a new task")


def _database_identity(path):
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
            f"journal storage must be an unlinked regular file: {path}")
    return metadata.st_dev, metadata.st_ino


def connect_database(run_dir, *, timeout=2.0, create=False):
    path = Path(run_dir).absolute() / ".journal/state.sqlite3"
    validate_directory(path.parent)
    if create:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            sync_directory(path.parent)
    before = _database_identity(path)
    for suffix in ("-journal", "-wal", "-shm"):
        companion = path.with_name(path.name + suffix)
        if companion.exists() or companion.is_symlink():
            _database_identity(companion)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                 timeout=timeout, isolation_level=None)
    try:
        require(_database_identity(path) == before, "journal database changed while opening")
        settings = {"journal_mode": "DELETE", "synchronous": "EXTRA",
                    "read_uncommitted": "OFF",
                    "fullfsync": "ON" if sys.platform == "darwin" else "OFF"}
        for name, value in settings.items():
            connection.execute(f"PRAGMA {name}={value}")
        expected = {"journal_mode": "delete", "synchronous": 3,
                    "read_uncommitted": 0, "fullfsync": int(sys.platform == "darwin")}
        for name, value in expected.items():
            require(connection.execute(f"PRAGMA {name}").fetchone()[0] == value,
                    f"unsupported SQLite setting: {name}")
        return connection
    except BaseException:
        connection.close()
        raise


@dataclass(frozen=True)
class JournalSnapshot:
    logical_root: Path
    source_root: Path
    run_uuid: str
    revision: int
    identities: object
    documents: object
    operations: object

    @classmethod
    def from_connection(cls, run_dir, connection):
        validate_storage_schema(connection)
        row = connection.execute("SELECT run_uuid, revision, logical_root, source_root, identities FROM run_state WHERE id=1").fetchone()
        require(row is not None and isinstance(row[0], str) and bool(row[0])
                and type(row[1]) is int and row[1] >= 1,
                "journal task identity is missing or corrupt")
        require(Path(row[2]) == Path(run_dir).absolute() and Path(row[3]).is_absolute(),
                "journal location does not match its task identity")
        identities = json.loads(row[4])
        require(isinstance(identities, dict), "journal identities must be an object")
        documents = {}
        for name, kind, content, sha in connection.execute("SELECT path, kind, content, sha256 FROM documents"):
            logical_path(name)
            require(kind == "file" and isinstance(content, bytes) and digest(content) == sha,
                    f"corrupt journal document: {name}")
            documents[name] = content
        operations = {}
        for identifier, payload_sha, revision, result in connection.execute("SELECT operation_id, payload_sha256, revision, result FROM operations"):
            require(isinstance(identifier, str) and bool(identifier) and type(revision) is int
                    and 1 <= revision <= row[1], "corrupt journal operation")
            value = json.loads(result)
            require(isinstance(value, dict), "journal operation result must be an object")
            operations[identifier] = {"operation_id": identifier, "payload_sha256": payload_sha,
                                      "revision": revision, "result": value}
        require(sum(value["revision"] == 1 for value in operations.values()) == 1,
                "journal initialization receipt missing")
        return cls(Path(row[2]), Path(row[3]), row[0], row[1], MappingProxyType(identities),
                   MappingProxyType(documents), MappingProxyType(operations))

    @classmethod
    def open(cls, run_dir):
        run_dir = Path(run_dir).absolute()
        connection = None
        try:
            connection = connect_database(run_dir)
            connection.execute("BEGIN")
            snapshot = cls.from_connection(run_dir, connection)
            connection.commit()
            return snapshot
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            raise JournalError(f"journal unavailable: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def exists(self, path):
        return logical_path(path) in self.documents

    def read_bytes(self, path):
        key = logical_path(path)
        if key not in self.documents:
            raise FileNotFoundError(f"unpublished journal document: {key}")
        return self.documents[key]

    def read_text(self, path):
        return self.read_bytes(path).decode("utf-8")

    def json(self, path):
        return json.loads(self.read_bytes(path))

    def operation_receipt(self, identifier):
        value = self.operations.get(identifier)
        return json.loads(encode_json(value)) if value is not None else None


def _put_documents(connection, documents):
    for name, data in documents.items():
        logical_path(name)
        require(data is None or isinstance(data, bytes), f"journal content must be bytes: {name}")
        if data is None:
            connection.execute("DELETE FROM documents WHERE path=?", (name,))
        else:
            connection.execute("INSERT OR REPLACE INTO documents VALUES (?, 'file', ?, ?)",
                               (name, data, digest(data)))


def initialize_journal(run_dir, documents, *, source_root, identities=None, identifier="initialize", payload=None):
    run_dir = Path(run_dir).absolute()
    source_root = Path(source_root).absolute()
    require(isinstance(identifier, str) and bool(identifier.strip()), "operation ID is required")
    require(isinstance(documents, dict) and all(isinstance(value, bytes) for value in documents.values()),
            "initial documents must contain bytes")
    require("final.json" not in documents, "new task cannot be completed at initialization")
    require(isinstance(identities or {}, dict), "identities must be an object")
    if payload is None:
        payload = {"documents": {logical_path(name): digest(data) for name, data in documents.items()},
                   "source_root": str(source_root), "identities": identities or {}}
    payload_sha = digest(encode_json(payload).encode())
    durable_mkdir(run_dir / ".journal")
    connection = connect_database(run_dir, create=True)
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = list(connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        if tables:
            snapshot = JournalSnapshot.from_connection(run_dir, connection)
            previous = snapshot.operation_receipt(identifier)
            require(previous is not None and previous["revision"] == 1
                    and previous["payload_sha256"] == payload_sha,
                    "initialization payload conflict")
        else:
            for statement in STORAGE_SCHEMA.values():
                connection.execute(statement)
            _put_documents(connection, documents)
            connection.execute("INSERT INTO run_state VALUES (1, ?, 1, ?, ?, ?)",
                               (str(uuid.uuid4()), str(run_dir), str(source_root), encode_json(identities or {})))
            connection.execute("INSERT INTO operations VALUES (?, ?, 1, '{}')", (identifier, payload_sha))
        connection.commit()
        sync_directory(run_dir / ".journal")
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return JournalSnapshot.open(run_dir)


def transact(run_dir, identifier, payload, publisher, *, timeout=2.0):
    require(isinstance(identifier, str) and bool(identifier.strip()), "operation ID is required")
    require(identifier != "initialize", "initialize is a reserved operation ID")
    payload_sha = digest(encode_json(payload).encode())
    connection = None
    try:
        connection = connect_database(run_dir, timeout=timeout)
        connection.execute("BEGIN IMMEDIATE")
        snapshot = JournalSnapshot.from_connection(run_dir, connection)
        previous = snapshot.operation_receipt(identifier)
        if previous is not None:
            require(previous["payload_sha256"] == payload_sha, "operation ID payload conflict")
            return previous
        require(not snapshot.exists("final.json"), "task is closed")
        updates, result = publisher(snapshot)
        require(isinstance(updates, dict) and isinstance(result, dict),
                "publisher must return document updates and an object result")
        _put_documents(connection, updates)
        revision = snapshot.revision + 1
        connection.execute("UPDATE run_state SET revision=? WHERE id=1", (revision,))
        connection.execute("INSERT INTO operations VALUES (?, ?, ?, ?)",
                           (identifier, payload_sha, revision, encode_json(result)))
        try:
            connection.commit()
        except sqlite3.Error:
            connection.close()
            connection = None
            recovered = JournalSnapshot.open(run_dir).operation_receipt(identifier)
            if recovered is not None and recovered["payload_sha256"] == payload_sha:
                return recovered
            raise
        return {"operation_id": identifier, "payload_sha256": payload_sha,
                "revision": revision, "result": result}
    except sqlite3.DatabaseError as exc:
        raise JournalError(f"journal transaction failed without confirmed receipt: {exc}") from exc
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()


def write_entry(run_dir, kind, request, *, identifier, source=None):
    from journal_structure import write_entry as semantic_write_entry
    return semantic_write_entry(run_dir, kind, request, identifier=identifier, source=source)
