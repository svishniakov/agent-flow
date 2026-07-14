#!/usr/bin/env python3
"""Least-privilege Codex permission profiles for model-evaluation processes."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping


PROFILE_NAME = "agent-flow-eval"
SAFE_PROCESS_ENV_KEYS = {
    "LANG",
    "LC_ALL",
    "NO_COLOR",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
}


class SandboxConfigError(RuntimeError):
    """Raised when a least-privilege sandbox cannot be constructed safely."""


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, Mapping):
        entries = ",".join(f"{json.dumps(str(key))}={_toml(item)}" for key, item in value.items())
        return "{" + entries + "}"
    raise SandboxConfigError(f"unsupported TOML value: {type(value).__name__}")


def sanitized_process_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep only non-secret runtime variables, never host homes, task paths, or credentials."""
    source = os.environ if environment is None else environment
    result = {
        key: value
        for key, value in source.items()
        if key in SAFE_PROCESS_ENV_KEYS and isinstance(value, str) and value
    }
    if "PATH" not in result:
        raise SandboxConfigError("PATH is required to launch Codex")
    result.setdefault("LANG", "C.UTF-8")
    result.setdefault("NO_COLOR", "1")
    return result


def prepare_scratch(path: Path) -> Path:
    scratch = path.expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    for relative in ("home", "tmp", "cache", "go-build", "go-mod"):
        (scratch / relative).mkdir(exist_ok=True)
    return scratch


def prepare_auth_only_codex_home(destination: Path, auth_source: Path) -> Path:
    """Copy only Codex authentication into a model-invisible ephemeral home."""
    raw_source = auth_source.expanduser()
    if raw_source.is_symlink():
        raise SandboxConfigError("Codex auth source must be a regular file")
    source = raw_source.resolve()
    if not source.is_file():
        raise SandboxConfigError("Codex auth source must be a regular file")
    if source.stat().st_size > 10 * 1024 * 1024:
        raise SandboxConfigError("Codex auth source is unexpectedly large")
    target = destination.expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise SandboxConfigError("ephemeral CODEX_HOME must be absent or empty")
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    auth_target = target / "auth.json"
    auth_target.write_bytes(source.read_bytes())
    auth_target.chmod(0o600)
    return target


def _existing(path: Path) -> str | None:
    resolved = path.expanduser().resolve()
    return str(resolved) if resolved.exists() else None


def safe_read_roots(
    environment: Mapping[str, str] | None = None,
    *,
    include_dependency_caches: bool = False,
    include_browser: bool = False,
) -> tuple[str, ...]:
    """Return narrowly scoped tool roots, with caches opt-in for trusted setup only."""
    source = os.environ if environment is None else environment
    home = Path(source.get("HOME", "~")).expanduser()
    candidates = [
        Path("/opt/homebrew/bin"),
        Path("/opt/homebrew/Cellar"),
        Path("/opt/homebrew/lib"),
        Path("/opt/homebrew/opt"),
        Path("/opt/homebrew/share"),
        Path("/opt/homebrew/etc/openssl@3/openssl.cnf"),
        Path("/System/Library/CoreServices/SystemAppearance.bundle"),
        home / ".bun" / "bin",
        home / "Library" / "pnpm" / ".tools" / "pnpm",
    ]
    if include_dependency_caches:
        candidates.extend(
            [
                home / ".bun" / "install" / "cache",
                home / "go" / "pkg" / "mod",
                home / "Library" / "Caches" / "bun",
                home / "Library" / "Caches" / "pnpm",
                home / "Library" / "pnpm" / "store",
            ]
        )
    if include_browser:
        candidates.append(home / "Library" / "Caches" / "ms-playwright")
    for executable in ("bun", "go", "node", "pnpm", "python3", "git"):
        resolved = shutil.which(executable, path=source.get("PATH"))
        if not resolved:
            continue
        executable_path = Path(resolved).resolve()
        if executable_path.is_relative_to(home / ".bun" / "bin"):
            candidates.append(home / ".bun" / "bin")
        elif executable_path.is_relative_to(Path("/Library/Frameworks/Python.framework/Versions")):
            candidates.append(executable_path.parents[1])
    roots = sorted({value for candidate in candidates if (value := _existing(candidate))})
    return tuple(roots)


