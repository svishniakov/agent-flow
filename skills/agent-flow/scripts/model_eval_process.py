#!/usr/bin/env python3
"""Run evaluation commands in disposable process groups."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from contextlib import ExitStack
from collections.abc import Sequence
from typing import Any


def _group_processes(process_group: int) -> list[int]:
    if os.name != "posix":
        return []
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,pgid=,uid="],
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return []
    user_id = os.getuid()
    processes: list[int] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, pgid, uid = (int(field) for field in fields)
        except ValueError:
            continue
        if pgid == process_group and uid == user_id and pid != os.getpid():
            processes.append(pid)
    return processes


def _terminate_group(process: subprocess.Popen[Any]) -> None:
    if os.name != "posix":
        if process.poll() is None:
            process.kill()
        return
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.05)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except (PermissionError, ProcessLookupError):
        pass
    for pid in _group_processes(process_group):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def run_process_group(
    command: Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Match subprocess.run while terminating the whole command process group."""
    if kwargs.pop("shell", False):
        raise ValueError("model evaluation commands must not use a shell")
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    input_value = kwargs.pop("input", None)
    capture_output = kwargs.pop("capture_output", False)
    if capture_output:
        if "stdout" in kwargs or "stderr" in kwargs:
            raise ValueError("capture_output cannot be combined with stdout or stderr")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input_value is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE

    text_mode = bool(
        kwargs.get("text")
        or kwargs.get("universal_newlines")
        or kwargs.get("encoding")
        or kwargs.get("errors")
    )
    encoding = kwargs.get("encoding") or "utf-8"
    errors = kwargs.get("errors") or "strict"

    def captured_value(stream: Any | None) -> str | bytes | None:
        if stream is None:
            return None
        stream.flush()
        stream.seek(0)
        value = stream.read()
        if text_mode:
            return value.decode(encoding, errors)
        return value

    with ExitStack() as stack:
        stdout_capture = None
        stderr_capture = None
        if kwargs.get("stdout") is subprocess.PIPE:
            stdout_capture = stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
            kwargs["stdout"] = stdout_capture
        if kwargs.get("stderr") is subprocess.PIPE:
            stderr_capture = stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
            kwargs["stderr"] = stderr_capture

        process = subprocess.Popen(
            list(command),
            start_new_session=os.name == "posix",
            **kwargs,
        )
        terminated = False
        try:
            process.communicate(input=input_value, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_group(process)
            terminated = True
            stdout = captured_value(stdout_capture)
            stderr = captured_value(stderr_capture)
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout if stdout is not None else exc.output,
                stderr=stderr if stderr is not None else exc.stderr,
            ) from exc
        except BaseException:
            _terminate_group(process)
            terminated = True
            raise
        finally:
            if not terminated and process.poll() is not None:
                _terminate_group(process)
        stdout = captured_value(stdout_capture)
        stderr = captured_value(stderr_capture)
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )
    return result
