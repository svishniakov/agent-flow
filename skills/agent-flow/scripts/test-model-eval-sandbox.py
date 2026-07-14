#!/usr/bin/env python3
"""Tests for least-privilege model-evaluation permission profiles."""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from model_eval_sandbox import (
    permission_profile_args,
    sanitized_process_environment,
    sandbox_command,
    shell_environment_values,
)


def test_environment_is_sanitized(root: Path) -> None:
    hostile = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root / "host-home"),
        "CODEX_HOME": str(root / "host-codex"),
        "OPENAI_API_KEY": "secret",
        "EVAL_REPO_OMNIPULSE": "/private/source",
        "SERVICE_TOKEN": "token",
        "BUN_INSTALL": "/private/source",
        "GOMODCACHE": "/private/source",
    }
    environment = sanitized_process_environment(hostile)
    for key in ("OPENAI_API_KEY", "EVAL_REPO_OMNIPULSE", "SERVICE_TOKEN", "BUN_INSTALL"):
        if key in environment:
            raise AssertionError(f"unsafe process environment key survived: {key}")

    scratch = root / "scratch-environment"
    shell_environment = shell_environment_values(scratch, hostile)
    if shell_environment["HOME"] != str((scratch / "home").resolve()):
        raise AssertionError("sandboxed HOME is not isolated")
    if shell_environment["BUN_INSTALL"] != str((scratch / "home" / ".bun").resolve()):
        raise AssertionError("sandboxed BUN_INSTALL is not isolated")
    if shell_environment["BUN_RUNTIME_TRANSPILER_CACHE_PATH"] != "0":
        raise AssertionError("Bun runtime cache was not disabled")
    if "/private/source" in shell_environment.values():
        raise AssertionError("host-controlled dependency path leaked into the sandbox")


def test_profile_shape(root: Path) -> None:
    offline = " ".join(permission_profile_args(root / "offline"))
    required = (
        '":minimal"="read"',
        '"."="write"',
        '"node_modules"="read"',
        '"vendor"="read"',
    )
    if any(value not in offline for value in required):
        raise AssertionError("filesystem profile is not least privilege")
    for unsafe_root in ('"/opt/homebrew/etc"', '"/opt/homebrew/var"'):
        if unsafe_root in offline:
            raise AssertionError(f"permission profile exposed unsafe host root {unsafe_root}")
    if 'network={"enabled"=false}' not in offline:
        raise AssertionError("offline profile does not disable network")

    localhost = " ".join(permission_profile_args(root / "localhost", allow_localhost=True))
    for value in ('"localhost"="allow"', '"127.0.0.1"="allow"', '"::1"="allow"'):
        if value not in localhost:
            raise AssertionError(f"localhost profile is missing {value}")


def _sandbox_environment(scratch: Path) -> dict[str, str]:
    environment = sanitized_process_environment()
    environment.update(shell_environment_values(scratch))
    codex_home = scratch / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    environment["CODEX_HOME"] = str(codex_home)
    return environment


