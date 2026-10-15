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
    critical: bool | None,
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
        if critical is not None:
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


STORAGE_VERSION = 3
UNSUPPORTED_STORAGE_VERSION = "unsupported journal storage version: pre-0.3 journals are archival; start a new run"

STORAGE_SCHEMA = {
    "run_state": "CREATE TABLE run_state (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, package_version TEXT NOT NULL, run_uuid TEXT NOT NULL, revision INTEGER NOT NULL, logical_root TEXT NOT NULL, source_root TEXT NOT NULL, identities TEXT NOT NULL)",
    "documents": "CREATE TABLE documents (path TEXT PRIMARY KEY, kind TEXT NOT NULL, content BLOB, sha256 TEXT)",
    "operations": "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, revision INTEGER NOT NULL, result TEXT NOT NULL)",
}


def current_package_version():
    """Version of the installed Agent Flow package; 'dev' when no package record exists."""
    here = Path(__file__).resolve()
    candidates = (here.parents[1] / "agent-flow-package.json",
                  here.parents[3] / ".codex-plugin" / "plugin.json")
    for path in candidates:
        try:
            if not path.is_file():
                continue
            version = json.loads(path.read_bytes()).get("version")
        except (OSError, ValueError):
            continue
        if isinstance(version, str) and version.strip():
            return version
    return "dev"


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
    """Capture exact run directory bytes; fail if an entry changes during capture."""
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
                        f"journal symlink escapes source or is not a file: {name}")
                target_stat = target.lstat()
                documents[name] = capture_file(target)
                after = path.lstat()
                require((before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) ==
                        (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) and path.resolve(strict=True) == target,
                        f"journal symlink changed: {name}")
                identities[name] = (target_stat.st_dev, target_stat.st_ino)
                continue
            if stat.S_ISDIR(before.st_mode):
                documents[name] = None
                scan(path)
            else:
                require(stat.S_ISREG(before.st_mode), f"journal special file: {name}")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    opened = os.fstat(fd)
                    require((before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), f"journal file changed: {name}")
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        documents[name] = stream.read()
                    after = os.fstat(fd)
                    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                            (after.st_size, after.st_mtime_ns, after.st_ctime_ns), f"journal file changed: {name}")
                finally:
                    os.close(fd)
            after = path.lstat()
            require((before.st_dev, before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_dev, after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns), f"journal file changed: {name}")
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
    storage_version: int = 0
    external_inputs: object = None
    archives: object = field(default_factory=lambda: MappingProxyType({}))
    package_version: str = "dev"

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
                               data["storage_version"], package_version=data["package_version"])

    @classmethod
    def from_connection(cls, run_dir, connection):
        row = connection.execute("SELECT version, package_version, run_uuid, revision, logical_root, source_root, identities FROM run_state WHERE id=1").fetchone()
        if row is None:
            return None
        require(row[0] == STORAGE_VERSION, UNSUPPORTED_STORAGE_VERSION)
        require(isinstance(row[1], str) and bool(row[1].strip()), "corrupt journal package version")
        require(connection.execute("SELECT revision FROM operations WHERE operation_id='initialize'").fetchone() == (1,),
                "committed journal initialization receipt missing; flat fallback forbidden")
        require(isinstance(row[2], str) and bool(row[2]) and isinstance(row[3], int) and row[3] >= 1
                and isinstance(row[4], str) and Path(row[4]).is_absolute()
                and isinstance(row[5], str) and Path(row[5]).is_absolute(), "corrupt journal run identity")
        documents = {}
        for name, kind, data, sha in connection.execute("SELECT path, kind, content, sha256 FROM documents"):
            logical_path(name)
            require(kind in {"file", "directory"} and
                    (isinstance(data, bytes) and digest(data) == sha if kind == "file" else data is None and sha is None),
                    f"corrupt journal document: {name}")
            documents[name] = data if kind == "file" else None
        validate_document_tree(documents)
        identities = json.loads(row[6])
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
        snapshot = cls(Path(run_dir), Path(row[5]), Path(row[4]), row[2], row[3],
                   MappingProxyType(documents), MappingProxyType({k: tuple(v) for k, v in identities.items()}), True,
                   MappingProxyType(receipts), row[0], archives=MappingProxyType(archives), package_version=row[1])
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
            if event.get("stage") != "reopen":
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
            require(event.get("generation") == archived.generation + 1,
                    "archive generation mismatch")
        require(linked == set(archives), "unlinked journal archive")
        return snapshot

    def operation_receipt(self, identifier):
        raw = self.receipts.get(identifier)
        return json.loads(raw) if raw is not None else None

    @classmethod
    def open(cls, run_dir):
        run_dir = Path(run_dir).absolute()
        require(".journal" not in run_dir.parts, "journal export cannot be used as a logical run")
        database = run_dir / ".journal/state.sqlite3"
        if database.exists() or (run_dir / ".journal").exists():
            require(database.exists(), UNSUPPORTED_STORAGE_VERSION)
            required = storage_required(run_dir)
            connection = None
            try:
                connection = connect_database(run_dir)
                connection.execute("BEGIN")
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "run_state" in tables:
                    columns = {r[1] for r in connection.execute("PRAGMA table_info(run_state)")}
                    if "version" in columns:
                        row = connection.execute("SELECT version FROM run_state WHERE id=1").fetchone()
                        require(row is None or row[0] == STORAGE_VERSION, UNSUPPORTED_STORAGE_VERSION)
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
                    return snapshot
            except sqlite3.DatabaseError as exc:
                raise JournalError(f"journal database unavailable; flat fallback forbidden: {exc}") from exc
            finally:
                if connection is not None:
                    connection.close()
        origin = _source_root(run_dir)
        documents, identities = capture_flat(run_dir, allow_file_links=True, source_root=origin)
        documents = capture_external_references(run_dir, documents, origin)
        return cls(run_dir, origin, run_dir, "flat:" + digest(os.fsencode(run_dir)), 0,
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


def initialize_journal(run_dir, documents, *, source_root, logical_root=None, run_uuid=None, identities=None,
                       result_contract_version=2):
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
            raw_timeline = documents.get("timeline.jsonl", b"")
            if isinstance(raw_timeline, str):
                raw_timeline = raw_timeline.encode()
            require(not any(json.loads(line).get("stage") in {"final", "reopen", "upgrade"}
                            for line in raw_timeline.splitlines() if line.strip()),
                    "new journal must start open; final requires journal finalize")
            _put_documents(connection, documents)
            validate_document_tree({name: data if kind == "file" else None for name, kind, data in
                                    connection.execute("SELECT path, kind, content FROM documents")})
            package_version = current_package_version()
            connection.execute("INSERT INTO run_state VALUES (1, ?, ?, ?, 1, ?, ?, ?)",
                               (STORAGE_VERSION, package_version, run_uuid or str(uuid.uuid4()),
                                str(logical_root or run_dir), str(source_root), encode_json(identities or {})))
            connection.execute("INSERT INTO operations VALUES ('initialize', ?, 1, ?)",
                               (digest(encode_json({k: digest(v) if v is not None else None for k, v in documents.items()}).encode()),
                                encode_json({"revision": 1, "result_contract_version": result_contract_version,
                                             "package_version": package_version})))
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
            return captured  # The domain validator reports malformed summaries.
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
    from verification_evidence import accepted_record, completed_turn, completion_follows, reference_bytes, review_subject_kind, source_bound_record
    lane_id, source, inputs = context
    require(review_subject_kind(record) == review_subject_kind(updated), "completion repair review subject changed")
    preliminary = review_subject_kind(record) == "requirements-plan"
    if record.get("role") not in {"qa-verifier", "reviewer", "reviewer.qa"}:
        from journal_structure import literal_label
        require(record.get("lane_id") == lane_id and not snapshot.closed
                and not record.get("legacy") and record.get("obligation", {}).get("state") == "current",
                "source completion requires a current native assignment")
        require(record.get("status") in {"pass", "pass-with-risks", "blocked", "fail"} and record.get("handoff"),
                "source completion requires an existing terminal handoff")
        require(not any(record.get(k) for k in ("completion_turn_id", "source_completion_sha256",
                                               "completion_task_started_event", "task_complete_event")),
                "completed assignment is immutable")
        additions = {"completion_turn_id", "source_completion_sha256", "completion_task_started_event",
                     "task_complete_event", "session_meta_event", "handoff_sha256", "evidence", "root_thread_id"}
        require(all(updated.get(key) == value for key, value in record.items()) and
                set(updated) - set(record) <= additions,
                "source completion may only add derived completion fields")
        expected = json.loads(encode_json(old_summary))
        expected["subagents"][expected["subagents"].index(record)] = updated
        require(next_summary == expected and set(documents) == {"delegation-summary.json"},
                "source completion must preserve unrelated state and histories")
        require(updated.get("root_thread_id", old_summary["verification"].get("root_thread_id")) ==
                old_summary["verification"].get("root_thread_id"), "source completion root identity mismatch")
        histories = [[event for event in snapshot.timeline() if event.get("lane_id") == lane_id],
                     [json.loads(line) for line in snapshot.read_bytes(record["trace"]).splitlines()
                      if line.strip() and json.loads(line).get("lane_id") == lane_id]]
        require(histories[0] == histories[1] and sum(e.get("stage") == "spawned" for e in histories[0]) == 1,
                "source completion requires matching observed histories")
        terminal = [e for e in histories[0] if e.get("stage") in {"handoff", "blocked", "fail"}]
        require(len(terminal) == 1 and terminal[0].get("status") == record["status"] and
                terminal[0].get("artifacts", [None])[0] == record["handoff"] and
                all(e.get("role") == record["role"] and e.get("codex_thread_id") == record["codex_thread_id"]
                    and e.get("execution_mode") == "subagent" for e in histories[0]),
                "source completion must preserve the original terminal handoff")
        prepared = replace(snapshot, external_inputs=inputs)
        handoff = reference_bytes(snapshot.artifact_root, {"path": record["handoff"]}, snapshot=prepared)
        require(updated.get("handoff_sha256") == digest(handoff), "source completion handoff hash mismatch")
        from verification_evidence import historical_evidence_bytes
        refs = record.get("evidence") or [{"path": p, "sha256": digest(reference_bytes(snapshot.artifact_root, {"path": p}, snapshot=prepared))}
                                         for p in terminal[0]["artifacts"][1:]]
        require([ref["path"] for ref in refs] == terminal[0]["artifacts"][1:], "source completion evidence paths changed")
        for ref in refs:
            historical_evidence_bytes(prepared, record, ref)
        require(updated.get("evidence") == refs and all(literal_label(ref["path"]) in handoff.decode() and
                ref["sha256"] in handoff.decode() for ref in refs), "source completion evidence changed")
        completion = completed_turn(source, record["codex_thread_id"], updated.get("completion_turn_id"),
                                    old_summary["verification"].get("root_thread_id"), record["role"])
        require(source.source.read(record["codex_thread_id"]) == source.read(record["codex_thread_id"]),
                "source completion changed after capture")
        require(updated.get("source_completion_sha256") == digest(completion["answer"].encode()) and
                updated.get("completion_task_started_event") == completion["task_started_event"] and
                updated.get("task_complete_event") == completion["task_complete_event"] and
                updated.get("session_meta_event") == completion["session_meta_event"],
                "source completion facts mismatch")
        return
    require(record.get("lane_id") == lane_id and record.get("role") in {"qa-verifier", "reviewer", "reviewer.qa"},
            "completion repair requires the current QA/reviewer assignment")
    require(not snapshot.closed and not record.get("legacy"),
            "completion repair requires an open native assignment")
    require(record.get("status") in {"pass", "pass-with-risks"}
            and record.get("obligation", {}).get("state") == "current",
            "completion repair requires a current successful spawned assignment")
    completion_fields = {"completion_turn_id", "handoff", "handoff_sha256", "reviewed_result_hash",
                         "evidence", "session_meta_event", "task_started_event", "task_complete_event",
                         "qa_handoff_sha256", "root_thread_id", "readiness_gates"}
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
    if not preliminary:
        verification[selected] = lane_id
    if selected == "qa" and not preliminary:
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
                "reviewed_result_hash", "session_meta_event", "task_started_event", "task_complete_event", "review_subject", "review_input_operation_id"):
        require(event.get(key) == updated.get(key), "completion repair event mismatch: " + key)
    allowed = {"delegation-summary.json", "timeline.jsonl", trace, "artifacts.json", f"artifacts/agents/{safe_path_segment(record['role'])}"}
    require(set(documents) <= allowed, "completion repair cannot publish other documents")
    prepared = replace(snapshot, external_inputs=inputs)
    qa = next((r for r in next_summary["subagents"] if r.get("lane_id") == verification.get("qa")), {})
    role = "qa-verifier" if selected == "qa" else "reviewer"
    acceptance = source_bound_record if preliminary else accepted_record
    completion = acceptance(snapshot.artifact_root, updated, verification, role, source,
                                 qa_handoff=qa.get("handoff") if selected == "reviewer" else None, snapshot=prepared)
    for key in ("session_meta_event", "task_started_event", "task_complete_event"):
        require(updated.get(key) == completion[key], "completion repair source pointer mismatch: " + key)
    if selected == "reviewer" and not preliminary:
        qa_completion = accepted_record(snapshot.artifact_root, qa, verification, "qa-verifier", source, snapshot=prepared)
        require(updated.get("qa_handoff_sha256") == qa.get("handoff_sha256") == completion["answer"].get("qa_handoff_sha256"),
                "completion repair reviewer QA hash mismatch")
        require(completion_follows(completion, qa_completion), "reviewer acceptance must follow QA completion")


def capture_archive(snapshot):
    data = {"run_uuid": snapshot.run_uuid, "revision": snapshot.revision,
            "storage_version": snapshot.storage_version, "package_version": snapshot.package_version,
            "logical_root": str(snapshot.logical_root),
            "manifest": {p: digest(raw) if raw is not None else None for p, raw in snapshot.documents.items()},
            "source_root": str(snapshot.source_root), "identities": dict(snapshot.identities),
            "documents": {p: base64.b64encode(raw).decode() if raw is not None else None
                          for p, raw in snapshot.documents.items()},
            "receipts": {k: base64.b64encode(v).decode() for k, v in snapshot.receipts.items()}}
    return "revision-" + str(snapshot.revision), encode_json(data).encode()


