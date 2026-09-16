#!/usr/bin/env python3
"""Sync Codex custom-agent TOML files for Agent Flow roles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from agent_config import AgentConfigError, default_agents_dir, read_frontmatter, validate_role_metadata


MANAGED_HEADER = "# Synced by Agent Flow. Edit agents/agent-identities.json or agents/*.md, then rerun sync.\n"
NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9 _-]+$")
BASELINE_NAME = ".agent-flow-baseline.json"


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


def role_body(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        end_index = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise AgentConfigError(f"{path}: missing closing frontmatter marker") from exc
    return "\n".join(lines[end_index + 1 :]).strip() + "\n"


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
        f"developer_instructions = {toml_string(role_body(role_path))}",
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
        if path.name in expected_files or path.is_symlink() or not path.is_file():
            continue
        try:
            if path.read_bytes().startswith(MANAGED_HEADER.encode("utf-8")):
                extras.append(path)
        except OSError:
            continue
    return extras


def load_baseline(path: Path) -> dict[str, str] | None:
    if path.is_symlink():
        raise AgentConfigError(f"symlink baseline: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise AgentConfigError(f"not a regular baseline file: {path}")

    def unique_entries(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        baseline = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_entries)
        if not isinstance(baseline, dict) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+\.toml", name)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for name, digest in baseline.items()
        ):
            raise ValueError("expected a map of role TOML filenames to SHA-256 hashes")
    except (OSError, ValueError) as exc:
        raise AgentConfigError(f"invalid baseline: {path}: {exc}") from exc
    return baseline


def replace_file(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_files(output_dir: Path, files: dict[str, str], check: bool) -> int:
    failures: list[str] = []
    expected_files = set(files)
    # Keep the output path unresolved so a symlink cannot silently become a write target.
    for directory in (output_dir, *output_dir.parents):
        if directory.is_symlink():
            print(f"symlink output directory: {directory}", file=sys.stderr)
            return 1
        if directory.exists() and not directory.is_dir():
            print(f"not an output directory: {directory}", file=sys.stderr)
            return 1

    baseline_path = output_dir / BASELINE_NAME
    try:
        baseline = load_baseline(baseline_path)
    except AgentConfigError as exc:
        failures.append(str(exc))
        baseline = None
    if check and baseline is None:
        failures.append(f"missing valid sync baseline: {baseline_path}; rerun sync")

    previous = baseline or {}
    next_baseline: dict[str, str] = {}
    updates: dict[Path, bytes] = {}
    for filename, content in files.items():
        path = output_dir / filename
        expected = content.encode("utf-8")
        next_baseline[filename] = hashlib.sha256(expected).hexdigest()
        if path.is_symlink():
            failures.append(f"symlink synced file: {path}")
            continue
        if path.exists() and not path.is_file():
            failures.append(f"not a regular synced file: {path}")
            continue
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        except OSError as exc:
            failures.append(f"cannot read synced file: {path}: {exc}")
            continue

        if check:
            if current is None:
                failures.append(f"missing synced file: {path}")
            elif current != expected:
                failures.append(f"stale synced file: {path}")
            if previous.get(filename) != next_baseline[filename]:
                failures.append(f"stale sync baseline for: {path}")
        elif current is not None:
            if filename in previous:
                if hashlib.sha256(current).hexdigest() != previous[filename]:
                    failures.append(f"edited managed synced file: {path}")
            elif current != expected:
                failures.append(f"unowned synced file (no trusted baseline): {path}")
        # Byte-identical legacy files may be adopted, but are never rewritten.
        if current != expected:
            updates[path] = expected

    extras = set(managed_extra_files(output_dir, expected_files))
    extras.update(output_dir / name for name in previous.keys() - expected_files)
    for path in sorted(extras):
        failures.append(f"extra managed synced file: {path}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    if not check:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            for path, content in updates.items():
                replace_file(path, content)
            if baseline != next_baseline:
                encoded = (json.dumps(next_baseline, indent=2, sort_keys=True) + "\n").encode("utf-8")
                replace_file(baseline_path, encoded)
        except OSError as exc:
            print(f"sync write failed: {exc}; rerun --check before retrying", file=sys.stderr)
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

    output_dir = args.output_dir.expanduser().absolute()
    result = write_files(output_dir, files, args.check)
    if result != 0:
        return result

    action = "validated" if args.check else "synced"
    print(f"{action} {len(files)} Codex agent config files in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
