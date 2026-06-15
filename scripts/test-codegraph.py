#!/usr/bin/env python3
"""Fixture tests for CodeGraph v1."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEGRAPH = ROOT / "scripts" / "codegraph.py"


def run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def run_codegraph(repo: Path, *args: str, check: bool = True) -> dict:
    result = run([sys.executable, str(CODEGRAPH), *args], cwd=repo, check=check)
    if result.stderr:
        raise AssertionError(f"CodeGraph must not write stderr, got: {result.stderr}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout must be JSON, got: {result.stdout!r}") from exc
    if check and not payload.get("ok"):
        raise AssertionError(f"CodeGraph returned error: {payload}")
    return payload


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_fixture(repo: Path) -> None:
    write(
        repo / "src/calculator.py",
        """
from .models import NumberBox

def add(left: int, right: int) -> int:
    return left + right

class Calculator:
    def total(self, box: NumberBox) -> int:
        return add(box.value, 1)
""".lstrip(),
    )
    write(
        repo / "src/models.py",
        """
class NumberBox:
    def __init__(self, value: int):
        self.value = value
""".lstrip(),
    )
    write(
        repo / "tests/test_calculator.py",
        """
from src.calculator import add, Calculator

def test_add():
    assert add(1, 2) == 3
""".lstrip(),
    )
    write(
        repo / "web/math.ts",
        """
export type Amount = number;

export function multiply(left: Amount, right: Amount): Amount {
  return left * right;
}
""".lstrip(),
    )
    write(
        repo / "web/math.test.ts",
        """
import { multiply } from './math';

test('multiply', () => {
  expect(multiply(2, 3)).toBe(6);
});
""".lstrip(),
    )
    write(
        repo / "web/common.js",
        """
const math = require('./math');

function double(value) {
  return math.multiply(value, 2);
}

module.exports = { double };
""".lstrip(),
    )
    write(repo / ".gitignore", ".agent-work/\nignored/\n")
    write(repo / "ignored/skip.py", "def skipped():\n    return True\n")
    run(["git", "init"], cwd=repo)
    run(["git", "config", "user.email", "fixture@example.com"], cwd=repo)
    run(["git", "config", "user.name", "Fixture"], cwd=repo)
    run(["git", "add", "."], cwd=repo)
    run(["git", "commit", "-m", "fixture"], cwd=repo)
    write(repo / "web/untracked.ts", "export function freshThing() { return multiply(1, 1); }\n")


def assert_ok(payload: dict, command: str) -> None:
    if payload.get("ok") is not True:
        raise AssertionError(f"{command} must return ok true: {payload}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-codegraph-") as temp_dir:
        repo = Path(temp_dir) / "repo"
        repo.mkdir()
        build_fixture(repo)

        doctor = run_codegraph(repo, "doctor")
        assert_ok(doctor, "doctor")
        if not all(check["ok"] for check in doctor["data"]["checks"]):
            raise AssertionError(f"doctor checks must pass: {doctor}")

        index = run_codegraph(repo, "index")
        assert_ok(index, "index")
        if index["data"]["files"] < 6:
            raise AssertionError(f"index must include tracked and untracked relevant files: {index}")

        status = run_codegraph(repo, "status")
        assert_ok(status, "status")
        if not status["data"]["fresh"]:
            raise AssertionError(f"status must report fresh index: {status}")
        if status["data"]["symbols"] < 6:
            raise AssertionError(f"expected Python and TS/JS symbols: {status}")

        impact = run_codegraph(repo, "impact", "--target", "src/calculator.py")
        impact_paths = {item["path"] for item in impact["data"]["files"]}
        if "tests/test_calculator.py" not in {item["path"] for item in impact["data"]["tests"]}:
            raise AssertionError(f"impact must include linked Python test: {impact}")
        if "src/calculator.py" not in impact_paths:
            raise AssertionError(f"impact must include target file: {impact}")

        ts_tests = run_codegraph(repo, "tests", "--target", "web/math.ts")
        if "web/math.test.ts" not in {item["path"] for item in ts_tests["data"]["tests"]}:
            raise AssertionError(f"tests must link TS test through naming/import: {ts_tests}")
        strategies = {item["strategy"] for item in ts_tests["data"]["tests"]}
        if "naming+import" not in strategies:
            raise AssertionError(f"tests must expose strategy: {ts_tests}")

        context = run_codegraph(repo, "context", "--target", "web/math.ts")
        if not context["data"]["files_to_read"]:
            raise AssertionError(f"context must return files_to_read: {context}")
        if not any(risk["kind"] == "graph_support_only" for risk in context["data"]["risks"]):
            raise AssertionError(f"context must include graph-support-only risk: {context}")

        boundary_pass = run_codegraph(
            repo,
            "boundary",
            "--path",
            "web/math.ts",
            "--allowed",
            "web/**",
        )
        if boundary_pass["data"]["status"] != "pass":
            raise AssertionError(f"boundary should pass allowed path: {boundary_pass}")

        boundary_fail = run_codegraph(
            repo,
            "boundary",
            "--path",
            "src/calculator.py",
            "--allowed",
            "web/**",
            "--forbidden",
            "src/**",
        )
        if boundary_fail["data"]["status"] != "fail":
            raise AssertionError(f"boundary should fail forbidden path: {boundary_fail}")

        dependent = run_codegraph(
            repo,
            "deps",
            "--active-task",
            "change src/calculator.py add behavior",
            "--new-task",
            "update tests for src/calculator.py",
        )
        if dependent["data"]["classification"] != "dependent":
            raise AssertionError(f"deps must detect shared file: {dependent}")

        clear = run_codegraph(
            repo,
            "deps",
            "--active-task",
            "change web/math.ts",
            "--new-task",
            "update src/models.py",
        )
        if clear["data"]["classification"] != "clear":
            raise AssertionError(f"deps must classify unrelated known files clear: {clear}")

        uncertain = run_codegraph(
            repo,
            "deps",
            "--active-task",
            "unknown product task",
            "--new-task",
            "another unknown task",
        )
        if uncertain["data"]["classification"] != "uncertain":
            raise AssertionError(f"deps must classify missing graph context uncertain: {uncertain}")

        write(
            repo / "src/calculator.py",
            (repo / "src/calculator.py").read_text(encoding="utf-8")
            + "\ndef subtract(left: int, right: int) -> int:\n    return left - right\n",
        )
        refreshed = run_codegraph(repo, "impact", "--target", "subtract")
        if not any(symbol["qualname"] == "subtract" for symbol in refreshed["data"]["symbols"]):
            raise AssertionError(f"query must auto-reindex stale graph: {refreshed}")

        error_result = run([sys.executable, str(CODEGRAPH), "boundary", "--path", "web/math.ts"], cwd=repo, check=False)
        if error_result.returncode == 0:
            raise AssertionError("boundary without --allowed must fail")
        if error_result.stderr:
            raise AssertionError(f"errors must be JSON-only stdout, stderr got: {error_result.stderr}")
        error_payload = json.loads(error_result.stdout)
        if error_payload.get("ok") is not False or "error" not in error_payload:
            raise AssertionError(f"error envelope must be JSON: {error_payload}")

    print("PASS CodeGraph fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