def _run_probe(command: list[str], scratch: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=_sandbox_environment(scratch),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_real_sandbox(root: Path) -> None:
    if shutil.which("codex") is None:
        return

    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside\n", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "dependency.txt").write_text("dependency\n", encoding="utf-8")
    denied = root / "denied.txt"
    denied.write_text("outside\n", encoding="utf-8")

    offline_scratch = root / "offline-scratch"
    probe = (
        "from pathlib import Path\n"
        "import socket\n"
        "import subprocess\n"
        "assert Path('inside.txt').read_text() == 'inside\\n'\n"
        "Path('written.txt').write_text('written\\n')\n"
        "subprocess.run(['python3', '-c', 'print(\"child-ok\")'], check=True)\n"
        "try:\n"
        "    Path('node_modules/dependency.txt').write_text('changed\\n')\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('dependency write was allowed')\n"
        f"denied = Path({str(denied)!r})\n"
        "try:\n"
        "    denied.read_text()\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('outside read was allowed')\n"
        "sock = socket.socket()\n"
        "sock.settimeout(1)\n"
        "try:\n"
        "    sock.connect(('1.1.1.1', 53))\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('external network was allowed')\n"
    )
    command = sandbox_command(
        ["python3", "-c", probe],
        workspace,
        offline_scratch,
        (workspace,),
        allow_localhost=False,
    )
    result = _run_probe(command, offline_scratch)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    if (workspace / "written.txt").read_text(encoding="utf-8") != "written\n":
        raise AssertionError("workspace write was denied")

    protected_directory = workspace / "protected"
    protected_directory.mkdir()
    protected = protected_directory / "hidden.py"
    protected.write_text("protected\n", encoding="utf-8")
    writable_output = workspace / "agent_flow_eval_output" / "probe"
    writable_output.mkdir(parents=True)
    protected_scratch = root / "protected-scratch"
    protected_probe = (
        "import os\n"
        "from pathlib import Path\n"
        "target = Path('protected/hidden.py')\n"
        "assert target.read_text() == 'protected\\n'\n"
        "operations = [\n"
        "    lambda: target.write_text('changed\\n'),\n"
        "    lambda: target.unlink(),\n"
        "    lambda: target.rename('protected/moved.py'),\n"
        "    lambda: os.link(target, 'protected/alias.py'),\n"
        "    lambda: Path('protected').rename('moved'),\n"
        "]\n"
        "for operation in operations:\n"
        "    try:\n"
        "        operation()\n"
        "    except OSError:\n"
        "        pass\n"
        "    else:\n"
        "        raise SystemExit('read-only evaluator workspace was mutable')\n"
        "try:\n"
        "    os.link(target, 'agent_flow_eval_output/probe/hardlink.py')\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('hidden evaluator file was hard-linked into writable output')\n"
        "os.symlink('../../protected/hidden.py', 'agent_flow_eval_output/probe/link.py')\n"
        "try:\n"
        "    Path('agent_flow_eval_output/probe/link.py').write_text('changed\\n')\n"
        "except OSError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('hidden evaluator file changed through writable symlink')\n"
        "Path('agent_flow_eval_output/probe/result.txt').write_text('result\\n')\n"
    )
    protected_command = sandbox_command(
        ["python3", "-c", protected_probe],
        workspace,
        protected_scratch,
        (workspace,),
        allow_localhost=False,
        writable_paths=(writable_output,),
        workspace_access="read",
    )
    protected_result = _run_probe(protected_command, protected_scratch)
    if protected_result.returncode:
        raise AssertionError(protected_result.stderr or protected_result.stdout)
    if protected.read_text(encoding="utf-8") != "protected\n":
        raise AssertionError("read-only evaluator file changed through a rename bypass")
    if (writable_output / "result.txt").read_text(encoding="utf-8") != "result\n":
        raise AssertionError("declared evaluator output directory was not writable")

    if shutil.which("pnpm") is not None:
        (workspace / "package.json").write_text(
            '{"packageManager":"pnpm@9.7.1"}\n',
            encoding="utf-8",
        )
        pnpm_scratch = root / "pnpm-scratch"
        pnpm_command = sandbox_command(
            ["pnpm", "--version"],
            workspace,
            pnpm_scratch,
            (workspace,),
            allow_localhost=False,
        )
        pnpm_result = _run_probe(pnpm_command, pnpm_scratch)
        if pnpm_result.returncode or pnpm_result.stdout.strip() != "9.7.1":
            raise AssertionError(pnpm_result.stderr or pnpm_result.stdout)

    browsers = sorted(
        (Path.home() / "Library" / "Caches" / "ms-playwright").glob(
            "chromium_headless_shell-*/chrome-headless-shell-mac-arm64/chrome-headless-shell"
        )
    )
    if browsers:
        browser_scratch = root / "browser-scratch"
        browser_command = sandbox_command(
            [
                str(browsers[-1]),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
                "--dump-dom",
                "data:text/html,ok",
            ],
            workspace,
            browser_scratch,
            (workspace,),
            allow_localhost=True,
            include_browser=True,
        )
        browser_result = _run_probe(browser_command, browser_scratch)
        if browser_result.returncode or "<body>ok</body>" not in browser_result.stdout:
            raise AssertionError(browser_result.stderr or browser_result.stdout)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        denied_localhost_scratch = root / "denied-localhost-scratch"
        denied_localhost_probe = (
            "import socket\n"
            "sock = socket.socket()\n"
            "sock.settimeout(1)\n"
            "try:\n"
            f"    sock.connect(('127.0.0.1', {port}))\n"
            "except OSError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('localhost was allowed by the offline profile')\n"
        )
        denied_localhost_command = sandbox_command(
            ["python3", "-c", denied_localhost_probe],
            workspace,
            denied_localhost_scratch,
            (workspace,),
            allow_localhost=False,
        )
        denied_localhost_result = _run_probe(
            denied_localhost_command,
            denied_localhost_scratch,
        )
        if denied_localhost_result.returncode:
            raise AssertionError(denied_localhost_result.stderr or denied_localhost_result.stdout)

        localhost_scratch = root / "localhost-scratch"
        localhost_probe = (
            "import socket\n"
            "sock = socket.socket()\n"
            "sock.settimeout(2)\n"
            f"sock.connect(('127.0.0.1', {port}))\n"
        )
        localhost_command = sandbox_command(
            ["python3", "-c", localhost_probe],
            workspace,
            localhost_scratch,
            (workspace,),
            allow_localhost=True,
        )
        localhost_result = _run_probe(localhost_command, localhost_scratch)
        if localhost_result.returncode:
            raise AssertionError(localhost_result.stderr or localhost_result.stdout)
    finally:
        listener.close()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-sandbox-") as raw_root:
        root = Path(raw_root)
        test_environment_is_sanitized(root)
        test_profile_shape(root)
        test_real_sandbox(root)
    print("PASS model eval sandbox tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