def _transact(run_dir, identifier, payload, mutation, *, replay_guard=None, timeout=2.0, lifecycle=None, completion=None):
    """Read, guard and mutate under one write lock. Callback performs no external work."""
    initial_snapshot = JournalSnapshot.open(run_dir)
    require(initial_snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
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
        require(not snapshot.closed or lifecycle in {"delivery", "reopen"}, "journal generation is closed")
        documents, result = mutation(snapshot)
        if lifecycle == "reopen":
            archive_id, archive_bytes = capture_archive(snapshot)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='archives'").fetchone():
                connection.execute(ARCHIVE_SCHEMA)
            connection.execute("INSERT INTO archives VALUES (?, ?, ?)", (archive_id, archive_bytes, digest(archive_bytes)))
        documents = {name: data.encode() if isinstance(data, str) else data for name, data in documents.items()}
        pinned_captures = set()
        for raw in snapshot.receipts.values():
            saved = json.loads(raw).get("result", {})
            if saved.get("command") == "prepare-review-input":
                pinned_captures.update(capture["path"] for capture in saved["captures"])
            pinned_captures.update(row["capture"]["path"] for row in saved.get("preserved_evidence", []))
        for name in pinned_captures & documents.keys():
            require(documents[name] == snapshot.read_bytes(name), "pinned review input capture is immutable")
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
        if snapshot.archives:
            first = min((snapshot.archive(k) for k in snapshot.archives), key=lambda item: item.revision)
            future = JournalSnapshot(snapshot.artifact_root, snapshot.source_root, snapshot.logical_root,
                snapshot.run_uuid, snapshot.revision, {**snapshot.documents, **documents}, snapshot.identities,
                True, snapshot.receipts, snapshot.storage_version, package_version=snapshot.package_version)
            require(canonical_identity(future) == canonical_identity(first), "recovered root/workspace/scope/result identity is immutable")
        summary_bytes = snapshot.documents.get("delegation-summary.json")
        summaries = [summary_bytes] if summary_bytes is not None else []
        if "delegation-summary.json" in documents:
            summaries.append(documents["delegation-summary.json"])
        for data in summaries:
            summary = json.loads(data) if data is not None else None
            require(isinstance(summary, dict), "delegation-summary.json must be an object")
            for kind in ("subagents", "role_lanes"):
                records = summary.get(kind, [])
                require(isinstance(records, list), f"delegation-summary.json.{kind} must be an array")
                for record in records:
                    require(isinstance(record, dict), f"delegation-summary.json.{kind} entry must be an object")
                    lane_id = record.get("lane_id")
                    require(isinstance(lane_id, str) and bool(lane_id.strip()),
                            f"delegation-summary.json.{kind} .lane_id invalid: {lane_id!r}")
        if not snapshot.exists("delegation-summary.json") and "delegation-summary.json" in documents:
            first_summary = json.loads(documents["delegation-summary.json"])
            require(all("legacy" not in record
                    for kind in ("subagents", "role_lanes") for record in first_summary.get(kind, [])),
                    "new legacy facts are unsupported; start a new run")
        if snapshot.exists("delegation-summary.json"):
            old_summary = json.loads(snapshot.read_bytes("delegation-summary.json"))
            next_summary = json.loads(documents.get("delegation-summary.json", snapshot.read_bytes("delegation-summary.json")))
            from journal_structure import DOCUMENT_PATHS, SERVICE_DOCUMENTS
            from verification_evidence import evidence_assignment, historical_evidence_bytes, reference_bytes, sha256
            mutable_inputs = (set(DOCUMENT_PATHS.values()) | SERVICE_DOCUMENTS | {"context.md"}) - {"final.md", "lane-map.json"}
            for kind in ("subagents", "role_lanes"):
                after = {r.get("lane_id"): r for r in next_summary.get(kind, [])}
                before_ids = {r.get("lane_id") for r in old_summary.get(kind, [])}
                require(all("legacy" not in record for lane_id, record in after.items() if lane_id not in before_ids),
                        "new legacy facts are unsupported; start a new run")
                for record in old_summary.get(kind, []):
                    require(record.get("lane_id") in after, "assignment history cannot be removed")
                    for field in ("review_subject", "review_input_operation_id"):
                        updated = after[record["lane_id"]]
                        require((field in record) == (field in updated) and record.get(field) == updated.get(field), "assignment review pin is immutable")
                    obligation = record.get("obligation")
                    if isinstance(obligation, dict):
                        updated_obligation = after[record["lane_id"]].get("obligation", {})
                        require(isinstance(updated_obligation, dict) and
                                all(updated_obligation.get(key) == obligation.get(key) for key in ("id", "required")),
                                "obligation identity and required flag are immutable")
                    if record.get("status") in {"pass", "pass-with-risks", "fail", "blocked"}:
                        updated = after[record["lane_id"]]
                        if completion is not None and kind == "subagents" and record.get("lane_id") == completion[0] and updated != record:
                            _validate_completion_update(snapshot, documents, old_summary, next_summary, record, updated, completion)
                            continue
                        require({k: v for k, v in updated.items() if k != "obligation"} ==
                                {k: v for k, v in record.items() if k != "obligation"},
                                "terminal assignment is immutable; use a new lane")
                        handoff = record.get("handoff")
                        if handoff in documents and snapshot.exists(handoff):
                            require(documents[handoff] == snapshot.read_bytes(handoff), "terminal handoff is immutable")
                        for ref in record.get("evidence", []):
                            key = ref.get("path")
                            if key not in mutable_inputs or key not in documents or documents[key] == snapshot.documents.get(key):
                                continue
                            historical_evidence_bytes(snapshot, record, ref)
                            old = snapshot.read_bytes(key)
                            if sha256(reference_bytes(run_dir, ref, snapshot=snapshot)) != ref["sha256"]:
                                continue  # An earlier receipt already preserves this version.
                            capture = "artifacts/source-captures/" + digest(old) + ".bin"
                            require(not snapshot.exists(capture) or snapshot.read_bytes(capture) == old, "preserved input capture conflict")
                            require(capture not in documents or documents[capture] == old, "preserved input capture conflict")
                            documents[capture] = old
                            result.setdefault("preserved_evidence", []).append({"lane_id": record["lane_id"],
                                "assignment": evidence_assignment(record), "reference": ref,
                                "capture": {"path": capture, "sha256": digest(old)}})
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
        from journal_structure import assignment_errors, archived_assignment_facts
        if lifecycle != "reopen":
            history = archived_assignment_facts(snapshot)
            existing_errors = set(assignment_errors(snapshot.documents, history=history))
            proposed_errors = assignment_errors({**snapshot.documents, **documents}, history=history)
            introduced = [error for error in proposed_errors if error not in existing_errors]
            require(not introduced, "; ".join(introduced))
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
    require(snapshot.durable, UNSUPPORTED_STORAGE_VERSION)
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


def encode_json_line(value):
    return encode_json(value).replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


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
    from journal_structure import EVENT_STATUS_RULES
    allowed = EVENT_STATUS_RULES
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
    validate_event(event)
    from journal_structure import CALLER_STATUSES
    require(event.get("status") in CALLER_STATUSES, "unknown caller status")
    run_dir = path.parent
    payload = {k: v for k, v in event.items() if not (assign_timestamp and k == "timestamp")}
    def mutation(snapshot):
        current = dict(event)
        if assign_timestamp:
            current["timestamp"] = now_iso()
        validate_append(path, current, snapshot=snapshot)
        old = snapshot.read_text(path) if snapshot.exists(path) else ""
        return {snapshot.key(path): old + encode_json_line(current) + "\n"}, {}
    return write_entry(run_dir, "event", {"summary": event["summary"], "status": event["status"],
        "stage": event["stage"], "role": event["role"]}, identifier=identifier, payload=payload, mutation=mutation)


def write_entry(run_dir, kind, request, *, identifier=None, mutation=None, replay_guard=None, completion=None, payload=None):
    """Single semantic writer; adapters may retain source-bound domain checks."""
    from journal_structure import validate_request, render_document, DOCUMENT_PATHS
    try:
        request = validate_request(kind, request)
    except ValueError as exc:
        raise JournalError(str(exc)) from exc
    run_dir = Path(run_dir)
    defaults = DOCUMENT_PATHS
    if "path" in request:
        require(kind in defaults and request["path"] == defaults[kind], "document target is writer-owned")
    for ref in request.get("evidence", []):
        logical_path(ref["path"])
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.durable, "semantic writes require a durable journal")
    if kind == "final" and mutation is None:
        from journal_lifecycle import finalize
        summary = request["summary"]
        evidence = [{"path": ref["path"], "sha256": digest(snapshot.read_bytes(ref["path"]))} for ref in request.get("evidence", [])]
        if evidence:
            summary += "\n\n" + render_document("progress", "Referenced verification evidence", request["status"], {"evidence": evidence})
        identifier = identifier or str(uuid.uuid4())
        prior = snapshot.operation_receipt(identifier)
        expected_revision = prior["result"]["validated_revision"] if prior else snapshot.revision
        expected_generation = prior["result"]["generation"] if prior else snapshot.generation
        receipt = finalize(run_dir, expected_run_uuid=snapshot.run_uuid, expected_revision=expected_revision,
            expected_generation=expected_generation, identifier=identifier, final_summary=summary,
            verdict="ship" if request["status"] == "pass" else request["status"])
        return {"operation_id": identifier, **receipt}
    payload = payload if payload is not None else {"kind": kind, "request": request}
    if completion is not None and isinstance(payload.get("artifacts"), dict):
        from verification_evidence import historical_evidence_bytes
        summary = json.loads(snapshot.read_text("delegation-summary.json"))
        previous = next((row for row in summary["subagents"] if row.get("lane_id") == completion["lane_id"]), {})
        if previous.get("evidence") and previous.get("status") in {"pass", "pass-with-risks", "blocked", "fail"} and previous.get("role") not in {"qa-verifier", "reviewer", "reviewer.qa"}:
            artifacts = dict(payload["artifacts"])
            require(set(artifacts) == {previous.get("handoff"), *(ref["path"] for ref in previous.get("evidence", []))},
                    "source completion evidence paths changed")
            for ref in previous.get("evidence", []):
                if ref["path"] in artifacts:
                    require(artifacts[ref["path"]] in {ref["sha256"], digest(snapshot.read_bytes(ref["path"]))},
                            "source completion evidence hash assertion differs")
                    historical_evidence_bytes(snapshot, previous, ref)
                    artifacts[ref["path"]] = ref["sha256"]
            payload = {**payload, "artifacts": artifacts}
    if identifier and snapshot.operation_receipt(identifier) is not None:
        # Idempotent retries never launch a second command.
        saved = snapshot.operation_receipt(identifier)
        require(saved["payload_sha256"] == digest(encode_json(payload).encode()), "operation ID payload conflict")
        if replay_guard is not None:
            replay_guard(snapshot)
        receipt = {"operation_id": identifier, **saved["result"]}
        if kind in {"task", "lesson"} and mutation is None:
            project_memory(snapshot, kind, identifier)
        return receipt
    require(not snapshot.closed, "semantic writes require an open durable journal")
    if mutation is None and kind == "event" and request["stage"] == "spawned":
        require(bool(request.get("lane_id")), "spawned requires the native assignment recorder")
    if kind in {"task", "lesson"} and mutation is None:
        validate_directory(memory_path(snapshot, kind).parent)
        require(not memory_path(snapshot, kind).is_symlink(), "memory target must not be a symlink")
    completed = None
    from verification_evidence import CapturedSource, CodexSessionSource
    service_source = CapturedSource(CodexSessionSource())
    if kind == "check":
        import subprocess
        import shlex
        cwd = Path(request["cwd"])
        require(cwd.is_absolute() and cwd.is_dir() and not cwd.is_symlink(), "check cwd must be an existing absolute directory")
        from task_workspace import registered_workspace
        workspace = registered_workspace(snapshot)
        if workspace is not None:
            protected = [Path(workspace[key]).resolve() for key in ("baseline_root", "working_root", "candidate_root") if workspace.get(key)]
            if workspace.get("seal", {}).get("candidate_root"):
                protected.append(Path(workspace["seal"]["candidate_root"]).resolve())
            require(not any(cwd.resolve() == root or root in cwd.resolve().parents for root in protected), "writing checks require a disposable workspace copy")
        require(cwd.resolve() != snapshot.source_root.resolve() and snapshot.source_root.resolve() not in cwd.resolve().parents, "checks require an explicit disposable working copy")
        readiness = json.loads(snapshot.documents.get("verification-readiness.json", b"{}"))
        for approval in readiness.get("approval_requests", []):
            if any(command["cwd"] == str(cwd) and shlex.split(command["command"]) == request["command"] for command in approval["commands"]):
                require_approval_observation(approval, service_source)
        identifier = operation_id(run_dir, "semantic-write", payload, identifier=identifier)
        try:
            completed = subprocess.run(request["command"], cwd=cwd, capture_output=True)
        except OSError as exc:
            from types import SimpleNamespace
            completed = SimpleNamespace(returncode=None, stdout=b"", stderr=(type(exc).__name__ + ": " + str(exc) + "\n").encode())

    def project(current):
        validate_request(kind, request)
        require(not current.closed, "journal generation is closed")
        if mutation is not None:
            return mutation(current)
        from journal_structure import SERVICE_FIELDS
        if kind in SERVICE_FIELDS:
            documents, result = service_projection(current, kind, request, source=service_source)
            event = {"timestamp": now_iso(), "stage": kind, "role": "orchestrator", "execution_mode": "role-lane",
                "stable_agent_name": "orchestrator", "stable_agent_slug": "orchestrator", "status": request["status"], "summary": request["summary"],
                "artifacts": sorted(key for key in documents if key not in {"timeline.jsonl", "artifacts.json"}), "next_step": ""}
            if "timeline.jsonl" not in documents:
                validate_append(run_dir / "timeline.jsonl", event, snapshot=current)
                raw = (encode_json_line(event) + "\n").encode()
                documents["timeline.jsonl"] = current.documents.get("timeline.jsonl", b"") + raw
                trace = "agents/orchestrator/trace.jsonl"
                documents[trace] = current.documents.get(trace, b"") + raw
            _, index = upsert_artifacts(run_dir / "artifacts.json", event["artifacts"], role="orchestrator", execution_mode="role-lane", stable_agent_name="orchestrator", stable_agent_slug="orchestrator", timestamp=event["timestamp"], lane_id=None, wave=None, critical=False, snapshot=current)
            if index is not None:
                documents["artifacts.json"] = encode_json(index, pretty=True) + "\n"
            return documents, result
        evidence = [{"path": ref["path"], "sha256": digest(current.read_bytes(ref["path"]))} for ref in request.get("evidence", [])]
        if kind == "check":
            # An actual process is the source of command, exit and output evidence.
            status = "pass" if completed.returncode == 0 else "fail"
            output = completed.stdout + completed.stderr
            data = {"command": request["command"], "cwd": request["cwd"], "exit_code": completed.returncode,
                    "status": status, "summary": request["summary"], "output_sha256": digest(output)}
            token = digest(identifier.encode())[:20]
            report_path, output_path = f"checks/command-{token}.json", f"checks/command-{token}.output"
            report = (encode_json(data, pretty=True) + "\n").encode()
            return {report_path: report, output_path: output}, {"exit_code": completed.returncode, "status": status,
                "evidence": [{"path": report_path, "sha256": digest(report)}, {"path": output_path, "sha256": digest(output)}]}
        if kind == "event" and not request.get("lane_id"):
            event = {"timestamp": now_iso(), "stage": request["stage"], "role": request["role"],
                     "stable_agent_name": request["role"], "stable_agent_slug": request["role"],
                     "status": request["status"], "summary": request["summary"],
                     "artifacts": [ref["path"] for ref in evidence], "next_step": request.get("next_step", "")}
            if request.get("commit_hash"):
                event["commit_hash"] = request["commit_hash"]
            validate_append(run_dir / "timeline.jsonl", event, snapshot=current)
            return {"timeline.jsonl": current.documents.get("timeline.jsonl", b"") + (encode_json_line(event) + "\n").encode()}, {}
        if kind in {"handoff", "architecture-outcome"} or kind == "event" and request.get("lane_id"):
            lane_map = json.loads(current.read_text("lane-map.json")) if current.exists("lane-map.json") else {"lanes": []}
            matches = [lane for lane in lane_map["lanes"] if lane["id"] == request["lane_id"]]
            if not matches:
                summary = json.loads(current.read_text("delegation-summary.json"))
                matches = [{"id": row["lane_id"], "execution_mode": mode, **row}
                           for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane"))
                           for row in summary[group] if row.get("lane_id") == request["lane_id"]]
            require(len(matches) == 1, "unknown lane")
            lane = matches[0]
            if kind == "event":
                require(lane["role"] == request["role"], "assignment role differs from registration")
            path = lane.get("handoff") or "handoffs/" + request["lane_id"] + ".md"
            require(isinstance(path, str) and path.startswith("handoffs/"), "lane requires owned handoff path")
            logical_path(path)
            require(lane.get("status") not in {"pass", "pass-with-risks", "fail", "blocked"}, "terminal handoff is immutable")
            text = render_document(kind, request["summary"], request["status"], {"evidence": evidence, "sections": request.get("sections", [])})
            completion_turn_id = None
            if kind == "architecture-outcome":
                from journal_structure import render_architecture_outcome
                from verification_evidence import completed_turn, resolve_completion, final_json
                require(lane.get("type") == "architecture" and lane.get("role") == "architect", "architecture outcome requires architect architecture lane")
                require(lane.get("execution_mode") == "subagent" and lane.get("codex_thread_id"), "architecture outcome requires source-bound architect subagent")
                summary = json.loads(current.read_text("delegation-summary.json"))
                registered = [row for row in summary["subagents"] if row.get("lane_id") == lane["id"]]
                require(len(registered) == 1 and registered[0].get("codex_thread_id") == lane["codex_thread_id"] and registered[0].get("role") == "architect", "architecture outcome requires registered architect source")
                root_thread = summary["verification"]["root_thread_id"]
                completion_turn_id = request.get("completion_turn_id") or resolve_completion(service_source, lane["codex_thread_id"], root_thread, "architect", None, [])
                answer = completed_turn(service_source, lane["codex_thread_id"], completion_turn_id, root_thread, "architect")["answer"]
                source_request = validate_request(kind, final_json(answer))
                semantic = lambda value: {key: item for key, item in value.items() if key != "completion_turn_id"}
                require(semantic(source_request) == semantic(request), "architecture outcome differs from source completion")
                if source_request.get("completion_turn_id"):
                    require(source_request["completion_turn_id"] == completion_turn_id, "architecture source completion selector differs")
                if lane_map.get("schema_version") == 2 and lane_map.get("architecture_contract_required"):
                    require(bool(request.get("claim_evidence_ids")) and bool(request.get("acceptance_criteria_ids")), "architecture outcome requires gate marker IDs")
                facets = list(dict.fromkeys(value for values in lane_map.get("architecture_context", {}).values() for value in values))
                capabilities = lane_map.get("architecture_capabilities", {}).get("selected", [])
                text = render_architecture_outcome(request, evidence, facets, capabilities)
                lane["handoff"] = lane["architecture_design_brief"] = path
            from types import SimpleNamespace
            from verification_evidence import CodexSessionSource
            prepared = replace(current, documents=MappingProxyType({**current.documents, path: text.encode(),
                **({"lane-map.json": (encode_json(lane_map, pretty=True) + "\n").encode()} if kind == "architecture-outcome" else {})})) if kind in {"handoff", "architecture-outcome"} else current
            checkpoint = None
            if kind == "event" and request["stage"] == "blocked-checkpoint":
                require(request["role"] == "orchestrator" and request["status"] == "blocked", "blocked checkpoint requires orchestrator blocked observation")
                checkpoint_path, checkpoint_bytes, archive_id = native_blocked_checkpoint(current)
                prepared = replace(current, documents=MappingProxyType({**current.documents, checkpoint_path: checkpoint_bytes}))
                checkpoint = {"lane_id": request["lane_id"], "snapshot": checkpoint_path}
            args = SimpleNamespace(lane_id=request["lane_id"], role=lane["role"], status=request["status"],
                stage=request["stage"] if kind == "event" else request["status"] if request["status"] in {"fail", "blocked"} else "handoff",
                summary=request["summary"], next_step="", execution_mode=lane["execution_mode"],
                codex_thread_id=lane.get("codex_thread_id"), stable_agent_name=None, stable_agent_slug=None,
                runtime_nickname=None, wave=None, critical=None, verification_json=None,
                commit_hash=request.get("commit_hash"),
                completion_turn_id=completion_turn_id, resolve_session=False, agent_path=None)
            require(kind != "handoff" or lane["execution_mode"] == "role-lane" or lane["role"] not in {"qa-verifier", "reviewer", "reviewer.qa"}, "QA/reviewer subagents require source-completion adapter")
            artifacts = ([path] if kind in {"handoff", "architecture-outcome"} else []) + [ref["path"] for ref in evidence]
            if checkpoint is not None:
                artifacts.append(checkpoint_path)
            documents, result = record_event(run_dir, args, artifacts, service_source, prepared)
            if kind in {"handoff", "architecture-outcome"}:
                documents[path] = text
                result["handoff"] = path
            if checkpoint is not None:
                documents[checkpoint_path] = checkpoint_bytes
                result.update({"checkpoint": checkpoint, "archive_id": archive_id, "evidence": [{"path": checkpoint_path, "sha256": digest(checkpoint_bytes)}]})
            return documents, result
        require(kind != "final", "final requires source-checked finalize")
        text = render_document(kind, request["summary"], request["status"], {"evidence": evidence})
        documents = {defaults[kind]: text}
        result = {}
        if kind == "lesson" and identifier is not None:
            path = "artifacts/memory/" + digest(identifier.encode()) + ".md"
            documents[path] = text
            result["memory_artifact"] = path
        return documents, result

    # Domain validation runs once before allocating a request, again while locked.
    if kind != "check":
        project(snapshot)
        service_source.frozen = True
    identifier = operation_id(run_dir, "semantic-write", payload, identifier=identifier)
    if completion:
        receipt = _transact_completion(run_dir, identifier, payload, project, replay_guard=replay_guard, **completion)
    else:
        receipt = transact(run_dir, identifier, payload, project, replay_guard=replay_guard)
    if kind in {"task", "lesson"} and mutation is None:
        project_memory(JournalSnapshot.open(run_dir), kind, identifier)
    return {"operation_id": identifier, **receipt}


