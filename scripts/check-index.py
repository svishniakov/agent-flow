#!/usr/bin/env python3
"""Run check-all.py against an independent checkout of the Git index."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


class CheckError(RuntimeError):
    pass


class Interrupted(CheckError):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(f"interrupted by signal {signum}")


def interrupt(signum, frame):
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    raise Interrupted(signum)


def run(argv, *, cwd, env, data=None, output=None):
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=output if output is not None else subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(data)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            process.wait()
        finally:
            # The leader can exit before a child that ignores SIGTERM.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise
    if process.returncode:
        if stdout:
            sys.stdout.buffer.write(stdout)
        raise CheckError(f"{' '.join(argv[:3])}: exit {process.returncode}\n{os.fsdecode(stderr).strip()}")
    if stderr:
        sys.stderr.buffer.write(stderr)
    return stdout or b""


def isolated_environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", PYTHONDONTWRITEBYTECODE="1")
    return env


def git(cwd, env, *args, data=None, output=None):
    return run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
                *args], cwd=cwd, env=env, data=data, output=output)


def materialize(checkout, env, tree):
    entries = git(checkout, env, "ls-tree", "-rz", tree).split(b"\0")
    paths = []
    for entry in entries:
        if not entry:
            continue
        header, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = header.split()
        path = Path(os.fsdecode(raw_path))
        if kind != b"blob" or mode not in {b"100644", b"100755", b"120000"}:
            raise CheckError(f"unsupported staged entry: {path} ({os.fsdecode(mode)})")
        if path.is_absolute() or any(part in {".", ".."} or part.lower() == ".git" for part in path.parts):
            raise CheckError(f"unsafe staged path: {path}")
        paths.append((mode, oid, checkout / path))

    # Read raw blobs; checkout filters and export-ignore must not change the index.
    for mode, oid, path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(checkout) or path.exists() or path.is_symlink():
            raise CheckError(f"staged path collision or escaping symlink: {path.relative_to(checkout)}")
        content = git(checkout, env, "cat-file", "blob", os.fsdecode(oid))
        if mode == b"120000":
            target = os.fsdecode(content)
            if not (path.parent / target).resolve().is_relative_to(checkout):
                raise CheckError(f"staged symlink escapes checkout: {path.relative_to(checkout)}")
            path.symlink_to(target)
        else:
            path.write_bytes(content)
            path.chmod(0o755 if mode == b"100755" else 0o644)


def check_index():
    if os.name != "posix":
        raise CheckError("pre-commit requires macOS or Linux")
    if os.environ.get("AGENT_FLOW_PRE_COMMIT_RUNNING"):
        raise CheckError("recursive Agent Flow pre-commit invocation")
    source_env = os.environ.copy()
    source_env["GIT_OPTIONAL_LOCKS"] = "0"
    cwd = Path.cwd()
    source = Path(os.fsdecode(git(cwd, source_env, "rev-parse", "--show-toplevel")).strip()).resolve()
    index = Path(os.fsdecode(git(source, source_env, "rev-parse", "--path-format=absolute", "--git-path", "index")).strip())
    objects = os.fsdecode(git(source, source_env, "rev-parse", "--path-format=absolute", "--git-path", "objects")).strip()
    object_format = os.fsdecode(git(source, source_env, "rev-parse", "--show-object-format")).strip()
    original = index.read_bytes()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agent-flow-index-") as raw_temp:
        scratch = Path(raw_temp).resolve()
        copied_index = scratch / "index"
        copied_index.write_bytes(original)
        temporary_objects = scratch / "objects"
        temporary_objects.mkdir()
        source_env.update(GIT_INDEX_FILE=str(copied_index), GIT_OBJECT_DIRECTORY=str(temporary_objects),
                          GIT_ALTERNATE_OBJECT_DIRECTORIES=json.dumps(objects, ensure_ascii=False))
        tree = os.fsdecode(git(source, source_env, "write-tree")).strip()
        pack = scratch / "objects.pack"
        with pack.open("wb") as stream:
            git(source, source_env, "pack-objects", "--stdout", "--revs", data=(tree + "\n").encode(), output=stream)
        env = isolated_environment()
        checkout = scratch / "checkout"
        git(scratch, env, "init", "-q", "--template=", "--object-format=" + object_format, str(checkout))
        git(checkout, env, "config", "core.hooksPath", os.devnull)
        git(checkout, env, "unpack-objects", "-q", data=pack.read_bytes())
        materialize(checkout, env, tree)
        git(checkout, env, "read-tree", tree)
        identity = env | {"GIT_AUTHOR_NAME": "Agent Flow index check", "GIT_AUTHOR_EMAIL": "index@localhost",
                          "GIT_COMMITTER_NAME": "Agent Flow index check", "GIT_COMMITTER_EMAIL": "index@localhost"}
        commit = git(checkout, identity, "commit-tree", tree, data=b"Temporary index snapshot\n").strip()
        git(checkout, env, "update-ref", "HEAD", os.fsdecode(commit))
        if index.read_bytes() != original:
            raise CheckError("Git index changed while preparing the snapshot; retry the commit")
        checker = checkout / "scripts/check-all.py"
        if not checker.is_file() or checker.is_symlink():
            raise CheckError("staged scripts/check-all.py is missing or is a symlink")
        env["AGENT_FLOW_PRE_COMMIT_RUNNING"] = "1"
        print(f"Agent Flow: full suite for index tree {tree}\nTemporary checkout: {checkout}", flush=True)
        try:
            output = run([sys.executable, "-B", str(checker)], cwd=checkout, env=env)
            sys.stdout.buffer.write(output)
        finally:
            if index.read_bytes() != original:
                raise CheckError("Git index changed during checks; commit refused, concurrent edits preserved")
    print(f"PASS staged full suite ({time.monotonic() - started:.2f}s); temporary checkout removed", flush=True)


def main():
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        if len(sys.argv) != 1:
            raise CheckError("Usage: python3 scripts/check-index.py")
        check_index()
        return 0
    except Interrupted as exc:
        print(f"FAIL pre-commit: {exc}; temporary checkout removed", file=sys.stderr)
        return 128 + exc.signum
    except (CheckError, OSError) as exc:
        print(f"FAIL pre-commit: {exc}", file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
