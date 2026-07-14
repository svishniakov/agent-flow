#!/usr/bin/env python3
"""Fixture tests for isolated model-evaluation workspaces."""

from __future__ import annotations

import errno
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import replace
from pathlib import Path

import model_eval_workspace
from model_eval_workspace import (
    WorkspaceError,
    apply_gold_patch,
    assert_repository_state,
    cleanup_synthetic_workspace,
    create_synthetic_workspace,
    extract_archive_safely,
    initialize_temp_root,
    snapshot_repository_state,
)


def run(
    command: list[str],
    cwd: Path,
    check: bool = True,
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
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


def create_repository(root: Path, filter_name: str | None = None) -> tuple[str, str]:
    root.mkdir()
    run(["git", "init", "--quiet"], root)
    run(["git", "config", "user.name", "Agent Flow Test"], root)
    run(["git", "config", "user.email", "agent-flow@example.invalid"], root)
    (root / "src").mkdir()
    (root / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env.build\n", encoding="utf-8")
    (root / ".env.build").write_text("tracked build fixture\n", encoding="utf-8")
    tracked = [".gitignore", "src/value.txt"]
    if filter_name is not None:
        (root / ".gitattributes").write_text(
            f"src/value.txt filter={filter_name}\n",
            encoding="utf-8",
        )
        tracked.append(".gitattributes")
    run(["git", "add", *tracked], root)
    run(["git", "add", "-f", ".env.build"], root)
    run(["git", "commit", "-qm", "base"], root)
    base = run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    (root / "src" / "value.txt").write_text("gold\n", encoding="utf-8")
    run(["git", "commit", "-qam", "gold"], root)
    gold = run(["git", "rev-parse", "HEAD"], root).stdout.strip()
    (root / "local-note.txt").write_text("pre-existing dirty file\n", encoding="utf-8")
    return base, gold


def expect_workspace_error(name: str, action, needle: str) -> None:
    try:
        action()
    except WorkspaceError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"{name}: expected WorkspaceError")
    if needle not in message:
        raise AssertionError(f"{name}: expected {needle!r} in {message!r}")


def test_synthetic_repository_hides_later_commit(root: Path) -> None:
    source = root / "source"
    base, gold = create_repository(source)
    source_state = snapshot_repository_state(source)
    temp_root = initialize_temp_root(root / "eval-root", "suite-one")
    workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "suite-one",
        "cell-one",
        "worker",
    )
    if (workspace.path / "src" / "value.txt").read_text(encoding="utf-8") != "base\n":
        raise AssertionError("workspace did not contain the base snapshot")
    tracked_ignored = run(
        ["git", "ls-files", "--error-unmatch", ".env.build"],
        workspace.path,
        check=False,
    )
    if tracked_ignored.returncode != 0:
        raise AssertionError("synthetic baseline dropped a tracked ignored file")
    commit_count = run(["git", "rev-list", "--count", "HEAD"], workspace.path).stdout.strip()
    if commit_count != "1":
        raise AssertionError("synthetic repository must have one commit")
    if run(["git", "cat-file", "-e", f"{gold}^{{commit}}"], workspace.path, check=False).returncode == 0:
        raise AssertionError("gold commit leaked into synthetic repository")
    if run(["git", "remote"], workspace.path).stdout.strip():
        raise AssertionError("synthetic repository must not have remotes")
    metadata = json.loads(workspace.metadata_path.read_text(encoding="utf-8"))
    if str(source) in json.dumps(metadata):
        raise AssertionError("source repository path leaked into metadata")
    assert_repository_state(source, source_state)
    cleanup_synthetic_workspace(workspace)
    if workspace.path.exists():
        raise AssertionError("workspace cleanup failed")
    assert_repository_state(source, source_state)


def test_gold_patch_applies_only_to_synthetic_workspace(root: Path) -> None:
    source = root / "source-gold-patch"
    base, gold = create_repository(source)
    source_state = snapshot_repository_state(source)
    patch = root / "gold.patch"
    patch.write_bytes(
        subprocess.run(
            ["git", "diff", "--binary", base, gold],
            cwd=source,
            capture_output=True,
            check=True,
        ).stdout
    )
    temp_root = initialize_temp_root(root / "eval-root-gold-patch", "suite-gold-patch")
    workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "suite-gold-patch",
        "cell-gold-patch",
        "worker",
    )
    apply_gold_patch(workspace, patch)
    if (workspace.path / "src" / "value.txt").read_text(encoding="utf-8") != "gold\n":
        raise AssertionError("gold patch was not applied to the synthetic workspace")
    if run(["git", "diff", "--quiet"], workspace.path, check=False).returncode != 1:
        raise AssertionError("gold patch must remain an uncommitted workspace change")
    assert_repository_state(source, source_state)
    cleanup_synthetic_workspace(workspace)
    assert_repository_state(source, source_state)


