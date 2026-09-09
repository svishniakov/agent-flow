#!/usr/bin/env python3
"""Sync Codex custom-agent TOML files for Agent Flow roles."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, role_instructions, validate_role_metadata


MANAGED_HEADER = "# Synced by Agent Flow. Edit agents/agent-identities.json or agents/*.md, then rerun sync.\n"
NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _-]+$")


def default_output_dir() -> Path:
    return Path.cwd() / ".codex" / "agents"


def load_identities(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AgentConfigError(f"{path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AgentConfigError(f"{path}: invalid JSON: {exc}") from exc

    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list):
        raise AgentConfigError(f"{path}: field 'agents' must be an array")

    result: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(agents):
        if not isinstance(entry, dict):
            raise AgentConfigError(f"{path}: agents[{index}] must be an object")
        role = entry.get("role")
        if not isinstance(role, str) or not role:
            raise AgentConfigError(f"{path}: agents[{index}] missing role")
        result[role] = entry
    return result


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def toml_array(values: list[str]) -> str:
    return "[" + ", ".join(toml_string(value) for value in values) + "]"


def nickname_candidates(identity: dict[str, Any], role: str) -> list[str]:
    nicknames = identity.get("nickname_candidates")
    label = f"identity for role {role}"
    if not isinstance(nicknames, list):
        raise AgentConfigError(f"{label}: missing nickname_candidates array")
    if not nicknames:
        raise AgentConfigError(f"{label}: nickname_candidates must not be empty")

    seen: set[str] = set()
    result: list[str] = []
    for index, nickname in enumerate(nicknames):
        if not isinstance(nickname, str) or not nickname:
            raise AgentConfigError(f"{label}: nickname_candidates[{index}] must be a non-empty string")
        if nickname in seen:
            raise AgentConfigError(f"{label}: duplicate nickname candidate: {nickname}")
        if not NICKNAME_PATTERN.fullmatch(nickname):
            raise AgentConfigError(f"{label}: nickname_candidates[{index}] contains unsupported characters")
        seen.add(nickname)
        result.append(nickname)
    return result


def render_agent_toml(role_path: Path, metadata: dict[str, str], identity: dict[str, Any]) -> str:
    lines = [
        MANAGED_HEADER.rstrip(),
        f"name = {toml_string(metadata['name'])}",
        f"description = {toml_string(metadata['description'])}",
        f"model = {toml_string(metadata['model'])}",
        f"model_reasoning_effort = {toml_string(metadata['reasoning_effort'])}",
        f"nickname_candidates = {toml_array(nickname_candidates(identity, role_path.stem))}",
        f"developer_instructions = {toml_string(role_instructions(role_path, metadata))}",
        "",
    ]
    return "\n".join(lines)


def build_files(agents_dir: Path) -> dict[str, str]:
    identities = load_identities(agents_dir / "agent-identities.json")
    files: dict[str, str] = {}
    for role_path in sorted(agents_dir.glob("*.md")):
        metadata = read_frontmatter(role_path)
        errors = validate_role_metadata(role_path, metadata)
        if errors:
            message = "\n".join(f"{role_path}: {error}" for error in errors)
            raise AgentConfigError(message)
        role = role_path.stem
        identity = identities.get(role)
        if identity is None:
            raise AgentConfigError(f"{agents_dir / 'agent-identities.json'}: missing identity for role: {role}")
        files[f"{role}.toml"] = render_agent_toml(role_path, metadata, identity)
    return files


def managed_extra_files(output_dir: Path, expected_files: set[str]) -> list[Path]:
    if not output_dir.exists():
        return []
    extras: list[Path] = []
    for path in sorted(output_dir.glob("*.toml")):
        if path.name in expected_files:
            continue
        try:
            if path.read_text(encoding="utf-8").startswith(MANAGED_HEADER):
                extras.append(path)
        except OSError:
            continue
    return extras


def write_files(output_dir: Path, files: dict[str, str], check: bool) -> int:
    failures: list[str] = []
    expected_files = set(files)
    for filename, content in files.items():
        path = output_dir / filename
        if check:
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                failures.append(f"missing synced file: {path}")
                continue
            if current != content:
                failures.append(f"stale synced file: {path}")
            continue

        output_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for path in managed_extra_files(output_dir, expected_files):
        failures.append(f"extra managed synced file: {path}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir(), help="Directory with role .md files.")
    parser.add_argument("--output-dir", type=Path, default=default_output_dir(), help="Target .codex/agents directory.")
    parser.add_argument("--check", action="store_true", help="Check synced files instead of writing them.")
    args = parser.parse_args()

    try:
        files = build_files(args.agents_dir.expanduser().resolve())
    except AgentConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    output_dir = args.output_dir.expanduser().resolve()
    result = write_files(output_dir, files, args.check)
    if result != 0:
        return result

    action = "validated" if args.check else "synced"
    print(f"{action} {len(files)} Codex agent config files in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
