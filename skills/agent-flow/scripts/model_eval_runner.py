#!/usr/bin/env python3
"""Run one isolated, score-compatible Agent Flow model-evaluation cell."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from agent_config import ALLOWED_MODELS, ALLOWED_REASONING_EFFORTS
from model_eval_adapter import (
    CODEX_CLI_VERSION,
    CODEX_MODEL_PROVIDER,
    MAX_LANE_ATTEMPTS,
    USAGE_FIELDS,
    AdapterConfig,
    AdapterRun,
    redact_text,
    resume_cli_lane,
    run_cli_lane,
)
from model_eval_evaluator import load_evaluator, run_evaluator, run_setup_commands
from model_eval_manifest import CorpusManifest
from model_eval_process import run_process_group
from model_eval_predictability import score_predictability
from model_eval_score import validate_cell
from model_eval_workspace import (
    RepositoryState,
    SyntheticWorkspace,
    assert_repository_state,
    cleanup_synthetic_workspace,
    create_synthetic_workspace,
    initialize_temp_root,
    snapshot_repository_state,
)


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
FILTER_CONFIG_PATTERN = re.compile(
    r"^(filter\..+)\.(clean|smudge|process|required)$",
    re.IGNORECASE,
)
LANE_STATUSES = {"pass", "fail", "blocked", "decision_request", "infrastructure-error"}
GIT_EXECUTABLE = shutil.which("git")
GIT_COMMAND_TIMEOUT_SECONDS = 30
GIT_CONFIG_OVERRIDES = (
    ("core.excludesFile", os.devnull),
    ("core.attributesFile", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.hooksPath", os.devnull),
    ("core.untrackedCache", "false"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
)
GIT_ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMPDIR",
    "TEMP",
    "TMP",
)


class RunnerError(RuntimeError):
    """Raised when a cell input or trusted runner contract is invalid."""


class IsolationViolation(RunnerError):
    """Raised when model-facing or persisted evidence exposes hidden eval data."""

    def __init__(self, findings: list[dict[str, str]]) -> None:
        super().__init__("hidden evaluation data appeared in a model-facing surface")
        self.findings = findings


@dataclass(frozen=True)
class EvalConfiguration:
    config_id: str
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class CellRun:
    cell: dict[str, Any]
    details: dict[str, Any]


@dataclass(frozen=True)
class IsolationMarker:
    kind: str
    value: str


@dataclass(frozen=True)
class WorkspaceMetadataFingerprint:
    head: str
    refs: str
    worktrees: str
    index_digest: str
    config_digest: str
    hooks_digest: str
    info_digest: str


FilesystemSnapshot = dict[str, tuple[str, int, str]]


LaneRunner = Callable[[AdapterConfig, str], AdapterRun]
ResumeLaneRunner = Callable[[AdapterConfig, str, str], AdapterRun]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def cell_identifier(task_id: str, config_id: str, repeat: int) -> str:
    """Build a stable workspace id without exposing configuration syntax limits."""
    if (
        not task_id
        or not config_id
        or isinstance(repeat, bool)
        or not isinstance(repeat, int)
        or repeat < 1
    ):
        raise RunnerError("task_id, config_id, and repeat must identify a cell")
    raw = f"{task_id}-{config_id}-r{repeat}".lower()
    readable = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_") or "cell"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{readable[:80]}-{digest}"


def _validate_configuration(configuration: EvalConfiguration, repeat: int) -> None:
    if not isinstance(configuration.config_id, str) or not configuration.config_id.strip():
        raise RunnerError("config_id must be non-empty")
    if configuration.model not in ALLOWED_MODELS:
        raise RunnerError(f"unsupported model: {configuration.model}")
    if configuration.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
        raise RunnerError(f"unsupported reasoning effort: {configuration.reasoning_effort}")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise RunnerError("repeat must be a positive integer")


def _task(corpus: CorpusManifest, task_id: str) -> dict[str, Any]:
    for task in corpus.tasks:
        if task["id"] == task_id:
            return task
    raise RunnerError(f"unknown task: {task_id}")


def _lane_owners(task: dict[str, Any]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for lane in task["lanes"]:
        for repository_id in lane["repository_ids"]:
            if repository_id in owners:
                raise RunnerError(f"repository {repository_id} is owned by more than one lane")
            owners[repository_id] = lane["id"]
    expected = set(task["repositories"])
    if set(owners) != expected:
        missing = ", ".join(sorted(expected - owners))
        raise RunnerError(f"task repositories without one lane owner: {missing}")
    return owners


def _strip_frontmatter(value: str) -> str:
    lines = value.splitlines()
    if not lines or lines[0].strip() != "---":
        return value.strip()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :]).strip()
            if not body:
                raise RunnerError("role prompt is empty after YAML frontmatter")
            return body
    raise RunnerError("role prompt has unterminated YAML frontmatter")


def _role_prompt(role: str) -> str:
    if not ID_PATTERN.fullmatch(role):
        raise RunnerError(f"invalid role id: {role}")
    path = Path(__file__).resolve().parents[1] / "agents" / f"{role}.md"
    try:
        return _strip_frontmatter(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(f"role prompt does not exist: {role}") from exc


def _lane_prompt(corpus: CorpusManifest, task: dict[str, Any], lane: dict[str, Any]) -> str:
    task_prompt = (corpus.root / task["prompt"]).read_text(encoding="utf-8").strip()
    if not task_prompt:
        raise RunnerError(f"task prompt is empty: {task['id']}")
    repositories = lane["repository_ids"]
    allowed = {repository_id: task["allowed_paths"][repository_id] for repository_id in repositories}
    forbidden = {
        repository_id: task["forbidden_paths"][repository_id] for repository_id in repositories
    }
    changed_path_format = (
        "repository-relative paths"
        if len(repositories) == 1
        else "<repository-id>/<repository-relative-path> entries"
    )
    scope = {
        "lane_id": lane["id"],
        "role": lane["role"],
        "primary_repository": lane["primary_repository"],
        "owned_repositories": repositories,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "task_facts": lane["task_facts"],
    }
    predictability = task.get("predictability")
    protocol = ""
    if predictability is not None:
        required_ids = [item["id"] for item in predictability["required_behaviors"]]
        forbidden_ids = [item["id"] for item in predictability["forbidden_behaviors"]]
        protocol = (
            "\n\n# Required behavior\n\n"
            + "\n".join(f"- {behavior_id}" for behavior_id in required_ids)
            + "\n\n# Must not\n\n"
            + "\n".join(f"- {behavior_id}" for behavior_id in forbidden_ids)
            + "\n\n# Definition of done\n\n"
            + "Complete every required behavior, respect every prohibition, run relevant checks, "
            + "and report one result for every required behavior id."
            + "\n\n# Decision protocol\n\n"
            + "If the repository and task leave a material product or contract decision unresolved, "
            + "return only decision_request. Do not choose an option, make an assumption, recommend "
            + "a choice, or change files before the architect responds. Otherwise complete the task."
        )
    return (
        "# Role contract\n\n"
        + _role_prompt(lane["role"])
        + "\n\n# Task\n\n"
        + task_prompt
        + "\n\n# Lane scope\n\n"
        + json.dumps(scope, indent=2, sort_keys=True)
        + protocol
        + "\n\nWork only in the owned repositories and declared allowed paths. "
        + "Do not inspect or modify git metadata. Do not use optional skills. "
        + f"Report changed_paths as {changed_path_format}."
    )


def _redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_json(item) for key, item in value.items()}
    return value


def _isolation_markers(
    corpus: CorpusManifest,
    task: dict[str, Any],
    evaluator_path: Path,
) -> list[IsolationMarker]:
    markers: list[IsolationMarker] = []
    for repository_id in task["repositories"]:
        revision = task["revisions"][repository_id]
        if "gold" in revision:
            markers.append(IsolationMarker("gold-sha", revision["gold"]))
        else:
            markers.append(IsolationMarker("gold-patch-path", revision["gold_patch"]))
        markers.append(
            IsolationMarker(
                "source-repository-path",
                str(corpus.repositories[repository_id].resolve()),
            )
        )
    markers.extend(
        [
            IsolationMarker("evaluator-relative-path", task["evaluator"]),
            IsolationMarker("evaluator-absolute-path", str(evaluator_path.resolve())),
        ]
    )
    predictability = task.get("predictability")
    if predictability is not None and predictability["ambiguity"] is not None:
        response_path = predictability["ambiguity"]["architect_response"]
        response = (corpus.root / response_path).read_text(encoding="utf-8").strip()
        markers.extend(
            [
                IsolationMarker("architect-response-path", response_path),
                IsolationMarker("architect-response-content", response),
            ]
        )
    unique: dict[tuple[str, str], IsolationMarker] = {}
    for marker in markers:
        if marker.value:
            unique[(marker.kind, marker.value)] = marker
    return list(unique.values())


def _isolation_findings(
    value: object,
    surface: str,
    markers: list[IsolationMarker],
) -> list[dict[str, str]]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return [
        {"kind": marker.kind, "surface": surface}
        for marker in markers
        if marker.value in text
    ]


def _redact_isolation_markers(value: Any, markers: list[IsolationMarker]) -> Any:
    if isinstance(value, str):
        redacted = value
        for marker in markers:
            replacement = "[REDACTED_" + marker.kind.upper().replace("-", "_") + "]"
            redacted = redacted.replace(marker.value, replacement)
        return redact_text(redacted)
    if isinstance(value, list):
        return [_redact_isolation_markers(item, markers) for item in value]
    if isinstance(value, tuple):
        return [_redact_isolation_markers(item, markers) for item in value]
    if isinstance(value, dict):
        return {key: _redact_isolation_markers(item, markers) for key, item in value.items()}
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_redact_json(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prepare_artifacts(path: Path, temp_root: Path) -> Path:
    artifacts = path.expanduser().resolve()
    temporary = temp_root.expanduser().resolve()
    try:
        artifacts.relative_to(temporary)
    except ValueError:
        pass
    else:
        raise RunnerError("cell artifacts must live outside the temporary workspace root")
    if artifacts.exists():
        if not artifacts.is_dir():
            raise RunnerError("cell artifact path is not a directory")
        if any(artifacts.iterdir()):
            raise RunnerError("cell artifact directory is not empty")
    artifacts.mkdir(parents=True, exist_ok=True)
    return artifacts


def _git_environment(filter_bases: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a Git-only environment that cannot inherit host Git behavior."""
    if GIT_EXECUTABLE is None:
        raise RunnerError("git executable was not found")
    environment = {key: os.environ[key] for key in GIT_ENV_ALLOWLIST if key in os.environ}
    path_entries = [str(Path(GIT_EXECUTABLE).resolve().parent)]
    for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if directory not in path_entries:
            path_entries.append(directory)
    environment.update(
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
            "PATH": os.pathsep.join(path_entries),
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
    environment["GIT_CONFIG_COUNT"] = str(len(config_entries))
    for index, (key, value) in enumerate(config_entries):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def _git_command(workspace: Path, args: list[str]) -> list[str]:
    if GIT_EXECUTABLE is None:
        raise RunnerError("git executable was not found")
    return [GIT_EXECUTABLE, "--no-pager", "-C", str(workspace), *args]


def _configured_filter_bases(workspace: Path) -> tuple[str, ...]:
    """Read local and included filter names without executing repository commands."""
    command = _git_command(
        workspace,
        [
            "config",
            "--null",
            "--includes",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|smudge|process|required)$",
        ],
    )
    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=_git_environment(),
            shell=False,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError("git filter configuration inspection timed out") from exc
    if process.returncode == 1:
        return ()
    if process.returncode:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RunnerError(f"cannot inspect repository filter config: {detail}")
    bases: set[str] = set()
    for raw_key in process.stdout.split(b"\0"):
        if not raw_key:
            continue
        try:
            key = raw_key.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RunnerError("repository returned a non-UTF-8 filter config key") from exc
        match = FILTER_CONFIG_PATTERN.fullmatch(key)
        if match is None:
            raise RunnerError("repository returned an invalid filter config key")
        bases.add(match.group(1))
    return tuple(sorted(bases))