def upsert_record(rows, row, key="id"):
    """Preserve order and unrelated rows while replacing one declared identity."""
    matches = [index for index, value in enumerate(rows) if value.get(key) == row[key]]
    require(len(matches) <= 1, "duplicate service record identity")
    if matches:
        rows[matches[0]] = row
    else:
        rows.append(row)


def native_blocked_checkpoint(snapshot):
    reopen = [event for event in snapshot.timeline() if event.get("stage") == "reopen"]
    require(bool(reopen), "checkpoint requires native finalize blocked followed by reopen")
    event = reopen[-1]
    archived = snapshot.archive(event["archive_id"])
    require(archived.closed and archived.generation + 1 == snapshot.generation, "checkpoint must use the last closed generation")
    final = archived.read_bytes("final.md")
    receipts = [archived.operation_receipt(identifier)["result"] for identifier in archived.receipts]
    require(any(receipt.get("command") == "finalize" and receipt.get("generation") == archived.generation and receipt.get("verdict") == "blocked" and receipt.get("final_sha256") == digest(final) for receipt in receipts), "checkpoint requires authoritative blocked final receipt")
    return "artifacts/checkpoints/generation-" + str(archived.generation) + "/final.md", final, event["archive_id"]


def service_lanes(snapshot):
    if snapshot.exists("lane-map.json"):
        return json.loads(snapshot.read_text("lane-map.json"))
    summary = json.loads(snapshot.read_text("delegation-summary.json")) if snapshot.exists("delegation-summary.json") else {}
    lanes = []
    for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane")):
        for row in summary.get(group, []):
            role = row["role"]
            lane_type = "qa" if role in {"qa-verifier", "senior-qa-verifier"} else "review" if role in {"reviewer", "reviewer.qa"} else "architecture" if role in {"architect", "supervising-architect", "orchestrator"} else "implementation"
            lanes.append({**row, "id": row["lane_id"], "type": lane_type, "execution_mode": mode})
    return {"lanes": lanes}


def service_projection(snapshot, kind, request, *, source=None):
    """Finite intent adapters over the existing journal documents and validators."""
    from journal_structure import domain_validator, render_document
    validator = domain_validator()
    root = snapshot.logical_root
    def load(path, default):
        return json.loads(snapshot.read_text(path)) if snapshot.exists(path) else default
    lanes = service_lanes(snapshot)
    by_id = {lane["id"]: lane for lane in lanes["lanes"]}
    statuses = {key: value.get("status") for key, value in by_id.items()}
    success = request["status"] in {"pass", "pass-with-risks"}
    paths = [ref["path"] for ref in request.get("evidence", [])]
    evidence = [{"path": path, "sha256": digest(snapshot.read_bytes(path))} for path in paths]
    summary = request["summary"]
    documents = {}
    errors = []
    def candidate(path, data):
        documents[path] = encode_json(data, pretty=True) + "\n"
        return replace(snapshot, documents=MappingProxyType({**snapshot.documents, **{key: value.encode() if type(value) is str else value for key, value in documents.items()}}))
    if kind == "claim":
        path = "claim-evidence.json"
        data = load(path, {"version": 1, "claims": []})
        row = {"id": request["id"], "claim": summary, "status": "supported" if success else "gap", "subjects": request["subjects"],
               "owner_lane": request.get("owner_lane"), "reviewed_by": request.get("reviewed_by"), "section": request.get("section"), "evidence": request.get("evidence", [])}
        upsert_record(data["claims"], row)
        current = candidate(path, data)
        errors = validator.validate_claim_evidence_records(root, required_claim_ids=[], required=False, final_verdict="blocked", lane_by_id=by_id,
            normalized_status_by_id=statuses, snapshot=current, presentation_required=False)
        if success and not errors:
            require_native_acceptance(snapshot, row["owner_lane"], "qa-verifier" if by_id[row["owner_lane"]]["type"] == "qa" else "reviewer", source=source)
            require_native_acceptance(snapshot, row["reviewed_by"], "reviewer", source=source)
    elif kind == "acceptance":
        path = "acceptance-traceability.json"
        data = load(path, {"version": 1, "acceptance": []})
        row = {key: request[key] for key in ("id", "source", "subjects", "contract_types", "surface_expectations")}
        row.update({"requirement": summary, "status": "supported" if success else "gap", "evidence": request.get("evidence", []), "negative_fixture_evidence": request.get("negative_fixture_evidence", [])})
        upsert_record(data["acceptance"], row)
        errors = validator.validate_acceptance_traceability_records(root, required_acceptance_ids=[], required=False, final_verdict="blocked", snapshot=candidate(path, data))
    elif kind == "risk":
        path = "risk-mitigations.json"
        data = load(path, {"version": 1, "risks": []})
        row = {key: request[key] for key in ("id", "category", "impact", "affected_scope", "owner_lane", "detected_by")}
        row.update({"problem": summary, "status": "identified", "next_gate": "resolution", "evidence": paths})
        upsert_record(data["risks"], row)
        _, errors = validator.validate_risk_mitigations(root, None, snapshot=candidate(path, data))
        errors.extend(validator.validate_risk_mitigation_lane_references(data["risks"], set(by_id)))
    elif kind in {"resolution", "resolution-attempt", "senior-qa-recovery", "architect-recovery", "supervising-recovery"}:
        path = "risk-resolutions.json"
        data = load(path, {"version": 1, "resolutions": []})
        risks = load("risk-mitigations.json", {"risks": []})["risks"]
        require(request["risk_id"] in {risk["id"] for risk in risks}, "resolution requires an identified risk")
        matches = [row for row in data["resolutions"] if row["risk_id"] == request["risk_id"]]
        require(len(matches) <= 1, "duplicate risk resolution")
        previous = matches[0] if matches else None
        if kind in {"resolution", "resolution-attempt"}:
            domain_status = {"fix": "fixed", "mitigate": "mitigated", "contain": "contained"}[request["effect"]] if success else "unresolved"
            row = {key: request[key] for key in ("risk_id", "resolution_type", "verification", "owner_lane")}
            row.update({"status": domain_status, "resolution": summary, "verified_by": request.get("verified_by"), "reviewed_by": request.get("reviewed_by"), "evidence": paths})
            if previous:
                row.update({key: previous[key] for key in ("attempts", "blocked_recovery") if key in previous})
            if kind == "resolution-attempt":
                attempts = row.setdefault("attempts", [])
                old = [attempt for attempt in attempts if attempt.get("id") == request["id"]]
                number = old[0]["attempt"] if old else len(attempts) + 1
                require(number <= 3, "resolution permits at most three attempts")
                if not old and attempts and attempts[-1]["status"] == "blocked":
                    recovery = previous.get("blocked_recovery", {})
                    recovery_errors = validator.validate_senior_qa_recovery(root, recovery.get("senior_qa_test_design_review"), "resolution", snapshot=snapshot)
                    recovery_errors.extend(validator.validate_architect_recovery(root, recovery.get("architect_review"), "resolution", "architect_review", validator.ARCHITECT_REVIEW_DECISIONS, require_instruction=True, snapshot=snapshot))
                    if number == 3:
                        recovery_errors.extend(validator.validate_architect_recovery(root, recovery.get("supervising_architect_review"), "resolution", "supervising_architect_review", validator.SUPERVISING_ARCHITECT_REVIEW_DECISIONS, snapshot=snapshot))
                    require(not recovery_errors, "blocked attempt requires completed recovery before retry: " + "; ".join(recovery_errors))
                    lane_errors = validator.validate_blocked_resolution_recovery_lanes(root, [previous], by_id, statuses, snapshot=snapshot)
                    require(not lane_errors, "retry requires actual recovery lanes: " + "; ".join(lane_errors))
                attempt = {key: value for key, value in row.items() if key not in {"risk_id", "attempts", "blocked_recovery"}}
                attempt.update({"id": request["id"], "attempt": number, "status": "blocked" if request["status"] == "blocked" else domain_status})
                if attempt["status"] == "blocked":
                    require("blocked_reason" in request and "rollback" in request and "lesson" in request, "blocked attempt requires reason, rollback and lesson")
                    attempt["blocked_reason"] = request["blocked_reason"]
                    rollback = request["rollback"]
                    attempt["rollback"] = {"status": rollback["disposition"], "summary": rollback["summary"]}
                    if "evidence" in rollback:
                        attempt["rollback"]["evidence"] = [ref["path"] for ref in rollback["evidence"]]
                    lesson = request["lesson"]
                    attempt["blocked_lesson"] = {"status": lesson["disposition"], **{key: lesson[key] for key in ("classification", "summary", "forbidden_repeat")}}
                upsert_record(attempts, attempt)
            upsert_record(data["resolutions"], row, "risk_id")
        else:
            require(previous is not None and any(attempt["status"] == "blocked" for attempt in previous.get("attempts", [])), "recovery requires an actual blocked attempt")
            require(request["lane_id"] in by_id, "unknown recovery lane")
            recovery = previous.setdefault("blocked_recovery", {})
            expected_role = "senior-qa-verifier" if kind == "senior-qa-recovery" else "architect" if kind == "architect-recovery" else "supervising-architect"
            recovery_lane = by_id[request["lane_id"]]
            require(recovery_lane["role"] == expected_role and recovery_lane["type"] == ("qa" if kind == "senior-qa-recovery" else "architecture"), "recovery lane role/type mismatch")
            require(not success or recovery_lane["status"] in {"pass", "pass-with-risks"}, "positive recovery requires actual completed lane")
            if kind != "senior-qa-recovery":
                require(recovery_lane.get("critical") is False, "recovery architecture lane must use existing noncritical contract")
            if kind == "senior-qa-recovery":
                observation = {key: request[key] for key in ("covered_acceptance_criteria", "missing_acceptance_criteria", "ambiguous_acceptance_criteria", "added_acceptance_criteria", "external_blockers", "test_cases", "edge_cases", "negative_cases", "regression_cases", "integration_cases", "data_state_cases", "environment_cases")}
                observation.update({"lane": request["lane_id"], "status": request["decision"], "recheck_result": request["recheck"], "evidence": paths})
                errors = validator.validate_senior_qa_recovery(root, observation, "resolution", snapshot=snapshot)
                recovery["senior_qa_test_design_review"] = observation
            else:
                key = "architect_review" if kind == "architect-recovery" else "supervising_architect_review"
                observation = {"lane": request["lane_id"], "decision": request["decision"], "instruction": request["instruction"], "forbidden_repeat": request["forbidden_repeat"], "evidence": paths}
                errors = validator.validate_architect_recovery(root, observation, "resolution", key, validator.ARCHITECT_REVIEW_DECISIONS if kind == "architect-recovery" else validator.SUPERVISING_ARCHITECT_REVIEW_DECISIONS, require_instruction=kind == "architect-recovery", snapshot=snapshot)
                recovery[key] = observation
        current = candidate(path, data)
        _, shape_errors = validator.validate_risk_resolutions(root, "blocked", risks, snapshot=current, presentation_required=False)
        errors.extend(shape_errors)
        if kind == "resolution-attempt" and number > 1 and not old:
            errors.extend(validator.validate_blocked_resolution_recovery_lanes(root, [row], by_id, statuses, snapshot=current, pending_latest_recovery=True))
        if success and kind in {"resolution", "resolution-attempt"}:
            errors.extend(validator.validate_risk_resolution_lane_references([row], by_id, statuses))
            if not errors:
                require_native_acceptance(snapshot, row["verified_by"], "qa-verifier", source=source)
                require_native_acceptance(snapshot, row["reviewed_by"], "reviewer", source=source)
    else:
        return service_observation_projection(snapshot, kind, request, source=source)
    require(not errors, "; ".join(errors))
    report_path = "checks/" + path.replace(".json", ".md")
    rows = data[next(key for key in ("claims", "acceptance", "risks", "resolutions") if key in data)]
    report_summary = "\n\n".join((row.get("id") or row.get("risk_id")) + " [" + row["status"] + "]: " + (row.get("claim") or row.get("requirement") or row.get("problem") or row.get("resolution")) for row in rows)
    report_status = "pass" if all(row["status"] in {"supported", "fixed", "mitigated", "contained"} for row in rows) else "active"
    documents[report_path] = render_document(kind, report_summary, report_status, {"evidence": evidence})
    return documents, {"document": path, "report": report_path, "record_id": request.get("id", request.get("risk_id")), "evidence": evidence}


