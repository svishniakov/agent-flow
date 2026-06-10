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
    path.write_text(text, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", message)


def run_updater(target: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        [sys.executable, str(UPDATER), "--target", str(target), "--branch", "main", "--skip-check", *args],
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
    commit_file(source, "README.md", "v1\n", "initial")
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

        run_updater(installed)
        assert_text(installed / "README.md", "v2\n")

        (installed / "README.md").write_text("dirty\n", encoding="utf-8")
        commit_file(source, "README.md", "v3\n", "update v3")
        git(source, "push", "origin", "main")

        blocked = run_updater(installed, check=False)
        if blocked.returncode != 2 or "blocked:" not in blocked.stderr:
            raise AssertionError(blocked.stdout + blocked.stderr)
        assert_text(installed / "README.md", "dirty\n")

        run_updater(installed, "--overwrite")
        assert_text(installed / "README.md", "v3\n")

    print("PASS update-agent-flow-skill fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
