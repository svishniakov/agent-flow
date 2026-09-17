#!/usr/bin/env python3
"""Check the explicitly prepared full-suite tools without installing anything."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = json.loads((ROOT / "check-tools.json").read_text(encoding="utf-8"))


class EnvironmentError(RuntimeError):
    pass


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise EnvironmentError(f"Required tool is missing: {name}. Run prepare-check-environment.py explicitly.")
    return str(Path(executable).absolute())


def version_of(command: list[str]) -> str:
    environment = os.environ.copy()
    environment.update(DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                       npm_config_manage_package_manager_versions="false")
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False,
                                timeout=30, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EnvironmentError(f"Cannot inspect required tool {command[0]}: {exc}") from exc
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+(?:\.\d+)?(?:[-+][\w.-]+)?)", result.stdout)
    if result.returncode or not match:
        raise EnvironmentError(f"Cannot inspect required tool {command[0]}: {result.stderr or result.stdout}")
    return match.group(1)


def require_version(name: str, expected: str, *, minimum: bool = False) -> str:
    actual = version_of([require_tool(name), "--version"])
    if minimum:
        accepted = bool(re.fullmatch(r"\d+\.\d+\.\d+", actual))
        if accepted:
            accepted = tuple(map(int, actual.split("."))) >= tuple(map(int, expected.split(".")))
    else:
        accepted = actual == expected
    if not accepted:
        relation = ">=" if minimum else "=="
        raise EnvironmentError(f"Required {name}{relation}{expected}; found {actual}.")
    return actual


def require_browser() -> Path:
    value = os.environ.get("AGENT_FLOW_TEST_BROWSER")
    if not value:
        raise EnvironmentError("Required browser is missing: set AGENT_FLOW_TEST_BROWSER to the prepared headless shell.")
    browser = Path(value).expanduser().resolve()
    if not browser.is_file() or not os.access(browser, os.X_OK):
        raise EnvironmentError(f"Required browser is not executable: {browser}")
    actual = version_of([str(browser), "--version"])
    if actual != TOOLS["browser"]:
        raise EnvironmentError(f"Required browser=={TOOLS['browser']}; found {actual}.")
    return browser


def check_environment() -> dict[str, str]:
    if sys.platform not in {"darwin", "linux"}:
        raise EnvironmentError("The full suite requires macOS or Linux; process and Codex sandbox checks cannot be skipped.")
    minimum = tuple(map(int, TOOLS["python"]["minimum"].split(".")))
    if sys.version_info[:3] < minimum:
        raise EnvironmentError(f"Required Python>={TOOLS['python']['minimum']}.")
    versions = {"python": ".".join(map(str, sys.version_info[:3]))}
    require_tool("git")
    versions["node"] = require_version("node", TOOLS["node"]["minimum"], minimum=True)
    for name, expected in TOOLS["cli"].items():
        versions[name] = require_version(name, expected)
    require_browser()
    versions["browser"] = TOOLS["browser"]
    for line in (ROOT / "requirements-codegraph.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, expected = line.split("==")
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise EnvironmentError(f"Required Python dependency is missing: {line}.") from exc
        if actual != expected:
            raise EnvironmentError(f"Required {line}; found {actual}.")
        versions[name] = actual
    return versions


def main() -> int:
    try:
        versions = check_environment()
    except EnvironmentError as exc:
        print(f"FAIL check environment: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(versions, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
