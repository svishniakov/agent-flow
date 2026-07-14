#!/usr/bin/env python3
"""Fixture tests for validate-role-catalog."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_ROLE_CATALOG = ROOT / "scripts" / "validate-role-catalog.py"


ROLE_FRONTMATTER = """---
name: {name}
description: "Fixture role."
model: gpt-5.4-mini
reasoning_effort: medium
escalation_model: gpt-5.4
escalation_reasoning_effort: high
escalation_triggers: [security]
skills: [humanize-ts]
tools: [Read, Write, Bash, Grep, Glob]
---

# {name}
"""


def write_role(agents_dir: Path, name: str) -> None:
    (agents_dir / f"{name}.md").write_text(ROLE_FRONTMATTER.format(name=name), encoding="utf-8")


def catalog_entry(name: str, *, status: str = "active", include_overlap: bool = True) -> str:
    lines = [
        f"### {name}",
        "",
        f"Status: {status}",
        "Use when: Fixture use case.",
        "Do not use when: Fixture exclusion.",
    ]
    if include_overlap:
        lines.append("Overlap notes: Fixture overlap.")
    return "\n".join(lines) + "\n"


def write_catalog(path: Path, entries: list[str]) -> None:
    path.write_text("# Role Catalog\n\n" + "\n".join(entries), encoding="utf-8")


def validate(agents_dir: Path, catalog: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATE_ROLE_CATALOG),
            "--agents-dir",
            str(agents_dir),
            "--catalog",
            str(catalog),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def expect_pass(name: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise AssertionError(f"{name}: expected pass\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def expect_fail(name: str, result: subprocess.CompletedProcess[str], needle: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{name}: expected fail")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name}: missing '{needle}'\nOutput:\n{output}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="role-catalog-tests-") as temp_dir:
        root = Path(temp_dir)
        agents_dir = root / "agents"
        agents_dir.mkdir()
        catalog = root / "role-catalog.md"
        write_role(agents_dir, "alpha-role")
        write_role(agents_dir, "beta-role")

        write_catalog(catalog, [catalog_entry("alpha-role"), catalog_entry("beta-role")])
        expect_pass("valid catalog", validate(agents_dir, catalog))

        write_catalog(catalog, [catalog_entry("alpha-role")])
        expect_fail("missing catalog role", validate(agents_dir, catalog), "missing catalog entries for roles: beta-role")

        write_catalog(catalog, [catalog_entry("alpha-role"), catalog_entry("beta-role"), catalog_entry("stale-role")])
        expect_fail("stale catalog role", validate(agents_dir, catalog), "stale catalog entries without role files: stale-role")

        write_catalog(catalog, [catalog_entry("alpha-role"), catalog_entry("alpha-role"), catalog_entry("beta-role")])
        expect_fail("duplicate catalog role", validate(agents_dir, catalog), "duplicate catalog role: alpha-role")

        write_catalog(catalog, [catalog_entry("alpha-role", status="paused"), catalog_entry("beta-role")])
        expect_fail("invalid status", validate(agents_dir, catalog), "invalid Status: paused")

        write_catalog(catalog, [catalog_entry("alpha-role", include_overlap=False), catalog_entry("beta-role")])
        expect_fail("missing field", validate(agents_dir, catalog), "missing field 'Overlap notes'")

    print("PASS role catalog fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