def test_cleanup_rejects_tampered_ownership(root: Path) -> None:
    source = root / "source-tamper"
    base, _ = create_repository(source)
    temp_root = initialize_temp_root(root / "eval-root-tamper", "suite-two")
    workspace = create_synthetic_workspace(
        source,
        base,
        "fixture",
        temp_root,
        "suite-two",
        "cell-two",
        "worker",
    )
    tampered = replace(workspace, path=source)
    expect_workspace_error(
        "source cleanup",
        lambda: cleanup_synthetic_workspace(tampered),
        "escapes the runner temporary root",
    )
    metadata = json.loads(workspace.metadata_path.read_text(encoding="utf-8"))
    metadata["lane_id"] = "other"
    workspace.metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    expect_workspace_error(
        "metadata mismatch",
        lambda: cleanup_synthetic_workspace(workspace),
        "does not match cleanup request",
    )


def write_tar(path: Path, name: str, content: bytes = b"bad") -> None:
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))


def test_archive_extraction_rejects_traversal(root: Path) -> None:
    archive = root / "unsafe.tar"
    write_tar(archive, "../escape.txt")
    destination = root / "unsafe-destination"
    expect_workspace_error(
        "archive traversal",
        lambda: extract_archive_safely(archive, destination),
        "escapes workspace",
    )
    if (root / "escape.txt").exists():
        raise AssertionError("archive traversal wrote outside destination")

    symlink_archive = root / "unsafe-symlink.tar"
    with tarfile.open(symlink_archive, "w") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        tar.addfile(info)
    expect_workspace_error(
        "symlink traversal",
        lambda: extract_archive_safely(symlink_archive, root / "symlink-destination"),
        "symlink target escapes workspace",
    )