def service_observation_projection(snapshot, kind, request, *, source=None):
    from journal_structure import domain_validator, render_document
    validator = domain_validator()
    root = snapshot.logical_root
    def load(path, default):
        return json.loads(snapshot.read_text(path)) if snapshot.exists(path) else default
    lane_map = service_lanes(snapshot)
    lanes = {lane["id"]: lane for lane in lane_map["lanes"]}
    context = lane_map.get("architecture_context", {})
    facets = {facet for values in context.values() for facet in values}
    raw_capabilities = lane_map.get("architecture_capabilities", [])
    capabilities = raw_capabilities.get("selected", []) if type(raw_capabilities) is dict else raw_capabilities
    selected_pairs = validator.selected_verification_gate_pairs(context)
    status = request["status"]
    success = status in {"pass", "pass-with-risks"}
    summary = request["summary"]
    paths = [ref["path"] for ref in request.get("evidence", [])]
    refs = [{"path": path, "sha256": digest(snapshot.read_bytes(path))} for path in paths]
    errors = []
    documents = {}
    if kind in {"lane", "lane-compliance", "lane-simplicity", "lane-scope", "simplicity-scope", "lane-gate"}:
        require(snapshot.exists("lane-map.json") or not lane_map["lanes"], "compact uses native trace recorder; declare extended lane-map at init before assignments")
        lane_map.setdefault("schema_version", 1)
    def store(path, data):
        documents[path] = encode_json(data, pretty=True) + "\n"
        if path == "lane-map.json":
            documents["checks/coverage-matrix.md"] = render_coverage_matrix(data)
        return replace(snapshot, documents=MappingProxyType({**snapshot.documents, **{key: value.encode() if type(value) is str else value for key, value in documents.items()}}))
    def lane():
        require(request["lane_id"] in lanes, "unknown observation lane")
        return lanes[request["lane_id"]]
    if kind == "lane":
        require(snapshot.exists("lane-map.json") or not lane_map["lanes"], "compact assignments already exist; use native recorder without materializing lane-map")
        path = "lane-map.json"
        lane_map.setdefault("schema_version", 1)
        old = lanes.get(request["id"])
        if old:
            require(all(old.get(key) == request[field] for key, field in (("role", "role"), ("type", "lane_type"))), "native lane declaration conflicts with registration")
            require(first_binding_allowed(snapshot, request["id"]), "registered lane assignment is immutable after start")
        row = {**(old or {}), "id": request["id"], "type": request["lane_type"], "role": request["role"], "execution_mode": request["execution_mode"],
               "wave": request["wave"], "critical": request["critical"], "status": "planned", "purpose": summary,
               "handoff": "handoffs/" + request["id"] + ".md", "evidence": old.get("evidence", []) if old else []}
        if "allowed_paths" in request:
            row["boundary"] = {"allowed_paths": request["allowed_paths"], "forbidden_paths": request.get("forbidden_paths", []), "changed_paths_artifact": "checks/lane-boundary-" + request["id"] + ".json"}
        upsert_record(lane_map["lanes"], row)
        data = lane_map
        store(path, data)
    elif kind in {"lane-compliance", "lane-simplicity", "lane-scope", "simplicity-scope", "lane-gate", "lane-report"}:
        path = "lane-map.json"
        data = lane_map
        if kind == "lane-report":
            selected = lane()
            report = "checks/" + selected["id"] + ".md"
            documents[report] = render_document(kind, summary, status, {"evidence": refs, "sections": request.get("sections", [])})
            return documents, {"report": report, "evidence": refs}
        if kind == "simplicity-scope":
            data["engineering_simplicity_scope"] = {"primary_surfaces": request["primary_surfaces"], "secondary_surfaces": request["secondary_surfaces"], "evidence": paths, "notes": summary}
            _, _, _, errors = validator.validate_engineering_simplicity_scope(root, data, required=success, allow_pending=not success, snapshot=snapshot)
        else:
            selected = lane()
            compliance = selected.setdefault("architecture_compliance", {}) if kind != "lane-gate" else None
            if kind == "lane-compliance":
                require(set(request["matrix_facets"]) <= facets, "compliance contains unselected matrix facets")
                require(not success or not request["needs_recheck"], "successful compliance cannot need recheck")
                compliance.update({"status": "compliant" if success and not request["needs_recheck"] else "drift", "contract_sections": request["sections"],
                    "matrix_facets": request["matrix_facets"], "notes": summary, "recheck_lane": request.get("recheck_lane")})
                _, errors = validator.validate_architecture_compliance_shape(selected, selected["id"], selected_matrix_facets=facets, known_matrix_facets=facets, selected_capabilities=capabilities, simplicity_required="engineering_simplicity" in compliance)
            elif kind == "lane-simplicity":
                simplicity = {"status": "fixed" if success and request.get("action") else "pass" if success else "drift", "checks": request["checked_checks"],
                    "findings": [summary] if request.get("action") or not success else [], "actions": [request["action"]] if request.get("action") else [], "notes": summary}
                simplicity.update({key: value for key, value in compliance.get("engineering_simplicity", {}).items() if key == "scope_coverage"})
                compliance["engineering_simplicity"] = simplicity
                errors = validator.validate_engineering_simplicity_shape(compliance, selected["id"], selected_capabilities=capabilities)
            elif kind == "lane-scope":
                simplicity = compliance.setdefault("engineering_simplicity", {})
                simplicity["scope_coverage"] = {"primary_surfaces": request["primary_surfaces"], "secondary_surfaces": request["secondary_surfaces"], "evidence": paths, "notes": summary}
                declared = data.get("engineering_simplicity_scope", {})
                _, _, errors = validator.validate_engineering_simplicity_scope_coverage(root, simplicity=simplicity, label=selected["id"], declared_primary_surfaces=set(declared.get("primary_surfaces", [])), declared_secondary_surfaces=set(declared.get("secondary_surfaces", [])), snapshot=snapshot)
            else:
                require(selected["type"] == "qa", "verification gate requires QA lane")
                gate = {"axis": request["axis"], "facet": request["facet"], "status": "pass" if success else "blocked", "check": request["check"], "evidence": paths, "notes": summary}
                require((gate["axis"], gate["facet"]) in selected_pairs, "unselected verification gate")
                if success:
                    require_command_evidence(snapshot, paths, successful=True)
                results = selected.setdefault("verification_results", {"status": "blocked", "gates": []})
                upsert_record(results["gates"], gate, "facet")
                results["status"] = "pass" if {(item["axis"], item["facet"]) for item in results["gates"]} == set(selected_pairs) and all(item["status"] == "pass" for item in results["gates"]) else "blocked"
                _, errors = validator.validate_verification_gate_records(root, [gate], "gate", [(gate["axis"], gate["facet"])], status_field="status", allowed_statuses=validator.VERIFICATION_RESULT_STATUSES, snapshot=snapshot)
        store(path, data)
    elif kind.startswith("harness-"):
        path = "harness-evaluation.json"
        data = load(path, {"version": 1, "status": "blocked-learning", "findings": [], "proposals": []})
        if kind == "harness-finding":
            row = {key: request[key] for key in ("id", "problem_class", "architecture_context", "architecture_capabilities", "outcome", "lesson", "reuse_when", "do_not_reuse_when")}
            row.update({"type": request["finding_type"], "approach": summary, "evidence": paths})
            validator.validate_harness_finding(root, row, 0, facets, set(capabilities), set(capabilities), set(), errors, snapshot=snapshot)
            upsert_record(data["findings"], row)
        elif kind == "harness-proposal":
            require(request["finding_id"] in {row["id"] for row in data["findings"]}, "proposal requires existing finding")
            row = {"id": request["id"], "finding_id": request["finding_id"], "type": request["proposal_type"], "status": "proposed", "target": validator.HARNESS_PROPOSAL_TARGET, "requires_human_approval": False, "rationale": summary, "evidence": paths}
            validator.validate_harness_proposal(root, row, 0, set(), errors, snapshot=snapshot)
            upsert_record(data["proposals"], row)
        else:
            require(not (success or status == "active") or data["findings"] and data["proposals"], "harness evaluation requires findings and proposals")
            if success:
                reviewer = lanes.get(request.get("reviewed_by"))
                require(reviewer is not None and reviewer["type"] == "review" and reviewer["status"] in {"pass", "pass-with-risks"}, "evaluated harness requires source-accepted reviewer")
                require_native_acceptance(snapshot, reviewer["id"], "reviewer", source=source)
            data["status"] = "evaluated" if success else "needs-review" if status == "active" else "blocked-learning"
            data["reviewed_by"] = request.get("reviewed_by")
        data["learning_triggers"] = sorted(validator.detect_harness_learning_triggers(root, final_verdict="blocked" if not success else "ship", architecture_contract_required=lane_map.get("architecture_contract_required", False), resolution_records=load("risk-resolutions.json", {"resolutions": []})["resolutions"], drifting_worker_lanes=[(key, value.get("wave"), value.get("architecture_compliance", {}).get("recheck_lane")) for key, value in lanes.items() if value.get("architecture_compliance", {}).get("status") == "drift"], verification_readiness_data=load("verification-readiness.json", None), snapshot=snapshot))
        data.update({"source_artifacts": sorted(set(data.get("source_artifacts", []) + paths)), "blocked_reason": summary, "blocked_evidence": paths})
        current = store(path, data)
        if kind == "harness-evaluation":
            reviewers = [(key, value.get("wave", 0), value.get("handoff")) for key, value in lanes.items() if value["type"] == "review" and value["status"] in {"pass", "pass-with-risks"}]
            errors.extend(validator.validate_harness_evaluation(root, final_verdict="ship" if success else "blocked", learning_triggers=set(data["learning_triggers"]), architecture_context_facets=list(facets), architecture_capabilities=capabilities, known_architecture_capability_ids=set(capabilities), successful_reviewer_lanes=reviewers, require_reviewer_review=True, snapshot=current, presentation_required=False))
    elif kind.startswith("readiness-"):
        return readiness_projection(snapshot, kind, request, source=source)
    elif kind in {"continuation", "continuation-blocker"}:
        return continuation_projection(snapshot, kind, request)
    else:
        raise JournalError("unknown semantic service operation")
    require(not errors, "; ".join(errors))
    report = "checks/" + path.replace(".json", ".md")
    documents[report] = render_document(kind, summary, "active" if kind == "lane" or kind in {"harness-finding", "harness-proposal"} else status, {"evidence": refs})
    return documents, {"document": path, "report": report, "evidence": refs}


def require_native_acceptance(snapshot, lane_id, role, *, source=None):
    from verification_evidence import CodexSessionSource, source_bound_record, review_subject_kind
    require(snapshot.exists("delegation-summary.json"), "native source acceptance required")
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    records = [row for row in summary["subagents"] if row["lane_id"] == lane_id]
    require(len(records) == 1, "source-accepted subagent required")
    require(review_subject_kind(records[0]) == "product-result", "preliminary review cannot authorize product acceptance")
    source_bound_record(snapshot.logical_root, records[0], summary["verification"], role, source or CodexSessionSource(), snapshot=snapshot, current=False, presentation_required=False)


def require_command_evidence(snapshot, paths, *, successful):
    # The command report namespace is machine-only. Output hash binds actual bytes.
    reports = [path for path in paths if path.startswith("checks/command-") and path.endswith(".json")]
    require(bool(reports), "gate requires actual command report evidence")
    for path in reports:
        receipts = [json.loads(raw)["result"] for raw in snapshot.receipts.values() if "exit_code" in json.loads(raw).get("result", {}) and
                    any(ref.get("path") == path and ref.get("sha256") == digest(snapshot.read_bytes(path)) for ref in json.loads(raw)["result"].get("evidence", []))]
        require(bool(receipts), "command evidence requires matching native receipt")
        report = json.loads(snapshot.read_text(path))
        output = path[:-5] + ".output"
        require(any(receipt["exit_code"] == report["exit_code"] and receipt["status"] == report["status"] and
                    {"path": output, "sha256": digest(snapshot.read_bytes(output))} in receipt["evidence"] for receipt in receipts), "command receipt/output/exit mismatch")
        require(digest(snapshot.read_bytes(output)) == report["output_sha256"], "command output changed")
        require(not successful or report["exit_code"] == 0 and report["status"] == "pass", "gate requires successful actual command")


