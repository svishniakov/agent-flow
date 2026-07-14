#!/usr/bin/env python3
"""Fixture tests for Architecture Capability Router registry validation."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-architecture-capabilities.py"


VALID_CAPABILITY = {
    "id": "go-backend-service-architecture",
    "matrix_facets": ["backend-service", "go", "unit"],
    "recommended_skills": ["golang-code-style"],
    "contract_sections": ["Selected Architecture", "Public Contracts"],
}


def write_registry(path: Path, capabilities: list[dict[str, Any]]) -> Path:
    registry = {"version": 1, "capabilities": capabilities}
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--registry",
            str(path),
            "--allow-partial-matrix-coverage",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def expect_pass(name: str, path: Path) -> None:
    result = validate(path)
    if result.returncode != 0:
        raise AssertionError(f"{name} expected pass\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def expect_fail(name: str, path: Path, needle: str) -> None:
    result = validate(path)
    if result.returncode == 0:
        raise AssertionError(f"{name} expected fail")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name} missing '{needle}'\nOutput:\n{output}")


def capability(**overrides: Any) -> dict[str, Any]:
    row = dict(VALID_CAPABILITY)
    row.update(overrides)
    return row


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-flow-capability-tests-") as temp_dir:
        temp = Path(temp_dir)

        expect_pass(
            "valid capability registry passes",
            write_registry(temp / "valid.json", [capability()]),
        )

        expect_pass(
            "empty recommended skills are allowed",
            write_registry(
                temp / "empty-skills.json",
                [capability(recommended_skills=[])],
            ),
        )

        expect_fail(
            "duplicate capability ids fail",
            write_registry(temp / "duplicate.json", [capability(), capability()]),
            "duplicate architecture capability id: go-backend-service-architecture",
        )

        expect_fail(
            "non kebab capability id fails",
            write_registry(temp / "non-kebab.json", [capability(id="GoBackend")]),
            "invalid architecture capability id: GoBackend",
        )

        expect_fail(
            "unknown matrix facet fails",
            write_registry(
                temp / "unknown-facet.json",
                [capability(matrix_facets=["unknown-facet"])],
            ),
            "unknown Architecture Matrix facet: unknown-facet",
        )

        expect_fail(
            "unknown skill id fails",
            write_registry(
                temp / "unknown-skill.json",
                [capability(recommended_skills=["unknown-skill"])],
            ),
            "unknown recommended skill: unknown-skill",
        )

        expect_fail(
            "invalid contract section fails",
            write_registry(
                temp / "invalid-contract-section.json",
                [capability(contract_sections=["Unknown Section"])],
            ),
            "unknown architecture contract section: Unknown Section",
        )

        expect_fail(
            "capability without matrix facets fails",
            write_registry(
                temp / "empty-matrix-facets.json",
                [capability(matrix_facets=[])],
            ),
            "matrix_facets must be a non-empty array",
        )

    print("PASS architecture capability registry fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
