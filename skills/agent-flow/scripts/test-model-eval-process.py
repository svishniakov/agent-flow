#!/usr/bin/env python3
"""Tests for evaluation process-group cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from model_eval_process import run_process_group


def _assert_process_gone(pid: int) -> None:
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"child process survived evaluation command cleanup: {pid}")


def test_timeout_kills_children() -> None:
    script = (
        "import subprocess,time\n"
        "child=subprocess.Popen(['sleep','30'])\n"
        "print(child.pid,flush=True)\n"
        "time.sleep(30)\n"
    )
    try:
        run_process_group(
            [sys.executable, "-c", script],
            text=True,
            capture_output=True,
            timeout=0.2,
        )
    except subprocess.TimeoutExpired as exc:
        child_pid = int((exc.stdout or "").strip().splitlines()[0])
    else:
        raise AssertionError("timeout fixture completed unexpectedly")
    _assert_process_gone(child_pid)


def test_normal_parent_exit_kills_detached_work() -> None:
    script = (
        "import subprocess\n"
        "child=subprocess.Popen(['sleep','30'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "print(child.pid,flush=True)\n"
    )
    result = run_process_group(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=5,
    )
    _assert_process_gone(int(result.stdout.strip()))


def test_normal_parent_exit_force_kills_term_ignoring_child() -> None:
    child_script = (
        "import signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready',flush=True)\n"
        "time.sleep(30)\n"
    )
    script = (
        "import subprocess,sys\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_script!r}],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)\n"
        "assert child.stdout.readline().strip() == 'ready'\n"
        "print(child.pid,flush=True)\n"
        "child.stdout.close()\n"
    )
    result = run_process_group(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=5,
    )
    _assert_process_gone(int(result.stdout.strip()))


def test_timeout_does_not_wait_for_detached_pipe_holder() -> None:
    child_script = "import time; time.sleep(30)"
    script = (
        "import subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_script!r}],start_new_session=True)\n"
        "print(child.pid,flush=True)\n"
        "time.sleep(30)\n"
    )
    started = time.monotonic()
    child_pid = None
    try:
        run_process_group(
            [sys.executable, "-c", script],
            text=True,
            capture_output=True,
            timeout=0.2,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        child_pid = int((exc.stdout or "").strip().splitlines()[0])
        if elapsed > 1.5:
            raise AssertionError(
                f"timeout waited for a detached pipe holder: {elapsed:.2f}s"
            )
    else:
        raise AssertionError("detached pipe-holder fixture completed unexpectedly")
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_interrupt_kills_process_group() -> None:
    with tempfile.TemporaryDirectory(prefix="model-eval-interrupt-") as raw_root:
        child_pid_path = Path(raw_root) / "child.pid"
        script = (
            "import os,signal,subprocess,sys,time\n"
            "child=subprocess.Popen(['sleep','30'])\n"
            f"open({str(child_pid_path)!r},'w').write(str(child.pid))\n"
            "os.kill(os.getppid(),signal.SIGINT)\n"
            "time.sleep(30)\n"
        )
        try:
            run_process_group(
                [sys.executable, "-c", script],
                text=True,
                capture_output=True,
                timeout=5,
            )
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("interrupt fixture completed unexpectedly")
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        _assert_process_gone(child_pid)


def test_text_input_and_output_are_captured() -> None:
    result = run_process_group(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        input="fixture input",
        text=True,
        capture_output=True,
        timeout=5,
    )
    if result.stdout.strip() != "FIXTURE INPUT" or result.stderr != "":
        raise AssertionError("text input/output capture does not match subprocess.run")


def main() -> int:
    if os.name == "posix":
        test_timeout_kills_children()
        test_normal_parent_exit_kills_detached_work()
        test_normal_parent_exit_force_kills_term_ignoring_child()
        test_timeout_does_not_wait_for_detached_pipe_holder()
        test_interrupt_kills_process_group()
        test_text_input_and_output_are_captured()
    print("PASS model eval process-group tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