def test_owned_cleanup_retries_transient_directory_race(root: Path) -> None:
    target = root / "transient-cleanup"
    target.mkdir()
    (target / "value.txt").write_text("value\n", encoding="utf-8")
    original = model_eval_workspace.shutil.rmtree
    calls = 0

    def transient_rmtree(path: Path, *args, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.ENOTEMPTY, "simulated concurrent git cleanup")
        original(path, *args, **kwargs)

    model_eval_workspace.shutil.rmtree = transient_rmtree
    try:
        model_eval_workspace._remove_owned_tree(target)
    finally:
        model_eval_workspace.shutil.rmtree = original
    if target.exists() or calls != 2:
        raise AssertionError("owned cleanup did not recover from a transient directory race")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_git_operations_ignore_host_config_hooks_and_filters(root: Path) -> None:
    filter_name = "agent-flow-evil"
    filter_script = root / "hostile-clean-filter"
    filter_marker = Path(f"{filter_script}.ran")
    write_executable(
        filter_script,
        '#!/bin/sh\nprintf "filter ran\\n" >> "$0.ran"\nprintf "FILTERED\\n"\n',
    )
    hooks = root / "hostile-hooks"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    hook_marker = Path(f"{pre_commit}.ran")
    write_executable(
        pre_commit,
        '#!/bin/sh\nprintf "hook ran\\n" >> "$0.ran"\n',
    )

    hostile_config = root / "hostile-global.gitconfig"
    run(
        ["git", "config", "--file", str(hostile_config), "core.hooksPath", str(hooks)],
        root,
    )
    run(
        [
            "git",
            "config",
            "--file",
            str(hostile_config),
            f"filter.{filter_name}.clean",
            shlex.quote(str(filter_script)),
        ],
        root,
    )
    run(
        [
            "git",
            "config",
            "--file",
            str(hostile_config),
            f"filter.{filter_name}.required",
            "true",
        ],
        root,
    )
    hostile_env = os.environ.copy()
    hostile_env.update(
        {
            "GIT_CONFIG_GLOBAL": str(hostile_config),
            "GIT_CONFIG_SYSTEM": str(hostile_config),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(hooks),
            "GIT_DIR": str(root / "wrong-git-dir"),
            "GIT_INDEX_FILE": str(root / "wrong-index"),
            "OPENAI_API_KEY": "must-not-reach-git",
        }
    )

    probe = root / "hostile-control"
    probe.mkdir()
    control_env = hostile_env.copy()
    control_env.pop("GIT_DIR")
    control_env.pop("GIT_INDEX_FILE")
    run(["git", "init", "--quiet"], probe, env=control_env)
    run(["git", "config", "user.name", "Hostile Control"], probe, env=control_env)
    run(["git", "config", "user.email", "hostile@example.invalid"], probe, env=control_env)
    (probe / ".gitattributes").write_text(
        f"value.txt filter={filter_name}\n",
        encoding="utf-8",
    )
    (probe / "value.txt").write_text("ORIGINAL\n", encoding="utf-8")
    run(["git", "add", "."], probe, env=control_env)
    run(["git", "commit", "-qm", "hostile control"], probe, env=control_env)
    if not filter_marker.exists() or not hook_marker.exists():
        raise AssertionError("hostile Git fixture did not execute in the unisolated control")
    filter_marker.unlink()
    hook_marker.unlink()
    shutil.rmtree(probe)

    source = root / "source-hostile-config"
    base, _ = create_repository(source, filter_name=filter_name)
    included_config = source / ".git" / "hostile-include.gitconfig"
    run(
        [
            "git",
            "config",
            "--file",
            str(included_config),
            f"filter.{filter_name}.clean",
            shlex.quote(str(filter_script)),
        ],
        source,
    )
    run(
        [
            "git",
            "config",
            "--file",
            str(included_config),
            f"filter.{filter_name}.required",
            "true",
        ],
        source,
    )
    run(["git", "config", "--local", "include.path", str(included_config)], source)
    value_path = source / "src" / "value.txt"
    value_stat = value_path.stat()
    os.utime(
        value_path,
        ns=(value_stat.st_atime_ns, value_stat.st_mtime_ns + 1_000_000_000),
    )
    run(["git", "status", "--porcelain=v1"], source, env=control_env)
    if not filter_marker.exists():
        raise AssertionError("source clean-filter fixture did not execute without isolation")
    filter_marker.unlink()
    value_stat = value_path.stat()
    os.utime(
        value_path,
        ns=(value_stat.st_atime_ns, value_stat.st_mtime_ns + 1_000_000_000),
    )

    original_env = os.environ.copy()
    workspace = None
    os.environ.clear()
    os.environ.update(hostile_env)
    try:
        isolated_env = model_eval_workspace._minimal_git_environment()
        forbidden = {"GIT_DIR", "GIT_INDEX_FILE", "OPENAI_API_KEY"}
        if forbidden.intersection(isolated_env):
            raise AssertionError("minimal Git environment retained host state or secrets")
        if isolated_env.get("GIT_CONFIG_GLOBAL") != os.devnull:
            raise AssertionError("minimal Git environment did not suppress global config")
        if isolated_env.get("GIT_CONFIG_SYSTEM") != os.devnull:
            raise AssertionError("minimal Git environment did not suppress system config")
        if isolated_env.get("GIT_CONFIG_KEY_0") != "core.hooksPath":
            raise AssertionError("minimal Git environment did not replace host command config")
        if isolated_env.get("GIT_CONFIG_VALUE_0") != os.devnull:
            raise AssertionError("minimal Git environment retained a hostile hook path")
        source_state = snapshot_repository_state(source)
        temp_root = initialize_temp_root(root / "eval-root-hostile", "suite-hostile")
        workspace = create_synthetic_workspace(
            source,
            base,
            "fixture",
            temp_root,
            "suite-hostile",
            "cell-hostile",
            "worker",
        )
        assert_repository_state(source, source_state)
    finally:
        os.environ.clear()
        os.environ.update(original_env)

    if workspace is None:
        raise AssertionError("synthetic workspace was not created")
    if filter_marker.exists() or hook_marker.exists():
        raise AssertionError("isolated Git operation executed a host hook or clean filter")
    source_tree = run(["git", "rev-parse", f"{base}^{{tree}}"], source).stdout.strip()
    synthetic_tree = run(["git", "rev-parse", "HEAD^{tree}"], workspace.path).stdout.strip()
    if synthetic_tree != source_tree:
        raise AssertionError("synthetic baseline did not preserve the exact source tree")
    if (workspace.path / "src" / "value.txt").read_text(encoding="utf-8") != "base\n":
        raise AssertionError("clean filter changed the synthetic working tree")
    cleanup_synthetic_workspace(workspace)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-workspace-") as raw_root:
        root = Path(raw_root)
        test_synthetic_repository_hides_later_commit(root)
        test_gold_patch_applies_only_to_synthetic_workspace(root)
        test_cleanup_rejects_tampered_ownership(root)
        test_archive_extraction_rejects_traversal(root)
        test_owned_cleanup_retries_transient_directory_race(root)
        test_git_operations_ignore_host_config_hooks_and_filters(root)
    print("PASS model eval workspace fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
