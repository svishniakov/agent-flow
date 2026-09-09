#!/usr/bin/env python3
"""Fixture tests for Codex custom-agent TOML sync."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_CONFIG = ROOT / "scripts" / "sync-codex-agent-config.py"


ROLE = """---
name: alpha-role
description: "Alpha role."
model: gpt-6-astra
reasoning_effort: medium
escalation_model: gpt-6-astra
escalation_reasoning_effort: high
escalation_triggers: [security]
skills: [humanize-ts]
tools: [Read, Write, Bash, Grep, Glob]
---

# alpha-role

## Identity
Use the delegation packet as the source of truth.
"""


def run_sync(agents_dir: Path, output_dir: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SYNC_CONFIG),
        "--agents-dir",
        str(agents_dir),
        "--output-dir",
        str(output_dir),
    ]
    if check:
        command.append("--check")
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-agent-config-tests-") as temp_dir:
        root = Path(temp_dir)
        agents_dir = root / "agents"
        output_dir = root / ".codex" / "agents"
        agents_dir.mkdir()
        (agents_dir / "alpha-role.md").write_text(ROLE, encoding="utf-8")
        (agents_dir / "agent-identities.json").write_text(
            json.dumps(
                {
                    "schema_version": "fixture",
                    "agents": [
                        {
                            "role": "alpha-role",
                            "stable_agent_name": "Alpha Role",
                            "stable_agent_slug": "alpha-role",
                            "nickname_candidates": ["Alpha Role"],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        expect_pass("sync", run_sync(agents_dir, output_dir))
        synced = (output_dir / "alpha-role.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(synced)
        if parsed["nickname_candidates"] != ["Alpha Role"]:
            raise AssertionError("nickname_candidates were not valid TOML")
        for needle in [
            'name = "alpha-role"',
            'description = "Alpha role."',
            'model = "gpt-6-astra"',
            'model_reasoning_effort = "medium"',
            'nickname_candidates = ["Alpha Role"]',
            "Use the delegation packet as the source of truth.",
            "# Astra execution instructions",
        ]:
            if needle not in synced:
                raise AssertionError(f"synced TOML missing: {needle}")

        expect_pass("check", run_sync(agents_dir, output_dir, check=True))

        resolved = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "resolve-agent-config.py"), "--agents-dir", str(agents_dir), "--role", "alpha-role", "--include-instructions"],
            text=True, capture_output=True, check=True,
        )
        if json.loads(resolved.stdout)["developer_instructions"] != parsed["developer_instructions"]:
            raise AssertionError("resolver and sync use different role instructions")

        role_path = agents_dir / "alpha-role.md"
        role_path.write_text(ROLE.replace("escalation_model: gpt-6-astra", "escalation_model: gpt-5.6-sol"), encoding="utf-8")
        expect_fail("reject cross-family escalation", run_sync(agents_dir, output_dir), "escalation_model must match model")
        if (output_dir / "alpha-role.toml").read_text(encoding="utf-8") != synced:
            raise AssertionError("failed validation changed the generated config")
        role_path.write_text(ROLE, encoding="utf-8")

        role_path.write_text(ROLE.replace("gpt-6-astra", "gpt-5.6-sol"), encoding="utf-8")
        expect_pass("sync Sol", run_sync(agents_dir, output_dir))
        sol = tomllib.loads((output_dir / "alpha-role.toml").read_text(encoding="utf-8"))
        if sol["model"] != "gpt-5.6-sol" or sol["developer_instructions"] != parsed["developer_instructions"]:
            raise AssertionError("Sol must use the selected model and unchanged workflow instructions")
        role_path.write_text(ROLE, encoding="utf-8")

        (output_dir / "alpha-role.toml").write_text(synced.replace("# Astra execution instructions", "stale instructions"), encoding="utf-8")
        expect_fail("detect stale instructions", run_sync(agents_dir, output_dir, check=True), "stale synced file")
        expect_pass("restore sync", run_sync(agents_dir, output_dir))

        stale = output_dir / "stale-role.toml"
        stale.write_text("# Synced by Agent Flow. Edit agents/agent-identities.json or agents/*.md, then rerun sync.\n", encoding="utf-8")
        expect_fail("stale managed file", run_sync(agents_dir, output_dir, check=True), "extra managed synced file")
        stale.unlink()

        identities_path = agents_dir / "agent-identities.json"
        valid_identities = identities_path.read_text(encoding="utf-8")
        identities_path.write_text(
            json.dumps(
                {
                    "schema_version": "fixture",
                    "agents": [
                        {
                            "role": "alpha-role",
                            "stable_agent_name": "Alpha Role",
                            "stable_agent_slug": "alpha-role",
                            "nickname_candidates": ["UI/UX Designer"],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        expect_fail("invalid nickname", run_sync(agents_dir, output_dir), "unsupported characters")
        identities_path.write_text(valid_identities, encoding="utf-8")

    print("PASS Codex agent config sync tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
