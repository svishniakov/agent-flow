#!/usr/bin/env python3
"""Fixture tests for update-agent-flow-skill.py."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "update-agent-flow-skill.py"


def run(command: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode:
        output = (result.stdout + result.stderr).strip()
        raise AssertionError(f"{' '.join(command)} failed with {result.returncode}:\n{output}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check)


def commit_file(repo: Path, name: str, text: str, message: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", message)


def run_updater(
    target: Path,
    *args: str,
    check: bool = True,
    skip_check: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(UPDATER), "--target", str(target), "--branch", "main"]
    if skip_check:
        command.append("--skip-check")
    command.extend(args)
    return run(
        command,
        check=check,
    )


def prepare_remote(root: Path) -> tuple[Path, Path, Path]:
    remote = root / "origin.git"
    source = root / "source"
    installed = root / "installed"

    run(["git", "init", "--bare", str(remote)])
    run(["git", "init", "--initial-branch=main", str(source)])
    git(source, "config", "user.email", "agent-flow-test@example.com")
    git(source, "config", "user.name", "Agent Flow Test")
    commit_file(source, "README.md", "v1\n", "initial readme")
    commit_file(
        source,
        "scripts/check-agent-deps.py",
        "print('INSTALL_HEALTH_CHECK')\n",
        "add install health check fixture",
    )
    commit_file(
        source,
        "scripts/check-all.py",
        "print('FULL_CHECK')\n",
        "add full check fixture",
    )
    git(source, "remote", "add", "origin", str(remote))
    git(source, "push", "-u", "origin", "main")
    run(["git", "clone", str(remote), str(installed)])
    return remote, source, installed


def assert_text(path: Path, expected: str) -> None:
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        raise AssertionError(f"{path} expected {expected!r}, got {actual!r}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-updater-test-") as raw_dir:
        _, source, installed = prepare_remote(Path(raw_dir))

        first = run_updater(installed, "--dry-run")
        if "no update needed" not in first.stdout:
            raise AssertionError(first.stdout)

        commit_file(source, "README.md", "v2\n", "update v2")
        git(source, "push", "origin", "main")

        dry_run = run_updater(installed, "--dry-run")
        if "fast-forward" not in dry_run.stdout:
            raise AssertionError(dry_run.stdout)
        assert_text(installed / "README.md", "v1\n")

        update = run_updater(installed)
        if "INSTALL_HEALTH_CHECK" not in update.stdout or "FULL_CHECK" in update.stdout:
            raise AssertionError(update.stdout)
        assert_text(installed / "README.md", "v2\n")

        full_check = run_updater(installed, "--full-check")
        if "FULL_CHECK" not in full_check.stdout or "INSTALL_HEALTH_CHECK" in full_check.stdout:
            raise AssertionError(full_check.stdout)

        skipped = run_updater(installed, "--skip-check", skip_check=False)
        if "INSTALL_HEALTH_CHECK" in skipped.stdout or "FULL_CHECK" in skipped.stdout:
            raise AssertionError(skipped.stdout)

        (installed / "README.md").write_text("dirty\n", encoding="utf-8")
        commit_file(source, "README.md", "v3\n", "update v3")
        git(source, "push", "origin", "main")

        blocked = run_updater(installed, check=False)
        if blocked.returncode != 2 or "blocked:" not in blocked.stderr:
            raise AssertionError(blocked.stdout + blocked.stderr)
        assert_text(installed / "README.md", "dirty\n")

        overwritten = run_updater(installed, "--overwrite")
        if "INSTALL_HEALTH_CHECK" not in overwritten.stdout:
            raise AssertionError(overwritten.stdout)
        assert_text(installed / "README.md", "v3\n")

    print("PASS update-agent-flow-skill fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
