"""Shared journal mechanics and source-checked completion writes."""
from __future__ import annotations

import json
import base64
import os
import re
import hashlib
import sqlite3
import sys
import tempfile
import shutil
import uuid
import stat
import fnmatch
import fcntl
from types import MappingProxyType
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HANDOFF_STATE_MODES = {"task", "batch"}
HANDOFF_STATE_STATUSES = {"queued", "accepted", "completed", "blocked", "failed"}
STATUS_TIMESTAMP_FIELDS = {
    "queued": "queued_at",
    "accepted": "accepted_at",
    "completed": "completed_at",
    "blocked": "completed_at",
    "failed": "completed_at",
}


def validate_mode(mode: str) -> None:
    if mode not in HANDOFF_STATE_MODES:
        allowed = ", ".join(sorted(HANDOFF_STATE_MODES))
        raise RuntimeError(f"invalid handoff_state mode {mode!r}; expected one of: {allowed}")


def validate_status(status: str) -> None:
    if status not in HANDOFF_STATE_STATUSES:
        allowed = ", ".join(sorted(HANDOFF_STATE_STATUSES))
        raise RuntimeError(f"invalid handoff_state status {status!r}; expected one of: {allowed}")


def normalize_existing_state(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError("existing handoff_state must be a JSON object")
    return dict(value)


def update_handoff_state(
    *,
    lane: dict[str, Any],
    lane_id: str,
    status: str,
    mode: str | None,
    from_lane: str | None,
    to_lane: str | None,
    task: str | None,
    handoff: str | None,
    batch_id: str | None,
    batch_items: list[str],
) -> dict[str, Any]:
    validate_status(status)

    lane_handoff = lane.get("handoff")
    if handoff and isinstance(lane_handoff, str) and lane_handoff and handoff != lane_handoff:
        raise RuntimeError(
            f"handoff must match lane handoff: expected {lane_handoff!r}, got {handoff!r}"
        )

    state = normalize_existing_state(lane.get("handoff_state"))
    selected_mode = mode or state.get("mode") or ("batch" if batch_id or batch_items else "task")
    if not isinstance(selected_mode, str):
        raise RuntimeError("handoff_state.mode must be a string")
    validate_mode(selected_mode)

    if selected_mode == "batch":
        existing_batch = state.get("batch") if isinstance(state.get("batch"), dict) else {}
        selected_batch_id = batch_id or existing_batch.get("id")
        existing_items = existing_batch.get("items", [])
        if not isinstance(existing_items, list):
            raise RuntimeError("existing handoff_state.batch.items must be an array")
        selected_items = [*existing_items, *batch_items]
        if not all(isinstance(item, str) and item for item in selected_items):
            raise RuntimeError("handoff_state.batch.items must contain only non-empty strings")
        selected_items = list(dict.fromkeys(selected_items))
        if not isinstance(selected_batch_id, str) or not selected_batch_id:
            raise RuntimeError("batch mode requires --batch-id or existing handoff_state.batch.id")
        if not selected_items:
            raise RuntimeError("batch mode requires at least one --batch-item or existing batch item")
        state["batch"] = {"id": selected_batch_id, "items": selected_items}
    elif batch_id or batch_items:
        raise RuntimeError("--batch-id and --batch-item require --mode batch")

    selected_handoff = handoff
    if selected_handoff is None and isinstance(lane_handoff, str):
        selected_handoff = lane_handoff
    if selected_handoff is None:
        selected_handoff = state.get("handoff")
    if not isinstance(selected_handoff, str) or not selected_handoff:
        raise RuntimeError("handoff_state.handoff is required; set lane handoff or pass --handoff")
    if isinstance(lane_handoff, str) and lane_handoff and selected_handoff != lane_handoff:
        raise RuntimeError(
            f"handoff_state.handoff must match lane handoff: expected {lane_handoff!r}, got {selected_handoff!r}"
        )

    state["version"] = 1
    state["mode"] = selected_mode
    state["status"] = status
    state["to"] = to_lane or state.get("to") or lane_id
    state["handoff"] = selected_handoff

    if from_lane:
        state["from"] = from_lane
    if task:
        state["task"] = task

    timestamp_field = STATUS_TIMESTAMP_FIELDS[status]
    if not state.get(timestamp_field):
        state[timestamp_field] = now_iso()

    return state




def load_artifact_index(path: Path, *, snapshot=None) -> tuple[list[Any], dict[str, Any] | None]:
    if not journal_exists(path, snapshot=snapshot):
        return [], None

    try:
        data = json.loads(journal_read_text(path, snapshot=snapshot) or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"artifacts.json invalid JSON: {exc}") from exc

    if isinstance(data, list):
        return data, None
    if isinstance(data, dict):
        artifacts = data.get("artifacts")
        if artifacts is None:
            artifacts = []
            data["artifacts"] = artifacts
        if not isinstance(artifacts, list):
            raise SystemExit("artifacts.json field 'artifacts' must be a JSON array")
        return artifacts, data

    raise SystemExit("artifacts.json must be a JSON array or an object with an artifacts array")


def artifact_index(artifacts: list[Any], container: dict[str, Any] | None):
    output: list[Any] | dict[str, Any]
    if container is None:
        output = artifacts
    else:
        container["artifacts"] = artifacts
        output = container

    return output


def upsert_artifacts(
    artifacts_path: Path,
    artifact_paths: list[str],
    *,
    role: str,
    execution_mode: str,
    stable_agent_name: str,
    stable_agent_slug: str,
    timestamp: str,
    lane_id: str | None,
    wave: int | None,
    critical: bool,
    snapshot=None,
) -> tuple[int, object]:
    if not artifact_paths:
        return 0, None

    artifacts, container = load_artifact_index(artifacts_path, snapshot=snapshot)
    indexed = 0

    for artifact_path in artifact_paths:
        entry = {
            "path": artifact_path,
            "role": role,
            "execution_mode": execution_mode,
            "stable_agent_name": stable_agent_name,
            "stable_agent_slug": stable_agent_slug,
            "source": "agent-trace",
            "timestamp": timestamp,
        }
        if lane_id:
            entry["lane_id"] = lane_id
        if wave is not None:
            entry["wave"] = wave
        if critical:
            entry["critical"] = critical

        match_index = next(
            (
                index
                for index, item in enumerate(artifacts)
                if isinstance(item, dict)
                and item.get("role") == role
                and item.get("path") == artifact_path
            ),
            None,
        )

        if match_index is None:
            artifacts.append(entry)
        else:
            existing = artifacts[match_index]
            artifacts[match_index] = {**existing, **entry}
        indexed += 1

    return indexed, artifact_index(artifacts, container)




class JournalError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def logical_path(value):
    value = str(value)
    path = Path(value)
    require(value and value != "." and not path.is_absolute() and
            path.as_posix() == value and ".." not in path.parts and
            "\\" not in value and path.parts[0] != ".journal", f"unsafe journal path: {value}")
    return value


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync_file(fd):
    os.fsync(fd)
    if sys.platform == "darwin":
        fcntl.fcntl(fd, 51)  # Darwin F_FULLFSYNC, absent from Python's fcntl constants.


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
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        require(identity(before) == identity(after) == identity(current), f"capture changed during read: {path}")
        return data
    finally:
        os.close(fd)


def durable_mkdir(path):
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


STORAGE_SCHEMA = {
    "run_state": "CREATE TABLE run_state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, run_uuid TEXT NOT NULL, revision INTEGER NOT NULL, logical_root TEXT NOT NULL, source_root TEXT NOT NULL, identities TEXT NOT NULL)",
    "documents": "CREATE TABLE documents (path TEXT PRIMARY KEY, kind TEXT NOT NULL, content BLOB, sha256 TEXT)",
    "operations": "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, revision INTEGER NOT NULL, result TEXT NOT NULL)",
}


ARCHIVE_SCHEMA = "CREATE TABLE archives (archive_id TEXT PRIMARY KEY, content BLOB NOT NULL, sha256 TEXT NOT NULL)"

def storage_required(run_dir):
    marker = Path(run_dir) / ".journal/storage-required"
    require(not marker.is_symlink(), "storage-required marker must not be a symlink")
    if not marker.exists():
        return False
    require(stat.S_ISREG(marker.stat().st_mode) and marker.stat().st_size == 0,
            "invalid storage-required marker")
    return True


def validate_storage_schema(connection):
    schema = dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
    require(schema == STORAGE_SCHEMA or schema == {**STORAGE_SCHEMA, "archives": ARCHIVE_SCHEMA}, "journal schema unavailable or corrupt; flat fallback forbidden")


def validate_bootstrap(connection):
    validate_storage_schema(connection)
    require(connection.execute("PRAGMA user_version").fetchone()[0] == 1 and all(
        connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == 0 for name in STORAGE_SCHEMA),
        "committed journal run_state missing; flat fallback forbidden")


def connect_database(run_dir, *, timeout=2.0, create=False):
    path = Path(run_dir) / ".journal/state.sqlite3"
    validate_directory(path.parent)
    require(not path.is_symlink(), "journal database must not be a symlink")
    if path.exists():
        require(stat.S_ISREG(path.stat().st_mode), "journal database must be a regular file")
    require(not create or not storage_required(run_dir), "journal database missing after lifecycle started")
    uri = path.absolute().as_uri() + ("?mode=rwc" if create else "?mode=rw")
    connection = sqlite3.connect(uri, uri=True, timeout=timeout, isolation_level=None)
    try:
        for name, value in (("journal_mode", "DELETE"), ("synchronous", "EXTRA"),
                            ("read_uncommitted", "OFF"), ("fullfsync", "ON" if sys.platform == "darwin" else "OFF")):
            connection.execute(f"PRAGMA {name}={value}")
        expected = {"journal_mode": "delete", "synchronous": 3, "read_uncommitted": 0,
                    "fullfsync": int(sys.platform == "darwin")}
        for name, value in expected.items():
            require(connection.execute(f"PRAGMA {name}").fetchone()[0] == value,
                    f"unsupported SQLite setting: {name}")
        return connection
    except BaseException:
        connection.close()
        raise


def probe_storage(run_dir):
    require(sys.platform in {"darwin", "linux"}, "journal requires local Darwin/Linux filesystem")
    directory = Path(run_dir) / ".journal"
    with tempfile.TemporaryFile(dir=directory) as handle:
        handle.write(b"journal sync probe")
        handle.flush()
        sync_file(handle.fileno())
    sync_directory(directory)
    first = connect_database(run_dir, timeout=0)
    second = connect_database(run_dir, timeout=0)
    try:
        first.execute("BEGIN IMMEDIATE")
        try:
            second.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            require("locked" in str(exc), f"SQLite lock probe failed: {exc}")
        else:
            second.rollback()
            raise JournalError("filesystem does not enforce SQLite write locks")
    finally:
        first.rollback()
        first.close()
        second.close()


def _source_root(run_dir):
    for parent in run_dir.parents:
        if parent.name == ".agent-work":
            return parent.parent
        if (parent / ".git").exists():
            return parent
    return run_dir.parent


def capture_flat(run_dir, *, allow_file_links=False, source_root=None):
    """Capture exact legacy bytes; fail if an entry changes during capture."""
    documents, identities = {}, {}
    def scan(directory):
        for path in sorted(directory.iterdir()):
            if path == run_dir / ".journal":
                continue
            name = path.relative_to(run_dir).as_posix()
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                require(allow_file_links, f"journal symlink unsupported: {name}")
                target = path.resolve(strict=True)
                require(target.is_relative_to(source_root or _source_root(run_dir)) and target.is_file(),
                        f"legacy journal symlink escapes source or is not a file: {name}")
                target_stat = target.lstat()
                documents[name] = capture_file(target)
                after = path.lstat()
                require((before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) ==
                        (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) and path.resolve(strict=True) == target,
                        f"legacy journal symlink changed: {name}")
                identities[name] = (target_stat.st_dev, target_stat.st_ino)
                continue
            if stat.S_ISDIR(before.st_mode):
                documents[name] = None
                scan(path)
            else:
                require(stat.S_ISREG(before.st_mode), f"legacy journal special file: {name}")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    opened = os.fstat(fd)
                    require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), f"legacy journal changed: {name}")
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        documents[name] = stream.read()
                    after = os.fstat(fd)
                    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                            (after.st_size, after.st_mtime_ns, after.st_ctime_ns), f"legacy journal changed: {name}")
                finally:
                    os.close(fd)
            after = path.lstat()
            require((before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns), f"legacy journal changed: {name}")
            identities[name] = (after.st_dev, after.st_ino)
    scan(run_dir)
    return documents, identities


