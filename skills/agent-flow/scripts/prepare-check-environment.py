#!/usr/bin/env python3
"""Explicitly prepare local full-suite dependencies; never called by the hook."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import venv
from pathlib import Path

from check_environment import EnvironmentError, ROOT, TOOLS, require_browser, require_tool, require_version


def prepare(prefix: Path, browser: Path) -> None:
    if sys.version_info[:3] < tuple(map(int, TOOLS["python"]["minimum"].split("."))):
        raise EnvironmentError(f"Required Python>={TOOLS['python']['minimum']}.")
    require_version("node", TOOLS["node"]["minimum"], minimum=True)
    require_version("codex", TOOLS["cli"]["codex"])
    os.environ["AGENT_FLOW_TEST_BROWSER"] = str(browser)
    browser = require_browser()
    executables = {name: Path(require_tool(name)).resolve() for name in ("node", "codex")}
    npm = require_tool("npm")
    marker = prefix / ".agent-flow-check-environment.json"
    expected_marker = {"schema_version": 1, "purpose": "agent-flow-check-environment"}
    if prefix.is_symlink():
        raise EnvironmentError("The dependency prefix must not be a symlink.")
    if prefix.exists() and any(prefix.iterdir()):
        if not marker.is_file() or json.loads(marker.read_text()) != expected_marker:
            raise EnvironmentError(f"Refusing to change an unowned nonempty prefix: {prefix}")
    prefix.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(expected_marker) + "\n", encoding="utf-8")
    venv.EnvBuilder(with_pip=True).create(prefix)
    python = prefix / "bin/python3"
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-r", str(ROOT / "requirements-codegraph.txt")], check=True)
    npm_root = prefix / "tools"
    subprocess.run([npm, "install", "--prefix", str(npm_root), "--ignore-scripts",
                    "--no-audit", "--no-fund", "--save-exact",
                    f"skills@{TOOLS['cli']['skills']}", f"pnpm@{TOOLS['cli']['pnpm']}"], check=True)
    for name in ("skills", "pnpm"):
        executables[name] = (npm_root / "node_modules/.bin" / name).resolve()
    for name, target in executables.items():
        link = prefix / "bin" / name
        if link.is_symlink() and link.resolve() == target:
            continue
        if link.exists() or link.is_symlink():
            raise EnvironmentError(f"Refusing to replace an existing executable: {link}")
        link.symlink_to(target)
    environment = os.environ.copy()
    environment["PATH"] = str(prefix / "bin") + os.pathsep + environment.get("PATH", "")
    subprocess.run([str(python), str(ROOT / "scripts/check_environment.py")], env=environment, check=True)
    print("Prepared dependencies. Connect the hook in the source checkout:")
    print(shlex.join([str(python), "scripts/install-pre-commit.py", "--python", str(python),
                      "--tool-bin", str(prefix / "bin"), "--browser", str(browser)]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True,
                        help=f"Already installed headless shell {TOOLS['browser']}")
    args = parser.parse_args()
    try:
        prepare(args.prefix.expanduser().absolute(), args.browser.expanduser().absolute())
    except (EnvironmentError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAIL prepare check environment: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
