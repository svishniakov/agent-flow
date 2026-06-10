#!/usr/bin/env python3
"""Safely update an installed Agent Flow skill checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitError(RuntimeError):
    def __init__(self, message: str, returncode: int = 1) -> None:
        super().__init__(message)
        self.returncode = returncode


def run_git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        command = "git " + " ".join(args)
        raise GitError(f"{command} failed: {detail}", result.returncode)
    return result


def short_sha(repo: Path, ref: str) -> str:
    return run_git(repo, ["rev-parse", "--short", ref]).stdout.strip()


def full_sha(repo: Path, ref: str) -> str:
    return run_git(repo, ["rev-parse", ref]).stdout.strip()


def discover_repo(path: Path) -> Path:
    if not path.exists():
        raise GitError(f"target not found: {path}")
    result = run_git(path, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode:
        raise GitError(f"target is not a git checkout: {path}")
    return Path(result.stdout.strip()).resolve()


def current_branch(repo: Path) -> str:
    branch = run_git(repo, ["branch", "--show-current"]).stdout.strip()
    if not branch:
        raise GitError("target is in detached HEAD; pass --branch explicitly")
    return branch


def dirty_status(repo: Path) -> str:
    return run_git(repo, ["status", "--porcelain"]).stdout


def is_ancestor(repo: Path, older_ref: str, newer_ref: str) -> bool:
    result = run_git(repo, ["merge-base", "--is-ancestor", older_ref, newer_ref], check=False)
    return result.returncode == 0


def run_check(repo: Path) -> int:
    check_script = repo / "scripts" / "check-all.py"
    if not check_script.exists():
        print(f"skip check: {check_script} not found")
        return 0
    result = subprocess.run([sys.executable, str(check_script)], cwd=repo, text=True, check=False)
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=ROOT, help="Installed Agent Flow checkout to update.")
    parser.add_argument("--remote", default="origin", help="Git remote to fetch from.")
    parser.add_argument("--branch", help="Remote branch to track. Defaults to current local branch.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and report what would happen without changing files.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Discard local uncommitted changes and reset divergent local commits to the remote branch.",
    )
    parser.add_argument("--skip-check", action="store_true", help="Skip scripts/check-all.py after a real update.")
    return parser.parse_args()


def describe_plan(repo: Path, remote_ref: str, dirty: str, relation: str, dry_run: bool) -> None:
    prefix = "DRY RUN" if dry_run else "PLAN"
    print(f"{prefix}: target={repo}")
    print(f"{prefix}: remote={remote_ref}")
    if dirty:
        print(f"{prefix}: local changes detected")
    print(f"{prefix}: relation={relation}")


def update_checkout(args: argparse.Namespace) -> int:
    repo = discover_repo(args.target.expanduser().resolve())
    branch = args.branch or current_branch(repo)
    remote_ref = f"{args.remote}/{branch}"

    print(f"fetching {args.remote}...")
    run_git(repo, ["fetch", "--prune", args.remote])
    run_git(repo, ["rev-parse", "--verify", remote_ref])

    local_ref = "HEAD"
    local_sha = full_sha(repo, local_ref)
    remote_sha = full_sha(repo, remote_ref)
    dirty = dirty_status(repo)

    if local_sha == remote_sha:
        relation = "up-to-date"
    elif is_ancestor(repo, local_ref, remote_ref):
        relation = f"fast-forward {short_sha(repo, local_ref)}..{short_sha(repo, remote_ref)}"
    elif is_ancestor(repo, remote_ref, local_ref):
        relation = f"local-ahead {short_sha(repo, remote_ref)}..{short_sha(repo, local_ref)}"
    else:
        relation = f"diverged local={short_sha(repo, local_ref)} remote={short_sha(repo, remote_ref)}"

    describe_plan(repo, remote_ref, dirty, relation, args.dry_run)

    if args.dry_run:
        if dirty or relation.startswith(("local-ahead", "diverged")):
            print("DRY RUN: real update would require --overwrite.")
        elif relation == "up-to-date":
            print("DRY RUN: no update needed.")
        else:
            print("DRY RUN: real update would fast-forward.")
        return 0

    unsafe = bool(dirty) or relation.startswith(("local-ahead", "diverged"))
    if unsafe and not args.overwrite:
        print("blocked: local changes or divergent commits detected; rerun with --overwrite to discard them.", file=sys.stderr)
        return 2

    if relation == "up-to-date" and not dirty:
        print("already up to date")
    elif args.overwrite:
        run_git(repo, ["reset", "--hard", remote_ref])
        run_git(repo, ["clean", "-fd"])
        print(f"reset to {remote_ref}")
    else:
        run_git(repo, ["merge", "--ff-only", remote_ref])
        print(f"updated to {remote_ref}")

    if args.skip_check:
        return 0

    return run_check(repo)


def main() -> int:
    args = parse_args()
    try:
        return update_checkout(args)
    except GitError as exc:
        print(exc, file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