@dataclass(frozen=True)
class JournalSnapshot:
    artifact_root: Path
    source_root: Path
    logical_root: Path
    run_uuid: str
    revision: int
    documents: object
    identities: object
    durable: bool
    receipts: object = field(default_factory=lambda: MappingProxyType({}))
    storage_version: int = 1
    external_inputs: object = None
    archives: object = field(default_factory=lambda: MappingProxyType({}))

    @property
    def generation(self):
        return 1 + sum(event.get("stage") == "reopen" for event in self.timeline())

    def timeline(self):
        return [json.loads(line) for line in self.documents.get("timeline.jsonl", b"").splitlines() if line.strip()]

    def current_events(self):
        events = self.timeline()
        start = max((i + 1 for i, e in enumerate(events) if e.get("stage") == "reopen"), default=0)
        return events[start:]

    @property
    def closed(self):
        return any(event.get("stage") == "final" for event in self.current_events())

    def archive(self, archive_id):
        require(archive_id in self.archives, "unknown archive: " + archive_id)
        data = json.loads(self.archives[archive_id])
        documents = {p: base64.b64decode(raw, validate=True) if raw is not None else None
                     for p, raw in data["documents"].items()}
        validate_document_tree(documents)
        require(data.get("manifest") == {p: digest(raw) if raw is not None else None for p, raw in documents.items()},
                "archive document manifest mismatch")
        return JournalSnapshot(self.artifact_root, Path(data["source_root"]), Path(data["logical_root"]),
                               data["run_uuid"], data["revision"], MappingProxyType(documents),
                               MappingProxyType({k: tuple(v) for k, v in data["identities"].items()}), True,
                               MappingProxyType({k: base64.b64decode(v, validate=True) for k, v in data["receipts"].items()}),
                               data["storage_version"])

    @classmethod
    def from_connection(cls, run_dir, connection):
        row = connection.execute("SELECT version, run_uuid, revision, logical_root, source_root, identities FROM run_state WHERE id=1").fetchone()
        if row is None:
            return None
        require(row[0] in {1, 2}, "unsupported journal storage version")
        require(connection.execute("SELECT revision FROM operations WHERE operation_id='initialize'").fetchone() == (1,),
                "committed journal initialization receipt missing; flat fallback forbidden")
        require(isinstance(row[1], str) and bool(row[1]) and isinstance(row[2], int) and row[2] >= 1
                and isinstance(row[3], str) and Path(row[3]).is_absolute()
                and isinstance(row[4], str) and Path(row[4]).is_absolute(), "corrupt journal run identity")
        documents = {}
        for name, kind, data, sha in connection.execute("SELECT path, kind, content, sha256 FROM documents"):
            logical_path(name)
            require(kind in {"file", "directory"} and
                    (isinstance(data, bytes) and digest(data) == sha if kind == "file" else data is None and sha is None),
                    f"corrupt journal document: {name}")
            documents[name] = data if kind == "file" else None
        validate_document_tree(documents)
        identities = json.loads(row[5])
        require(isinstance(identities, dict) and all(
            name in documents and isinstance(identity, list) and len(identity) == 2
            and all(isinstance(value, int) for value in identity)
            for name, identity in identities.items()), "corrupt imported journal identities")
        receipts = {name: encode_json({"payload_sha256": payload, "revision": revision, "result": json.loads(result)}).encode()
                    for name, payload, revision, result in connection.execute("SELECT operation_id, payload_sha256, revision, result FROM operations")}
        archives = {}
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='archives'").fetchone():
            for key, content, sha in connection.execute("SELECT archive_id, content, sha256 FROM archives"):
                require(isinstance(content, bytes) and digest(content) == sha, "corrupt journal archive: " + key)
                archives[key] = content
        snapshot = cls(Path(run_dir), Path(row[4]), Path(row[3]), row[1], row[2],
                   MappingProxyType(documents), MappingProxyType({k: tuple(v) for k, v in identities.items()}), True,
                   MappingProxyType(receipts), row[0], archives=MappingProxyType(archives))
        require(row[0] == 2 or not archives, "version 1 cannot contain lifecycle archives")
        linked = set()
        try:
            events = snapshot.timeline()
        except (json.JSONDecodeError, UnicodeError):
            require(not archives, "archive timeline is unreadable")
            events = []  # Ordinary malformed reports remain readable for the full validator's diagnostics.
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                require(not archives, "archive timeline event is malformed")
                continue
            if event.get("stage") not in {"reopen", "upgrade"}:
                continue
            key = event.get("archive_id")
            require(key in archives and key not in linked, "lifecycle archive link missing or repeated")
            linked.add(key)
            archived = snapshot.archive(key)
            require(archived.run_uuid == snapshot.run_uuid and archived.revision < snapshot.revision,
                    "archive identity/revision mismatch")
            prefix = archived.documents.get("timeline.jsonl", b"")
            require(documents.get("timeline.jsonl", b"").startswith(prefix) and
                    len(archived.timeline()) == index, "archive timeline prefix mismatch")
            require(event.get("archive_sha256") == digest(archives[key]), "archive event hash mismatch")
            require(event.get("generation") == archived.generation + (event["stage"] == "reopen"),
                    "archive generation mismatch")
        require(linked == set(archives), "unlinked journal archive")
        return snapshot

    def operation_receipt(self, identifier):
        raw = self.receipts.get(identifier)
        return json.loads(raw) if raw is not None else None

    @classmethod
    def open(cls, run_dir, *, legacy_source_root=None):
        run_dir = Path(run_dir).absolute()
        require(".journal" not in run_dir.parts, "journal export cannot be used as a logical run")
        database = run_dir / ".journal/state.sqlite3"
        required = storage_required(run_dir)
        require(database.exists() or not required, "journal database missing after lifecycle started; flat fallback forbidden")
        if database.exists():
            connection = None
            try:
                connection = connect_database(run_dir)
                connection.execute("BEGIN")
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables or required:
                    validate_storage_schema(connection)
                else:
                    require(connection.execute("PRAGMA user_version").fetchone()[0] == 0,
                            "journal schema unavailable or corrupt; flat fallback forbidden")
                snapshot = cls.from_connection(run_dir, connection) if "run_state" in tables else None
                if snapshot is None and tables:
                    validate_bootstrap(connection)
                connection.commit()
                if snapshot is not None:
                    require(legacy_source_root is None or Path(legacy_source_root) == snapshot.source_root,
                            "committed journal source provenance cannot be overridden")
                    return snapshot
            except sqlite3.DatabaseError as exc:
                raise JournalError(f"journal database unavailable; flat fallback forbidden: {exc}") from exc
            finally:
                if connection is not None:
                    connection.close()
        origin = Path(legacy_source_root) if legacy_source_root else _source_root(run_dir)
        documents, identities = capture_flat(run_dir, allow_file_links=True, source_root=origin)
        documents = capture_external_references(run_dir, documents, origin)
        return cls(run_dir, origin, run_dir, "legacy:" + digest(os.fsencode(run_dir)), 0,
                   MappingProxyType(documents), MappingProxyType(identities), False)

    def key(self, path):
        path = Path(path)
        if path.is_absolute():
            for root in (self.artifact_root, self.logical_root):
                if path.is_relative_to(root):
                    path = path.relative_to(root)
                    break
            else:
                index = json.loads(self.documents.get("artifacts/source-references.json", b"{}"))
                require(path.is_relative_to(self.source_root) and ".journal" not in path.parts,
                        f"path is outside source provenance: {path}")
                captured = index.get(str(path))
                require(isinstance(captured, str), f"path is not a published journal reference: {path}")
                return logical_path(captured)
        if str(path) == ".":
            return ""
        return logical_path(path.as_posix())

    def read_bytes(self, path):
        key = self.key(path)
        if key not in self.documents:
            raise FileNotFoundError(f"unpublished journal document: {key}")
        data = self.documents[key]
        if data is None:
            raise IsADirectoryError(key)
        return data

    def read_text(self, path, encoding="utf-8"):
        return self.read_bytes(path).decode(encoding)

    def exists(self, path):
        key = self.key(path)
        return not key or key in self.documents

    def is_file(self, path):
        key = self.key(path)
        return key in self.documents and self.documents[key] is not None

    def is_dir(self, path):
        key = self.key(path)
        return not key or key in self.documents and self.documents[key] is None

    def list_paths(self, path="", pattern=None):
        key = self.key(path) if path else ""
        prefix = key + "/" if key else ""
        return tuple(self.artifact_root / name for name in sorted(self.documents)
                     if name.startswith(prefix) and "/" not in name[len(prefix):] and
                     (pattern is None or fnmatch.fnmatchcase(name[len(prefix):], pattern)))


