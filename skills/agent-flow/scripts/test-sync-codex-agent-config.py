#!/usr/bin/env python3
"""Fixture tests for Codex custom-agent TOML sync."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_CONFIG = ROOT / "scripts" / "sync-codex-agent-config.py"
BASELINE_NAME = ".agent-flow-baseline.json"


ROLE = """---
name: alpha-role
description: "Alpha role."
model: gpt-5.6-luna
reasoning_effort: medium
escalation_model: gpt-5.6-luna
escalation_reasoning_effort: max
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


def snapshot(directory: Path) -> dict[str, tuple[int, bytes | str]]:
    return {
        str(path.relative_to(directory)): (
            path.lstat().st_mtime_ns,
            str(path.readlink()) if path.is_symlink() else path.read_bytes(),
        )
        for path in directory.rglob("*")
        if path.is_file() or path.is_symlink()
    }


def safety_tests(root: Path) -> None:
    agents = root / "safety-agents"
    agents.mkdir()
    names = ["alpha-role", "zeta-role"]
    for name in names:
        (agents / f"{name}.md").write_text(ROLE.replace("alpha-role", name), encoding="utf-8")
    (agents / "agent-identities.json").write_text(json.dumps({
        "agents": [{"role": name, "nickname_candidates": [name]} for name in names],
    }), encoding="utf-8")
    output = root / "safety-output"
    missing = run_sync(agents, output, True)
    for name in names:
        expect_fail("empty check", missing, f"missing synced file: {output / (name + '.toml')}")
    assert not output.exists(), "--check created the output directory"
    expect_pass("clean two roles", run_sync(agents, output))
    baseline = output / BASELINE_NAME
    hashes = json.loads(baseline.read_text())
    assert hashes == {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in hashes}
    clean = snapshot(output)
    expect_pass("repeat", run_sync(agents, output))
    expect_pass("unchanged check", run_sync(agents, output, True))
    assert snapshot(output) == clean, "repeat or check rewrote unchanged files"

    late = output / "zeta-role.toml"
    saved = late.read_bytes()
    late.write_bytes(saved + b"# user edit\n")
    before = snapshot(output)
    (agents / "alpha-role.md").write_text(ROLE + "\nUpdated instructions.\n", encoding="utf-8")
    expect_fail("late edited managed conflict", run_sync(agents, output), str(late))
    expect_fail("edited check", run_sync(agents, output, True), str(late))
    assert snapshot(output) == before, "late conflict or --check caused partial writes"
    late.write_bytes(saved)
    expect_pass("intact managed update", run_sync(agents, output))
    expect_pass("updated check", run_sync(agents, output, True))
    assert b"Updated instructions." in (output / "alpha-role.toml").read_bytes()

    saved_baseline = baseline.read_bytes()
    invalid_baselines = [b"{", b"[]", b'{}\xff', b'{"../escape.toml":"bad"}',
                         b'{"alpha-role.toml":42}',
                         b'{"alpha-role.toml":"bad","alpha-role.toml":"bad"}']
    for invalid in invalid_baselines:
        baseline.write_bytes(invalid)
        before = snapshot(output)
        expect_fail("malformed baseline", run_sync(agents, output), str(baseline))
        expect_fail("malformed baseline check", run_sync(agents, output, True), str(baseline))
        assert snapshot(output) == before, "malformed baseline caused writes"
    baseline.write_bytes(saved_baseline)

    wrong_hashes = json.loads(saved_baseline)
    wrong_hashes["zeta-role.toml"] = "0" * 64
    baseline.write_text(json.dumps(wrong_hashes), encoding="utf-8")
    before = snapshot(output)
    expect_fail("wrong baseline hash", run_sync(agents, output), str(late))
    expect_fail("incomplete prior sync check", run_sync(agents, output, True), str(late))
    assert snapshot(output) == before
    baseline.write_bytes(saved_baseline)

    baseline.unlink()
    before = snapshot(output)
    expect_fail("missing baseline check", run_sync(agents, output, True), str(baseline))
    assert snapshot(output) == before
    expect_pass("identical legacy adoption", run_sync(agents, output))
    adopted = snapshot(output)
    assert {name: state for name, state in adopted.items() if name != BASELINE_NAME} == before
    baseline.unlink()
    late.write_bytes(saved + b"# legacy edit\n")
    before = snapshot(output)
    expect_fail("legacy header cannot authorize overwrite", run_sync(agents, output), str(late))
    assert snapshot(output) == before

    custom = root / "custom-output"
    custom.mkdir()
    custom_file = custom / "zeta-role.toml"
    custom_file.write_bytes(b"# my own role\xff\n")
    before = snapshot(custom)
    expect_fail("late custom conflict", run_sync(agents, custom), str(custom_file))
    assert snapshot(custom) == before, "late custom conflict created the first role or baseline"
    custom_file.unlink()
    external = root / "external.toml"
    external.write_bytes(b"untouched")
    for target in (custom_file, custom / BASELINE_NAME):
        target.symlink_to(external)
        before = snapshot(custom)
        expect_fail("symlink conflict", run_sync(agents, custom), str(target))
        assert snapshot(custom) == before and external.read_bytes() == b"untouched"
        target.unlink()
    custom_file.symlink_to(root / "missing-target")
    before = snapshot(custom)
    expect_fail("dangling symlink conflict", run_sync(agents, custom), str(custom_file))
    assert snapshot(custom) == before and not (root / "missing-target").exists()
    custom_file.unlink()
    linked = root / "linked-output"
    linked.symlink_to(custom, target_is_directory=True)
    expect_fail("output directory symlink", run_sync(agents, linked), str(linked))
    assert not list(custom.iterdir())

    expect_pass("stale fixture setup", run_sync(agents, custom))
    unrelated = custom / "my-role.toml"
    unrelated.write_bytes(b"# unrelated user config\xff\n")
    before = snapshot(custom)
    expect_pass("unrelated custom extra", run_sync(agents, custom))
    expect_pass("unrelated custom extra check", run_sync(agents, custom, True))
    assert snapshot(custom) == before
    (agents / "zeta-role.md").unlink()
    (custom / "zeta-role.toml").write_bytes(b"# header removed by user\n")
    before = snapshot(custom)
    expect_fail("stale owned file", run_sync(agents, custom), str(custom / "zeta-role.toml"))
    assert snapshot(custom) == before
    print("PASS role sync safety: clean/repeat/update/custom/edited/legacy/baseline/symlinks/stale/check")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-agent-config-tests-") as temp_dir:
        root = Path(temp_dir).resolve()
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
            'model = "gpt-5.6-luna"',
            'model_reasoning_effort = "medium"',
            'nickname_candidates = ["Alpha Role"]',
            "Use the delegation packet as the source of truth.",
        ]:
            if needle not in synced:
                raise AssertionError(f"synced TOML missing: {needle}")

        expect_pass("check", run_sync(agents_dir, output_dir, check=True))

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
        safety_tests(root)

    print("PASS Codex agent config sync tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