def validate_preliminary_gates(snapshot, gates, *, record=None, source=None, completion=None, execution_required=False, current=True):
    """Finite source facts, using the existing gate vocabulary and evidence reader."""
    from journal_structure import domain_validator
    from verification_evidence import verify_reference, historical_evidence_bytes
    require(type(gates) is list, "readiness_gates must be an array")
    if not gates:
        return []
    rows = []
    for gate in gates:
        require(type(gate) is dict and set(gate) == {"axis", "facet", "check", "readiness", "evidence", "notes"}, "unknown readiness gate fields")
        require(type(gate["evidence"]) is list and bool(gate["evidence"]), "readiness gate evidence required")
        for ref in gate["evidence"]:
            require(type(ref) is dict and {"path", "sha256"} <= set(ref) <= {"path", "sha256", "source_event", "source_call_id", "source_part"}, "readiness gate requires hashed evidence references")
            require(set(ref) == {"path", "sha256"} or {"source_event", "source_call_id"} <= set(ref), "readiness tool locator incomplete")
            if current:
                verify_reference(snapshot.logical_root, ref, snapshot=snapshot)
            else:
                require(record is not None, "historical readiness evidence requires assignment")
                historical_evidence_bytes(snapshot, record, {"path": ref["path"], "sha256": ref["sha256"]})
        tools = [ref for ref in gate["evidence"] if "source_event" in ref]
        if tools:
            from verification_evidence import readiness_tool_evidence
            require(record is not None and source is not None, "readiness tool source required")
            for ref in tools:
                readiness_tool_evidence(snapshot, record, ref, source, completion=completion, current=current)
        if execution_required and gate["readiness"] == "ready":
            paths = [ref["path"] for ref in gate["evidence"]]
            reports = [path for path in paths if path.startswith("checks/command-") and path.endswith(".json")]
            require(bool(tools) or bool(reports), "gate requires actual command or source-bound tool evidence")
            if reports:
                require_command_evidence(snapshot, reports, successful=True)
        rows.append({**gate, "evidence": [ref["path"] for ref in gate["evidence"]]})
    validator = domain_validator()
    context = service_lanes(snapshot).get("architecture_context", {})
    _, errors = validator.validate_verification_gate_records(snapshot.logical_root, rows, "preliminary readiness", validator.selected_verification_gate_pairs(context), status_field="readiness", allowed_statuses=validator.VERIFICATION_GATE_READINESS_STATUSES, snapshot=snapshot)
    require(not errors, "; ".join(errors))
    return rows


def require_approval_observation(approval, source):
    observation = approval.get("source_observation")
    require(approval.get("status") == "approved" and observation is not None and observation.get("decision") == "approved", "approval requires actual scoped user observation")
    actual = source.read(observation["thread_id"], event_indices=(observation["event"],))[observation["event"] - 1]
    require(digest(encode_json(actual).encode()) == observation["sha256"] and
            digest(encode_json(approval["commands"]).encode()) == observation["commands_sha256"], "approval source/command binding changed")


def automatic_readiness(snapshot, source):
    """Materialize existing forms from applicable source facts, without a subprocess."""
    from verification_evidence import review_subject_kind, source_bound_record, require_current_architecture_inputs, readiness_obligation_endpoints
    require_current_architecture_inputs(snapshot)
    from journal_structure import domain_validator, render_document
    lanes = service_lanes(snapshot)
    pairs = domain_validator().selected_verification_gate_pairs(lanes.get("architecture_context", {}))
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    obligations = lanes.get("verification_readiness", {}).get("lanes", [])
    endpoints = readiness_obligation_endpoints(snapshot, summary, obligations) if obligations else set()
    candidates = [row for row in summary["subagents"] if row.get("role") == "qa-verifier" and
        row.get("status") in {"pass", "pass-with-risks"} and review_subject_kind(row) == "requirements-plan" and row.get("completion_turn_id") and row.get("readiness_gates") and
        (not obligations or row.get("lane_id") in endpoints)]
    require(bool(candidates), "verification_readiness requires source-confirmed preliminary QA gate mapping")
    record = candidates[-1]
    require(record.get("obligation", {}).get("state") == "current" and record["obligation"].get("required") is True, "readiness requires current mandatory obligation endpoint")
    source_bound_record(snapshot.logical_root, record, summary["verification"], "qa-verifier", source, snapshot=snapshot, current=True)
    gates = validate_preliminary_gates(snapshot, record["readiness_gates"], record=record, source=source, execution_required=True)
    require({(gate["axis"], gate["facet"]) for gate in gates} == set(pairs) and all(gate["readiness"] == "ready" for gate in gates), "verification readiness requires all applicable gates ready")
    bindings = set()
    for gate in gates:
        for path in gate["evidence"]:
            if path.startswith("checks/command-") and path.endswith(".json"):
                report = json.loads(snapshot.read_text(path))
                bindings.add((tuple(report["command"]), report["cwd"]))
    receipts = [json.loads(raw) for raw in snapshot.receipts.values()]
    latest = {}
    for receipt in receipts:
        if "exit_code" not in receipt.get("result", {}):
            continue
        for ref in receipt["result"].get("evidence", []):
            path = ref["path"]
            if path.startswith("checks/command-") and path.endswith(".json"):
                report = json.loads(snapshot.read_text(path))
                binding = (tuple(report["command"]), report["cwd"])
                if binding in bindings and receipt["revision"] > latest.get(binding, (0, ""))[0]:
                    latest[binding] = (receipt["revision"], path)
    for _, path in latest.values():
        require_command_evidence(snapshot, [path], successful=True)
    data = json.loads(snapshot.read_text("verification-readiness.json")) if snapshot.exists("verification-readiness.json") else {"version": 1, "attempts": [], "approval_requests": [], "approval_executions": []}
    require(not any(item["status"] in {"pending", "declined"} for item in data["approval_requests"]), "verification readiness retains real approval blocker")
    endorsed_revision = max((receipt["revision"] for receipt in receipts if
        receipt.get("result", {}).get("completion_fields", {}).get("completion_turn_id") == record["completion_turn_id"] and
        receipt["result"]["completion_fields"].get("review_subject") == record["review_subject"]), default=0)
    observed_revision = max((receipt["revision"] for receipt in receipts if receipt.get("result", {}).get("document") == "verification-readiness.json"), default=0)
    known_blocker = any((not obligations or attempt["lane"] in endpoints) and
        (attempt.get("blockers") or any(gate["readiness"] != "ready" for gate in attempt["gates"])) for attempt in data["attempts"])
    require(not (observed_revision > endorsed_revision and known_blocker), "verification readiness retains newer environment/check blocker")
    for approval in data["approval_requests"]:
        require_approval_observation(approval, source)
    attempt = {"id": record["lane_id"], "lane": record["lane_id"], "status": "ready", "gates": gates, "blockers": [], "approval_requests": [], "notes": "Source-confirmed preliminary QA", "evidence": [ref["path"] for ref in record["evidence"]]}
    upsert_record(data["attempts"], attempt)
    data["status"] = "ready"
    lane = next((row for row in lanes["lanes"] if row["id"] == record["lane_id"]), None)
    require(lane is not None and lane.get("type") == "qa" and lane.get("critical") is True, "readiness requires critical QA lane")
    lane["verification_results"] = {"status": "pass", "gates": [{"axis": gate["axis"], "facet": gate["facet"], "status": "pass", "evidence": gate["evidence"], "notes": gate["notes"]} for gate in gates]}
    lanes["verification_readiness"] = {"artifact": "verification-readiness.json", "lanes": obligations or [record["lane_id"]]}
    # Handoff headings were generated before source completion and remain immutable.
    require("\n## Verification Gate Results\n" in snapshot.read_text(record["handoff"]), "preliminary handoff requires generated Verification Gate Results")
    return {"lane-map.json": encode_json(lanes, pretty=True) + "\n", "verification-readiness.json": encode_json(data, pretty=True) + "\n", "checks/verification-readiness.md": render_document("readiness", attempt["notes"], "pass")}


def worker_readiness_snapshot(snapshot, args, source):
    lanes = service_lanes(snapshot)
    lane = next((row for row in lanes["lanes"] if row["id"] == args.lane_id), None)
    if args.stage != "spawned" or lane is None or lane.get("type") not in {"implementation", "integration"} or lanes.get("schema_version") != 2 or not lanes.get("architecture_contract_required"):
        return snapshot, {}
    documents = automatic_readiness(snapshot, source)
    return replace(snapshot, documents=MappingProxyType({**snapshot.documents, **{key: value.encode() if isinstance(value, str) else value for key, value in documents.items()}})), documents


def require_source_readiness_gate(snapshot, gate, source):
    from verification_evidence import review_subject_kind, source_bound_record
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    matches = []
    for record in summary["subagents"]:
        if record.get("role") == "qa-verifier" and record.get("status") in {"pass", "pass-with-risks"} and review_subject_kind(record) == "requirements-plan" and record.get("completion_turn_id"):
            rows = validate_preliminary_gates(snapshot, record.get("readiness_gates", []), record=record, source=source, execution_required=True)
            if gate in rows:
                source_bound_record(snapshot.logical_root, record, summary["verification"], "qa-verifier", source, snapshot=snapshot, current=True)
                matches.append(record)
    require(bool(matches), "readiness gate requires matching source-confirmed QA applicability")


def readiness_projection(snapshot, kind, request, *, source=None):
    from journal_structure import domain_validator, render_document
    validator = domain_validator()
    path = "verification-readiness.json"
    data = json.loads(snapshot.read_text(path)) if snapshot.exists(path) else {"version": 1, "status": "blocked", "attempts": [], "approval_requests": [], "approval_executions": []}
    lane_map = service_lanes(snapshot)
    by_id = {lane["id"]: lane for lane in lane_map["lanes"]}
    context = lane_map.get("architecture_context", {})
    pairs = validator.selected_verification_gate_pairs(context)
    success = request["status"] in {"pass", "pass-with-risks"}
    if success and kind in {"readiness-attempt", "readiness-gate"}:
        from verification_evidence import require_current_architecture_inputs
        require_current_architecture_inputs(snapshot)
    paths = [ref["path"] for ref in request.get("evidence", [])]
    for evidence_path in paths:
        snapshot.read_bytes(evidence_path)
    if kind == "readiness-attempt":
        lane_id = request["lane_id"]
        require(lane_id in by_id and by_id[lane_id]["type"] == "qa" and by_id[lane_id]["role"] == "qa-verifier", "readiness attempt requires registered QA lane")
        prior = next((attempt for attempt in data["attempts"] if attempt["id"] == request["id"]), None)
        row = {"id": request["id"], "lane": lane_id, "status": "blocked", "gates": prior["gates"] if prior else [], "blockers": request["blockers"], "approval_requests": request.get("approval_requests", []), "notes": request["summary"], "evidence": paths}
        require(all(identifier in {item["id"] for item in data["approval_requests"]} for identifier in row["approval_requests"]), "attempt references unknown approval request")
        if success:
            require(bool(pairs), "ready requires selected substantive verification gates")
            require(not row["blockers"] and not row["approval_requests"], "ready attempt cannot retain blockers or pending approvals")
            require({(gate["axis"], gate["facet"]) for gate in row["gates"]} == set(pairs) and all(gate["readiness"] == "ready" for gate in row["gates"]), "ready attempt requires all selected gates")
            for gate in row["gates"]:
                require_source_readiness_gate(snapshot, gate, source)
                require_command_evidence(snapshot, gate["evidence"], successful=True)
            row["status"] = "ready"
        else:
            require(bool(row["blockers"]), "pending readiness attempt requires honest blockers")
            row["status"] = "needs-approval" if row["approval_requests"] else "blocked"
        upsert_record(data["attempts"], row)
    elif kind == "readiness-gate":
        attempt = next((attempt for attempt in data["attempts"] if attempt["id"] == request["attempt_id"]), None)
        require(attempt is not None, "gate requires existing readiness attempt")
        require((request["axis"], request["facet"]) in pairs, "gate must reference selected context")
        if success:
            require_command_evidence(snapshot, paths, successful=True)
        approval_id = request.get("approval_request")
        if approval_id:
            require(any(item["id"] == approval_id and item["status"] == "pending" and request["facet"] in item["affected_gates"] for item in data["approval_requests"]), "gate approval requires actual pending request")
        gate = {"axis": request["axis"], "facet": request["facet"], "readiness": "ready" if success else "needs-approval" if approval_id else "blocked", "check": request["check"], "evidence": paths, "notes": request["summary"]}
        if success:
            require_source_readiness_gate(snapshot, gate, source)
        _, errors = validator.validate_verification_gate_records(snapshot.logical_root, [gate], "readiness gate", [(gate["axis"], gate["facet"])], status_field="readiness", allowed_statuses=validator.VERIFICATION_GATE_READINESS_STATUSES, snapshot=snapshot)
        require(not errors, "; ".join(errors))
        upsert_record(attempt["gates"], gate, "facet")
        attempt["status"] = "needs-approval" if approval_id else "blocked"
    elif kind == "readiness-approval":
        prior = next((item for item in data["approval_requests"] if item["id"] == request["id"]), None)
        require(not pairs or set(request["affected_gates"]) <= {facet for axis, facet in pairs}, "approval references unselected gate")
        for command in request["commands"]:
            snapshot.read_bytes(command["source"])
        row = {"id": request["id"], "status": "pending", "reason": request["summary"], "commands": [{**command, "requires_user_approval": True} for command in request["commands"]],
               "manual_instruction": request["manual_instruction"], "resume_phrase": "Готово", "affected_gates": request["affected_gates"], "created_at": prior["created_at"] if prior else now_iso()}
        if request.get("source_event") is not None:
            require(prior is not None, "approval observation requires a prior pending request")
            summary = json.loads(snapshot.read_text("delegation-summary.json"))
            thread = summary["verification"]["root_thread_id"]
            index = request["source_event"]
            events = source.read(thread, event_indices=(index,))
            require(0 < index <= len(events), "approval source event missing")
            event = events[index - 1]
            require(event.get("type") == "response_item" and event.get("payload", {}).get("role") == "user", "approval must come from actual root user message")
            message = "".join(part.get("text", "") for part in event["payload"].get("content", []))
            require(request.get("source_quote") and request["source_quote"] in message, "approval quote must occur in actual root user message")
            require(request.get("decision") == ("approve" if success else "decline"), "approval intent/status mismatch")
            row["source_observation"] = {"thread_id": thread, "event": index, "sha256": digest(encode_json(event).encode()), "quote": request["source_quote"], "decision": "approved" if success else "declined", "commands_sha256": digest(encode_json(row["commands"]).encode())}
            row["status"] = "approved" if success else "declined"
        else:
            require(not success and request["status"] != "blocked", "terminal approval requires actual user source event")
        upsert_record(data["approval_requests"], row)
    else:
        approval = next((item for item in data["approval_requests"] if item["id"] == request["request_id"]), None)
        require(approval is not None and approval.get("source_observation", {}).get("decision") == "approved", "execution requires actual approved user observation")
        require_approval_observation(approval, source)
        saved = snapshot.operation_receipt(request["check_id"])
        require(saved is not None and "exit_code" in saved["result"], "approval execution requires actual command receipt")
        result = saved["result"]
        report_ref = next(ref for ref in result["evidence"] if ref["path"].endswith(".json"))
        report = json.loads(snapshot.read_text(report_ref["path"]))
        import shlex
        require(any(report["command"] == shlex.split(command["command"]) and report["cwd"] == command["cwd"] for command in approval["commands"]), "execution differs from approved command")
        paths = [ref["path"] for ref in result["evidence"]]
        require_command_evidence(snapshot, paths, successful=success)
        require(success == (result["exit_code"] == 0), "execution status differs from actual exit")
        prior_execution = next((item for item in data["approval_executions"] if item["request_id"] == approval["id"]), None)
        paths = list(dict.fromkeys((prior_execution.get("evidence", []) if prior_execution else []) + paths))
        completed_commands = {}
        for report_path in paths:
            if report_path.endswith(".json"):
                actual_report = json.loads(snapshot.read_text(report_path))
                completed_commands[(actual_report["cwd"], tuple(actual_report["command"]))] = actual_report["exit_code"]
        all_succeeded = all(completed_commands.get((command["cwd"], tuple(shlex.split(command["command"])))) == 0 for command in approval["commands"])
        execution_status = "succeeded" if all_succeeded else "not-run" if success else "failed"
        upsert_record(data["approval_executions"], {"request_id": approval["id"], "status": execution_status, "evidence": paths}, "request_id")
        if all_succeeded:
            approval["status"] = "approved"
    pending = any(item["status"] == "pending" for item in data["approval_requests"])
    declined = any(item["status"] == "declined" for item in data["approval_requests"])
    data["status"] = "paused-blocked" if declined else "needs-approval" if pending else "ready" if data["attempts"] and data["attempts"][-1]["status"] == "ready" else "blocked"
    require(data["status"] != "ready" or not pending, "pending approvals block readiness")
    documents = {path: encode_json(data, pretty=True) + "\n"}
    report = "checks/verification-readiness.md"
    text = "\n".join(attempt["id"] + " [" + attempt["status"] + "]: " + attempt.get("notes", "") for attempt in data["attempts"])
    documents[report] = render_document("readiness", text or request["summary"], "pass" if data["status"] == "ready" else "blocked")
    return documents, {"document": path, "report": report, "status": data["status"], "evidence": [{"path": p, "sha256": digest(snapshot.read_bytes(p))} for p in paths]}


