#!/usr/bin/env python3
"""Fixture tests for validate-agent-config integration checks."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATE_AGENT_CONFIG = ROOT / "scripts" / "validate-agent-config.py"


ROLE_TEMPLATE = """---
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
    (agents_dir / f"{name}.md").write_text(ROLE_TEMPLATE.format(name=name), encoding="utf-8")


def write_identities(agents_dir: Path, entries: list[dict[str, object]]) -> Path:
    path = agents_dir / "agent-identities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "fixture",
                "agents": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_readmes(root: Path, count: int) -> None:
    russian_role_count = f"{count} \u0440\u043e\u043b\u0435\u0439"
    (root / "README.md").write_text(f"roles-{count}\n{count} roles\n", encoding="utf-8")
    (root / "README.ru.md").write_text(f"roles-{count}\n{russian_role_count}\n", encoding="utf-8")


def validate(agents_dir: Path, identities_path: Path | None = None, skip_readme_counts: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(VALIDATE_AGENT_CONFIG), "--agents-dir", str(agents_dir)]
    if identities_path:
        command.extend(["--identities", str(identities_path)])
    if skip_readme_counts:
        command.append("--skip-readme-counts")
    return subprocess.run(command, text=True, capture_output=True, check=False)


def expect_pass(name: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise AssertionError(f"{name}: expected pass\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def expect_fail(name: str, result: subprocess.CompletedProcess[str], needle: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{name}: expected fail")
    output = result.stdout + result.stderr
    if needle not in output:
        raise AssertionError(f"{name}: missing '{needle}'\nOutput:\n{output}")


def identity(role: str, stable_slug: str | None = None, **extra: object) -> dict[str, object]:
    return {
        "role": role,
        "stable_agent_name": role.replace("-", " ").title(),
        "stable_agent_slug": stable_slug or role,
        **extra,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-config-validation-tests-") as temp_dir:
        root = Path(temp_dir)
        agents_dir = root / "agents"
        agents_dir.mkdir()
        write_role(agents_dir, "alpha-role")
        write_role(agents_dir, "beta-role")
        write_readmes(root, 2)

        valid_identities = write_identities(agents_dir, [identity("alpha-role"), identity("beta-role")])
        expect_pass("valid fixture", validate(agents_dir, valid_identities))

        missing = write_identities(agents_dir, [identity("alpha-role")])
        expect_fail("missing identity", validate(agents_dir, missing), "missing identities for roles: beta-role")

        stale = write_identities(agents_dir, [identity("alpha-role"), identity("beta-role"), identity("stale-role")])
        expect_fail("stale identity", validate(agents_dir, stale), "stale identities without role files: stale-role")

        duplicate = write_identities(agents_dir, [identity("alpha-role"), identity("alpha-role"), identity("beta-role")])
        expect_fail("duplicate identity", validate(agents_dir, duplicate), "duplicate identity role: alpha-role")

        bad_slug = write_identities(agents_dir, [identity("alpha-role", stable_slug="alpha"), identity("beta-role")])
        expect_fail("bad stable slug", validate(agents_dir, bad_slug), "stable_agent_slug must match role")

        runtime_config = write_identities(agents_dir, [identity("alpha-role", model="gpt-5.4"), identity("beta-role")])
        expect_fail("runtime config in identities", validate(agents_dir, runtime_config), "runtime config keys: model")

        valid_identities = write_identities(agents_dir, [identity("alpha-role"), identity("beta-role")])
        write_readmes(root, 25)
        expect_fail("README role count drift", validate(agents_dir, valid_identities), "visible role count does not match")
        expect_pass("README count skip", validate(agents_dir, valid_identities, skip_readme_counts=True))

    print("PASS validate-agent-config fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
