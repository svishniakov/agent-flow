#!/usr/bin/env python3
"""Create isolated synthetic git workspaces for model evaluation."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


ROOT_MARKER = ".agent-flow-eval-root.json"
METADATA_DIR = ".agent-flow-eval-metadata"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
GIT_EXECUTABLE = shutil.which("git")
FILTER_CONFIG_PATTERN = re.compile(
    r"^(filter\..+)\.(clean|smudge|process|required)$",
    re.IGNORECASE,
)
GIT_CONFIG_OVERRIDES = (
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.untrackedCache", "false"),
    ("core.attributesFile", os.devnull),
    ("commit.gpgSign", "false"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
)
GIT_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMPDIR",
    "TEMP",
    "TMP",
)
GIT_EXTRA_ENV_KEYS = frozenset({"GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"})


class WorkspaceError(RuntimeError):
    """Raised when a synthetic workspace cannot be created or removed safely."""


@dataclass(frozen=True)
class RepositoryState:
    head: str
    status: str
    worktrees: str
    refs: str


@dataclass(frozen=True)
class SyntheticWorkspace:
    path: Path
    temp_root: Path
    suite_id: str
    cell_id: str
    lane_id: str
    repository_id: str
    source_revision: str
    synthetic_commit: str
    metadata_path: Path


def _id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise WorkspaceError(f"{label} must be a lowercase id")
    return value


def _run(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkspaceError(f"command failed ({' '.join(command[:3])}): {detail}")
    return result


def _minimal_git_environment(
    extra: dict[str, str] | None = None,
    filter_bases: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build a deterministic Git environment without host config or Git state overrides."""
    env = {key: os.environ[key] for key in GIT_ENV_ALLOWLIST if key in os.environ}
    env.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PAGER": "",
        }
    )
    config_entries = list(GIT_CONFIG_OVERRIDES)
    for base in filter_bases:
        config_entries.extend(
            [
                (f"{base}.clean", ""),
                (f"{base}.smudge", ""),
                (f"{base}.process", ""),
                (f"{base}.required", "false"),
            ]
        )
    env["GIT_CONFIG_COUNT"] = str(len(config_entries))
    for index, (key, value) in enumerate(config_entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    if extra:
        unexpected = set(extra).difference(GIT_EXTRA_ENV_KEYS)
        if unexpected:
            raise WorkspaceError("unsupported Git environment override")
        env.update(extra)
    return env


def _git_command(
    args: list[str],
    repo: Path | None = None,
) -> list[str]:
    if GIT_EXECUTABLE is None:
        raise WorkspaceError("git executable was not found")
    command = [GIT_EXECUTABLE]
    if repo is not None:
        command.extend(["-C", str(repo)])
    command.extend(args)
    return command


def _configured_filter_bases(repo: Path) -> tuple[str, ...]:
    """Find repository-local filter drivers without loading host config."""
    result = subprocess.run(
        _git_command(
            [
                "config",
                "--null",
                "--includes",
                "--name-only",
                "--get-regexp",
                r"^filter\..*\.(clean|smudge|process|required)$",
            ],
            repo=repo,
        ),
        env=_minimal_git_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 1:
        return ()
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise WorkspaceError(f"cannot inspect repository filter config: {detail}")
    bases: set[str] = set()
    for key in result.stdout.split("\0"):
        if not key:
            continue
        match = FILTER_CONFIG_PATTERN.fullmatch(key)
        if match is None:
            raise WorkspaceError("repository returned an invalid filter config key")
        bases.add(match.group(1))
    return tuple(sorted(bases))


def _git(
    repo: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
) -> str:
    filter_bases = _configured_filter_bases(repo)
    return _run(
        _git_command(args, repo=repo),
        env=_minimal_git_environment(extra_env, filter_bases=filter_bases),
    ).stdout.strip()


def snapshot_repository_state(repo: Path) -> RepositoryState:
    """Capture source state that the evaluation runner must preserve exactly."""
    root = Path(_git(repo, ["rev-parse", "--show-toplevel"])).resolve()
    if root != repo.resolve():
        raise WorkspaceError("source repository path must point to its git root")
    return RepositoryState(
        head=_git(root, ["rev-parse", "HEAD"]),
        status=_git(root, ["status", "--porcelain=v1", "--untracked-files=all"]),
        worktrees=_git(root, ["worktree", "list", "--porcelain"]),
        refs=_git(root, ["for-each-ref", "--format=%(refname) %(objectname)"]),
    )


def assert_repository_state(repo: Path, expected: RepositoryState) -> None:
    """Fail if the source repository changed while a cell was running."""
    actual = snapshot_repository_state(repo)
    if actual != expected:
        raise WorkspaceError("source repository state changed during evaluation")


def initialize_temp_root(temp_root: Path, suite_id: str) -> Path:
    """Create or verify a runner-owned temporary root."""
    suite = _id(suite_id, "suite_id")
    root = temp_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ROOT_MARKER
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError("temporary root ownership marker is invalid") from exc
        if data != {"schema_version": 1, "suite_id": suite}:
            raise WorkspaceError("temporary root belongs to a different suite")
    else:
        existing = [path for path in root.iterdir()]
        if existing:
            raise WorkspaceError("temporary root is not empty and has no ownership marker")
        marker.write_text(
            json.dumps({"schema_version": 1, "suite_id": suite}, indent=2) + "\n",
            encoding="utf-8",
        )
    (root / METADATA_DIR).mkdir(exist_ok=True)
    (root / "cells").mkdir(exist_ok=True)
    return root


def _verify_temp_root(temp_root: Path, suite_id: str) -> Path:
    root = temp_root.expanduser().resolve()
    marker = root / ROOT_MARKER
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError("temporary root ownership marker is missing or invalid") from exc
    if data != {"schema_version": 1, "suite_id": _id(suite_id, "suite_id")}:
        raise WorkspaceError("temporary root ownership marker does not match suite")
    return root


def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceError(f"{label} escapes the runner temporary root") from exc
    return resolved


def _safe_member_path(root: Path, name: str) -> Path:
    if not name or "\\" in name:
        raise WorkspaceError("archive contains an invalid path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkspaceError(f"archive path escapes workspace: {name}")
    destination = _inside(root / Path(*relative.parts), root, "archive path")
    parent = destination.parent
    while parent != root:
        if parent.is_symlink():
            raise WorkspaceError(f"archive path traverses a symlink: {name}")
        parent = parent.parent
    return destination


def _safe_symlink_target(root: Path, destination: Path, target: str) -> None:
    if not target or "\\" in target:
        raise WorkspaceError("archive contains an invalid symlink target")
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        raise WorkspaceError("archive contains an absolute symlink target")
    resolved = (destination.parent / Path(*target_path.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceError("archive symlink target escapes workspace") from exc


def extract_archive_safely(
    archive_path: Path,
    destination: Path,
    max_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> None:
    """Extract a git archive without tar path traversal or special files."""
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:*") as archive:
        members = archive.getmembers()
        total_size = sum(member.size for member in members if member.isfile())
        if total_size > max_bytes:
            raise WorkspaceError("archive exceeds the configured size limit")
        for member in members:
            target = _safe_member_path(root, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                _safe_symlink_target(root, target, member.linkname)
                os.symlink(member.linkname, target)
                continue
            if member.islnk():
                raise WorkspaceError("archive hard links are not allowed")
            if not member.isfile():
                raise WorkspaceError("archive contains a special file")
            source = archive.extractfile(member)
            if source is None:
                raise WorkspaceError(f"cannot read archive member: {member.name}")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def _export_revision(repo: Path, revision: str, archive_path: Path) -> None:
    filter_bases = _configured_filter_bases(repo)
    with archive_path.open("wb") as output:
        result = subprocess.run(
            _git_command(
                ["archive", "--format=tar", revision],
                repo=repo,
            ),
            stdout=output,
            stderr=subprocess.PIPE,
            env=_minimal_git_environment(filter_bases=filter_bases),
            check=False,
        )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceError(f"git archive failed: {detail}")


def _initialize_repository(workspace: Path) -> str:
    with tempfile.TemporaryDirectory(
        prefix=".agent-flow-empty-git-template-",
        dir=workspace.parent,
    ) as template:
        _run(
            _git_command(["init", "--quiet", f"--template={template}"]),
            cwd=workspace,
            env=_minimal_git_environment(),
        )
    _git(workspace, ["config", "user.name", "Agent Flow Eval"])
    _git(workspace, ["config", "user.email", "agent-flow-eval@example.invalid"])
    _git(workspace, ["config", "gc.auto", "0"])
    _git(workspace, ["config", "gc.autoDetach", "false"])
    _git(workspace, ["config", "maintenance.auto", "false"])
    _git(workspace, ["add", "-f", "--all", "--", "."])
    _git(
        workspace,
        ["commit", "-qm", "Synthetic evaluation baseline"],
        extra_env={
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        },
    )
    return _git(workspace, ["rev-parse", "HEAD"])


def _remove_owned_tree(path: Path) -> None:
    """Remove an ownership-checked tree despite harmless concurrent ENOENT races."""

    def ignore_disappeared(_function, _path, error) -> None:
        exception = error[1]
        if isinstance(exception, FileNotFoundError):
            return
        raise exception

    for attempt in range(4):
        try:
            shutil.rmtree(path, onerror=ignore_disappeared)
        except FileNotFoundError:
            return
        except OSError as exc:
            if attempt == 3 or exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise
        if not path.exists():
            return
        time.sleep(0.05 * (attempt + 1))
    raise WorkspaceError("owned workspace could not be removed")


def create_synthetic_workspace(
    source_repo: Path,
    source_revision: str,
    repository_id: str,
    temp_root: Path,
    suite_id: str,
    cell_id: str,
    lane_id: str,
) -> SyntheticWorkspace:
    """Export one revision into an independent one-commit git repository."""
    root = _verify_temp_root(temp_root, suite_id)
    cell = _id(cell_id, "cell_id")
    lane = _id(lane_id, "lane_id")
    repository = _id(repository_id, "repository_id")
    source = source_repo.expanduser().resolve()
    source_root = Path(_git(source, ["rev-parse", "--show-toplevel"])).resolve()
    if source_root != source:
        raise WorkspaceError("source repository path must point to its git root")
    _git(source, ["cat-file", "-e", f"{source_revision}^{{commit}}"])

    workspace = _inside(root / "cells" / cell / lane, root, "workspace")
    if workspace.exists():
        raise WorkspaceError("synthetic workspace already exists")
    metadata_path = _inside(root / METADATA_DIR / cell / f"{lane}.json", root, "metadata path")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path = _inside(root / METADATA_DIR / cell / f"{lane}.tar", root, "archive path")
    workspace.mkdir(parents=True)
    try:
        _export_revision(source, source_revision, archive_path)
        extract_archive_safely(archive_path, workspace)
        synthetic_commit = _initialize_repository(workspace)
        source_tree = _git(source, ["rev-parse", f"{source_revision}^{{tree}}"])
        synthetic_tree = _git(workspace, ["rev-parse", "HEAD^{tree}"])
        if synthetic_tree != source_tree:
            raise WorkspaceError("synthetic baseline does not preserve the source tree exactly")
        metadata = {
            "schema_version": 1,
            "suite_id": suite_id,
            "cell_id": cell,
            "lane_id": lane,
            "repository_id": repository,
            "source_revision": source_revision,
            "synthetic_commit": synthetic_commit,
            "workspace": workspace.relative_to(root).as_posix(),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        metadata_path.unlink(missing_ok=True)
        raise
    finally:
        archive_path.unlink(missing_ok=True)

    return SyntheticWorkspace(
        path=workspace,
        temp_root=root,
        suite_id=suite_id,
        cell_id=cell,
        lane_id=lane,
        repository_id=repository,
        source_revision=source_revision,
        synthetic_commit=synthetic_commit,
        metadata_path=metadata_path,
    )


def apply_gold_patch(workspace: SyntheticWorkspace, patch_path: Path) -> None:
    """Apply a corpus-owned gold patch to a disposable synthetic workspace."""
    patch = patch_path.expanduser().absolute()
    if not patch.is_file() or patch.is_symlink():
        raise WorkspaceError("gold patch must be a regular file")
    filter_bases = _configured_filter_bases(workspace.path)
    _run(
        _git_command(
            ["apply", "--binary", "--whitespace=nowarn", str(patch)],
            repo=workspace.path,
        ),
        env=_minimal_git_environment(filter_bases=filter_bases),
    )


def cleanup_synthetic_workspace(workspace: SyntheticWorkspace) -> None:
    """Remove only a workspace whose root and metadata prove runner ownership."""
    root = _verify_temp_root(workspace.temp_root, workspace.suite_id)
    path = _inside(workspace.path, root, "workspace")
    expected_parent = _inside(root / "cells" / workspace.cell_id, root, "cell path")
    if path.parent != expected_parent or path.name != workspace.lane_id:
        raise WorkspaceError("workspace path does not match cell and lane ownership")
    try:
        metadata = json.loads(workspace.metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError("workspace metadata is missing or invalid") from exc
    expected = {
        "schema_version": 1,
        "suite_id": workspace.suite_id,
        "cell_id": workspace.cell_id,
        "lane_id": workspace.lane_id,
        "repository_id": workspace.repository_id,
        "source_revision": workspace.source_revision,
        "synthetic_commit": workspace.synthetic_commit,
        "workspace": path.relative_to(root).as_posix(),
    }
    if metadata != expected:
        raise WorkspaceError("workspace metadata does not match cleanup request")
    _remove_owned_tree(path)
    workspace.metadata_path.unlink()
    if expected_parent.exists() and not any(expected_parent.iterdir()):
        expected_parent.rmdir()
    metadata_parent = workspace.metadata_path.parent
    if metadata_parent.exists() and not any(metadata_parent.iterdir()):
        metadata_parent.rmdir()


def temporary_eval_root(suite_id: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Create a temporary directory and attach an Agent Flow ownership marker."""
    context = tempfile.TemporaryDirectory(prefix="agent-flow-model-eval-")
    return context, initialize_temp_root(Path(context.name), suite_id)