def continuation_projection(snapshot, kind, request):
    from journal_structure import domain_validator, render_document
    validator = domain_validator()
    path = "continuation-summary.json"
    data = json.loads(snapshot.read_text(path)) if snapshot.exists(path) else {"version": 1, "resolved_blockers": []}
    data["notes"] = request["summary"]
    lane_map = service_lanes(snapshot)
    by_id = {lane["id"]: lane for lane in lane_map["lanes"]}
    success = request["status"] in {"pass", "pass-with-risks"}
    paths = [ref["path"] for ref in request.get("evidence", [])]
    for evidence in paths:
        snapshot.read_bytes(evidence)
    if kind == "continuation-blocker":
        upsert_record(data["resolved_blockers"], {"id": request["id"], "resolution": request["summary"], "evidence": paths})
    elif request["status"] in {"planned", "active"}:
        return write_event_projection(snapshot, "continuation", request)
    else:
        require(any(json.loads(raw).get("result", {}).get("checkpoint") == request["checkpoint"] for raw in snapshot.receipts.values()), "continuation requires an actual native checkpoint receipt")
        data.update({key: request[key] for key in ("readiness_lane", "historical_worker_lanes", "new_worker_lanes", "revalidated_lanes")})
        data.update({"status": "resumed-ready" if success else "resumed-blocked" if request["status"] == "blocked" else "resumed-fail",
            "previous_checkpoint": {**request["checkpoint"], "verdict": "blocked"}, "qa_recheck_lane": request.get("qa_recheck_lane"), "reviewer_recheck_lane": request.get("reviewer_recheck_lane")})
        current = replace(snapshot, documents=MappingProxyType({**snapshot.documents, path: (encode_json(data, pretty=True) + "\n").encode()}))
        workers = [(key, lane.get("wave", 0), lane.get("handoff")) for key, lane in by_id.items() if lane["type"] in {"implementation", "integration"} and lane["status"] in {"pass", "pass-with-risks"}]
        readiness = json.loads(snapshot.read_text("verification-readiness.json")) if snapshot.exists("verification-readiness.json") else None
        errors = validator.validate_continuation_summary(snapshot.logical_root, final_verdict="ship" if success else request["status"], lane_by_id=by_id, normalized_status_by_id={key: lane["status"] for key, lane in by_id.items()}, successful_worker_lanes=workers, verification_readiness_data=readiness, snapshot=current, presentation_required=False)
        require(not errors, "; ".join(errors))
    report = "checks/continuation-summary.md"
    return {path: encode_json(data, pretty=True) + "\n", report: render_document(kind, request["summary"], request["status"])}, {"document": path, "report": report}


def write_event_projection(snapshot, stage, request):
    event = {"timestamp": now_iso(), "stage": stage, "role": "orchestrator", "stable_agent_name": "orchestrator", "stable_agent_slug": "orchestrator", "status": request["status"], "summary": request["summary"], "artifacts": [], "next_step": ""}
    validate_append(snapshot.logical_root / "timeline.jsonl", event, snapshot=snapshot)
    return {"timeline.jsonl": snapshot.documents.get("timeline.jsonl", b"") + (encode_json_line(event) + "\n").encode()}, {}


def memory_path(snapshot, kind):
    root = snapshot.source_root
    return root / ".agent-work" / "tasks" / ("todo.md" if kind == "task" else "lessons.md")


