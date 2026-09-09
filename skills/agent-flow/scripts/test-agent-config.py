#!/usr/bin/env python3
"""Fixture tests for Agent Flow role model config parsing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from itertools import combinations
from unittest.mock import patch
from pathlib import Path

from agent_config import (
    AgentConfigError,
    default_agents_dir,
    read_frontmatter,
    role_config,
    role_instructions,
    split_inline_list,
    validate_role_metadata,
)


HIGH_REASONING_ROLES = {
    "architect", "reviewer", "product-manager", "marketing-growth-strategist",
    "rag-retrieval-engineer", "design-orchestrator", "ui-ux-design-director",
    "senior-qa-verifier", "visual-qa", "documenter", "qa-verifier",
}


VALID_FRONTMATTER = """---
name: fixture-role
description: "Fixture role."
model: gpt-5.6-luna
reasoning_effort: medium
escalation_model: gpt-5.6-luna
escalation_reasoning_effort: max
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

    skills_comment = read_frontmatter(
        write_file(root, "skills-comment.md", "---\nskills: [Read, Write] # note\n---\n")
    )
    if split_inline_list(skills_comment["skills"]) != ["Read", "Write"]:
        raise AssertionError("inline comment after list was not stripped")

    quoted_hash = read_frontmatter(
        write_file(root, "quoted-hash.md", '---\ndescription: "Role # note" # comment\n---\n')
    )
    if quoted_hash["description"] != "Role # note":
        raise AssertionError("hash inside quoted value was not preserved")

    unquoted_comment = read_frontmatter(
        write_file(root, "unquoted-comment.md", "---\ndescription: Role # note\n---\n")
    )
    if unquoted_comment["description"] != "Role":
        raise AssertionError("inline comment after unquoted scalar was not stripped")

    literal_hash = read_frontmatter(
        write_file(root, "literal-hash.md", "---\ndescription: abc#def\n---\n")
    )
    if literal_hash["description"] != "abc#def":
        raise AssertionError("hash without preceding whitespace was not preserved")

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
        "multiline yaml rejected",
        lambda: read_frontmatter(write_file(root, "multiline.md", "---\nname: role\ndescription: >-\n  folded\n---\n")),
        "invalid frontmatter line",
    )


def test_role_validation(root: Path) -> None:
    role_path = write_file(root, "fixture-role.md", VALID_FRONTMATTER)
    metadata = read_frontmatter(role_path)
    assert_no_errors("valid role", validate_role_metadata(role_path, metadata))

    config = role_config(metadata, "fixture-role")
    if config["model"] != "gpt-5.6-luna" or config["escalated"]:
        raise AssertionError("default config selection failed")
    escalated = role_config(metadata, "fixture-role", ["security"])
    if (
        escalated["model"] != "gpt-5.6-luna"
        or escalated["reasoning_effort"] != "max"
        or not escalated["escalated"]
    ):
        raise AssertionError("escalated config selection failed")

    for model in ("gpt-6-astra", "gpt-5.6-sol"):
        same_model = {**metadata, "model": model, "escalation_model": model, "escalation_reasoning_effort": "high"}
        assert_no_errors("same-model escalation", validate_role_metadata(role_path, same_model))
        assert role_config(same_model, "fixture-role", ["security"])["model"] == model

    for changes, needle in [
        ({"reasoning_effort": "high", "escalation_reasoning_effort": "medium"}, "must not decrease"),
        ({"escalation_service_tier": "priority"}, "must match service_tier"),
        ({"model": "gpt-6-astra", "escalation_model": "gpt-6-astra", "escalation_reasoning_effort": "max"}, "ceiling"),
        ({"model": "gpt-5.6-sol", "escalation_model": "gpt-5.6-sol", "escalation_reasoning_effort": "xhigh"}, "ceiling"),
    ]:
        invalid = {**metadata, **changes}
        assert_has_error("invalid escalation", validate_role_metadata(role_path, invalid), needle)
        expect_error("resolver rejects invalid escalation", lambda: role_config(invalid, "fixture-role"), needle)
    priority = {**metadata, "service_tier": "priority", "escalation_service_tier": "priority"}
    assert role_config(priority, "fixture-role", ["security"])["service_tier"] == "priority"

    for target in ("gpt-6-astra", "gpt-5.6-terra"):
        changed_model = {**metadata, "escalation_model": target}
        assert_has_error("model substitution", validate_role_metadata(role_path, changed_model), "escalation_model must match model")
        expect_error("resolver rejects model substitution", lambda: role_config(changed_model, "fixture-role", ["security"]), "escalation_model must match model")

    for effort in ("none", "minimal", "ultra"):
        unsupported = {**metadata, "model": "gpt-6-astra", "escalation_model": "gpt-6-astra", "reasoning_effort": effort}
        assert_has_error("unsupported portable reasoning", validate_role_metadata(role_path, unsupported), "invalid reasoning_effort")

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


