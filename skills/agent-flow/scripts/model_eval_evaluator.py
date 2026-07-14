#!/usr/bin/env python3
"""Inject and execute hidden evaluator checks without a shell."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from model_eval_adapter import redact_text
from model_eval_manifest import ManifestError, read_git_blob
from model_eval_process import run_process_group
from model_eval_sandbox import (
    sanitized_process_environment,
    sandbox_command,
    shell_environment_values,
)


MAX_EVIDENCE_CHARS = 20_000
INFRASTRUCTURE_EXIT_CODES = {126, 127, 134, 137}
INFRASTRUCTURE_ERROR_MARKERS = (
    "spawn eperm",
    "out of memory",
    "permission profile",
    "sandbox policy",
    "failed to apply sandbox",
)
EVALUATOR_OUTPUT_ROOT = "agent_flow_eval_output"


class EvaluatorError(RuntimeError):
    """Raised when the trusted evaluator harness cannot run safely."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class InjectionRecord:
    path: Path
    original_bytes: bytes | None
    original_mode: int | None
    created_directories: tuple[Path, ...]


def load_evaluator(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvaluatorError(f"evaluator does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluatorError(f"evaluator JSON is invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluatorError("evaluator must be an object")
    return value


def _relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise EvaluatorError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise EvaluatorError(f"{label} must be a portable relative path")
    return path


def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise EvaluatorError(f"{label} escapes its root") from exc
    return resolved


def _read_regular_file(file_descriptor: int, label: str) -> tuple[bytes, int]:
    metadata = os.fstat(file_descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise EvaluatorError(f"{label} must be an unlinked regular file")
    chunks: list[bytes] = []
    while chunk := os.read(file_descriptor, 1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks), stat.S_IMODE(metadata.st_mode)


def _target_metadata(parent_descriptor: int, filename: str) -> os.stat_result | None:
    try:
        return os.stat(filename, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _inject_file_at(
    workspace: Path,
    target_relative: PurePosixPath,
    source_bytes: bytes,
    source_mode: int,
    *,
    replace: bool,
    replace_or_create: bool,
    label: str,
) -> InjectionRecord:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if os.name != "posix" or not no_follow or not directory_flag:
        raise EvaluatorError("safe evaluator injection requires POSIX no-follow file access")

    root = workspace.expanduser().absolute()
    target = root.joinpath(*target_relative.parts)
    created_directories: list[Path] = []
    descriptors: list[int] = []
    try:
        parent_descriptor = os.open(root, os.O_RDONLY | directory_flag | no_follow)
        descriptors.append(parent_descriptor)
        current = root
        for part in target_relative.parts[:-1]:
            current /= part
            try:
                os.mkdir(part, mode=0o755, dir_fd=parent_descriptor)
                created_directories.append(current)
            except FileExistsError:
                pass
            try:
                child_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise EvaluatorError(f"{label} traverses an unsafe directory") from exc
            descriptors.append(child_descriptor)
            parent_descriptor = child_descriptor

        filename = target_relative.name
        existing = _target_metadata(parent_descriptor, filename)
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise EvaluatorError(f"{label} must be an unlinked regular file")
        if existing is not None and not (replace or replace_or_create):
            raise EvaluatorError(f"{label} already exists")
        if existing is None and replace:
            raise EvaluatorError(f"{label} replacement target must be a regular file")

        original_bytes: bytes | None = None
        original_mode: int | None = None
        if existing is not None:
            try:
                read_descriptor = os.open(
                    filename,
                    os.O_RDONLY | no_follow,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise EvaluatorError(f"{label} changed during injection") from exc
            try:
                original_bytes, original_mode = _read_regular_file(read_descriptor, label)
            finally:
                os.close(read_descriptor)

        flags = os.O_WRONLY | no_follow
        flags |= os.O_TRUNC if existing is not None else os.O_CREAT | os.O_EXCL
        try:
            write_descriptor = os.open(filename, flags, source_mode, dir_fd=parent_descriptor)
        except OSError as exc:
            raise EvaluatorError(f"{label} changed during injection") from exc
        try:
            metadata = os.fstat(write_descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise EvaluatorError(f"{label} changed to an unsafe file")
            with os.fdopen(write_descriptor, "wb", closefd=False) as destination:
                destination.write(source_bytes)
                destination.flush()
            os.fchmod(write_descriptor, source_mode)
        finally:
            os.close(write_descriptor)
        return InjectionRecord(
            target,
            original_bytes,
            original_mode,
            tuple(created_directories),
        )
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _safe_environment(
    workspaces: dict[str, Path],
    scratch: Path,
    *,
    setup: bool,
    include_browser: bool,
) -> dict[str, str]:
    environment = sanitized_process_environment()
    environment.update(
        shell_environment_values(
            scratch,
            include_dependency_caches=setup,
            include_browser=include_browser,
        )
    )
    codex_home = scratch / "codex-home"
    codex_home.mkdir(exist_ok=True)
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "CI": "1",
            "NO_COLOR": "1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        }
    )
    for repository_id, workspace in workspaces.items():
        key = "AGENT_FLOW_EVAL_REPO_" + re.sub(r"[^A-Z0-9]", "_", repository_id.upper())
        environment[key] = str(workspace.resolve())
    return environment


def inject_evaluator_files(
    evaluator_path: Path,
    evaluator: dict[str, Any],
    workspaces: dict[str, Path],
    gold_sources: dict[str, tuple[Path, str]] | None = None,
) -> list[InjectionRecord]:
    """Copy hidden files with no-follow descriptors into a disposable workspace."""
    injected: list[InjectionRecord] = []
    evaluator_root = evaluator_path.parent.resolve()
    for index, injection in enumerate(evaluator.get("injections", [])):
        repository_id = injection.get("repository_id")
        if repository_id not in workspaces:
            raise EvaluatorError(f"injection {index} uses unknown repository")
        target_relative = _relative_path(injection.get("target"), f"injection {index} target")
        if target_relative.parts[0] == EVALUATOR_OUTPUT_ROOT:
            raise EvaluatorError(f"injection {index} uses the reserved evaluator output root")
        source_value = injection.get("source")
        gold_path_value = injection.get("gold_path")
        if (source_value is None) == (gold_path_value is None):
            raise EvaluatorError(f"injection {index} requires exactly one source")
        source_mode = 0o644
        if source_value is not None:
            source_relative = _relative_path(source_value, f"injection {index} source")
            source = _inside(
                evaluator_root / Path(*source_relative.parts),
                evaluator_root,
                f"injection {index} source",
            )
            if not source.is_file() or source.is_symlink():
                raise EvaluatorError(f"injection {index} source must be a regular file")
            source_bytes = source.read_bytes()
            source_mode = 0o755 if source.stat().st_mode & stat.S_IXUSR else 0o644
        else:
            gold_path = _relative_path(gold_path_value, f"injection {index} gold_path").as_posix()
            if ":" in gold_path or not gold_sources or repository_id not in gold_sources:
                raise EvaluatorError(f"injection {index} gold source is unavailable")
            source_repo, gold_revision = gold_sources[repository_id]
            try:
                process = read_git_blob(source_repo, gold_revision, gold_path)
            except ManifestError as exc:
                raise EvaluatorError(f"injection {index} cannot read gold blob: {exc}") from exc
            if process.returncode:
                detail = process.stderr.decode("utf-8", errors="replace").strip()
                raise EvaluatorError(f"injection {index} cannot read gold blob: {detail}")
            source_bytes = process.stdout
        workspace = workspaces[repository_id].resolve()
        replace = injection.get("replace", False)
        if not isinstance(replace, bool):
            raise EvaluatorError(f"injection {index} replace must be a boolean")
        replace_or_create = injection.get("replace_or_create", False)
        if not isinstance(replace_or_create, bool):
            raise EvaluatorError(f"injection {index} replace_or_create must be a boolean")
        if replace and replace_or_create:
            raise EvaluatorError(f"injection {index} cannot enable both replacement modes")
        injected.append(
            _inject_file_at(
                workspace,
                target_relative,
                source_bytes,
                source_mode,
                replace=replace,
                replace_or_create=replace_or_create,
                label=f"injection {index} target",
            )
        )
    return injected


def cleanup_injected_files(records: list[InjectionRecord]) -> None:
    """Deprecated no-op: callers must delete the ownership-checked workspace tree."""
    records.clear()


def _ensure_write_directory(
    workspace: Path,
    relative: PurePosixPath,
    label: str,
) -> Path:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if os.name != "posix" or not no_follow or not directory_flag:
        raise EvaluatorError("safe evaluator outputs require POSIX no-follow access")

    root = workspace.expanduser().absolute()
    descriptors: list[int] = []
    try:
        parent_descriptor = os.open(root, os.O_RDONLY | directory_flag | no_follow)
        descriptors.append(parent_descriptor)
        current = root
        for part in relative.parts:
            current /= part
            try:
                os.mkdir(part, mode=0o755, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            try:
                child_descriptor = os.open(
                    part,
                    os.O_RDONLY | directory_flag | no_follow,
                    dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise EvaluatorError(f"{label} traverses an unsafe directory") from exc
            descriptors.append(child_descriptor)
            parent_descriptor = child_descriptor
        return current
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _prepare_command_write_roots(
    evaluator: dict[str, Any],
    workspaces: dict[str, Path],
) -> dict[str, tuple[Path, ...]]:
    declared: dict[str, list[PurePosixPath]] = {}
    repositories: set[str] = set()
    for command in evaluator.get("commands", []):
        command_id = command.get("id")
        if not isinstance(command_id, str) or not command_id or command_id in declared:
            raise EvaluatorError("evaluator command ids must be unique non-empty strings")
        repository_id = command.get("repository_id")
        if repository_id not in workspaces:
            raise EvaluatorError(f"command {command_id} uses unknown repository")
        values = command.get("write_paths", [])
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise EvaluatorError(f"command {command_id} write_paths must be an array")
        if len(values) != len(set(values)):
            raise EvaluatorError(f"command {command_id} write_paths must be unique")
        paths: list[PurePosixPath] = []
        for index, value in enumerate(values):
            relative = _relative_path(value, f"command {command_id} write path {index}")
            if len(relative.parts) < 2 or relative.parts[0] != EVALUATOR_OUTPUT_ROOT:
                raise EvaluatorError(
                    f"command {command_id} write paths must be under {EVALUATOR_OUTPUT_ROOT}/"
                )
            paths.append(relative)
            repositories.add(repository_id)
        declared[command_id] = paths

    for repository_id in repositories:
        output_root = workspaces[repository_id] / EVALUATOR_OUTPUT_ROOT
        try:
            output_root.lstat()
        except FileNotFoundError:
            pass
        else:
            raise EvaluatorError(
                f"workspace {repository_id} already contains the reserved evaluator output root"
            )

    prepared: dict[str, tuple[Path, ...]] = {}
    for command in evaluator.get("commands", []):
        command_id = command["id"]
        repository_id = command["repository_id"]
        prepared[command_id] = tuple(
            _ensure_write_directory(
                workspaces[repository_id],
                relative,
                f"command {command_id} write path",
            )
            for relative in declared[command_id]
        )
    return prepared


def _command_result(
    command: dict[str, Any],
    status: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "id": command["id"],
        "scope": command.get("scope", "setup"),
        "repository_id": command["repository_id"],
        "argv": command["argv"],
        "status": status,
        "exit_code": exit_code,
        "stdout": redact_text(stdout)[:MAX_EVIDENCE_CHARS],
        "stderr": redact_text(stderr)[:MAX_EVIDENCE_CHARS],
        "duration_ms": duration_ms,
    }


def _completed_status(process: subprocess.CompletedProcess[str]) -> str:
    if process.returncode == 0:
        return "pass"
    combined = ((process.stdout or "") + "\n" + (process.stderr or "")).lower()
    if process.returncode < 0 or process.returncode in INFRASTRUCTURE_EXIT_CODES:
        return "infrastructure-error"
    if any(marker in combined for marker in INFRASTRUCTURE_ERROR_MARKERS):
        return "infrastructure-error"
    return "fail"


def _run_command(
    command: dict[str, Any],
    workspaces: dict[str, Path],
    timeout_seconds: int,
    runner: Runner,
    scratch_root: Path | None = None,
    write_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    repository_id = command["repository_id"]
    if repository_id not in workspaces:
        raise EvaluatorError(f"command {command['id']} uses unknown repository")
    argv = command["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise EvaluatorError(f"command {command['id']} argv is invalid")
    if scratch_root is not None:
        scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(
        tempfile.mkdtemp(
            prefix=f"{command['id']}-",
            dir=str(scratch_root) if scratch_root is not None else None,
        )
    )
    setup = command.get("scope", "setup") == "setup"
    include_browser = any("playwright" in argument for argument in argv)
    environment = _safe_environment(
        workspaces,
        scratch,
        setup=setup,
        include_browser=include_browser,
    )
    wrapped_argv = sandbox_command(
        argv,
        workspaces[repository_id],
        scratch,
        tuple(workspaces.values()),
        allow_localhost=False,
        setup=setup,
        include_browser=include_browser,
        writable_paths=write_roots,
        workspace_access="write" if setup else "read",
    )
    start = time.monotonic()
    try:
        process = runner(
            wrapped_argv,
            cwd=workspaces[repository_id],
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        timeout_status = "infrastructure-error" if command.get("scope", "setup") == "setup" else "fail"
        return _command_result(command, timeout_status, None, stdout, stderr, duration_ms)
    except OSError as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        return _command_result(command, "infrastructure-error", None, "", str(exc), duration_ms)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    duration_ms = round((time.monotonic() - start) * 1000)
    return _command_result(
        command,
        _completed_status(process),
        process.returncode,
        process.stdout or "",
        process.stderr or "",
        duration_ms,
    )


def run_setup_commands(
    evaluator_path: Path,
    workspaces: dict[str, Path],
    timeout_seconds: int,
    runner: Runner = run_process_group,
    scratch_root: Path | None = None,
) -> dict[str, Any]:
    """Prepare deterministic dependencies before any model lane starts."""
    evaluator = load_evaluator(evaluator_path)
    checks: list[dict[str, Any]] = []
    try:
        for command in evaluator.get("setup_commands", []):
            result = _run_command(
                command,
                workspaces,
                timeout_seconds,
                runner,
                scratch_root,
            )
            checks.append(result)
            if result["status"] != "pass":
                return {
                    "status": "infrastructure-error",
                    "error_kind": "setup-failed",
                    "checks": checks,
                }
    except EvaluatorError as exc:
        return {
            "status": "infrastructure-error",
            "error_kind": "evaluator-contract",
            "summary": str(exc),
            "checks": checks,
        }
    return {"status": "pass", "error_kind": None, "checks": checks}


def run_evaluator(
    evaluator_path: Path,
    workspaces: dict[str, Path],
    timeout_seconds: int,
    runner: Runner = run_process_group,
    gold_sources: dict[str, tuple[Path, str]] | None = None,
    *,
    skip_setup_commands: bool = False,
    scratch_root: Path | None = None,
) -> dict[str, Any]:
    """Run hidden checks in a disposable synthetic workspace.

    Injected files deliberately remain in the workspace. The caller must delete the
    whole ownership-checked workspace after this function returns; restoring files
    after untrusted code ran would create a symlink race in the host process.
    """
    evaluator = load_evaluator(evaluator_path)
    injected: list[InjectionRecord] = []
    checks: list[dict[str, Any]] = []
    if not skip_setup_commands:
        setup = run_setup_commands(
            evaluator_path,
            workspaces,
            timeout_seconds,
            runner,
            scratch_root,
        )
        checks.extend(setup["checks"])
        if setup["status"] != "pass":
            return {
                "status": "infrastructure-error",
                "error_kind": setup["error_kind"],
                "summary": setup.get("summary"),
                "checks": checks,
                "integration_pass": None,
                "positive_checks": evaluator.get("positive_checks", []),
                "negative_checks": evaluator.get("negative_checks", []),
            }
    try:
        command_write_roots = _prepare_command_write_roots(evaluator, workspaces)
        injected = inject_evaluator_files(evaluator_path, evaluator, workspaces, gold_sources)
        for command in evaluator.get("commands", []):
            checks.append(
                _run_command(
                    command,
                    workspaces,
                    timeout_seconds,
                    runner,
                    scratch_root,
                    command_write_roots[command["id"]],
                )
            )
    except EvaluatorError as exc:
        return {
            "status": "infrastructure-error",
            "error_kind": "evaluator-contract",
            "summary": str(exc),
            "checks": checks,
            "integration_pass": None,
            "positive_checks": evaluator.get("positive_checks", []),
            "negative_checks": evaluator.get("negative_checks", []),
        }
    infrastructure_error = any(check["status"] == "infrastructure-error" for check in checks)
    command_fail = any(check["status"] == "fail" for check in checks)
    integration_checks = [check for check in checks if check["scope"] == "integration"]
    if infrastructure_error:
        status = "infrastructure-error"
        error_kind = "command-infrastructure"
    elif command_fail:
        status = "fail"
        error_kind = None
    else:
        status = "pass"
        error_kind = None
    integration_pass = None
    if integration_checks:
        integration_pass = all(check["status"] == "pass" for check in integration_checks)
    return {
        "status": status,
        "error_kind": error_kind,
        "checks": checks,
        "integration_pass": integration_pass,
        "positive_checks": evaluator.get("positive_checks", []),
        "negative_checks": evaluator.get("negative_checks", []),
    }