def project_memory(snapshot, kind, identifier):
    """Repairable journal projection; preserve all unrelated memory bytes."""
    path = memory_path(snapshot, kind)
    validate_directory(path.parent)
    require(not path.is_symlink(), "memory target must not be a symlink")
    token = snapshot.run_uuid if kind == "task" else digest(identifier.encode())
    begin = ("<!-- agent-flow:" + token + ":begin -->\n").encode()
    end = ("<!-- agent-flow:" + token + ":end -->\n").encode()
    old = capture_file(path) if path.exists() else b""
    require(old.count(begin) == old.count(end) <= 1, "malformed memory section")
    artifact = "run.md" if kind == "task" else snapshot.operation_receipt(identifier)["result"]["memory_artifact"]
    body = snapshot.read_bytes(artifact)
    section = begin + body + end
    if begin in old:
        start, stop = old.index(begin), old.index(end) + len(end)
        require(stop > start, "malformed memory section")
        updated = old[:start] + section + old[stop:]
    else:
        updated = old + (b"\n" if old and not old.endswith(b"\n") else b"") + section
    if updated == old:
        return
    durable_mkdir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".memory-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(updated)
            handle.flush()
            sync_file(handle.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_review_input(run_dir, lane_id, sources, *, identifier=None):
    """Freeze declared full inputs under the existing writer lock, before dispatch."""
    from verification_evidence import review_source_bytes, requirements_digest, declared_review_sources, review_context_sources
    require(type(lane_id) is str and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", lane_id), "review lane must be kebab-case")
    require(type(sources) is list and bool(sources) and all(type(name) is str and name.strip() for name in sources), "review requires full declared sources")
    payload = {"command": "prepare-review-input", "lane_id": lane_id, "sources": sorted(sources)}
    identifier = operation_id(run_dir, "prepare-review-input", payload, identifier=identifier)
    def freeze(current):
        require(first_binding_allowed(current, lane_id), "review input cannot change bound assignment")
        captures, documents = [], {}
        contexts = review_context_sources(current)
        for name in sorted(sources):
            key, raw = review_source_bytes(current, name, context_sources=contexts)
            path = "artifacts/source-captures/" + digest(raw) + ".bin"
            require(path not in current.documents or current.read_bytes(path) == raw, "immutable review capture conflict")
            documents[path] = raw
            captures.append({"key": key, "source": name, "path": path})
        captures.sort(key=lambda row: row["key"])
        require(len({row["key"] for row in captures}) == len(captures), "duplicate logical review sources")
        rows = [{"key": row["key"], "sha256": digest(documents[row["path"]])} for row in captures]
        missing = declared_review_sources(current, context_sources=contexts) - {row["key"] for row in rows}
        require(not missing, "review omits declared inputs: " + ", ".join(sorted(missing)))
        from task_workspace import registered_workspace, current_scope_digest
        workspace = registered_workspace(current)
        if workspace is not None:
            current_scope_digest(workspace, inputs=current.external_inputs)
        subject = {"kind": "requirements-plan", "sources": rows, "digest": requirements_digest(rows)}
        # This is the instant all bytes are captured, not caller/preflight time.
        frozen_at = now_iso()
        marker = {"operation_id": identifier, "run_uuid": current.run_uuid, "generation": current.generation,
                  "lane_id": lane_id, "kind": subject["kind"], "digest": subject["digest"]}
        return documents, {"command": "prepare-review-input", "run_uuid": current.run_uuid,
            "generation": current.generation, "lane_id": lane_id, "review_subject": subject,
            "captures": captures, "frozen_at": frozen_at,
            "delegation_input": "AgentFlow-Review-Input: " + encode_json(marker)}
    return {"operation_id": identifier, **transact(run_dir, identifier, payload, freeze)}


def first_binding_allowed(snapshot, lane_id):
    """A plan is unbound only when the loaded journal has no execution identity."""
    from journal_structure import assignment_errors, archived_assignment_facts
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    if any(row.get("lane_id") == lane_id for group in ("subagents", "role_lanes") for row in summary.get(group, [])):
        return False
    history = archived_assignment_facts(snapshot, include_native=True)
    require(not assignment_errors(snapshot.documents, history=history), "assignment journal integrity invalid")
    if lane_id in history:
        return False
    lanes = json.loads(snapshot.documents.get("lane-map.json", b"{}"))
    lane = next((row for row in lanes.get("lanes", []) if row.get("id") == lane_id), None)
    return lane is None or lane.get("status") == "planned" and not any(lane.get(key) is not None for key in ("codex_thread_id", "trace"))


def assignment_lane(snapshot, args):
    from journal_structure import canonical_role, normalized
    def inherit_metadata(row):
        for field in ("wave", "critical"):
            if field in row:
                supplied = getattr(args, field)
                require(supplied is None or supplied == row[field], f"assignment {args.lane_id}.{field} differs from assignment")
                setattr(args, field, row[field])
    if not args.lane_id:
        return None, None
    unbound = first_binding_allowed(snapshot, args.lane_id)
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    registered = False
    for group, mode in (("subagents", "subagent"), ("role_lanes", "role-lane")):
        for record in summary.get(group, []):
            if record.get("lane_id") == args.lane_id:
                inherit_metadata(record)
                registered = True
                require(mode == args.execution_mode and canonical_role(record.get("role")) == canonical_role(args.role),
                        "assignment identity changed; use a new lane_id")
                require(not args.codex_thread_id or record.get("codex_thread_id") == args.codex_thread_id,
                        "assignment identity changed; use a new lane_id")
    require(unbound or registered, "assignment has prior execution identity; use existing recovery or a new lane_id")
    for event in snapshot.timeline():
        if event.get("lane_id") == args.lane_id:
            inherit_metadata(event)
            require(event.get("stable_agent_name") == (args.stable_agent_name or args.role)
                    and event.get("stable_agent_slug") == (args.stable_agent_slug or safe_path_segment(args.role)),
                    "assignment stable identity changed; use a new lane_id")
    if not snapshot.exists("lane-map.json"):
        return None, None
    lane_map = json.loads(snapshot.read_text("lane-map.json"))
    matches = [lane for lane in lane_map.get("lanes", [])
               if isinstance(lane, dict) and lane.get("id") == args.lane_id]
    require(len(matches) == 1, f"assignment {args.lane_id}: unknown lane or duplicate id")
    lane = matches[0]
    inherit_metadata(lane)
    require(canonical_role(lane.get("role")) == canonical_role(args.role),
            f"assignment {args.lane_id}.role must match lane role {lane.get('role')!r}")
    require(unbound or normalized(lane.get("execution_mode")) == normalized(args.execution_mode),
            f"assignment {args.lane_id}.execution_mode must match lane mode {lane.get('execution_mode')!r}")
    require(args.execution_mode != "role-lane" or not lane_map.get("architecture_contract_independent")
            or lane.get("type") != "architecture" or lane.get("role") != "architect",
            "independent architecture contract requires subagent execution")
    return lane_map, lane


def worker_start_preflight(snapshot, lane_map, lane, verification, args):
    from task_workspace import registered_workspace
    summary = json.loads(snapshot.documents.get("delegation-summary.json", b"{}"))
    bound = any(row.get("lane_id") == args.lane_id and row.get("status") != "planned" for group in ("subagents", "role_lanes") for row in summary.get(group, []))
    admission = args.stage == "spawned" or bool(args.lane_id and args.status == "active" and not bound)
    explicit_product = args.stage in {"implementation", "integration", "fix"}
    starting = admission or explicit_product or (
        lane is not None and lane.get("status") == "planned" and args.status in {"active", "spawned"})
    if not starting:
        return
    label = f"assignment {args.lane_id}"
    mapped_product = lane is not None and lane.get("type") in {"implementation", "integration"}
    if admission:
        require(verification.get("task_kind") in {"change", "analysis"}, label + ": admission requires resolved task_kind=change or analysis")
        if verification["task_kind"] == "change":
            require(registered_workspace(snapshot) is not None, label + (": implementation requires prepared workspace" if mapped_product else ": change admission requires prepared workspace"))
    if not explicit_product and not mapped_product:
        return
    from verification_evidence import require_current_architecture_inputs
    require_current_architecture_inputs(snapshot)
    require(verification.get("task_kind") == "change", label + ": implementation requires task_kind=change")
    workspace = registered_workspace(snapshot)
    require(workspace is not None, label + ": implementation requires prepared workspace")
    author = args.codex_thread_id if args.execution_mode == "subagent" else verification.get("root_thread_id")
    require(isinstance(author, str) and bool(author), label + ": implementation author identity missing")
    if not mapped_product:
        from workspace_scope import previous_roots
        require(bool(previous_roots(workspace)), label + ": implementation requires declared product scope")
        verification["author_thread_ids"] = list(dict.fromkeys([*verification.get("author_thread_ids", []), author]))
        return
    boundary = lane.get("boundary")
    require(isinstance(boundary, dict) and isinstance(boundary.get("allowed_paths"), list)
            and bool(boundary["allowed_paths"]) and all(isinstance(p, str) and p.strip() for p in boundary["allowed_paths"]),
            label + ".boundary.allowed_paths must contain implementation paths")
    if lane_map.get("schema_version") == 2 and lane_map.get("architecture_contract_required"):
        from journal_lifecycle import load_validator
        validator_path = Path(__file__).with_name("validate-run.py")
        raw = snapshot.external_inputs.file(validator_path) if snapshot.external_inputs is not None else validator_path.read_bytes()
        validator = load_validator(raw)
        by_id = {item["id"]: item for item in lane_map["lanes"]}
        context = lane_map.get("architecture_context", {})
        readiness, errors = validator.validate_verification_readiness(
            snapshot.logical_root, lane_map, required=True, architecture_context_by_axis=context,
            lane_by_id=by_id, normalized_status_by_id={key: value.get("status") for key, value in by_id.items()},
            final_verdict=None, snapshot=snapshot)
        require(not errors, label + ": " + "; ".join(errors))
        require(readiness is not None and readiness.get("status") == "ready",
                label + ": implementation requires verification readiness=ready")
    author = args.codex_thread_id if args.execution_mode == "subagent" else verification.get("root_thread_id")
    require(isinstance(author, str) and bool(author), label + ": implementation author identity missing")
    verification["author_thread_ids"] = list(dict.fromkeys([*verification.get("author_thread_ids", []), author]))


def prepare_summary(run_dir: Path, args, artifact_paths: list[str], source, *, snapshot=None) -> tuple[dict | None, dict]:
    from verification_evidence import EvidenceError, completed_turn, empty_verification, reference_bytes, result_hash, sha256, validate_verification, resolve_completion, completion_follows, validate_summary_shape, changed_paths, accepted_record, require_own_reviewer_handoff, require_workspace_or_historical, historical_result_hash, SHA256, delivery_scope
    from task_workspace import registered_workspace
    """Validate everything before writing any trace, directory or index."""
    snapshot = snapshot or JournalSnapshot.open(run_dir)
    if not (args.verification_json or args.completion_turn_id or args.lane_id or args.resolve_session or args.stage in {"spawned", "implementation", "integration", "fix"}):
        return None, {}
    path = run_dir / "delegation-summary.json"
    lane_path = run_dir / "lane-map.json"
    lane_map = json.loads(snapshot.read_text(lane_path)) if snapshot.exists(lane_path) else {}
    _, assigned_lane = assignment_lane(snapshot, args)
    data = json.loads(snapshot.read_text(path)) if snapshot.exists(path) else {
        "version": 1, "subagents_used": False, "role_lanes_used": False,
        "subagents": [], "role_lanes": [], "notes": "Recorded agent assignments.",
        "verification": empty_verification(),
    }
    validate_summary_shape(data)
    prior_task_kind = data["verification"].get("task_kind")
    if args.verification_json:
        raw = args.verification_json
        if not raw.lstrip().startswith("{"):
            raw = Path(raw).expanduser().read_text()
        verification = json.loads(raw)
        if not isinstance(verification, dict):
            raise EvidenceError("--verification-json must contain the verification object")
        data["verification"] = verification
    verification = data.get("verification")
    if not isinstance(verification, dict):
        raise EvidenceError("verification must be an object")
    from verification_evidence import review_subject_kind, review_input_receipt, require_review_completion
    prior_assignment = next((row for row in data["subagents"] if row.get("lane_id") == args.lane_id), {})
    pin_operation = getattr(args, "review_input_operation_id", None) or prior_assignment.get("review_input_operation_id")
    asserted_subject = getattr(args, "review_subject", None)
    subject = prior_assignment.get("review_subject")
    if pin_operation:
        require(args.role in {"qa-verifier", "reviewer", "reviewer.qa"} and args.execution_mode == "subagent", "review input requires independent QA/reviewer")
        receipt = snapshot.operation_receipt(pin_operation)
        require(receipt is not None, "native review input receipt missing")
        pinned = receipt["result"].get("review_subject")
        require(subject is None or subject == pinned, "assignment review subject changed")
        require(not prior_assignment or prior_assignment.get("review_input_operation_id") == pin_operation, "cannot add review subject after binding")
        subject = pinned
        review_input_receipt(snapshot, {"lane_id": args.lane_id, "review_subject": subject, "review_input_operation_id": pin_operation}, current=not prior_assignment)
    elif asserted_subject:
        require(asserted_subject == "product-result", "requirements review requires prepare operation")
        subject = {"kind": "product-result"}
    elif (snapshot.durable and args.lane_id and args.execution_mode == "subagent"
          and args.role in {"qa-verifier", "reviewer", "reviewer.qa"}
          and first_binding_allowed(snapshot, args.lane_id)):
        subject = {"kind": "product-result"}
    preliminary = review_subject_kind({"review_subject": subject} if subject is not None else prior_assignment) == "requirements-plan"
    require(asserted_subject is None or asserted_subject == (subject or {}).get("kind"), "review subject assertion differs from binding")
    from verification_evidence import result_contract
    contract = result_contract(snapshot)
    initialized = snapshot.operation_receipt("initialize")
    original_contract = initialized["result"].get("result_contract_version", 1) if initialized else 1
    historical_change = prior_task_kind == "change" and (original_contract < 2 or contract.get("historical_identity") is not None)
    if args.verification_json and verification.get("task_kind") == "change" and args.status not in {"blocked", "fail"} and not historical_change:
        require(registered_workspace(snapshot) is not None, "prepare workspace before initial change verification")
    worker_start_preflight(snapshot, lane_map, assigned_lane, verification, args)
    for field in ("initial_snapshot", "task_scope"):
        ref = verification.get(field)
        if isinstance(ref, dict) and "sha256" not in ref:
            ref["sha256"] = sha256(reference_bytes(run_dir, ref, snapshot=snapshot))
    delivery_scope(snapshot, verification, summary=data)
    if verification.get("task_kind") == "change":
        workspace = registered_workspace(snapshot)
        if workspace is not None and "seal" in workspace:
            verification["result_files"] = workspace["seal"]["changed_paths"]
        completing = not preliminary and args.role in {"qa-verifier", "reviewer", "reviewer.qa"} and args.status in {"pass", "pass-with-risks"} and bool(args.completion_turn_id or args.resolve_session and args.stage == "handoff")
        if completing and workspace is None and snapshot.operation_receipt("result-contract-v2") is not None:
            verification["result_hash"] = historical_result_hash(run_dir, verification, snapshot=snapshot)
        else:
            verification["result_hash"] = result_hash(run_dir, verification, snapshot=snapshot, require_ready=completing)
        selected = [r for r in data["subagents"] if r.get("lane_id") in (verification.get("qa"), verification.get("reviewer"))]
        if any(r.get("reviewed_result_hash") != verification["result_hash"] for r in selected):
            verification["qa"] = verification["reviewer"] = None

    extra = {}
    if args.execution_mode == "subagent" and args.lane_id:
        existing = next((r for r in data["subagents"] if r.get("lane_id") == args.lane_id), None)
        record = dict(existing or {})
        if subject is not None:
            record["review_subject"] = subject
            extra["review_subject"] = subject
        if pin_operation:
            record["review_input_operation_id"] = pin_operation
            extra["review_input_operation_id"] = pin_operation
        thread_id = args.codex_thread_id or record.get("codex_thread_id")
        role = "reviewer" if args.role == "reviewer.qa" else args.role
        if args.resolve_session:
            extra.update(source.resolve_session(args.agent_path, verification.get("root_thread_id"), role,
                                                thread_id=thread_id))
            thread_id = extra["codex_thread_id"]
            args.codex_thread_id = thread_id
            record.update({k: extra[k] for k in ("agent_path", "observed_spawn_at", "session_meta_event", "task_started_event")})
        if not thread_id:
            raise EvidenceError("subagent assignment requires --codex-thread-id")
        if existing and (existing.get("codex_thread_id") != thread_id or existing.get("role") != args.role):
            raise EvidenceError("assignment identity changed; use a new lane_id")
        record.update(lane_id=args.lane_id, role=args.role, codex_thread_id=thread_id,
                      trace=f"agents/{safe_path_segment(args.role)}/trace.jsonl")
        if snapshot.durable:
            obligation_id = getattr(args, "obligation_id", None) or args.lane_id
            record.setdefault("obligation", {"id": obligation_id, "required": True, "state": "current"})
            require(not getattr(args, "obligation_id", None) or record["obligation"]["id"] == obligation_id,
                    "obligation identity changed; use a new lane")
            if existing and existing.get("status") in {"pass", "pass-with-risks", "fail", "blocked"}:
                require(existing["status"] == args.status, "terminal outcome is immutable; use a new lane")
            record["status"] = args.status
            if args.stage == "handoff" and args.status in {"pass", "pass-with-risks"}:
                require(bool(artifact_paths), "accepted handoff requires artifact")
                require(bool(snapshot.read_bytes(artifact_paths[0]).strip()), "accepted handoff artifact missing or empty")
        if not preliminary and args.role == "qa-verifier" and args.stage == "spawned":
            verification["qa"] = verification["reviewer"] = None
        elif not preliminary and args.role in {"reviewer", "reviewer.qa"} and args.stage == "spawned":
            verification["reviewer"] = None
        if args.resolve_session and args.stage in {"handoff", "blocked", "fail"} and not args.completion_turn_id:
            args.completion_turn_id = resolve_completion(source, thread_id, verification.get("root_thread_id"),
                                                         role, None if preliminary else verification.get("result_hash"), artifact_paths,
                                                         review_subject=subject, review_input_operation_id=pin_operation)
        if args.completion_turn_id and role not in {"qa-verifier", "reviewer"}:
            require(args.status in {"pass", "pass-with-risks", "blocked", "fail"} and
                    args.stage == (args.status if args.status in {"blocked", "fail"} else "handoff"),
                    "completion stage must match handoff or negative status")
            require(bool(artifact_paths), "worker completion requires handoff artifact")
            completion = completed_turn(source, thread_id, args.completion_turn_id,
                                        verification.get("root_thread_id"), role)
            handoff = artifact_paths[0]
            require(bool(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot).strip()),
                    "worker handoff artifact missing or empty")
            extra.update(completion_turn_id=args.completion_turn_id, handoff=handoff,
                         handoff_sha256=sha256(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot)),
                         source_completion_sha256=sha256(completion["answer"].encode()))
            extra.update({k: completion[k] for k in ("session_meta_event", "task_complete_event")})
            extra["completion_task_started_event"] = completion["task_started_event"]
            if existing and existing.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}:
                require(existing.get("handoff") == handoff, "source completion handoff changed")
                terminal = [e for e in snapshot.timeline() if e.get("lane_id") == args.lane_id
                            and e.get("stage") in {"handoff", "blocked", "fail"}]
                require(len(terminal) == 1 and set(artifact_paths) <= set(terminal[0].get("artifacts", [])),
                        "source completion cannot add or replace evidence")
                artifact_paths = terminal[0]["artifacts"]
            record.update(extra)
            if existing and existing.get("evidence") and existing.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}:
                from verification_evidence import historical_evidence_bytes
                record["evidence"] = existing.get("evidence", [])
                require([ref["path"] for ref in record["evidence"]] == artifact_paths[1:], "source completion evidence paths changed")
                for ref in record["evidence"]:
                    historical_evidence_bytes(snapshot, existing, ref)
            else:
                record["evidence"] = [{"path": p, "sha256": sha256(reference_bytes(run_dir, {"path": p}, snapshot=snapshot))}
                                      for p in artifact_paths[1:]]
            if existing and record == existing:
                extra["unchanged"] = True
                return data, extra
        elif args.completion_turn_id:
            negative = args.status in {"blocked", "fail"}
            require(args.stage == (args.status if negative else "handoff"),
                    "completion stage must match handoff or negative status")
            role = "reviewer" if args.role == "reviewer.qa" else args.role
            completion = completed_turn(source, thread_id, args.completion_turn_id, verification.get("root_thread_id"), role)
            answer = completion["answer"]
            if negative:
                require(answer.get("verdict") == args.status, "negative source verdict/status mismatch")
                require(isinstance(answer.get("reason"), str) and answer["reason"].strip(), "negative source reason required")
                require(isinstance(answer.get("unverified"), list) and bool(answer["unverified"]) and
                        all(isinstance(item, str) and item.strip() for item in answer["unverified"]),
                        "negative source unverified required")
                extra.update(reason=answer["reason"], unverified=answer["unverified"])
            elif not isinstance(answer.get("verdict"), str) or answer["verdict"] not in {"passed", "pass-with-risks"} or args.status not in {"pass", "pass-with-risks"}:
                raise EvidenceError("successful handoff requires accepted source verdict and status")
            handoff = answer.get("handoff")
            if handoff not in artifact_paths:
                raise EvidenceError("source handoff must be included in --artifact; pass its exact path relative to the run directory")
            source_hash = answer.get("reviewed_result_hash")
            if preliminary:
                require_review_completion(snapshot, record, completion, source, current=True)
                extra.update(review_subject=subject, review_input_operation_id=pin_operation)
                record["readiness_gates"] = answer.get("readiness_gates", [])
                validate_preliminary_gates(snapshot, record["readiness_gates"], record=record, source=source, completion=completion, execution_required=True)
            else:
                require(review_subject_kind(answer) == "product-result", "preliminary source cannot accept product")
                require(answer.get("review_subject") == subject, "source product tag differs from assignment")
            require(preliminary and source_hash is None or negative and source_hash is None and verification.get("result_hash") is None or
                    isinstance(source_hash, str) and SHA256.fullmatch(source_hash),
                    "source reviewed_result_hash must be SHA-256")
            if not preliminary and source_hash != verification.get("result_hash"):
                raise EvidenceError("source reviewed_result_hash does not match current result; recompute with --verification-json and obtain acceptance of that current hash")
            if sha256(reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot)) != answer.get("handoff_sha256"):
                raise EvidenceError("source handoff_sha256 mismatch")
            extra.update({k: answer.get(k) for k in ("reviewed_result_hash", "handoff", "handoff_sha256")})
            extra["completion_turn_id"] = args.completion_turn_id
            extra.update({k: completion[k] for k in ("session_meta_event", "task_started_event", "task_complete_event")})
            record.update(extra)
            record["evidence"] = [{"path": p, "sha256": sha256(reference_bytes(run_dir, {"path": p}, snapshot=snapshot))}
                                  for p in artifact_paths if p != handoff]
            if not record["evidence"]:
                raise EvidenceError("QA/reviewer handoff requires at least one --artifact evidence reference")
            handoff_text = reference_bytes(run_dir, {"path": handoff}, snapshot=snapshot).decode()
            for ref in record["evidence"]:
                require(ref["path"] in handoff_text and ref["sha256"] in handoff_text,
                        "evidence reference and sha256 must appear in source-bound handoff")
            require(thread_id not in [verification.get("root_thread_id"), *verification.get("author_thread_ids", [])],
                    "QA, reviewer, root and authors must be distinct")
            if role == "reviewer" and not negative and not preliminary:
                qa = next((r for r in data["subagents"] if r.get("lane_id") == verification.get("qa")), {})
                require_own_reviewer_handoff(run_dir, handoff, qa.get("handoff"), snapshot=snapshot)
                if answer.get("qa_handoff_sha256") != qa.get("handoff_sha256") or not qa.get("handoff_sha256"):
                    raise EvidenceError("source qa_handoff_sha256 mismatch")
                qa_completion = accepted_record(run_dir, qa, verification, "qa-verifier", source, snapshot=snapshot)
                if not completion_follows(completion, qa_completion):
                    raise EvidenceError("reviewer acceptance must follow QA completion")
                record["qa_handoff_sha256"] = qa["handoff_sha256"]
            fields = ("role", "lane_id", "codex_thread_id", "completion_turn_id", "reviewed_result_hash", "handoff", "handoff_sha256", "review_subject", "review_input_operation_id", "readiness_gates")
            same = existing and all(existing.get(k) == record.get(k) for k in fields)
            refs_key = lambda refs: sorted(encode_json(r) for r in refs)
            same = same and refs_key(existing.get("evidence", [])) == refs_key(record["evidence"])
            selected_key = "reviewer" if role == "reviewer" else "qa"
            if same and (preliminary or negative or verification.get(selected_key) == args.lane_id):
                errors = validate_verification(run_dir, data, lane_map, "ship" if not negative and verification.get("reviewer") else "pending", source, snapshot=snapshot)
                if errors:
                    raise EvidenceError("; ".join(errors))
                extra["unchanged"] = True
                return data, extra
            if not preliminary and role == "qa-verifier":
                verification["reviewer"] = None
            if negative and not preliminary:
                if verification.get(selected_key) == args.lane_id:
                    verification[selected_key] = None
                verification["blocker"] = answer["reason"]
                verification["unverified"] = answer["unverified"]
            elif not preliminary:
                verification["reviewer" if role == "reviewer" else "qa"] = args.lane_id
        elif artifact_paths and (args.stage == "handoff" or
                                role not in {"qa-verifier", "reviewer"} and args.stage in {"blocked", "fail"}):
            record["handoff"] = artifact_paths[0]
        if existing:
            data["subagents"][data["subagents"].index(existing)] = record
        else:
            data["subagents"].append(record)
        data["subagents_used"] = True
        if not preliminary and args.completion_turn_id and role in {"qa-verifier", "reviewer"} and args.status in {"pass", "pass-with-risks"} and verification.get("task_kind") != "analysis":
            require_workspace_or_historical(snapshot, data)
    elif args.execution_mode == "role-lane" and args.lane_id and snapshot.durable:
        existing = next((r for r in data["role_lanes"] if r.get("lane_id") == args.lane_id), None)
        record = dict(existing or {})
        require(existing is None or existing.get("role") == args.role, "assignment identity changed; use a new lane_id")
        obligation_id = getattr(args, "obligation_id", None) or args.lane_id
        record.setdefault("obligation", {"id": obligation_id, "required": True, "state": "current"})
        require(not getattr(args, "obligation_id", None) or record["obligation"]["id"] == obligation_id,
                "obligation identity changed; use a new lane")
        record.update(lane_id=args.lane_id, role=args.role, status=args.status,
                      trace=f"agents/{safe_path_segment(args.role)}/trace.jsonl")
        if args.codex_thread_id:
            record["codex_thread_id"] = args.codex_thread_id
        record.setdefault("reason", args.summary)
        if args.stage == "handoff":
            require(bool(artifact_paths), "accepted handoff requires artifact")
            require(bool(snapshot.read_bytes(artifact_paths[0]).strip()), "handoff artifact missing or empty")
            record["handoff"] = artifact_paths[0]
        if existing:
            data["role_lanes"][data["role_lanes"].index(existing)] = record
        else:
            data["role_lanes"].append(record)
        data["role_lanes_used"] = True
    elif args.completion_turn_id:
        raise EvidenceError("--completion-turn-id requires subagent and --lane-id")
    if args.lane_id and args.execution_mode in {"subagent", "role-lane"} and existing is None:
        record.update({field: getattr(args, field) for field in ("wave", "critical") if getattr(args, field) is not None})
        if assigned_lane and assigned_lane.get("handoff"):
            record.setdefault("handoff", assigned_lane["handoff"])
    errors = validate_verification(run_dir, data, lane_map, "pending", source, snapshot=snapshot)
    if errors:
        raise EvidenceError("; ".join(errors))
    return data, extra