def test_astra_sol_assignments() -> None:
    agents_dir = default_agents_dir()
    paths = sorted(agents_dir.glob("*.md"))
    assert len(paths) == 27
    for path in paths:
        metadata = read_frontmatter(path)
        role = path.stem
        effort = "xhigh" if role == "supervising-architect" else "high" if role in HIGH_REASONING_ROLES else "medium"
        escalation = "xhigh" if effort in {"high", "xhigh"} else "high"
        model = "gpt-5.6-sol" if role == "reviewer" else "gpt-6-astra"
        expected = (model, effort, model, escalation)
        actual = tuple(metadata[key] for key in ("model", "reasoning_effort", "escalation_model", "escalation_reasoning_effort"))
        assert actual == expected, (role, actual, expected)
        triggers = split_inline_list(metadata["escalation_triggers"])
        for count in range(len(triggers) + 1):
            for selected in combinations(triggers, count):
                result = role_config(metadata, role, list(selected))
                assert result["model"] == model
                assert result["reasoning_effort"] == (escalation if selected else effort)
                assert result["escalated"] == bool(selected and effort != escalation)
                assert result["service_tier"] == metadata.get("service_tier")
        assert role_config(metadata, role, ["unmatched"])["reasoning_effort"] == effort

    reviewer = agents_dir / "reviewer.md"
    metadata = read_frontmatter(reviewer)
    astra_metadata = {**metadata, "model": "gpt-6-astra", "escalation_model": "gpt-6-astra"}
    assert role_instructions(reviewer, metadata) == role_instructions(reviewer, astra_metadata)
    assert role_config(metadata, "reviewer", ["qa-critical"])["reasoning_effort"] == "xhigh"
    original_read = Path.read_text
    def missing_guidance(path, *args, **kwargs):
        if path.name == "astra-instructions.md":
            raise FileNotFoundError(path)
        return original_read(path, *args, **kwargs)
    with patch.object(Path, "read_text", missing_guidance):
        expect_error("missing shared instructions", lambda: role_instructions(reviewer, metadata), "could not read Astra instructions")


def test_resolver_instructions() -> None:
    command = [sys.executable, str(Path(__file__).with_name("resolve-agent-config.py")), "--role", "python-worker"]
    default = json.loads(subprocess.check_output(command, text=True))
    assert "developer_instructions" not in default
    escalated = json.loads(subprocess.check_output(command + ["--trigger", "public-contract", "--include-instructions"], text=True))
    assert escalated["model"] == default["model"] == "gpt-6-astra"
    assert default["reasoning_effort"] == "medium"
    assert escalated["reasoning_effort"] == "high"
    assert escalated["developer_instructions"].startswith("# Astra execution instructions\n")
    assert "Use the delegation packet as the source of truth" in escalated["developer_instructions"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-config-tests-") as temp_dir:
        root = Path(temp_dir)
        test_frontmatter_reader(root)
        test_role_validation(root)
        test_astra_sol_assignments()
        test_resolver_instructions()
    print("PASS agent config fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