def _put_documents(connection, documents):
    for name, data in documents.items():
        name = logical_path(name)
        if isinstance(data, str):
            data = data.encode("utf-8")
        require(data is None or isinstance(data, bytes), f"journal content must be bytes: {name}")
        for parent in reversed(Path(name).parents):
            if str(parent) != ".":
                connection.execute("INSERT OR IGNORE INTO documents VALUES (?, 'directory', NULL, NULL)", (parent.as_posix(),))
        connection.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)",
                           (name, "directory" if data is None else "file", data, digest(data) if data is not None else None))


def validate_document_tree(documents):
    for name in documents:
        for parent in Path(name).parents:
            if str(parent) != ".":
                require(parent.as_posix() in documents and documents[parent.as_posix()] is None,
                        f"journal file/directory collision: {parent}")


def initialize_journal(run_dir, documents, *, source_root, logical_root=None, run_uuid=None, identities=None, import_guard=None,
                       result_contract_version=2, storage_version=2):
    run_dir = Path(run_dir).absolute()
    durable_mkdir(run_dir / ".journal")
    database = run_dir / ".journal/state.sqlite3"
    required = storage_required(run_dir)
    require(database.exists() or not required, "journal database missing after lifecycle started; flat fallback forbidden")
    connection = connect_database(run_dir, create=not database.exists())
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = list(connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        if not tables and not required and connection.execute("PRAGMA user_version").fetchone()[0] == 0:
            for statement in STORAGE_SCHEMA.values():
                connection.execute(statement)
            connection.execute("PRAGMA user_version=1")
        else:
            validate_storage_schema(connection)
            if JournalSnapshot.from_connection(run_dir, connection) is None:
                validate_bootstrap(connection)
        connection.commit()
        fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            sync_file(fd)
        finally:
            os.close(fd)
        sync_directory(database.parent)
        marker = database.parent / "storage-required"
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            require(storage_required(run_dir), "storage-required marker unavailable")
            fd = os.open(marker, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(fd)
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_size == 0, "invalid storage-required marker")
            sync_file(fd)
        finally:
            os.close(fd)
        sync_directory(database.parent)
        probe_storage(run_dir)
        connection.execute("BEGIN IMMEDIATE")
        validate_storage_schema(connection)
        existing = JournalSnapshot.from_connection(run_dir, connection)
        if existing is None:
            validate_bootstrap(connection)
            if storage_version == 2:
                raw_timeline = documents.get("timeline.jsonl", b"")
                if isinstance(raw_timeline, str):
                    raw_timeline = raw_timeline.encode()
                require(not any(json.loads(line).get("stage") in {"final", "reopen", "upgrade"}
                                for line in raw_timeline.splitlines() if line.strip()),
                        "new journal must start open; final requires journal finalize")
            _put_documents(connection, documents)
            validate_document_tree({name: data if kind == "file" else None for name, kind, data in
                                    connection.execute("SELECT path, kind, content FROM documents")})
            require(storage_version in {1, 2}, "unsupported journal storage version")
            connection.execute("INSERT INTO run_state VALUES (1, ?, ?, 1, ?, ?, ?)",
                               (storage_version, run_uuid or str(uuid.uuid4()), str(logical_root or run_dir), str(source_root), encode_json(identities or {})))
            connection.execute("INSERT INTO operations VALUES ('initialize', ?, 1, ?)",
                               (digest(encode_json({k: digest(v) if v is not None else None for k, v in documents.items()}).encode()),
                                encode_json({"revision": 1, "result_contract_version": result_contract_version})))
            if import_guard is not None:
                import_guard()
        connection.commit()
        sync_directory(run_dir / ".journal")
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return JournalSnapshot.open(run_dir)


def capture_external_references(run_dir, documents, source_root, *, previous=None):
    captured = dict(documents)
    index = json.loads((previous or {}).get("artifacts/source-references.json", b"{}"))
    references = {}
    def visit(value):
        if isinstance(value, dict):
            path = value.get("path")
            if isinstance(path, str) and Path(path).is_absolute() and not Path(path).is_relative_to(run_dir):
                target = Path(path)
                require(".." not in target.parts and target.is_relative_to(source_root) and ".journal" not in target.parts,
                        f"external reference escapes source provenance: {path}")
                name = "artifacts/source-captures/" + digest(os.fsencode(path))
                require(index.get(path, name) == name, "invalid published source reference index")
                captured[name] = capture_file(target)
                references[path] = name
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    data = documents.get("delegation-summary.json")
    if data is not None:
        try:
            summary = json.loads(data)
        except (ValueError, UnicodeError):
            return captured  # The domain validator reports malformed legacy summaries.
        if not isinstance(summary, dict):
            return captured
        verification = summary.get("verification", {})
        if not isinstance(verification, dict):
            verification = {}
        for field in ("initial_snapshot", "task_scope"):
            visit(verification.get(field))
        checks = verification.get("behavioral_checks", [])
        for check in checks if isinstance(checks, list) else []:
            if isinstance(check, dict):
                visit(check.get("inputs", []))
                visit(check.get("outputs", []))
        records = summary.get("subagents", [])
        for record in records if isinstance(records, list) else []:
            if isinstance(record, dict):
                visit(record.get("evidence", []))
    if references:
        captured["artifacts/source-references.json"] = encode_json(references).encode()
        for name in tuple(captured):
            for parent in Path(name).parents:
                if str(parent) != ".":
                    captured.setdefault(parent.as_posix(), None)
    return captured


def import_legacy(run_dir, *, source_root=None):
    snapshot = JournalSnapshot.open(run_dir, legacy_source_root=source_root)
    if snapshot.durable:
        require(source_root is None or Path(source_root) == snapshot.source_root,
                "committed journal source provenance cannot be overridden")
        if not storage_required(run_dir):
            return initialize_journal(run_dir, {}, source_root=snapshot.source_root)
        return snapshot
    documents, identities = capture_flat(Path(run_dir), allow_file_links=True, source_root=snapshot.source_root)
    require(capture_external_references(Path(run_dir), documents, snapshot.source_root) == dict(snapshot.documents)
            and identities == dict(snapshot.identities), "legacy journal changed during import")
    for name, data in documents.items():
        if data is None:
            continue
        if name.endswith(".json"):
            json.loads(data)
        elif name.endswith(".jsonl"):
            for line in data.splitlines():
                if line.strip():
                    json.loads(line)
    origin = Path(source_root) if source_root is not None else snapshot.source_root
    original = dict(documents)
    documents = capture_external_references(Path(run_dir), documents, origin)
    def import_guard():
        current, current_identities = capture_flat(Path(run_dir), allow_file_links=True, source_root=origin)
        require(current == original and current_identities == identities, "legacy journal changed before import commit")
        require(capture_external_references(Path(run_dir), current, origin) == documents,
                "legacy source references changed before import commit")
    return initialize_journal(run_dir, documents, source_root=origin, logical_root=snapshot.logical_root,
                              identities=identities, import_guard=import_guard, result_contract_version=1, storage_version=1)


def operation_id(run_dir, command, payload, *, identifier=None):
    """Persist a new request, or verify the caller's saved retry identifier."""
    snapshot = JournalSnapshot.open(run_dir)
    identifier = identifier or str(uuid.uuid4())
    require(re.fullmatch(r"[a-zA-Z0-9._-]+", identifier) is not None, "invalid operation ID")
    encoded = encode_json({"run_uuid": snapshot.run_uuid, "operation_id": identifier,
                           "command": command, "payload": payload}).encode()
    directory = Path(run_dir) / ".journal/requests"
    durable_mkdir(directory)
    path = directory / identifier
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        require(capture_file(path) == encoded, "operation envelope payload conflict")
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            sync_file(handle.fileno())
        sync_directory(directory)
    return identifier


def transact(run_dir, identifier, payload, mutation, *, replay_guard=None, timeout=2.0):
    return _transact(run_dir, identifier, payload, mutation, replay_guard=replay_guard, timeout=timeout)


def _transact_completion(run_dir, identifier, payload, mutation, *, source, lane_id,
                         external_inputs, replay_guard):
    from verification_evidence import CapturedSource
    require(isinstance(source, CapturedSource) and source.frozen,
            "completion transaction requires captured and frozen source")
    return _transact(run_dir, identifier, payload, mutation, replay_guard=replay_guard,
                     completion=(lane_id, source, external_inputs))


def _validate_completion_update(snapshot, documents, old_summary, next_summary, record, updated, context):
    from verification_evidence import accepted_record, completion_follows
    lane_id, source, inputs = context
    require(record.get("lane_id") == lane_id and record.get("role") in {"qa-verifier", "reviewer", "reviewer.qa"},
            "completion repair requires the current QA/reviewer assignment")
    require(snapshot.storage_version == 2 and not snapshot.closed and not record.get("legacy"),
            "completion repair requires an open native v2 assignment")
    require(record.get("status") in {"pass", "pass-with-risks"}
            and record.get("obligation", {}).get("state") == "current",
            "completion repair requires a current successful spawned assignment")
    completion_fields = {"completion_turn_id", "handoff", "handoff_sha256", "reviewed_result_hash",
                         "evidence", "session_meta_event", "task_started_event", "task_complete_event",
                         "qa_handoff_sha256", "root_thread_id"}
    require(not any(record.get(k) for k in ("completion_turn_id", "handoff", "handoff_sha256", "task_complete_event", "evidence", "qa_handoff_sha256")),
            "completed assignment is immutable")
    require({k: v for k, v in record.items() if k not in completion_fields} ==
            {k: v for k, v in updated.items() if k not in completion_fields},
            "completion repair may only add completion fields")
    for key in ("reviewed_result_hash", "root_thread_id", "session_meta_event"):
        require(key not in record or record[key] == updated.get(key), "completion repair changed " + key)
    expected_summary = json.loads(encode_json(old_summary))
    expected_summary["subagents"][expected_summary["subagents"].index(record)] = updated
    verification = expected_summary["verification"]
    selected = "qa" if record["role"] == "qa-verifier" else "reviewer"
    verification[selected] = lane_id
    if selected == "qa":
        verification["reviewer"] = None
    require(next_summary == expected_summary, "completion repair changed unrelated summary fields or result")
    require(updated.get("root_thread_id", verification.get("root_thread_id")) == verification.get("root_thread_id"),
            "completion repair root identity mismatch")
    trace = record.get("trace")
    require(trace == f"agents/{safe_path_segment(record['role'])}/trace.jsonl", "completion repair trace identity mismatch")
    require(any(e.get("lane_id") == lane_id and e.get("stage") == "spawned" for e in snapshot.current_events()),
            "completion repair requires a spawn in the current generation")
    histories, additions = [], []
    for path in ("timeline.jsonl", trace):
        old = snapshot.read_bytes(path)
        new = documents.get(path, old)
        require(new.startswith(old), "completion repair must preserve history")
        history = [json.loads(line) for line in old.splitlines() if line.strip()
                   and json.loads(line).get("lane_id") == lane_id]
        require(history and sum(e.get("stage") == "spawned" for e in history) == 1,
                "completion repair requires one observed spawn in both histories")
        for event in history:
            require(event.get("stage") not in {"handoff", "blocked", "fail", "completion"}
                    and not any(event.get(k) for k in ("completion_turn_id", "handoff", "task_complete_event")),
                    "completed assignment history is immutable")
            require(all(event.get(k) == record.get(k) for k in ("lane_id", "role", "codex_thread_id"))
                    and event.get("execution_mode") == "subagent", "completion repair history identity mismatch")
            if event.get("stage") == "spawned":
                require(event.get("status") == record["status"], "completion repair spawn status mismatch")
        histories.append(history)
        appended = [json.loads(line) for line in new[len(old):].splitlines() if line.strip()]
        require(len(appended) == 1, "completion repair requires one handoff in both histories")
        additions.append(appended[0])
    require(histories[0] == histories[1] and additions[0] == additions[1], "completion repair histories disagree")
    event = additions[0]
    require(event.get("stage") == "handoff" and event.get("execution_mode") == "subagent",
            "completion repair requires a subagent handoff")
    for key in ("lane_id", "role", "codex_thread_id", "status", "completion_turn_id", "handoff", "handoff_sha256",
                "reviewed_result_hash", "session_meta_event", "task_started_event", "task_complete_event"):
        require(event.get(key) == updated.get(key), "completion repair event mismatch: " + key)
    allowed = {"delegation-summary.json", "timeline.jsonl", trace, "artifacts.json", f"artifacts/agents/{safe_path_segment(record['role'])}"}
    require(set(documents) <= allowed, "completion repair cannot publish other documents")
    prepared = replace(snapshot, external_inputs=inputs)
    qa = next((r for r in next_summary["subagents"] if r.get("lane_id") == verification.get("qa")), {})
    role = "qa-verifier" if selected == "qa" else "reviewer"
    completion = accepted_record(snapshot.artifact_root, updated, verification, role, source,
                                 qa_handoff=qa.get("handoff") if selected == "reviewer" else None, snapshot=prepared)
    for key in ("session_meta_event", "task_started_event", "task_complete_event"):
        require(updated.get(key) == completion[key], "completion repair source pointer mismatch: " + key)
    if selected == "reviewer":
        qa_completion = accepted_record(snapshot.artifact_root, qa, verification, "qa-verifier", source, snapshot=prepared)
        require(updated.get("qa_handoff_sha256") == qa.get("handoff_sha256") == completion["answer"].get("qa_handoff_sha256"),
                "completion repair reviewer QA hash mismatch")
        require(completion_follows(completion, qa_completion), "reviewer acceptance must follow QA completion")


def capture_archive(snapshot):
    data = {"run_uuid": snapshot.run_uuid, "revision": snapshot.revision,
            "storage_version": snapshot.storage_version, "logical_root": str(snapshot.logical_root),
            "manifest": {p: digest(raw) if raw is not None else None for p, raw in snapshot.documents.items()},
            "source_root": str(snapshot.source_root), "identities": dict(snapshot.identities),
            "documents": {p: base64.b64encode(raw).decode() if raw is not None else None
                          for p, raw in snapshot.documents.items()},
            "receipts": {k: base64.b64encode(v).decode() for k, v in snapshot.receipts.items()}}
    return "revision-" + str(snapshot.revision), encode_json(data).encode()


def _transact(run_dir, identifier, payload, mutation, *, replay_guard=None, timeout=2.0, lifecycle=None, completion=None):
    """Read, guard and mutate under one write lock. Callback performs no external work."""
    initial_snapshot = JournalSnapshot.open(run_dir)
    require(initial_snapshot.durable, "legacy run requires explicit import before writing")
    from journal_recovery import canonical_identity
    payload_sha = digest(encode_json(payload).encode())
    connection = connect_database(run_dir, timeout=timeout)
    committed = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        snapshot = JournalSnapshot.from_connection(run_dir, connection)
        receipt = connection.execute("SELECT payload_sha256, result FROM operations WHERE operation_id=?", (identifier,)).fetchone()
        if receipt:
            require(receipt[0] == payload_sha, "operation ID payload conflict")
            if replay_guard is not None:
                replay_guard(snapshot)
            connection.rollback()
            return json.loads(receipt[1])
        require(snapshot.storage_version == 2 or lifecycle in {"upgrade", "reopen-upgrade"}, "journal storage version 1 is read-only; explicit upgrade required")
        require(not snapshot.closed or lifecycle in {"delivery", "reopen", "reopen-upgrade"}, "journal generation is closed")
        documents, result = mutation(snapshot)
        if lifecycle in {"upgrade", "reopen", "reopen-upgrade"}:
            archive_id, archive_bytes = capture_archive(snapshot)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='archives'").fetchone():
                connection.execute(ARCHIVE_SCHEMA)
            connection.execute("INSERT INTO archives VALUES (?, ?, ?)", (archive_id, archive_bytes, digest(archive_bytes)))
            connection.execute("UPDATE run_state SET version=2 WHERE id=1")
        documents = {name: data.encode() if isinstance(data, str) else data for name, data in documents.items()}
        for name, data in documents.items():
            old = snapshot.documents.get(name)
            if name == "timeline.jsonl" or name.startswith("agents/") and name.endswith("/trace.jsonl"):
                old = old or b""
                require(data is not None and data.startswith(old), "journal history must remain a byte prefix: " + name)
                for line in data[len(old):].splitlines():
                    if line.strip():
                        validate_assignment_event(json.loads(line))
                if name == "timeline.jsonl":
                    terminal = {event.get("lane_id") for line in old.splitlines() if line.strip()
                                for event in [json.loads(line)] if event.get("lane_id")
                                and event.get("stage") in {"handoff", "blocked", "fail"}
                                and event.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}}
                    for line in data[len(old):].splitlines():
                        if line.strip():
                            event = json.loads(line)
                            require(event.get("lane_id") not in terminal,
                                    "terminal assignment history is immutable; use a new lane")
        if "artifacts/lifecycle/legacy-classification.json" in documents:
            require(lifecycle == "classify" and not snapshot.exists("artifacts/lifecycle/legacy-classification.json"),
                    "legacy classification is immutable and requires its domain command")
        if snapshot.archives:
            first = min((snapshot.archive(k) for k in snapshot.archives), key=lambda item: item.revision)
            future = JournalSnapshot(snapshot.artifact_root, snapshot.source_root, snapshot.logical_root,
                snapshot.run_uuid, snapshot.revision, {**snapshot.documents, **documents}, snapshot.identities,
                True, snapshot.receipts, snapshot.storage_version)
            require(canonical_identity(future) == canonical_identity(first), "recovered root/workspace/scope/result identity is immutable")
        if snapshot.exists("delegation-summary.json"):
            old_summary = json.loads(snapshot.read_bytes("delegation-summary.json"))
            next_summary = json.loads(documents.get("delegation-summary.json", snapshot.read_bytes("delegation-summary.json")))
            for kind in ("subagents", "role_lanes"):
                after = {r.get("lane_id"): r for r in next_summary.get(kind, [])}
                for record in old_summary.get(kind, []):
                    require(record.get("lane_id") in after, "assignment history cannot be removed")
                    old_legacy = record.get("legacy")
                    new_legacy = after[record["lane_id"]].get("legacy")
                    require(old_legacy == new_legacy or old_legacy is None and lifecycle == "classify",
                            "legacy facts require immutable domain classification")
                    obligation = record.get("obligation")
                    if isinstance(obligation, dict):
                        updated_obligation = after[record["lane_id"]].get("obligation", {})
                        require(isinstance(updated_obligation, dict) and
                                all(updated_obligation.get(key) == obligation.get(key) for key in ("id", "required")),
                                "obligation identity and required flag are immutable")
                    if old_legacy or record.get("status") in {"pass", "pass-with-risks", "fail", "blocked"}:
                        updated = after[record["lane_id"]]
                        if completion is not None and kind == "subagents" and record.get("lane_id") == completion[0] and updated != record:
                            _validate_completion_update(snapshot, documents, old_summary, next_summary, record, updated, completion)
                            continue
                        require({k: v for k, v in updated.items() if k not in ({"obligation", "legacy"} if lifecycle == "classify" else {"obligation"})} ==
                                {k: v for k, v in record.items() if k not in ({"obligation", "legacy"} if lifecycle == "classify" else {"obligation"})},
                                "terminal assignment is immutable; use a new lane")
                        handoff = (old_legacy.get("classification", {}).get("handoff", {}).get("path") if old_legacy else None) or record.get("handoff")
                        if handoff in documents and snapshot.exists(handoff):
                            require(documents[handoff] == snapshot.read_bytes(handoff), "terminal handoff is immutable")
        if lifecycle is None and "timeline.jsonl" in documents:
            timeline = documents["timeline.jsonl"]
            if isinstance(timeline, str):
                timeline = timeline.encode()
            require(not any(json.loads(line).get("stage") in {"final", "reopen", "upgrade"}
                            for line in timeline[len(snapshot.documents.get("timeline.jsonl", b"")):].splitlines() if line.strip()),
                    "final requires journal finalize; reopen requires explicit lifecycle command")
        if lifecycle == "delivery":
            require(snapshot.closed, "delivery requires closed generation")
            from task_workspace import registered_workspace
            workspace = registered_workspace(snapshot, require_sealed=True)
            expected = f"artifacts/workspaces/{workspace['workspace_id']}/delivery/{workspace['seal']['candidate_id']}.json"
            require(set(documents) == {expected}, "delivery may only publish its own artifact")
        _put_documents(connection, documents)
        validate_document_tree({name: data if kind == "file" else None for name, kind, data in
                                connection.execute("SELECT path, kind, content FROM documents")})
        revision = snapshot.revision + bool(documents)
        result = {**result, "revision": revision}
        connection.execute("UPDATE run_state SET revision=? WHERE id=1", (revision,))
        connection.execute("INSERT INTO operations VALUES (?, ?, ?, ?)", (identifier, payload_sha, revision, encode_json(result)))
        committed_snapshot = JournalSnapshot.from_connection(run_dir, connection)
        if snapshot.archives:
            require(canonical_identity(committed_snapshot) == canonical_identity(first),
                    "recovered root/workspace/scope/result identity is immutable")
        try:
            connection.commit()
            committed = True
        except sqlite3.Error:
            connection.close()
            check = connect_database(run_dir)
            try:
                saved = check.execute("SELECT payload_sha256, result FROM operations WHERE operation_id=?", (identifier,)).fetchone()
                if saved and saved[0] == payload_sha:
                    return json.loads(saved[1])
            finally:
                check.close()
            raise
        return result
    except sqlite3.OperationalError as exc:
        raise JournalError(f"journal transaction failed without confirmed receipt: {exc}") from exc
    finally:
        if not committed:
            try:
                connection.rollback()
            except sqlite3.ProgrammingError:
                pass
        connection.close()