def _git(
    workspace: Path,
    args: list[str],
    accepted: set[int] | None = None,
    *,
    input_data: bytes | None = None,
) -> bytes:
    filter_bases = _configured_filter_bases(workspace)
    try:
        process = subprocess.run(
            _git_command(workspace, args),
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=_git_environment(filter_bases),
            shell=False,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError("git evidence command timed out") from exc
    if process.returncode not in (accepted or {0}):
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise RunnerError(f"git evidence command failed: {detail}")
    return process.stdout


def _decode_paths(value: bytes) -> list[str]:
    paths: list[str] = []
    for raw_path in value.split(b"\0"):
        if not raw_path:
            continue
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RunnerError("workspace contains a non-UTF-8 changed path") from exc
        portable = PurePosixPath(path)
        if portable.is_absolute() or ".." in portable.parts or "." in portable.parts:
            raise RunnerError(f"git returned an unsafe changed path: {path}")
        _validate_patch_value(path, "changed path")
        paths.append(path)
    return paths


def _validate_patch_value(value: str, label: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RunnerError(f"{label} is not valid UTF-8") from exc
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise RunnerError(f"{label} contains a control character")


def _dependency_directories(workspace: Path) -> set[str]:
    roots: set[str] = set()
    pending = [workspace.resolve()]
    while pending:
        directory = pending.pop()
        for entry in os.scandir(directory):
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            path = Path(entry.path)
            if entry.name == ".git":
                continue
            if entry.name in {"node_modules", "vendor"}:
                roots.add(path.relative_to(workspace).as_posix())
                continue
            pending.append(path)
    return roots


def _under_roots(path: str, roots: set[str]) -> bool:
    return any(path == root or path.startswith(root + "/") for root in roots)


def _changed_paths(
    workspace: SyntheticWorkspace,
    setup_dependency_roots: set[str] | None = None,
) -> list[str]:
    tracked = _decode_paths(
        _git(
            workspace.path,
            [
                "diff",
                "--name-only",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "-z",
                workspace.synthetic_commit,
                "--",
            ],
        )
    )
    untracked = _decode_paths(
        _git(workspace.path, ["ls-files", "--others", "--exclude-standard", "-z"])
    )
    excluded = setup_dependency_roots or set()
    return sorted(path for path in set(tracked + untracked) if not _under_roots(path, excluded))


def _validate_symlink_target(workspace: Path, target: Path, value: str) -> None:
    _validate_patch_value(value, "symlink target")
    if "\\" in value or PurePosixPath(value).is_absolute():
        raise RunnerError("symlink target must stay inside the evaluator workspace")
    resolved = (target.parent / value).resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError as exc:
        raise RunnerError("symlink target escapes the evaluator workspace") from exc


def _workspace_patch(workspace: SyntheticWorkspace, changed_paths: list[str]) -> bytes:
    for path in changed_paths:
        _validate_patch_value(path, "changed path")
        target = workspace.path / Path(*PurePosixPath(path).parts)
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            _validate_symlink_target(workspace.path, target, os.readlink(target))
        elif not stat.S_ISREG(metadata.st_mode):
            raise RunnerError(f"cannot capture non-regular changed path: {path}")
    tracked_patch = _git(
        workspace.path,
        [
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            workspace.synthetic_commit,
            "--",
        ],
    )
    tracked = set(
        _decode_paths(
            _git(
                workspace.path,
                [
                    "diff",
                    "--name-only",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    "-z",
                    workspace.synthetic_commit,
                    "--",
                ],
            )
        )
    )
    additions: list[bytes] = []
    for path in changed_paths:
        if path in tracked:
            continue
        target = workspace.path / Path(*PurePosixPath(path).parts)
        additions.append(
            _git(
                workspace.path,
                [
                    "diff",
                    "--binary",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-index",
                    "--",
                    os.devnull,
                    path,
                ],
                accepted={0, 1},
            )
        )
    return tracked_patch + b"".join(additions)


def _path_digest(path: Path) -> str:
    digest = hashlib.sha256()

    def visit(current: Path, relative: str) -> None:
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            digest.update(b"\0missing\0")
            return
        digest.update(f"\0{stat.S_IFMT(metadata.st_mode):o}\0{stat.S_IMODE(metadata.st_mode):o}\0".encode())
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(os.readlink(current).encode("utf-8", errors="surrogateescape"))
            return
        if stat.S_ISREG(metadata.st_mode):
            with current.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return
        if stat.S_ISDIR(metadata.st_mode):
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                child_relative = f"{relative}/{child.name}" if relative else child.name
                visit(child, child_relative)

    visit(path, path.name)
    return digest.hexdigest()


def _workspace_metadata_fingerprint(workspace: SyntheticWorkspace) -> WorkspaceMetadataFingerprint:
    git_directory = workspace.path / ".git"
    if git_directory.is_symlink() or not git_directory.is_dir():
        raise RunnerError("synthetic git directory is missing or unsafe")
    head = _git(workspace.path, ["rev-parse", "HEAD"]).decode("utf-8").strip()
    refs = _git(
        workspace.path,
        ["for-each-ref", "--format=%(refname) %(objectname)"],
    ).decode("utf-8")
    worktrees = _git(workspace.path, ["worktree", "list", "--porcelain"]).decode("utf-8")
    index_digest = hashlib.sha256(
        _git(workspace.path, ["ls-files", "--stage", "-z"])
    ).hexdigest()
    return WorkspaceMetadataFingerprint(
        head=head,
        refs=refs,
        worktrees=worktrees,
        index_digest=index_digest,
        config_digest=_path_digest(git_directory / "config"),
        hooks_digest=_path_digest(git_directory / "hooks"),
        info_digest=_path_digest(git_directory / "info"),
    )


def _metadata_changed(
    before: WorkspaceMetadataFingerprint,
    after: WorkspaceMetadataFingerprint,
) -> bool:
    return before != after


def _workspace_filesystem_snapshot(workspace: SyntheticWorkspace) -> FilesystemSnapshot:
    """Hash every non-dependency file without following workspace symlinks."""
    snapshot: FilesystemSnapshot = {}

    def visit(directory: Path, relative: PurePosixPath) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise RunnerError(f"cannot scan workspace path: {relative.as_posix()}") from exc
        for entry in entries:
            child_relative = relative / entry.name
            if entry.name in {".git", "node_modules", "vendor"} and entry.is_dir(
                follow_symlinks=False
            ):
                continue
            path = Path(entry.path)
            try:
                metadata = path.lstat()
            except FileNotFoundError as exc:
                raise RunnerError(
                    f"workspace changed while it was being fingerprinted: {child_relative}"
                ) from exc
            mode = stat.S_IMODE(metadata.st_mode)
            portable = child_relative.as_posix()
            if stat.S_ISDIR(metadata.st_mode):
                visit(path, child_relative)
            elif stat.S_ISREG(metadata.st_mode):
                snapshot[portable] = ("file", mode, _path_digest(path))
            elif stat.S_ISLNK(metadata.st_mode):
                snapshot[portable] = ("symlink", mode, os.readlink(path))
            else:
                snapshot[portable] = ("special", mode, str(stat.S_IFMT(metadata.st_mode)))

    visit(workspace.path, PurePosixPath())
    return snapshot


def _filesystem_changes(before: FilesystemSnapshot, after: FilesystemSnapshot) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _snapshot_after_patch(
    baseline: FilesystemSnapshot,
    model_snapshot: FilesystemSnapshot,
    changed_paths: list[str],
) -> FilesystemSnapshot:
    expected = dict(baseline)
    for path in changed_paths:
        if path in model_snapshot:
            expected[path] = model_snapshot[path]
        else:
            expected.pop(path, None)
    return expected


def _apply_workspace_patch(workspace: SyntheticWorkspace, patch: bytes) -> None:
    if not patch:
        return
    _git(
        workspace.path,
        ["apply", "--binary", "--whitespace=nowarn"],
        input_data=patch,
    )


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _reported_paths(lane: dict[str, Any], actual_by_repository: dict[str, list[str]]) -> list[str]:
    repositories = lane["repository_ids"]
    if len(repositories) == 1:
        return list(actual_by_repository[repositories[0]])
    return sorted(
        f"{repository_id}/{path}"
        for repository_id in repositories
        for path in actual_by_repository[repository_id]
    )


def _boundary_evidence(
    task: dict[str, Any],
    lane: dict[str, Any],
    lane_result: dict[str, Any],
    actual_by_repository: dict[str, list[str]],
    metadata_changes: dict[str, bool],
    ignored_changes: dict[str, list[str]],
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    for repository_id in lane["repository_ids"]:
        for path in actual_by_repository[repository_id]:
            if not _matches(path, task["allowed_paths"][repository_id]):
                violations.append(
                    {"kind": "outside-allowed-paths", "repository_id": repository_id, "path": path}
                )
            if _matches(path, task["forbidden_paths"][repository_id]):
                violations.append(
                    {"kind": "forbidden-path", "repository_id": repository_id, "path": path}
                )
        if metadata_changes[repository_id]:
            violations.append(
                {"kind": "git-metadata-changed", "repository_id": repository_id, "path": ".git/**"}
            )
        for path in ignored_changes[repository_id]:
            violations.append(
                {"kind": "ignored-path-changed", "repository_id": repository_id, "path": path}
            )
    actual_report = _reported_paths(lane, actual_by_repository)
    reported = sorted(lane_result.get("changed_paths", []))
    if reported != actual_report:
        violations.append(
            {
                "kind": "changed-paths-mismatch",
                "repository_id": None,
                "path": None,
                "reported": reported,
                "actual": actual_report,
            }
        )
    return {
        "allowed_paths": {
            repository_id: task["allowed_paths"][repository_id]
            for repository_id in lane["repository_ids"]
        },
        "forbidden_paths": {
            repository_id: task["forbidden_paths"][repository_id]
            for repository_id in lane["repository_ids"]
        },
        "actual_changed_paths": actual_report,
        "reported_changed_paths": reported,
        "ignored_changed_paths": {
            repository_id: ignored_changes[repository_id]
            for repository_id in lane["repository_ids"]
        },
        "violations": violations,
        "status": "pass" if not violations else "fail",
    }


def _reported_model_attempts(lane_result: dict[str, Any], *, max_attempts: int = MAX_LANE_ATTEMPTS) -> int:
    attempts = lane_result.get("attempts")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 0 <= attempts <= max_attempts
    ):
        raise RunnerError("lane result attempts is invalid")
    return attempts


def _validate_lane_result(
    lane_result: dict[str, Any],
    lane: dict[str, Any],
    configuration: EvalConfiguration,
    *,
    predictability: bool = False,
) -> None:
    expected = {
        "lane_id": lane["id"],
        "role": lane["role"],
        "requested_model": configuration.model,
        "requested_reasoning": configuration.reasoning_effort,
    }
    for field, value in expected.items():
        if lane_result.get(field) != value:
            raise RunnerError(f"lane result {field} does not match the requested lane")
    status = lane_result.get("status")
    if status not in LANE_STATUSES:
        raise RunnerError("lane result status is invalid")
    selection_status = lane_result.get("selection_status")
    if not isinstance(selection_status, str) or not selection_status:
        raise RunnerError("lane result selection_status is invalid")
    max_attempts = MAX_LANE_ATTEMPTS * (2 if predictability else 1)
    attempts = _reported_model_attempts(lane_result, max_attempts=max_attempts)
    if attempts == 0 and status != "infrastructure-error":
        raise RunnerError("completed lane result has no model attempts")
    if lane_result.get("selected_skills") != []:
        raise RunnerError("optional skills appeared in a model-evaluation lane")
    usage = lane_result.get("usage")
    if not isinstance(usage, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in usage.values()
    ):
        raise RunnerError("lane result usage is invalid")
    if selection_status == "exact":
        if set(usage) != set(USAGE_FIELDS):
            raise RunnerError("scorable lane result usage is incomplete")
        if usage["input_tokens"] == 0:
            raise RunnerError("scorable lane result has no accounted input tokens")
        if usage["cached_input_tokens"] > usage["input_tokens"]:
            raise RunnerError("scorable lane cached input exceeds total input")
        if usage["reasoning_output_tokens"] > usage["output_tokens"]:
            raise RunnerError("scorable lane reasoning output exceeds total output")
    elif attempts == 0 and usage:
        raise RunnerError("lane result without model attempts contains usage")
    if selection_status == "exact":
        if (
            lane_result.get("selected_model") != configuration.model
            or lane_result.get("selected_reasoning") != configuration.reasoning_effort
        ):
            raise RunnerError("exact lane result does not match the requested configuration")
        evidence = lane_result.get("selection_evidence")
        if not isinstance(evidence, list) or len(evidence) != attempts:
            raise RunnerError("exact lane result selection evidence does not match attempts")
        thread_ids: list[str] = []
        turn_ids: list[str] = []
        for item in evidence:
            if not isinstance(item, dict):
                raise RunnerError("exact lane result selection evidence is invalid")
            if (
                item.get("source") != "codex-rollout"
                or item.get("cli_version") != CODEX_CLI_VERSION
                or item.get("model_provider") != CODEX_MODEL_PROVIDER
                or item.get("configured_model") != configuration.model
                or item.get("configured_reasoning") != configuration.reasoning_effort
                or item.get("selected_model") != configuration.model
                or item.get("selected_reasoning") != configuration.reasoning_effort
                or item.get("reroutes") != []
            ):
                raise RunnerError("exact lane result selection evidence is inconsistent")
            thread_id = item.get("thread_id")
            turn_id = item.get("turn_id")
            if not isinstance(thread_id, str) or not thread_id:
                raise RunnerError("exact lane result selection thread is missing")
            if not isinstance(turn_id, str) or not turn_id:
                raise RunnerError("exact lane result selection turn is missing")
            thread_ids.append(thread_id)
            turn_ids.append(turn_id)
        if predictability:
            if len(set(thread_ids)) != 1:
                raise RunnerError("predictability lane result did not preserve one thread")
        elif len(thread_ids) != len(set(thread_ids)):
            raise RunnerError("exact lane result selection threads are duplicated")
        if len(turn_ids) != len(set(turn_ids)):
            raise RunnerError("exact lane result selection turns are duplicated")
    duration = lane_result.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise RunnerError("lane result duration_ms is invalid")


def _selection_status(lane_results: list[dict[str, Any]]) -> str:
    statuses = [result["selection_status"] for result in lane_results]
    if not statuses:
        return "unverified"
    if all(status == "exact" for status in statuses):
        return "exact"
    if all(status in {"exact", "equivalent"} for status in statuses):
        return "equivalent"
    if len(set(statuses)) == 1:
        return statuses[0]
    return "mixed"


def _merge_predictability_runs(initial: AdapterRun, terminal: AdapterRun) -> AdapterRun:
    initial_result = initial.lane_result
    terminal_result = terminal.lane_result
    if initial_result.get("thread_id") != terminal_result.get("thread_id"):
        raise RunnerError("predictability continuation changed the Codex thread")
    usage = {
        field: initial_result.get("usage", {}).get(field, 0)
        + terminal_result.get("usage", {}).get(field, 0)
        for field in USAGE_FIELDS
    }
    merged = {
        **terminal_result,
        "selection_status": _selection_status([initial_result, terminal_result]),
        "selection_evidence": [
            *initial_result.get("selection_evidence", []),
            *terminal_result.get("selection_evidence", []),
        ],
        "usage": usage,
        "duration_ms": initial_result.get("duration_ms", 0)
        + terminal_result.get("duration_ms", 0),
        "attempts": initial_result.get("attempts", 0) + terminal_result.get("attempts", 0),
        "turns": [
            *initial_result.get("turns", []),
            *terminal_result.get("turns", []),
        ],
    }
    return AdapterRun(
        lane_result=merged,
        stdout_jsonl=initial.stdout_jsonl + terminal.stdout_jsonl,
        stderr="\n".join(part for part in (initial.stderr, terminal.stderr) if part),
    )


def _decision_workspace_evidence(
    lane: dict[str, Any],
    workspaces: dict[str, SyntheticWorkspace],
    setup_fingerprints: dict[str, WorkspaceMetadataFingerprint],
    filesystem_baselines: dict[str, FilesystemSnapshot],
    setup_dependency_roots: dict[str, set[str]],
) -> dict[str, Any]:
    metadata_changes: list[str] = []
    changed_paths: dict[str, list[str]] = {}
    filesystem_changes: dict[str, list[str]] = {}
    for repository_id in lane["repository_ids"]:
        workspace = workspaces[repository_id]
        if _workspace_metadata_fingerprint(workspace) != setup_fingerprints[repository_id]:
            metadata_changes.append(repository_id)
        changed_paths[repository_id] = _changed_paths(
            workspace,
            setup_dependency_roots[repository_id],
        )
        filesystem_changes[repository_id] = _filesystem_changes(
            filesystem_baselines[repository_id],
            _workspace_filesystem_snapshot(workspace),
        )
    clean = not metadata_changes and not any(changed_paths.values()) and not any(
        filesystem_changes.values()
    )
    return {
        "clean": clean,
        "metadata_changes": metadata_changes,
        "changed_paths": changed_paths,
        "filesystem_changes": filesystem_changes,
    }


def _decision_protocol_evidence(
    predictability: dict[str, Any],
    lane_result: dict[str, Any],
    workspace_evidence: dict[str, Any],
) -> dict[str, Any]:
    ambiguity = predictability["ambiguity"]
    request_observed = lane_result.get("status") == "decision_request"
    expected_decision_id = ambiguity["decision_id"] if ambiguity is not None else None
    expected_requirements = (
        sorted(ambiguity["affected_requirements"]) if ambiguity is not None else []
    )
    observed_requirements = sorted(lane_result.get("affected_requirements", []))
    request_matches = bool(
        ambiguity is not None
        and request_observed
        and lane_result.get("decision_id") == expected_decision_id
        and observed_requirements == expected_requirements
    )
    return {
        "decision_required": ambiguity is not None,
        "request_observed": request_observed,
        "expected_decision_id": expected_decision_id,
        "observed_decision_id": lane_result.get("decision_id"),
        "expected_affected_requirements": expected_requirements,
        "observed_affected_requirements": observed_requirements,
        "request_matches_contract": request_matches,
        "workspace_before_response": workspace_evidence,
        "resume_allowed": request_matches and workspace_evidence["clean"],
        "resumed": False,
        "terminal_status": None,
        "terminal_requirement_ids_match": None,
    }


def _token_usage(lane_results: list[dict[str, Any]]) -> int:
    return sum(
        result["usage"].get("input_tokens", 0) + result["usage"].get("output_tokens", 0)
        for result in lane_results
    )


def _evidence_duration(
    setup: dict[str, Any],
    evaluator_setup: dict[str, Any],
    lane_results: list[dict[str, Any]],
    evaluator: dict[str, Any],
) -> int:
    return (
        sum(check.get("duration_ms", 0) for check in setup.get("checks", []))
        + sum(check.get("duration_ms", 0) for check in evaluator_setup.get("checks", []))
        + sum(result["duration_ms"] for result in lane_results)
        + sum(check.get("duration_ms", 0) for check in evaluator.get("checks", []))
    )


def _infrastructure_cell(
    task: dict[str, Any],
    configuration: EvalConfiguration,
    repeat: int,
    lane_statuses: list[str] | None = None,
    critical_failures: int = 0,
    integration_required: bool = False,
    model_attempts: int = 0,
) -> dict[str, Any]:
    return {
        "task_id": task["id"],
        "config_id": configuration.config_id,
        "repeat": repeat,
        "status": "infrastructure-error",
        "selection_status": "unverified",
        "critical_failures": critical_failures,
        "lane_statuses": lane_statuses or ["infrastructure-error" for _ in task["lanes"]],
        "integration_required": integration_required,
        "integration_pass": False if integration_required else None,
        "pairwise_quality": "not-run",
        "model_attempts": model_attempts,
        "token_usage": 0,
        "duration_ms": 0,
    }


def run_eval_cell(
    corpus: CorpusManifest,
    task_id: str,
    configuration: EvalConfiguration,
    repeat: int,
    temp_root: Path,
    artifact_dir: Path,
    *,
    cell_id: str | None = None,
    lane_runner: LaneRunner = run_cli_lane,
    resume_lane_runner: ResumeLaneRunner = resume_cli_lane,
    command_runner: CommandRunner = run_process_group,
) -> CellRun:
    """Run task lanes sequentially and persist only redacted trusted evidence."""
    _validate_configuration(configuration, repeat)
    task = _task(corpus, task_id)
    _lane_owners(task)
    workspace_cell_id = cell_id or cell_identifier(task_id, configuration.config_id, repeat)
    if not ID_PATTERN.fullmatch(workspace_cell_id):
        raise RunnerError("cell_id must be a lowercase id")
    suite_id = corpus.data["corpus_id"]
    root = initialize_temp_root(temp_root, suite_id)
    artifacts = _prepare_artifacts(artifact_dir, root)
    evaluator_path = corpus.root / task["evaluator"]
    evaluator_contract = load_evaluator(evaluator_path)
    isolation_markers = _isolation_markers(corpus, task, evaluator_path)
    integration_required = any(
        command.get("scope") == "integration" for command in evaluator_contract.get("commands", [])
    )
    predictability = task.get("predictability")
    schema_name = (
        "agent-output-predictability.schema.json"
        if predictability is not None
        else "agent-output.schema.json"
    )
    schema = Path(__file__).resolve().parents[1] / "testdata" / "model-evals" / schema_name

    details: dict[str, Any] = {
        "schema_version": 1,
        "cell_id": workspace_cell_id,
        "task_id": task_id,
        "config_id": configuration.config_id,
        "requested_configuration": {
            "model": configuration.model,
            "reasoning_effort": configuration.reasoning_effort,
        },
        "repeat": repeat,
        "task_facts": {lane["id"]: lane["task_facts"] for lane in task["lanes"]},
        "lanes": [],
        "patches": {},
        "setup": None,
        "evaluator_setup": None,
        "evaluator": None,
        "source_state_preserved": False,
        "runtime_errors": [],
    }
    source_states: dict[str, RepositoryState] = {}
    workspaces: dict[str, SyntheticWorkspace] = {}
    evaluator_workspaces: dict[str, SyntheticWorkspace] = {}
    workspace_baselines: dict[str, WorkspaceMetadataFingerprint] = {}
    filesystem_baselines: dict[str, FilesystemSnapshot] = {}
    dependency_roots_before_setup: dict[str, set[str]] = {}
    setup_dependency_roots: dict[str, set[str]] = {}
    lane_results: list[dict[str, Any]] = []
    observed_model_attempts = 0
    cell: dict[str, Any] | None = None

    try:
        for repository_id in task["repositories"]:
            source = corpus.repositories[repository_id]
            source_states[repository_id] = snapshot_repository_state(source)
            workspace = create_synthetic_workspace(
                source,
                task["revisions"][repository_id]["base"],
                repository_id,
                root,
                suite_id,
                workspace_cell_id,
                f"repo-{repository_id}",
            )
            workspaces[repository_id] = workspace
            workspace_baselines[repository_id] = _workspace_metadata_fingerprint(workspace)
            dependency_roots_before_setup[repository_id] = _dependency_directories(workspace.path)

        workspace_paths = {repository_id: workspace.path for repository_id, workspace in workspaces.items()}
        initial_setup_scratch = root / "scratch" / workspace_cell_id / "initial-setup"
        setup = _redact_json(
            run_setup_commands(
                evaluator_path,
                workspace_paths,
                task["timeout_seconds"],
                command_runner,
                initial_setup_scratch,
            )
        )
        setup_metadata_mutations: list[str] = []
        setup_fingerprints: dict[str, WorkspaceMetadataFingerprint] = {}
        for repository_id, workspace in workspaces.items():
            fingerprint = _workspace_metadata_fingerprint(workspace)
            setup_fingerprints[repository_id] = fingerprint
            if _metadata_changed(workspace_baselines[repository_id], fingerprint):
                setup_metadata_mutations.append(repository_id)
        setup_mutations: dict[str, list[str]] = {}
        for repository_id, workspace in workspaces.items():
            setup_dependency_roots[repository_id] = (
                _dependency_directories(workspace.path)
                - dependency_roots_before_setup[repository_id]
            )
            if repository_id in setup_metadata_mutations:
                continue
            changed_paths = _changed_paths(workspace, setup_dependency_roots[repository_id])
            if changed_paths:
                setup_mutations[repository_id] = changed_paths
            filesystem_baselines[repository_id] = _workspace_filesystem_snapshot(workspace)
        if setup_mutations or setup_metadata_mutations:
            setup = {
                **setup,
                "status": "infrastructure-error",
                "error_kind": "setup-mutated-workspace",
                "changed_paths": setup_mutations,
                "metadata_changes": setup_metadata_mutations,
            }
        details["setup"] = setup
        _write_json(artifacts / "setup.json", setup)

        if setup["status"] != "pass":
            cell = _infrastructure_cell(
                task,
                configuration,
                repeat,
                integration_required=integration_required,
            )
        else:
            for lane in task["lanes"]:
                decision_protocol: dict[str, Any] | None = None
                lane_artifacts = artifacts / "lanes" / lane["id"]
                lane_artifacts.mkdir(parents=True, exist_ok=True)
                primary = workspaces[lane["primary_repository"]].path
                additional = tuple(
                    workspaces[repository_id].path
                    for repository_id in lane["repository_ids"]
                    if repository_id != lane["primary_repository"]
                )
                output_path = (
                    workspaces[lane["primary_repository"]].metadata_path.parent
                    / f"agent-output-{lane['id']}.json"
                )
                lane_scratch = root / "scratch" / workspace_cell_id / lane["id"]
                client_codex_home = root / "client-homes" / workspace_cell_id / lane["id"]
                host_codex_home = Path(
                    os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
                ).expanduser()
                adapter_config = AdapterConfig(
                    lane_id=lane["id"],
                    role=lane["role"],
                    model=configuration.model,
                    reasoning_effort=configuration.reasoning_effort,
                    primary_workspace=primary,
                    additional_workspaces=additional,
                    output_schema=schema,
                    output_path=output_path,
                    scratch_path=lane_scratch,
                    client_codex_home=client_codex_home,
                    auth_source=host_codex_home / "auth.json",
                    timeout_seconds=task["timeout_seconds"],
                    selected_skills=(),
                    active_gates=(),
                    predictability_output=predictability is not None,
                )
                prompt = _lane_prompt(corpus, task, lane)
                prompt_findings = _isolation_findings(
                    prompt,
                    f"lane:{lane['id']}:prompt",
                    isolation_markers,
                )
                if prompt_findings:
                    raise IsolationViolation(prompt_findings)
                try:
                    initial_run = lane_runner(adapter_config, prompt)
                finally:
                    output_path.unlink(missing_ok=True)
                observed_model_attempts += _reported_model_attempts(initial_run.lane_result)
                lane_run = initial_run
                lane_markers = isolation_markers
                if predictability is not None:
                    _validate_lane_result(
                        initial_run.lane_result,
                        lane,
                        configuration,
                        predictability=True,
                    )
                    turn_one = lane_artifacts / "turn-1"
                    turn_one.mkdir(parents=True, exist_ok=True)
                    (turn_one / "stdout.jsonl").write_text(
                        _redact_isolation_markers(
                            initial_run.stdout_jsonl,
                            isolation_markers,
                        ),
                        encoding="utf-8",
                    )
                    (turn_one / "stderr.log").write_text(
                        _redact_isolation_markers(initial_run.stderr, isolation_markers),
                        encoding="utf-8",
                    )
                    _write_json(turn_one / "lane-result.json", initial_run.lane_result)
                    workspace_evidence = _decision_workspace_evidence(
                        lane,
                        workspaces,
                        setup_fingerprints,
                        filesystem_baselines,
                        setup_dependency_roots,
                    )
                    decision_protocol = _decision_protocol_evidence(
                        predictability,
                        initial_run.lane_result,
                        workspace_evidence,
                    )
                    initial_findings = (
                        _isolation_findings(
                            initial_run.lane_result,
                            f"lane:{lane['id']}:turn-1-result",
                            isolation_markers,
                        )
                        + _isolation_findings(
                            initial_run.stdout_jsonl,
                            f"lane:{lane['id']}:turn-1-stdout",
                            isolation_markers,
                        )
                        + _isolation_findings(
                            initial_run.stderr,
                            f"lane:{lane['id']}:turn-1-stderr",
                            isolation_markers,
                        )
                    )
                    if initial_findings:
                        _write_json(turn_one / "isolation-findings.json", initial_findings)
                        raise IsolationViolation(initial_findings)
                    ambiguity = predictability["ambiguity"]
                    if ambiguity is not None and decision_protocol["resume_allowed"]:
                        architect_response = (
                            corpus.root / ambiguity["architect_response"]
                        ).read_text(encoding="utf-8").strip()
                        continuation_prompt = (
                            "# Architect response\n\n"
                            + architect_response
                            + "\n\nContinue the original task. Complete every required behavior and return "
                            + "a terminal result; do not send another decision_request."
                        )
                        (lane_artifacts / "architect-response.md").write_text(
                            redact_text(architect_response) + "\n",
                            encoding="utf-8",
                        )
                        thread_id = initial_run.lane_result.get("thread_id")
                        if not isinstance(thread_id, str) or not thread_id:
                            raise RunnerError("decision request has no resumable thread id")
                        try:
                            terminal_run = resume_lane_runner(
                                adapter_config,
                                continuation_prompt,
                                thread_id,
                            )
                        finally:
                            output_path.unlink(missing_ok=True)
                        observed_model_attempts += _reported_model_attempts(
                            terminal_run.lane_result
                        )
                        lane_run = _merge_predictability_runs(initial_run, terminal_run)
                        decision_protocol["resumed"] = True
                        decision_protocol["terminal_status"] = terminal_run.lane_result.get(
                            "status"
                        )
                        expected_requirement_ids = sorted(
                            item["id"] for item in predictability["required_behaviors"]
                        )
                        terminal_requirement_ids = sorted(
                            item.get("id")
                            for item in terminal_run.lane_result.get(
                                "requirement_results", []
                            )
                            if isinstance(item, dict) and isinstance(item.get("id"), str)
                        )
                        decision_protocol["terminal_requirement_ids_match"] = (
                            terminal_requirement_ids == expected_requirement_ids
                        )
                        turn_two = lane_artifacts / "turn-2"
                        turn_two.mkdir(parents=True, exist_ok=True)
                        continuation_markers = [
                            marker
                            for marker in isolation_markers
                            if not marker.kind.startswith("architect-response")
                        ]
                        lane_markers = continuation_markers
                        (turn_two / "stdout.jsonl").write_text(
                            _redact_isolation_markers(
                                terminal_run.stdout_jsonl,
                                continuation_markers,
                            ),
                            encoding="utf-8",
                        )
                        (turn_two / "stderr.log").write_text(
                            _redact_isolation_markers(
                                terminal_run.stderr,
                                continuation_markers,
                            ),
                            encoding="utf-8",
                        )
                        _write_json(turn_two / "lane-result.json", terminal_run.lane_result)
                    if not decision_protocol["resumed"]:
                        decision_protocol["terminal_status"] = initial_run.lane_result.get(
                            "status"
                        )
                        expected_requirement_ids = sorted(
                            item["id"] for item in predictability["required_behaviors"]
                        )
                        observed_requirement_ids = sorted(
                            item.get("id")
                            for item in initial_run.lane_result.get(
                                "requirement_results", []
                            )
                            if isinstance(item, dict) and isinstance(item.get("id"), str)
                        )
                        decision_protocol["terminal_requirement_ids_match"] = (
                            observed_requirement_ids == expected_requirement_ids
                        )
                    _write_json(lane_artifacts / "decision-protocol.json", decision_protocol)
                    if initial_run.lane_result.get("status") == "decision_request":
                        _write_json(
                            lane_artifacts / "decision-request.json",
                            {
                                key: initial_run.lane_result.get(key)
                                for key in (
                                    "decision_id",
                                    "missing_decision",
                                    "affected_requirements",
                                    "decision_evidence",
                                )
                            },
                        )
                stdout = lane_run.stdout_jsonl
                stderr = lane_run.stderr
                findings = (
                    _isolation_findings(
                        lane_run.lane_result,
                        f"lane:{lane['id']}:lane-result",
                        lane_markers,
                    )
                    + _isolation_findings(
                        stdout,
                        f"lane:{lane['id']}:stdout",
                        lane_markers,
                    )
                    + _isolation_findings(
                        stderr,
                        f"lane:{lane['id']}:stderr",
                        lane_markers,
                    )
                )
                lane_result = _redact_isolation_markers(lane_run.lane_result, lane_markers)
                stdout = _redact_isolation_markers(stdout, lane_markers)
                stderr = _redact_isolation_markers(stderr, lane_markers)
                if findings:
                    (lane_artifacts / "stdout.jsonl").write_text(stdout, encoding="utf-8")
                    (lane_artifacts / "stderr.log").write_text(stderr, encoding="utf-8")
                    _write_json(lane_artifacts / "lane-result.json", lane_result)
                    _write_json(lane_artifacts / "isolation-findings.json", findings)
                    details["lanes"].append(
                        {
                            "lane_id": lane["id"],
                            "role": lane["role"],
                            "lane_result": lane_result,
                            "effective_status": "infrastructure-error",
                            "boundary": None,
                            "isolation_findings": findings,
                            "artifacts": {
                                "stdout": f"lanes/{lane['id']}/stdout.jsonl",
                                "stderr": f"lanes/{lane['id']}/stderr.log",
                                "lane_result": f"lanes/{lane['id']}/lane-result.json",
                                "isolation_findings": (
                                    f"lanes/{lane['id']}/isolation-findings.json"
                                ),
                            },
                        }
                    )
                    raise IsolationViolation(findings)
                _validate_lane_result(
                    lane_result,
                    lane,
                    configuration,
                    predictability=predictability is not None,
                )
                lane_results.append(lane_result)
                (lane_artifacts / "stdout.jsonl").write_text(stdout, encoding="utf-8")
                (lane_artifacts / "stderr.log").write_text(stderr, encoding="utf-8")
                _write_json(lane_artifacts / "lane-result.json", lane_result)
                details["lanes"].append(
                    {
                        "lane_id": lane["id"],
                        "role": lane["role"],
                        "lane_result": lane_result,
                        "effective_status": lane_result["status"],
                        "boundary": None,
                        "decision_protocol": decision_protocol,
                        "artifacts": {
                            "stdout": f"lanes/{lane['id']}/stdout.jsonl",
                            "stderr": f"lanes/{lane['id']}/stderr.log",
                            "lane_result": f"lanes/{lane['id']}/lane-result.json",
                        },
                    }
                )
                if decision_protocol is not None:
                    details["lanes"][-1]["artifacts"]["decision_protocol"] = (
                        f"lanes/{lane['id']}/decision-protocol.json"
                    )
                    if decision_protocol["request_observed"]:
                        details["lanes"][-1]["artifacts"]["decision_request"] = (
                            f"lanes/{lane['id']}/decision-request.json"
                        )
                    if decision_protocol["resumed"]:
                        details["lanes"][-1]["artifacts"]["architect_response"] = (
                            f"lanes/{lane['id']}/architect-response.md"
                        )

            post_lane_fingerprints = {
                repository_id: _workspace_metadata_fingerprint(workspace)
                for repository_id, workspace in workspaces.items()
            }
            metadata_changes = {
                repository_id: _metadata_changed(
                    setup_fingerprints[repository_id], post_lane_fingerprints[repository_id]
                )
                for repository_id, workspace in workspaces.items()
            }
            actual_by_repository = {
                repository_id: _changed_paths(
                    workspace,
                    setup_dependency_roots[repository_id],
                )
                for repository_id, workspace in workspaces.items()
            }
            post_lane_snapshots = {
                repository_id: _workspace_filesystem_snapshot(workspace)
                for repository_id, workspace in workspaces.items()
            }
            filesystem_changes = {
                repository_id: _filesystem_changes(
                    filesystem_baselines[repository_id],
                    post_lane_snapshots[repository_id],
                )
                for repository_id, workspace in workspaces.items()
            }
            ignored_changes = {
                repository_id: sorted(
                    set(filesystem_changes[repository_id])
                    - set(actual_by_repository[repository_id])
                )
                for repository_id in workspaces
            }
            patch_findings: list[dict[str, str]] = []
            raw_patches: dict[str, bytes] = {}
            for repository_id, workspace in workspaces.items():
                raw_patch = _workspace_patch(workspace, actual_by_repository[repository_id])
                raw_patches[repository_id] = raw_patch
                patch = raw_patch.decode("utf-8", errors="replace")
                findings = _isolation_findings(
                    patch,
                    f"patch:{repository_id}",
                    isolation_markers,
                )
                patch_findings.extend(findings)
                patch = _redact_isolation_markers(patch, isolation_markers)
                patch_path = artifacts / "patches" / f"{repository_id}.patch"
                patch_path.parent.mkdir(parents=True, exist_ok=True)
                patch_path.write_text(patch, encoding="utf-8")
                details["patches"][repository_id] = {
                    "artifact": f"patches/{repository_id}.patch",
                    "changed_paths": actual_by_repository[repository_id],
                    "isolation_findings": findings,
                }
            if patch_findings:
                _write_json(artifacts / "patch-isolation-findings.json", patch_findings)
                raise IsolationViolation(patch_findings)

            for repository_id, workspace in workspaces.items():
                if _workspace_metadata_fingerprint(workspace) != post_lane_fingerprints[repository_id]:
                    raise RunnerError(
                        f"model workspace metadata changed during patch capture: {repository_id}"
                    )
                if _workspace_filesystem_snapshot(workspace) != post_lane_snapshots[repository_id]:
                    raise RunnerError(
                        f"model workspace changed during patch capture: {repository_id}"
                    )
                if _changed_paths(workspace, setup_dependency_roots[repository_id]) != actual_by_repository[
                    repository_id
                ]:
                    raise RunnerError(
                        f"model changed-path evidence drifted during patch capture: {repository_id}"
                    )

            scope_failures = 0
            effective_lane_statuses: list[str] = []
            for lane, lane_detail, lane_result in zip(task["lanes"], details["lanes"], lane_results):
                boundary = _boundary_evidence(
                    task,
                    lane,
                    lane_result,
                    actual_by_repository,
                    metadata_changes,
                    ignored_changes,
                )
                lane_detail["boundary"] = boundary
                scope_failures += len(boundary["violations"])
                effective_status = lane_result["status"]
                protocol = lane_detail.get("decision_protocol")
                if protocol is not None:
                    decision_failed = (
                        protocol["decision_required"] and not protocol["resumed"]
                    ) or (
                        not protocol["decision_required"] and protocol["request_observed"]
                    ) or (
                        protocol["resumed"]
                        and (
                            protocol["terminal_status"] == "decision_request"
                            or not protocol["terminal_requirement_ids_match"]
                        )
                    )
                    if decision_failed and effective_status != "infrastructure-error":
                        effective_status = "fail"
                if effective_status == "decision_request":
                    effective_status = "fail"
                if boundary["violations"] and effective_status != "infrastructure-error":
                    effective_status = "fail"
                lane_detail["effective_status"] = effective_status
                effective_lane_statuses.append(effective_status)
                _write_json(
                    artifacts / "lanes" / lane["id"] / "boundary.json",
                    boundary,
                )
                lane_detail["artifacts"]["boundary"] = f"lanes/{lane['id']}/boundary.json"

            evaluator_cell_id = f"{workspace_cell_id}-evaluator"
            evaluator_workspace_baselines: dict[str, WorkspaceMetadataFingerprint] = {}
            evaluator_dependencies_before_setup: dict[str, set[str]] = {}
            for repository_id in task["repositories"]:
                evaluator_workspace = create_synthetic_workspace(
                    corpus.repositories[repository_id],
                    task["revisions"][repository_id]["base"],
                    repository_id,
                    root,
                    suite_id,
                    evaluator_cell_id,
                    f"repo-{repository_id}",
                )
                evaluator_workspaces[repository_id] = evaluator_workspace
                evaluator_workspace_baselines[repository_id] = _workspace_metadata_fingerprint(
                    evaluator_workspace
                )
                evaluator_dependencies_before_setup[repository_id] = _dependency_directories(
                    evaluator_workspace.path
                )

            evaluator_workspace_paths = {
                repository_id: workspace.path
                for repository_id, workspace in evaluator_workspaces.items()
            }
            evaluator_setup_scratch = (
                root / "scratch" / workspace_cell_id / "evaluator-setup"
            )
            evaluator_setup = _redact_json(
                run_setup_commands(
                    evaluator_path,
                    evaluator_workspace_paths,
                    task["timeout_seconds"],
                    command_runner,
                    evaluator_setup_scratch,
                )
            )
            details["evaluator_setup"] = evaluator_setup
            _write_json(artifacts / "evaluator-setup.json", evaluator_setup)
            if evaluator_setup["status"] != "pass":
                raise RunnerError("fresh evaluator workspace setup failed")

            evaluator_dependency_roots: dict[str, set[str]] = {}
            for repository_id, evaluator_workspace in evaluator_workspaces.items():
                setup_fingerprint = _workspace_metadata_fingerprint(evaluator_workspace)
                if _metadata_changed(
                    evaluator_workspace_baselines[repository_id],
                    setup_fingerprint,
                ):
                    raise RunnerError(
                        f"fresh evaluator setup changed Git metadata: {repository_id}"
                    )
                evaluator_dependency_roots[repository_id] = (
                    _dependency_directories(evaluator_workspace.path)
                    - evaluator_dependencies_before_setup[repository_id]
                )
                setup_changed_paths = _changed_paths(
                    evaluator_workspace,
                    evaluator_dependency_roots[repository_id],
                )
                if setup_changed_paths:
                    raise RunnerError(
                        f"fresh evaluator setup changed source files: {repository_id}"
                    )
                evaluator_setup_snapshot = _workspace_filesystem_snapshot(
                    evaluator_workspace
                )
                if evaluator_setup_snapshot != filesystem_baselines[repository_id]:
                    raise RunnerError(
                        f"fresh evaluator setup did not reproduce the model baseline: {repository_id}"
                    )

                _apply_workspace_patch(
                    evaluator_workspace,
                    raw_patches[repository_id],
                )
                if _workspace_metadata_fingerprint(evaluator_workspace) != setup_fingerprint:
                    raise RunnerError(
                        f"applying the model patch changed Git metadata: {repository_id}"
                    )
                evaluator_changed_paths = _changed_paths(
                    evaluator_workspace,
                    evaluator_dependency_roots[repository_id],
                )
                if evaluator_changed_paths != actual_by_repository[repository_id]:
                    raise RunnerError(
                        f"fresh evaluator changed paths do not match model evidence: {repository_id}"
                    )
                expected_snapshot = _snapshot_after_patch(
                    filesystem_baselines[repository_id],
                    post_lane_snapshots[repository_id],
                    actual_by_repository[repository_id],
                )
                if _workspace_filesystem_snapshot(evaluator_workspace) != expected_snapshot:
                    raise RunnerError(
                        f"fresh evaluator content does not match model evidence: {repository_id}"
                    )

            gold_sources = {
                repository_id: (
                    corpus.repositories[repository_id],
                    task["revisions"][repository_id]["gold"],
                )
                for repository_id in task["repositories"]
                if "gold" in task["revisions"][repository_id]
            }
            evaluator = _redact_json(
                run_evaluator(
                    evaluator_path,
                    evaluator_workspace_paths,
                    task["timeout_seconds"],
                    command_runner,
                    gold_sources,
                    skip_setup_commands=True,
                    scratch_root=(
                        root / "scratch" / workspace_cell_id / "evaluator-commands"
                    ),
                )
            )
            details["evaluator"] = evaluator
            _write_json(artifacts / "evaluator.json", evaluator)

            predictability_result: dict[str, Any] | None = None
            if predictability is not None:
                predictability_result = score_predictability(
                    predictability,
                    evaluator,
                    details["lanes"][0]["boundary"],
                    details["lanes"][0]["decision_protocol"],
                    lane_results[0],
                )
                details["predictability"] = predictability_result
                _write_json(artifacts / "predictability.json", predictability_result)
                if (
                    not predictability_result["pass"]
                    and effective_lane_statuses[0] != "infrastructure-error"
                ):
                    effective_lane_statuses[0] = "fail"
                    details["lanes"][0]["effective_status"] = "fail"

            critical_failures = scope_failures
            if task["criticality"] == "critical" and evaluator["status"] == "fail":
                critical_failures += 1
            if any(status == "infrastructure-error" for status in effective_lane_statuses) or evaluator[
                "status"
            ] == "infrastructure-error":
                status = "infrastructure-error"
            elif any(status == "fail" for status in effective_lane_statuses) or evaluator["status"] == "fail":
                status = "fail"
            elif any(status == "blocked" for status in effective_lane_statuses):
                status = "blocked"
            else:
                status = "pass"
            integration_pass = evaluator.get("integration_pass")
            if integration_required and integration_pass is None:
                integration_pass = False
            cell = {
                "task_id": task_id,
                "config_id": configuration.config_id,
                "repeat": repeat,
                "status": status,
                "selection_status": _selection_status(lane_results),
                "critical_failures": critical_failures,
                "lane_statuses": effective_lane_statuses,
                "integration_required": integration_required,
                "integration_pass": integration_pass,
                "pairwise_quality": "not-run",
                "model_attempts": observed_model_attempts,
                "token_usage": _token_usage(lane_results),
                "duration_ms": _evidence_duration(
                    setup,
                    evaluator_setup,
                    lane_results,
                    evaluator,
                ),
            }
            if predictability_result is not None:
                cell["predictability"] = predictability_result
    except Exception as exc:
        if isinstance(exc, IsolationViolation):
            details["runtime_errors"].append(
                {"kind": "isolation-violation", "findings": exc.findings}
            )
        else:
            details["runtime_errors"].append(
                {"kind": "runner-exception", "summary": redact_text(str(exc))}
            )
        cell = _infrastructure_cell(
            task,
            configuration,
            repeat,
            [result.get("status", "infrastructure-error") for result in lane_results]
            + ["infrastructure-error" for _ in task["lanes"][len(lane_results) :]],
            integration_required=integration_required,
            model_attempts=observed_model_attempts,
        )
    finally:
        for workspace in reversed(list(evaluator_workspaces.values())):
            try:
                cleanup_synthetic_workspace(workspace)
            except Exception as exc:
                details["runtime_errors"].append(
                    {"kind": "cleanup-failed", "summary": redact_text(str(exc))}
                )
        for workspace in reversed(list(workspaces.values())):
            try:
                cleanup_synthetic_workspace(workspace)
            except Exception as exc:
                details["runtime_errors"].append(
                    {"kind": "cleanup-failed", "summary": redact_text(str(exc))}
                )
        source_preserved = True
        for repository_id, expected in source_states.items():
            try:
                assert_repository_state(corpus.repositories[repository_id], expected)
            except Exception as exc:
                source_preserved = False
                details["runtime_errors"].append(
                    {
                        "kind": "source-state-changed",
                        "repository_id": repository_id,
                        "summary": redact_text(str(exc)),
                    }
                )
        details["source_state_preserved"] = source_preserved
        shutil.rmtree(root / "scratch" / workspace_cell_id, ignore_errors=True)
        shutil.rmtree(root / "client-homes" / workspace_cell_id, ignore_errors=True)

    if cell is None:
        cell = _infrastructure_cell(
            task,
            configuration,
            repeat,
            integration_required=integration_required,
        )
    if details["runtime_errors"]:
        cell["status"] = "infrastructure-error"
        if not cell["lane_statuses"]:
            cell["lane_statuses"] = ["infrastructure-error" for _ in task["lanes"]]
        if cell["integration_required"] and cell["integration_pass"] is None:
            cell["integration_pass"] = False
    cell = validate_cell(cell)
    details["model_attempts"] = observed_model_attempts
    details["status"] = cell["status"]
    _write_json(artifacts / "cell.json", cell)
    _write_json(artifacts / "details.json", details)
    return CellRun(cell=cell, details=_redact_json(details))
