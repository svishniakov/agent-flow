#!/usr/bin/env python3
"""Acceptance tests for persisted Golden Trace Runs.

This is the test-golden-traces runner for testdata/golden-traces.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = ROOT / "testdata" / "golden-traces"
MANIFEST = GOLDEN_ROOT / "manifest.json"
VALIDATE_RUN = ROOT / "scripts" / "validate-run.py"

CASE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_EXPECTED = {"pass", "fail"}
VALID_MODES = {"full"}


def fail(message: str) -> None:
    raise AssertionError(message)


def load_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        fail(f"missing golden trace manifest: {MANIFEST}")
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        fail(f"invalid golden trace manifest JSON: {error}")
    if not isinstance(data, dict):
        fail("golden trace manifest must be a JSON object")
    if data.get("version") != 1:
        fail("golden trace manifest version must be 1")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("golden trace manifest cases must be a non-empty array")
    return data


def resolve_case_path(case_id: str, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        fail(f"{case_id}: path must be a non-empty string")
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        fail(f"{case_id}: path must stay inside testdata/golden-traces")
    resolved = (GOLDEN_ROOT / path).resolve()
    if not resolved.is_relative_to(GOLDEN_ROOT.resolve()):
        fail(f"{case_id}: path escapes testdata/golden-traces")
    if not resolved.exists():
        fail(f"{case_id}: golden trace path does not exist: {raw_path}")
    if not resolved.is_dir():
        fail(f"{case_id}: golden trace path must be a directory: {raw_path}")
    return resolved


def validate_case_shape(raw_case: Any, index: int, seen_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw_case, dict):
        fail(f"case #{index}: must be an object")
    case_id = raw_case.get("id")
    if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
        fail(f"case #{index}: id must be kebab-case")
    if case_id in seen_ids:
        fail(f"{case_id}: duplicate golden trace id")
    seen_ids.add(case_id)

    mode = raw_case.get("mode", "full")
    if mode not in VALID_MODES:
        fail(f"{case_id}: mode must be full")

    expected = raw_case.get("expected")
    if expected not in VALID_EXPECTED:
        fail(f"{case_id}: expected must be pass or fail")

    covers = raw_case.get("covers")
    if not isinstance(covers, list) or not covers or not all(
        isinstance(item, str) and item.strip() for item in covers
    ):
        fail(f"{case_id}: covers must be a non-empty string array")

    expected_error = raw_case.get("expected_error")
    if expected == "fail":
        if not isinstance(expected_error, str) or not expected_error:
            fail(f"{case_id}: failing golden trace requires expected_error")
    elif expected_error is not None:
        fail(f"{case_id}: passing golden trace must not set expected_error")

    case_path = resolve_case_path(case_id, raw_case.get("path"))
    return {
        "id": case_id,
        "path": case_path,
        "mode": mode,
        "expected": expected,
        "expected_error": expected_error,
    }


def run_validate(case: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATE_RUN),
            "--run-dir",
            str(case["path"]),
            "--mode",
            case["mode"],
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def check_case(case: dict[str, Any]) -> None:
    result = run_validate(case)
    combined = result.stdout + result.stderr
    if case["expected"] == "pass":
        if result.returncode != 0:
            fail(
                f"{case['id']}: expected pass\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
        print(f"PASS {case['id']}")
        return

    if result.returncode == 0:
        fail(f"{case['id']}: expected validation failure")
    expected_error = case["expected_error"]
    if expected_error not in combined:
        fail(
            f"{case['id']}: expected error substring not found: {expected_error!r}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    print(f"PASS {case['id']}")


def main() -> int:
    manifest = load_manifest()
    seen_ids: set[str] = set()
    cases = [
        validate_case_shape(raw_case, index, seen_ids)
        for index, raw_case in enumerate(manifest["cases"], start=1)
    ]
    for case in cases:
        check_case(case)
    print("PASS golden trace runs")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
