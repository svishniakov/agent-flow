#!/usr/bin/env python3
"""Install the repository index checker in this clone without replacing hooks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Prepared Python executable (venv path is preserved)")
    parser.add_argument("--tool-bin", type=Path, help="Directory containing prepared tools")
    parser.add_argument("--browser", type=Path, help="Prepared browser executable")
    args = parser.parse_args()
    try:
        root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()).resolve()
        configured = subprocess.run(["git", "config", "--show-origin", "--get-all", "core.hooksPath"],
                                    cwd=root, capture_output=True, text=True)
        if configured.returncode != 1:
            raise RuntimeError("core.hooksPath already configured; preserved. Integrate scripts/check-index.py "
                               "into that hook explicitly before changing its configuration.\n" + configured.stdout + configured.stderr)
        hooks = Path(subprocess.check_output(["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
                                            cwd=root, text=True).strip())
        if hooks.is_symlink():
            raise RuntimeError("hooks directory is a symlink; preserved")
        python = Path(args.python).expanduser().absolute()
        if not python.is_file() or not os.access(python, os.X_OK):
            raise RuntimeError(f"Python executable unavailable: {python}")
        lines = ["#!/bin/sh", "# Agent Flow staged full-suite hook"]
        if args.tool_bin:
            tool_bin = args.tool_bin.expanduser().resolve()
            if not tool_bin.is_dir():
                raise RuntimeError(f"prepared tools directory unavailable: {tool_bin}")
            lines.extend([f"PATH={shlex.quote(str(tool_bin))}:\"$PATH\"", "export PATH"])
        if args.browser:
            browser = args.browser.expanduser().absolute()
            if not browser.is_file() or not os.access(browser, os.X_OK):
                raise RuntimeError(f"browser executable unavailable: {browser}")
            lines.extend([f"AGENT_FLOW_TEST_BROWSER={shlex.quote(str(browser))}", "export AGENT_FLOW_TEST_BROWSER"])
        runner = hooks / "agent-flow-check-index.py"
        lines.append(f"exec {shlex.quote(str(python))} {shlex.quote(str(runner))} \"$@\"")
        files = {runner: (root / "scripts/check-index.py").read_bytes(),
                 hooks / "pre-commit": ("\n".join(lines) + "\n").encode()}
        for path, content in files.items():
            if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != content)):
                raise RuntimeError(f"existing hook file preserved: {path}. Integrate the index checker into "
                                   "your existing hook explicitly; no files or Git settings were changed")
            if path.exists() and not os.access(path, os.X_OK):
                raise RuntimeError(f"existing non-executable hook preserved: {path}")
        hooks.mkdir(parents=True, exist_ok=True)
        created = []
        try:
            for path, content in files.items():
                if path.exists():
                    continue
                with path.open("xb") as stream:
                    created.append(path)
                    stream.write(content)
                path.chmod(0o755)
        except BaseException:
            for path in reversed(created):
                path.unlink()
            raise
        print(f"PASS pre-commit installed: {hooks / 'pre-commit'}")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FAIL hook setup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