def export_snapshot(snapshot):
    require(snapshot.durable, "legacy diagnostics do not produce durable exports")
    directory = snapshot.artifact_root / ".journal/views"
    durable_mkdir(directory)
    destination = directory / str(snapshot.revision)
    def matches(path):
        if not path.is_dir() or path.is_symlink():
            return False
        actual, _ = capture_flat(path)
        return actual == dict(snapshot.documents)
    if destination.exists() and matches(destination):
        return destination
    staging = Path(tempfile.mkdtemp(prefix=".export-", dir=directory))
    try:
        for name, data in sorted(snapshot.documents.items()):
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if data is None:
                target.mkdir(exist_ok=True)
            else:
                with target.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    sync_file(handle.fileno())
        for root, dirs, files in os.walk(staging, topdown=False):
            sync_directory(root)
        require(matches(staging), "export verification failed")
        if destination.exists():
            damaged = directory / (".damaged-" + uuid.uuid4().hex)
            destination.rename(damaged)
        staging.rename(destination)
        sync_directory(directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def journal_read_text(path, encoding="utf-8", *, snapshot=None):
    return snapshot.read_text(path, encoding) if snapshot else path.read_text(encoding=encoding)


def journal_read_bytes(path, *, snapshot=None):
    return snapshot.read_bytes(path) if snapshot else path.read_bytes()


def journal_exists(path, *, snapshot=None):
    return snapshot.exists(path) if snapshot else path.exists()


def journal_is_file(path, *, snapshot=None):
    return snapshot.is_file(path) if snapshot else path.is_file()


def journal_is_dir(path, *, snapshot=None):
    return snapshot.is_dir(path) if snapshot else path.is_dir()


def journal_iterdir(path, *, snapshot=None):
    return snapshot.list_paths(path) if snapshot else path.iterdir()


def journal_glob(path, pattern, *, snapshot=None):
    return snapshot.list_paths(path, pattern) if snapshot else path.glob(pattern)


def require(condition, message):
    if not condition:
        raise JournalError(message)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encode_json(value, *, pretty=False):
    return json.dumps(value, ensure_ascii=False, indent=2 if pretty else None,
                      sort_keys=True, allow_nan=False)


def write_json(path, value, *, run_dir, identifier=None):
    return write_files({path: encode_json(value, pretty=True) + "\n"}, run_dir=run_dir, identifier=identifier)


def validate_directory(path):
    require(not path.exists() or path.is_dir(), f"write directory unavailable: {path}")
    require(not path.is_symlink() and all(not parent.is_symlink() for parent in path.parents),
            f"write target parent must not be a symlink: {path}")


def write_files(contents, *, append=(), run_dir=None, identifier=None):
    """Publish related documents; callers supply the logical run explicitly."""
    require(run_dir is not None, "published writes require explicit run_dir and initialized journal")
    snapshot = JournalSnapshot.open(run_dir)
    documents = {snapshot.key(path): text.encode() if isinstance(text, str) else text for path, text in contents.items()}
    appended = {snapshot.key(path) for path in append}
    payload = {"files": {name: digest(data) for name, data in documents.items()}, "append": sorted(appended)}
    identifier = operation_id(run_dir, "write-files", payload, identifier=identifier)
    def mutation(current):
        updated = dict(documents)
        for name in appended:
            if current.exists(name):
                updated[name] = current.read_bytes(name) + updated[name]
        return updated, {}
    return transact(run_dir, identifier, payload, mutation)


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
    coarse = type(value) is int
    try:
        before = start + timedelta(seconds=1) if coarse else None
    except OverflowError as exc:
        raise JournalError("invalid source timestamp interval") from exc
    precise_outer = outer if isinstance(event.get("timestamp"), str) else None
    return SourceTime(start, before, origin, precise_outer)


def event_time(event, field):
    value = source_time(event, field)
    return value.event_at, value.event_before


def safe_path_segment(value):
    require(isinstance(value, str) and value.strip(), "role must be a non-empty string")
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-")[:80] or "agent"


def unique_paths(paths):
    require(isinstance(paths, list) and all(isinstance(p, str) and p.strip() for p in paths), "paths must be non-empty strings")
    return list(dict.fromkeys(paths))


def display_path(value, run_dir):
    require(isinstance(value, str) and value.strip(), "artifact path must be non-empty")
    path = Path(value).expanduser()
    require(".." not in path.parts, f"unsafe artifact path: {value}")
    if path.is_absolute():
        try:
            return path.resolve().relative_to(run_dir).as_posix()
        except ValueError:
            return path.resolve().as_posix()
    return path.as_posix()


def validate_event(event):
    require(isinstance(event, dict), "event must be an object")
    for field in ("timestamp", "stage", "role", "stable_agent_name", "stable_agent_slug", "status", "summary"):
        require(isinstance(event.get(field), str) and event[field].strip(), f"{field} must be a non-empty string")
    timestamp(event["timestamp"])
    unique_paths(event.get("artifacts"))
    require(isinstance(event.get("next_step"), str), "next_step must be a string")
    require(event["stage"] != "spawn", "stage spawn is invalid; use spawned")
    require(event["stage"] != "final" or event["role"] == "orchestrator", "final event must be owned by role=orchestrator")
    if event.get("execution_mode") == "subagent" and event["stage"] == "spawned":
        require(bool(event.get("codex_thread_id")), "spawned subagent requires codex_thread_id")


def validate_assignment_event(event):
    """Validate new assignment commands only; historical facts remain readable."""
    allowed = {"spawned": {"active"}, "handoff": {"pass", "pass-with-risks"},
               "blocked": {"blocked"}, "fail": {"fail"}}
    statuses = allowed.get(event.get("stage"))
    if event.get("lane_id") and statuses is not None:
        require(event.get("status") in statuses,
                f"stage {event['stage']} requires status: {', '.join(sorted(statuses))}")


def validate_append(path, event, *, snapshot=None):
    validate_assignment_event(event)
    validate_event(event)
    events = [json.loads(line) for line in journal_read_text(path, snapshot=snapshot).splitlines() if line.strip()] if journal_exists(path, snapshot=snapshot) else []
    require(not (snapshot.closed if snapshot is not None else any(e.get("stage") == "final" for e in events)), "timeline already has final event")
    if events:
        require(timestamp(event["timestamp"]) >= timestamp(events[-1].get("timestamp")), "timestamp must be non-decreasing")


def append_event(path, event, *, identifier=None, assign_timestamp=False):
    validate_assignment_event(event)
    run_dir = path.parent
    payload = {k: v for k, v in event.items() if not (assign_timestamp and k == "timestamp")}
    identifier = operation_id(run_dir, "append", payload, identifier=identifier)
    def mutation(snapshot):
        current = dict(event)
        if assign_timestamp:
            current["timestamp"] = now_iso()
        validate_append(path, current, snapshot=snapshot)
        old = snapshot.read_text(path) if snapshot.exists(path) else ""
        return {snapshot.key(path): old + encode_json(current) + "\n"}, {}
    return transact(run_dir, identifier, payload, mutation)


def declared_paths(run_dir, lane_map, verification, *, evidence_path, confined_path, root, snapshot=None):
    paths = set()
    for source in (lane_map, verification):
        for field in ("changed_files", "changed_paths", "run_changed_files"):
            value = source.get(field, [])
            if isinstance(value, dict) and source is lane_map:
                value = [p for group in value.values() if isinstance(group, list) for p in group]
            paths.update(unique_paths(value))
    for lane in lane_map.get("lanes", []):
        boundary = lane.get("boundary", {}) if isinstance(lane, dict) else {}
        if isinstance(boundary, dict) and boundary.get("changed_paths_artifact"):
            path = evidence_path(run_dir, boundary["changed_paths_artifact"], snapshot=snapshot)
            require(journal_is_file(path, snapshot=snapshot), "Boundary Evidence missing")
            data = json.loads(journal_read_text(path, snapshot=snapshot))
            require(isinstance(data, dict), "Boundary Evidence must be an object")
            for field in ("changed_paths", "tracked_changed_paths", "untracked_paths"):
                paths.update(unique_paths(data.get(field, [])))
    for path in paths:
        confined_path(root, path, relative=True)
    return paths


def render_final(text, summary, paths, *, declared):
    def escaped(value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "&#96;").replace("\n", "&#10;")

    blocks = {
        "worktree": "## Worktree Hygiene\n\nRun-owned changed files:\n" + (
            "".join(f"- `{escaped(p)}`\n" for p in sorted(paths)) if paths else
            "none\n" if declared else "Machine file declaration unavailable.\n"),
        "delegation": "## Delegation Trace\n\n" +
            f"Subagents Used: {'yes' if summary['subagents_used'] else 'no'}\n" +
            f"Role Lanes Used: {'yes' if summary['role_lanes_used'] else 'no'}\n" +
            "Subagent Lanes: " + (", ".join(escaped(r['lane_id']) for r in summary['subagents']) or "none") + "\n" +
            "Role Lanes: " + (", ".join(escaped(r['lane_id']) for r in summary['role_lanes']) or "none") + "\n" +
            "Subagent Trace Evidence: " + (", ".join(escaped(r.get('trace', '')) for r in summary['subagents']) or "none") + "\n",
    }
    for name, body in blocks.items():
        begin, end = f"<!-- agent-flow:{name}:begin -->", f"<!-- agent-flow:{name}:end -->"
        require(text.count(begin) == text.count(end) <= 1, f"malformed generated {name} block")
        block = begin + "\n" + body + end
        if begin in text:
            start, stop = text.index(begin), text.index(end) + len(end)
            require(start < stop - len(end), f"malformed generated {name} block")
            text = text[:start] + block + text[stop:]
        else:
            text += ("\n" if text.endswith("\n") else "\n\n") + block + "\n"
    return text