def _minimal_path(environment: Mapping[str, str]) -> str:
    directories: list[str] = []
    home = Path(environment.get("HOME", "~")).expanduser().resolve()
    allowed = (
        Path("/opt/homebrew/bin"),
        home / ".bun" / "bin",
        Path("/Library/Frameworks/Python.framework/Versions"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    )
    for executable in ("codex", "bun", "go", "node", "pnpm", "python3", "git"):
        value = shutil.which(executable, path=environment.get("PATH"))
        if value:
            directory_path = Path(value).expanduser().absolute().parent
            if not any(
                directory_path == root or directory_path.is_relative_to(root)
                for root in allowed
            ):
                continue
            directory = str(directory_path)
            if directory not in directories:
                directories.append(directory)
    for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if directory not in directories:
            directories.append(directory)
    return os.pathsep.join(directories)


def _dependency_roots(workspaces: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for workspace in workspaces:
        pending = [workspace.expanduser().resolve()]
        while pending:
            directory = pending.pop()
            try:
                children = list(directory.iterdir())
            except OSError as exc:
                raise SandboxConfigError(f"cannot inspect dependency roots: {directory}") from exc
            for child in children:
                if child.is_symlink() or not child.is_dir():
                    continue
                if child.name in {".git", "node_modules", "vendor"}:
                    if child.name != ".git":
                        roots.append(child.resolve())
                    continue
                pending.append(child)
    return tuple(sorted(set(roots)))


def shell_environment_values(
    scratch: Path,
    environment: Mapping[str, str] | None = None,
    *,
    include_dependency_caches: bool = False,
    include_browser: bool = False,
) -> dict[str, str]:
    source = os.environ if environment is None else environment
    runtime = prepare_scratch(scratch)
    values = {
        "PATH": _minimal_path(source),
        "HOME": str(runtime / "home"),
        "TMPDIR": str(runtime / "tmp"),
        "XDG_CACHE_HOME": str(runtime / "cache"),
        "BUN_INSTALL": str(runtime / "home" / ".bun"),
        "BUN_RUNTIME_TRANSPILER_CACHE_PATH": "0",
        "GOCACHE": str(runtime / "go-build"),
        "GOMODCACHE": str(runtime / "go-mod"),
        "GIT_OPTIONAL_LOCKS": "0",
        "NO_COLOR": "1",
        "CI": "1",
    }
    home = Path(source.get("HOME", "~")).expanduser()
    optional = {
        "PNPM_HOME": home / "Library" / "pnpm",
    }
    if include_dependency_caches:
        optional["GOMODCACHE"] = home / "go" / "pkg" / "mod"
        optional["BUN_INSTALL_CACHE_DIR"] = home / ".bun" / "install" / "cache"
    if include_browser:
        optional["PLAYWRIGHT_BROWSERS_PATH"] = home / "Library" / "Caches" / "ms-playwright"
    for key, path in optional.items():
        existing = _existing(path)
        if existing:
            values[key] = existing
    if include_dependency_caches:
        pnpm_store = _existing(home / "Library" / "pnpm" / "store")
        if pnpm_store:
            values["npm_config_store_dir"] = pnpm_store
    else:
        values["GOFLAGS"] = "-mod=vendor"
    if not values["PATH"]:
        raise SandboxConfigError("PATH is required for sandboxed commands")
    return values


def permission_profile_args(
    scratch: Path,
    *,
    extra_read_roots: tuple[Path, ...] = (),
    extra_write_roots: tuple[Path, ...] = (),
    allow_localhost: bool = False,
    select_profile: bool = False,
    protect_dependencies: bool = True,
    include_dependency_caches: bool = False,
    include_browser: bool = False,
    workspace_roots: tuple[Path, ...] = (),
    workspace_access: str = "write",
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    if workspace_access not in {"read", "write"}:
        raise SandboxConfigError("workspace_access must be read or write")
    runtime = prepare_scratch(scratch)
    workspace_rules = {".": workspace_access}
    if protect_dependencies:
        workspace_rules.update({"node_modules": "read", "vendor": "read"})
    filesystem: dict[str, Any] = {
        ":minimal": "read",
        ":workspace_roots": workspace_rules,
        str(runtime): "write",
    }
    for root in safe_read_roots(
        environment,
        include_dependency_caches=include_dependency_caches,
        include_browser=include_browser,
    ):
        filesystem[root] = "read"
    for root in extra_read_roots:
        filesystem[str(root.expanduser().resolve())] = "read"
    for root in extra_write_roots:
        filesystem[str(root.expanduser().resolve())] = "write"
    if protect_dependencies:
        for root in _dependency_roots(workspace_roots):
            filesystem[str(root)] = "read"

    if allow_localhost:
        network: dict[str, Any] = {
            "enabled": True,
            "allow_local_binding": True,
            "domains": {
                "localhost": "allow",
                "127.0.0.1": "allow",
                "::1": "allow",
            },
        }
    else:
        network = {"enabled": False}

    arguments = [
        "-c",
        f"default_permissions={_toml(PROFILE_NAME)}",
        "-c",
        f"permissions.{PROFILE_NAME}.description={_toml('Isolated Agent Flow evaluation')}",
        "-c",
        f"permissions.{PROFILE_NAME}.workspace_roots={_toml({str(path.expanduser().resolve()): True for path in workspace_roots})}",
        "-c",
        f"permissions.{PROFILE_NAME}.filesystem={_toml(filesystem)}",
        "-c",
        f"permissions.{PROFILE_NAME}.network={_toml(network)}",
    ]
    if select_profile:
        arguments.extend(["-P", PROFILE_NAME])
    return arguments


def shell_policy_args(
    scratch: Path,
    environment: Mapping[str, str] | None = None,
    *,
    include_dependency_caches: bool = False,
    include_browser: bool = False,
) -> list[str]:
    values = shell_environment_values(
        scratch,
        environment,
        include_dependency_caches=include_dependency_caches,
        include_browser=include_browser,
    )
    return [
        "-c",
        'shell_environment_policy.inherit="none"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=false",
        "-c",
        f"shell_environment_policy.set={_toml(values)}",
    ]


def sandbox_command(
    argv: list[str],
    cwd: Path,
    scratch: Path,
    workspaces: tuple[Path, ...],
    *,
    allow_localhost: bool,
    setup: bool = False,
    include_browser: bool = False,
    protected_paths: tuple[Path, ...] = (),
    writable_paths: tuple[Path, ...] = (),
    workspace_access: str = "write",
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise SandboxConfigError("sandbox command argv must contain non-empty strings")
    root = cwd.expanduser().resolve()
    return [
        "codex",
        "sandbox",
        *permission_profile_args(
            scratch,
            extra_read_roots=protected_paths,
            extra_write_roots=writable_paths,
            allow_localhost=allow_localhost,
            select_profile=True,
            protect_dependencies=not setup,
            include_dependency_caches=setup,
            include_browser=include_browser,
            workspace_roots=workspaces,
            workspace_access=workspace_access,
            environment=environment,
        ),
        "-C",
        str(root),
        "--",
        *argv,
    ]
