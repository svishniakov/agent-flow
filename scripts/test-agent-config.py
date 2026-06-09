#!/usr/bin/env python3
"""Fixture tests for Agent Flow role model config parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent_config import AgentConfigError, read_frontmatter, role_config, split_inline_list, validate_role_metadata


VALID_FRONTMATTER = """---
name: fixture-role
description: "Fixture role."
model: gpt-5.4-mini
reasoning_effort: medium
escalation_model: gpt-5.4
escalation_reasoning_effort: high
escalation_triggers: [security, failing-tests]
skills: [humanize-ts, "skill, with comma"]
tools: [Read, Write, Bash, Grep, Glob]
---

# fixture-role
"""


def write_file(root: Path, name: str, content: str) -> Path:
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


def expect_error(name: str, fn, needle: str) -> None:
    try:
        fn()
    except AgentConfigError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"{name}: expected AgentConfigError")
    if needle not in message:
        raise AssertionError(f"{name}: expected '{needle}' in '{message}'")


def assert_no_errors(name: str, errors: list[str]) -> None:
    if errors:
        raise AssertionError(f"{name}: unexpected errors: {errors}")


def assert_has_error(name: str, errors: list[str], needle: str) -> None:
    if not any(needle in error for error in errors):
        raise AssertionError(f"{name}: missing '{needle}' in {errors}")


def test_frontmatter_reader(root: Path) -> None:
    role_path = write_file(root, "fixture-role.md", VALID_FRONTMATTER)
    metadata = read_frontmatter(role_path)
    if metadata["name"] != "fixture-role":
        raise AssertionError("name was not parsed")
    if metadata["description"] != "Fixture role.":
        raise AssertionError("quoted description was not unquoted")
    if split_inline_list(metadata["skills"]) != ["humanize-ts", "skill, with comma"]:
        raise AssertionError("inline list with quoted comma parsed incorrectly")

    quoted_hash = read_frontmatter(
        write_file(root, "quoted-hash.md", '---\ndescription: "Role # note"\n---\n')
    )
    if quoted_hash["description"] != "Role # note":
        raise AssertionError("hash inside quoted value was not preserved")

    full_line_comment = read_frontmatter(
        write_file(root, "full-line-comment.md", "---\n# comment\nname: commented-role\n---\n")
    )
    if full_line_comment["name"] != "commented-role":
        raise AssertionError("full-line comment was not ignored")

    expect_error(
        "missing opening marker",
        lambda: read_frontmatter(write_file(root, "missing-open.md", "name: broken\n")),
        "missing opening frontmatter marker",
    )
    expect_error(
        "missing closing marker",
        lambda: read_frontmatter(write_file(root, "missing-close.md", "---\nname: broken\n")),
        "missing closing frontmatter marker",
    )
    expect_error(
        "duplicate key",
        lambda: read_frontmatter(write_file(root, "duplicate.md", "---\nname: a\nname: b\n---\n")),
        "duplicate frontmatter key",
    )
    expect_error(
        "inline comments rejected",
        lambda: read_frontmatter(write_file(root, "inline-comment.md", "---\nskills: [Read, Write] # note\n---\n")),
        "inline comments are not supported in role frontmatter",
    )
    expect_error(
        "multiline yaml rejected",
        lambda: read_frontmatter(write_file(root, "multiline.md", "---\nname: role\ndescription: >-\n  folded\n---\n")),
        "invalid frontmatter line",
    )


def test_role_validation(root: Path) -> None:
    role_path = write_file(root, "fixture-role.md", VALID_FRONTMATTER)
    metadata = read_frontmatter(role_path)
    assert_no_errors("valid role", validate_role_metadata(role_path, metadata))

    config = role_config(metadata, "fixture-role")
    if config["model"] != "gpt-5.4-mini" or config["escalated"]:
        raise AssertionError("default config selection failed")
    escalated = role_config(metadata, "fixture-role", ["security"])
    if escalated["model"] != "gpt-5.4" or not escalated["escalated"]:
        raise AssertionError("escalated config selection failed")

    bad_metadata = {
        "name": "wrong-name",
        "description": "Bad role.",
        "model": "gpt-0",
        "reasoning_effort": "huge",
        "escalation_model": "gpt-5.4",
        "escalation_reasoning_effort": "high",
        "escalation_triggers": "[not-a-trigger]",
        "skills": "humanize-ts",
        "tools": "[Read, Network]",
        "fallback_model": "gpt-5.4",
    }
    errors = validate_role_metadata(role_path, bad_metadata)
    for needle in [
        "deprecated frontmatter keys",
        "invalid model",
        "invalid reasoning_effort",
        "skills must be an inline list",
        "invalid tools",
        "invalid escalation_triggers",
        "name does not match file stem",
    ]:
        assert_has_error("bad role", errors, needle)

    missing_errors = validate_role_metadata(role_path, {"name": "fixture-role"})
    for key in ["description", "model", "reasoning_effort", "tools", "skills"]:
        assert_has_error("missing keys", missing_errors, f"missing required frontmatter key: {key}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-config-tests-") as temp_dir:
        root = Path(temp_dir)
        test_frontmatter_reader(root)
        test_role_validation(root)
    print("PASS agent config fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
