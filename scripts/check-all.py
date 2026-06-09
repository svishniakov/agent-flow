#!/usr/bin/env python3
"""Run the Agent Flow repository validation suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PRODUCT_SEARCH_PATHS = [
    "README.md",
    "README.ru.md",
    "SKILL.md",
    "docs",
    "references",
    "scripts",
    "agents",
    "registries",
]


def run_step(name: str, command: list[str]) -> int:
    print(f"==> {name}")
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        print(f"FAIL {name}: exit {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"PASS {name}")
    return 0


def run_content_guard(name: str, needle: str) -> int:
    print(f"==> {name}")
    matches: list[str] = []
    for raw_path in PRODUCT_SEARCH_PATHS:
        path = ROOT / raw_path
        if path.is_dir():
            candidates = [candidate for candidate in path.rglob("*") if candidate.is_file()]
        elif path.exists():
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            if candidate == Path(__file__).resolve():
                continue
            if candidate.suffix not in {".md", ".py", ".json", ".yaml", ".yml"}:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if needle in text:
                matches.append(str(candidate.relative_to(ROOT)))
    if matches:
        print(f"FAIL {name}: found forbidden text '{needle}'", file=sys.stderr)
        for match in matches:
            print(f"- {match}", file=sys.stderr)
        return 1
    print(f"PASS {name}")
    return 0


def main() -> int:
    python_files = sorted(str(path.relative_to(ROOT)) for path in SCRIPTS.glob("*.py"))
    command_steps = [
        ("py_compile scripts", [sys.executable, "-m", "py_compile", *python_files]),
        ("agent config fixtures", [sys.executable, "scripts/test-agent-config.py"]),
        ("validate-agent-config fixtures", [sys.executable, "scripts/test-validate-agent-config.py"]),
        ("role catalog fixtures", [sys.executable, "scripts/test-validate-role-catalog.py"]),
        ("agent config validation", [sys.executable, "scripts/validate-agent-config.py"]),
        ("role catalog validation", [sys.executable, "scripts/validate-role-catalog.py"]),
        ("agent skill registry validation", [sys.executable, "scripts/validate-agent-skill-registry.py"]),
        ("validate-run CLI", [sys.executable, "scripts/validate-run.py", "--help"]),
        ("lane fixture tests", [sys.executable, "scripts/test-validate-run-lanes.py"]),
        ("git diff hygiene", ["git", "diff", "--check"]),
    ]
    content_steps = [
        ("personal path guard", "/Users/" + "ucnlejumper"),
        ("old README/docs check block guard", "python3 -m py_compile " + "scripts/*.py"),
    ]

    failures = 0
    for name, command in command_steps:
        if run_step(name, command):
            failures += 1
    for name, needle in content_steps:
        if run_content_guard(name, needle):
            failures += 1

    if failures:
        print(f"FAILED {failures} check(s)", file=sys.stderr)
        return 1
    print("PASS all Agent Flow checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