def require_subagent_identity(snapshot, args, source):
    from task_workspace import registered_workspace
    if args.execution_mode != 'subagent':
        first_root_binding = args.lane_id and first_binding_allowed(snapshot, args.lane_id)
        if args.codex_thread_id or first_root_binding:
            from verification_evidence import own_metadata, child_spawn
            summary = json.loads(snapshot.documents.get('delegation-summary.json', b'{}'))
            root = summary.get('verification', {}).get('root_thread_id')
            thread = args.codex_thread_id or root
            require(bool(thread), 'role-lane first binding requires registered root identity')
            require(thread == root, 'role-lane requires root execution metadata')
            if first_root_binding:
                _, metadata = own_metadata(source.read(thread), thread)
                require(not child_spawn(metadata) and not metadata.get('parent_thread_id'),
                        'role-lane requires root execution metadata')
                args.codex_thread_id = thread
        return
    from verification_evidence import session_metadata
    data = json.loads(snapshot.read_text('delegation-summary.json')) if snapshot.exists('delegation-summary.json') else {}
    verification = data.get('verification', {})
    root = verification.get('root_thread_id')
    thread = args.codex_thread_id
    if args.resolve_session:
        thread = source.resolve_session(args.agent_path, root, args.role, thread_id=thread)['codex_thread_id']
    if not thread and args.lane_id:
        candidates = [r for r in data.get('subagents', []) if r.get('lane_id') == args.lane_id and r.get('role') == args.role]
        require(len(candidates) == 1, 'subagent lane identity missing or ambiguous; root metadata requires explicit --execution-mode role-lane')
        thread = candidates[0].get('codex_thread_id')
    require(thread and thread != root, 'subagent identity required; root metadata requires explicit --execution-mode role-lane')
    session_metadata(source.read(thread), thread, root, 'reviewer' if args.role == 'reviewer.qa' else args.role, source=source)
    if args.stage != 'spawned':
        trace = f'agents/{safe_path_segment(args.role)}/trace.jsonl'
        events = [json.loads(line) for line in snapshot.read_text(trace).splitlines()] if snapshot.exists(trace) else []
        require(any(e.get('stage') == 'spawned' and e.get('codex_thread_id') == thread
                    and (not args.lane_id or e.get('lane_id') == args.lane_id) for e in events),
                'subagent event requires its own registered spawn')
    args.codex_thread_id = thread


def record_event(run_dir, args, artifact_paths, source, snapshot):
    from agent_config import AgentConfigError
    from verification_evidence import EvidenceError, evidence_path, reference_bytes, result_hash, sha256, delivery_scope
    from task_workspace import registered_workspace
    from journal_structure import validate_request
    event_request = {"summary": args.summary, "status": args.status, "stage": args.stage, "role": args.role}
    if args.lane_id:
        event_request["lane_id"] = args.lane_id
    validate_request("event", event_request)
    validate_assignment_event(vars(args))
    snapshot, readiness_documents = worker_readiness_snapshot(snapshot, args, source)
    require_subagent_identity(snapshot, args, source)
    role_segment = safe_path_segment(args.role)
    stable_agent_name = args.stable_agent_name or args.role
    stable_agent_slug = args.stable_agent_slug or role_segment
    timestamp = now_iso()

    timeline_path = run_dir / "timeline.jsonl"
    agent_dir = run_dir / "agents" / role_segment
    agent_trace_path = agent_dir / "trace.jsonl"
    agent_artifact_dir = run_dir / "artifacts" / "agents" / role_segment
    artifacts_path = run_dir / "artifacts.json"
    require(not snapshot.exists(agent_trace_path) or snapshot.is_file(agent_trace_path),
            f"write target must be a file: {agent_trace_path}")

    try:
        for path in artifact_paths:
            evidence_path(run_dir, path, snapshot=snapshot)
        summary_data, completion_fields = prepare_summary(run_dir, args, artifact_paths, source, snapshot=snapshot)
        if completion_fields.pop("unchanged", False):
            return {}, {"unchanged": True}
        previous = next((r for r in json.loads(snapshot.read_text("delegation-summary.json"))["subagents"]
                         if r.get("lane_id") == args.lane_id), None) if (
                         args.completion_turn_id and args.lane_id and snapshot.exists("delegation-summary.json")) else None
        if (args.completion_turn_id and previous and previous.get("role") not in {"qa-verifier", "reviewer", "reviewer.qa"}
                and previous.get("status") in {"pass", "pass-with-risks", "blocked", "fail"}):
            return {"delegation-summary.json": encode_json(summary_data, pretty=True) + "\n"}, {
                "indexed_artifacts": 0, "completion_fields": completion_fields,
                "result_hash": summary_data["verification"].get("result_hash")}
        load_artifact_index(artifacts_path, snapshot=snapshot)
        if args.stage == "behavior-input-prepared":
            if len(artifact_paths) != 1:
                raise EvidenceError("behavior-input-prepared requires exactly one --artifact input")
            completion_fields["input_sha256"] = sha256(reference_bytes(run_dir, {"path": artifact_paths[0]}, snapshot=snapshot))
    except (EvidenceError, AgentConfigError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    event = {
        "timestamp": timestamp,
        "stage": args.stage,
        "role": args.role,
        "stable_agent_name": stable_agent_name,
        "stable_agent_slug": stable_agent_slug,
        "status": args.status,
        "summary": args.summary,
        "artifacts": artifact_paths,
        "next_step": args.next_step,
        "execution_mode": args.execution_mode,
        "agent_trace": agent_trace_path.relative_to(run_dir).as_posix(),
        "agent_artifact_dir": agent_artifact_dir.relative_to(run_dir).as_posix(),
        **completion_fields,
    }
    if args.codex_thread_id:
        event["codex_thread_id"] = args.codex_thread_id
    if getattr(args, "commit_hash", None):
        event["commit_hash"] = args.commit_hash
    if args.runtime_nickname:
        event["runtime_nickname"] = args.runtime_nickname
    if args.lane_id:
        event["lane_id"] = args.lane_id
    if args.wave is not None:
        event["wave"] = args.wave
    if args.critical is not None:
        event["critical"] = args.critical
    if summary_data is not None:
        scope = delivery_scope(snapshot, summary_data["verification"], summary=summary_data)
        if scope is not None:
            event["delivery_scope_binding"] = {"delivery_scope": scope, "task_scope": summary_data["verification"].get("task_scope")}

    try:
        validate_append(timeline_path, event, snapshot=snapshot)
    except (EvidenceError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    indexed_count, artifact_data = upsert_artifacts(
        artifacts_path,
        artifact_paths,
        role=args.role,
        execution_mode=args.execution_mode,
        stable_agent_name=stable_agent_name,
        stable_agent_slug=stable_agent_slug,
        timestamp=timestamp,
        lane_id=args.lane_id,
        wave=args.wave,
        critical=args.critical,
        snapshot=snapshot,
    )

    encoded_event = encode_json_line(event) + "\n"
    contents = {timeline_path: encoded_event, agent_trace_path: encoded_event}
    if artifact_data is not None:
        contents[artifacts_path] = encode_json(artifact_data, pretty=True) + "\n"
    if summary_data is not None:
        contents[run_dir / "delegation-summary.json"] = encode_json(summary_data, pretty=True) + "\n"
    lane_map, lane = assignment_lane(snapshot, args)
    if lane is not None:
        lane["execution_mode"] = args.execution_mode
        lane["status"] = args.status
        if lane.get("handoff"):
            from journal_io import update_handoff_state
            from journal_structure import HANDOFF_STATE_BY_CALLER_STATUS
            state = HANDOFF_STATE_BY_CALLER_STATUS[args.status]
            lane["handoff_state"] = update_handoff_state(lane=lane, lane_id=args.lane_id, status=state, mode=None,
                from_lane=None, to_lane=None, task=None, handoff=None, batch_id=None, batch_items=[])
        if args.codex_thread_id:
            lane["codex_thread_id"] = args.codex_thread_id
        for field in ("review_subject", "review_input_operation_id"):
            if field in completion_fields:
                lane[field] = completion_fields[field]
        if args.stage == "handoff" and artifact_paths:
            require(not lane.get("handoff") or lane["handoff"] == artifact_paths[0],
                    f"assignment {args.lane_id}.handoff must match lane handoff")
            lane["handoff"] = artifact_paths[0]
        contents[run_dir / "lane-map.json"] = encode_json(lane_map, pretty=True) + "\n"
        contents[run_dir / "checks/coverage-matrix.md"] = render_coverage_matrix(lane_map)
    documents = dict(readiness_documents)
    for path, text in contents.items():
        key = snapshot.key(path)
        if path in (timeline_path, agent_trace_path) and snapshot.exists(path):
            text = snapshot.read_text(path) + text
        documents[key] = text
    documents[snapshot.key(agent_artifact_dir)] = None
    result = {"indexed_artifacts": indexed_count, "completion_fields": completion_fields}
    if event.get("delivery_scope_binding") is not None:
        result["delivery_scope_binding_sha256"] = sha256(json.dumps(event["delivery_scope_binding"], ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode())
        result["timeline_index"] = len(snapshot.timeline()) + 1
    if summary_data is not None:
        result["result_hash"] = summary_data["verification"].get("result_hash")
    return documents, result


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


def render_coverage_matrix(lane_map):
    """Human-readable projection of lane-map.json; regenerated on every lane mutation."""
    from journal_structure import literal_label

    def cell(value):
        return literal_label(str(value)).replace("|", "\\|")

    lines = ["# Coverage Matrix", "",
             "Generated from `lane-map.json` by the journal writer; do not edit manually.", "",
             "| Lane | Acceptance Area | Evidence | Status | Notes |",
             "| --- | --- | --- | --- | --- |"]
    for lane in lane_map.get("lanes", []):
        evidence = [ref.get("path") if isinstance(ref, dict) else str(ref) for ref in lane.get("evidence", [])]
        handoff = lane.get("handoff")
        paths = ([handoff] if isinstance(handoff, str) else []) + evidence
        area = " ".join(str(part) for part in (lane.get("type"), lane.get("role")) if part)
        lines.append("| " + " | ".join((cell(lane.get("id", "")), cell(area or "-"),
                                        cell(", ".join(paths) or "-"), cell(lane.get("status", "planned")),
                                        cell(lane.get("purpose") or "-"))) + " |")
    return "\n".join(lines) + "\n"


def render_service_sections(snapshot):
    from journal_structure import literal_content, literal_label
    sections = (("risk-mitigations.json", "Risk Mitigations", "risks"), ("risk-resolutions.json", "Risk Resolutions", "resolutions"),
                ("claim-evidence.json", "Claim Evidence", "claims"), ("acceptance-traceability.json", "Acceptance Traceability", "acceptance"),
                ("verification-readiness.json", "Verification Readiness", "attempts"), ("continuation-summary.json", "Continuation Summary", "resolved_blockers"),
                ("harness-evaluation.json", "Harness Evaluation", "findings"))
    text = ""
    for path, heading, key in sections:
        if snapshot.exists(path):
            data = json.loads(snapshot.read_text(path))
            rows = data.get(key, []) + (data.get("proposals", []) if key == "findings" else [])
            text += "\n## " + heading + "\n\n" + "\n".join("- " + literal_label(row.get("id") or row.get("risk_id")) + ": " + literal_label(row.get("status", data.get("status", "recorded"))) for row in rows) + "\n"
            if path == "verification-readiness.json":
                text += "Resume phrase: Готово\n\n" + "\n\n".join(literal_content(blocker) for attempt in rows for blocker in attempt.get("blockers", [])) + "\n"
    return text


def render_final(text, summary, paths, *, declared, scope=None):
    from journal_structure import literal_label

    def escaped(value):
        return literal_label(str(value))

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
    verification = summary.get("verification", {})
    if re.search(r"^Verdict:\s*(blocked|fail)\s*$", text, re.MULTILINE) and verification.get("blocker"):
        unverified = "; ".join(escaped(value) for value in verification.get("unverified", []))
        blocks["halt"] = "Reason: " + escaped(verification["blocker"]) + "\nUnverified: " + (unverified or "Machine unverified declaration unavailable.") + "\n"
    if scope is not None:
        source = summary["verification"]["task_scope"]
        blocks["delivery-scope"] = "## Local Delivery Scope\n\nПринят локальный результат: local-deliverable.\nИсходная внешняя цель остаётся незавершённой. Исходное задание: " + escaped(source["path"]) + " sha256=" + escaped(source["sha256"]) + ".\nНепроверенный external follow-up:\n" + "\n".join(
            "- " + name + ": " + escaped(json.dumps(values, ensure_ascii=False, sort_keys=True))
            for name, values in scope["external_followup"].items()) + "\n"
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
