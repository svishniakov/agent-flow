"""Shared Agent Flow skill dependency registry helpers."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


FRONTMATTER_BOUNDARY = "---"
SKILL_FILE = "SKILL.md"
REGISTRY_VERSION = 1

TIERS = {"core", "recommended", "optional"}
SOURCE_TYPES = {"git", "plugin", "prompt", "manual", "local"}
SCOPES = {"core", "full"}
TARGETS = {"auto", "global", "project"}
EXECUTABLE_SOURCE_TYPES = {"git", "local"}
NON_EXECUTABLE_SOURCE_TYPES = {"manual", "plugin", "prompt"}

CORE_SKILLS = {
    "application-quality-assurance",
    "apple-hig-designer",
    "apple-ui-designer",
    "browser-debugging",
    "browser-use",
    "build-web-apps:frontend-skill",
    "build-web-apps:react-best-practices",
    "bun",
    "bun-dev",
    "find-skills",
    "golang-code-style",
    "golang-lint",
    "golang-modernize",
    "lazyweb",
    "python-packaging",
    "python-testing-patterns",
    "test-scenarios",
}

CORE_PREFIXES = (
    "build-ios-apps:",
    "figma:",
    "stitch::",
)

PLUGIN_PREFIXES = {
    "build-ios-apps",
    "build-web-apps",
    "codex-reviewer",
    "figma",
    "game-studio",
    "github",
    "hugging-face",
    "lazyweb",
}


@dataclass
class RoleSkills:
    name: str
    path: Path
    skills: list[str]


@dataclass
class SkillHit:
    locations: set[str] = field(default_factory=set)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_agents_dir() -> Path:
    return repo_root() / "agents"


def default_registry_path() -> Path:
    return repo_root() / "registries" / "agent-skills.json"


def default_skill_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".codex" / "skills",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
    ]


def default_plugin_caches() -> list[Path]:
    return [Path.home() / ".codex" / "plugins" / "cache"]


def parse_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_BOUNDARY:
        return {}

    try:
        end_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == FRONTMATTER_BOUNDARY
        )
    except StopIteration as exc:
        raise ValueError(f"{path}: missing closing frontmatter marker") from exc

    metadata: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:end_index], start=2):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{line_number}: invalid frontmatter line")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"{path}:{line_number}: empty frontmatter key")
        metadata[key] = raw_value.strip()
    return metadata


def strip_yaml_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_inline_list(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    for char in value:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escape = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            current.append(char)
            continue
        if char == "," and quote is None:
            item = strip_yaml_quotes("".join(current).strip())
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)

    item = strip_yaml_quotes("".join(current).strip())
    if item:
        items.append(item)
    return items


def discover_role_skills(agents_dir: Path) -> list[RoleSkills]:
    roles: list[RoleSkills] = []
    for path in sorted(agents_dir.glob("*.md")):
        metadata = parse_frontmatter(path)
        role_name = strip_yaml_quotes(metadata.get("name", "")).strip() or path.stem
        skills = split_inline_list(metadata.get("skills", ""))
        roles.append(RoleSkills(name=role_name, path=path, skills=skills))
    return roles


def role_skill_map(roles: list[RoleSkills]) -> dict[str, list[str]]:
    usage: dict[str, set[str]] = {}
    for role in roles:
        for skill in role.skills:
            usage.setdefault(skill, set()).add(role.name)
    return {skill: sorted(names) for skill, names in sorted(usage.items())}


def read_skill_name(skill_file: Path) -> str | None:
    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    if not lines or lines[0].strip() != FRONTMATTER_BOUNDARY:
        return None

    try:
        end_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == FRONTMATTER_BOUNDARY
        )
    except StopIteration:
        return None

    for line in lines[1:end_index]:
        stripped = line.strip()
        if stripped.startswith("name:"):
            return strip_yaml_quotes(stripped.split(":", 1)[1].strip()) or None
    return None


def add_hit(index: dict[str, SkillHit], alias: str | None, location: Path) -> None:
    if not alias:
        return
    normalized = alias.strip()
    if not normalized:
        return
    index.setdefault(normalized, SkillHit()).locations.add(str(location))


def skill_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.name == SKILL_FILE else []
    return sorted(root.rglob(SKILL_FILE))


def index_skill_root(root: Path, index: dict[str, SkillHit]) -> None:
    root = root.expanduser()
    for skill_file in skill_files(root):
        skill_dir = skill_file.parent
        add_hit(index, skill_dir.name, skill_file)
        add_hit(index, read_skill_name(skill_file), skill_file)


def infer_plugin_slug(cache_root: Path, skill_file: Path) -> str | None:
    try:
        parts = skill_file.relative_to(cache_root).parts
    except ValueError:
        return None
    if "skills" not in parts:
        return None
    skills_index = parts.index("skills")
    if skills_index >= 2:
        return parts[skills_index - 2]
    if skills_index >= 1:
        return parts[skills_index - 1]
    return None


def index_plugin_cache(cache_root: Path, index: dict[str, SkillHit]) -> None:
    cache_root = cache_root.expanduser()
    for skill_file in skill_files(cache_root):
        skill_dir = skill_file.parent
        name = read_skill_name(skill_file)
        plugin_slug = infer_plugin_slug(cache_root, skill_file)

        add_hit(index, skill_dir.name, skill_file)
        add_hit(index, name, skill_file)
        if plugin_slug:
            add_hit(index, plugin_slug, skill_file)
            for alias in {skill_dir.name, name}:
                if alias:
                    add_hit(index, f"{plugin_slug}:{alias}", skill_file)
                    add_hit(index, f"{plugin_slug}::{alias}", skill_file)


def build_installed_index(skill_roots: list[Path], plugin_caches: list[Path]) -> dict[str, SkillHit]:
    index: dict[str, SkillHit] = {}
    for root in skill_roots:
        index_skill_root(root, index)
    for cache in plugin_caches:
        index_plugin_cache(cache, index)
    return index


def load_registry(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: registry must be a JSON object")
    if not isinstance(data.get("skills"), list):
        raise ValueError(f"{path}: registry field 'skills' must be an array")
    return data


def registry_entries(registry: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    if not registry:
        return entries
    for entry in registry.get("skills", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            entries[entry["name"]] = entry
    return entries


def skill_slug(name: str) -> str:
    slug = name.replace("::", "-").replace(":", "-").replace("/", "-").lower()
    return re.sub(r"[^a-z0-9._-]+", "-", slug).strip("-")


def source_type_for(name: str) -> str:
    if name == "lazyweb" or name.startswith("lazyweb"):
        return "prompt"
    if name.startswith("stitch::"):
        return "manual"
    if ":" in name and name.split(":", 1)[0] in PLUGIN_PREFIXES:
        return "plugin"
    return "manual"


def tier_for(name: str) -> str:
    if name in CORE_SKILLS or any(name.startswith(prefix) for prefix in CORE_PREFIXES):
        return "core"
    return "optional"


def aliases_for(name: str) -> list[str]:
    aliases: set[str] = set()
    if name.startswith("stitch::"):
        aliases.add(name.replace("::", "-"))
    if name == "lazyweb":
        aliases.update({"lazyweb-quick-references", "lazyweb:lazyweb-quick-references"})
    if ":" in name:
        prefix, rest = name.split(":", 1)
        aliases.add(rest.lstrip(":"))
        aliases.add(name.replace("::", ":"))
        aliases.add(f"{prefix}::{rest.lstrip(':')}")
    aliases.discard(name)
    return sorted(alias for alias in aliases if alias)


def target_paths(name: str, source_type: str) -> tuple[str, str]:
    slug = skill_slug(name)
    if source_type == "plugin":
        plugin = name.split(":", 1)[0]
        plugin_skill = skill_slug(name.split(":", 1)[1].lstrip(":"))
        return (
            f"~/.codex/plugins/cache/{plugin}/**/skills/{plugin_skill}",
            f".codex/plugins/{plugin} (if project plugins are supported by the host)",
        )
    return (f"~/.codex/skills/{slug}", f".codex/skills/{slug}")


def install_instructions(name: str, source_type: str) -> tuple[str, str, str | None, str]:
    if source_type == "prompt" and name == "lazyweb":
        prompt = (
            "Enable the global Lazyweb plugin, or get the free one-line install prompt at "
            "https://lazyweb.com/#pricing, paste it into this agent, then rerun the dependency check."
        )
        return (
            "Install Lazyweb globally through its official plugin or one-line prompt.",
            "Use the same Lazyweb prompt in the project agent context if project-local setup is required.",
            prompt,
            "Prompt/manual source; the checker must not execute this automatically.",
        )
    if source_type == "plugin":
        plugin = name.split(":", 1)[0]
        text = f"Enable or install the `{plugin}` plugin in the Codex host, then rerun the dependency check."
        return (
            text,
            f"Enable `{plugin}` for this project if the host supports project-scoped plugins; otherwise install globally.",
            None,
            "Plugin-provided skill; no direct skill copy command is bundled.",
        )
    if name.startswith("stitch::"):
        return (
            "Install the Stitch skills bundle into the global skill root and enable Stitch MCP tools.",
            "Install the Stitch skills bundle into this project's `.codex/skills` directory and enable Stitch MCP tools for the project.",
            None,
            "Manual source; keep Stitch skill directory names such as `stitch-generate-design`.",
        )
    return (
        f"Install `{name}` into the global skill root, then rerun the dependency check.",
        f"Install `{name}` into the project skill root, then rerun the dependency check.",
        None,
        "Manual source; this registry has no authoritative public source yet.",
    )


def default_install(name: str, source_type: str) -> dict[str, Any]:
    global_path, project_path = target_paths(name, source_type)
    global_instructions, project_instructions, prompt, notes = install_instructions(name, source_type)
    return {
        "source_type": source_type,
        "global": {
            "path": global_path,
            "instructions": global_instructions,
        },
        "project": {
            "path": project_path,
            "instructions": project_instructions,
        },
        "commands": {
            "global": [],
            "project": [],
        },
        "prompt": prompt,
        "notes": notes,
    }


def make_registry_entry(
    name: str,
    roles: list[str],
    installed: dict[str, SkillHit],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if existing:
        entry = dict(existing)
        entry.pop("installed_locations", None)
        entry["required_by_roles"] = roles
        entry.setdefault("aliases", aliases_for(name))
        entry.setdefault("tier", tier_for(name))
        entry.setdefault("install", default_install(name, source_type_for(name)))
        return entry

    source_type = source_type_for(name)
    hit = installed.get(name)
    entry = {
        "name": name,
        "aliases": aliases_for(name),
        "tier": tier_for(name),
        "required_by_roles": roles,
        "install": default_install(name, source_type),
    }
    return entry


def build_registry(
    roles: list[RoleSkills],
    installed: dict[str, SkillHit],
    existing_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    usage = role_skill_map(roles)
    existing = registry_entries(existing_registry)
    existing_names = set(existing)
    active_names = set(usage)

    skills = [make_registry_entry(name, usage[name], installed, existing.get(name)) for name in sorted(active_names)]

    for name in sorted(existing_names - active_names):
        entry = dict(existing[name])
        if entry.get("status") == "retired":
            skills.append(entry)

    return {
        "version": REGISTRY_VERSION,
        "description": "Canonical registry for optional skills referenced by bundled Agent Flow subagents.",
        "skills": skills,
    }


def detect_target(explicit_target: str) -> str:
    if explicit_target != "auto":
        return explicit_target

    root = repo_root().resolve()
    home = Path.home().resolve()
    global_skill = home / ".codex" / "skills" / "agent-flow"
    try:
        if global_skill.exists() and global_skill.resolve() == root:
            return "global"
    except OSError:
        pass

    parts = root.parts
    for index, part in enumerate(parts[:-2]):
        if part == ".codex" and parts[index + 1] == "skills" and parts[index + 2] == "agent-flow":
            return "project"
    return "global"


def entry_matches_installed(entry: dict[str, Any], installed: dict[str, SkillHit]) -> SkillHit | None:
    names = [entry.get("name", ""), *entry.get("aliases", [])]
    locations: set[str] = set()
    for name in names:
        hit = installed.get(name)
        if hit:
            locations.update(hit.locations)
    if not locations:
        return None
    return SkillHit(locations=locations)


def role_filter(roles: list[RoleSkills], scope: str) -> set[str] | None:
    if not scope.startswith("role:"):
        return None
    role_name = scope.split(":", 1)[1]
    for role in roles:
        if role.name == role_name:
            return set(role.skills)
    raise ValueError(f"unknown role scope: {role_name}")


def build_report(
    roles: list[RoleSkills],
    installed: dict[str, SkillHit],
    registry: dict[str, Any] | None,
    scope: str = "full",
    target: str = "auto",
) -> dict[str, Any]:
    usage = role_skill_map(roles)
    registry_by_name = registry_entries(registry)
    selected_role_skills = role_filter(roles, scope)
    selected_target = detect_target(target)

    skill_rows: list[dict[str, Any]] = []
    for skill in sorted(usage):
        entry = registry_by_name.get(skill)
        tier = entry.get("tier", "optional") if entry else "optional"
        if scope == "core" and tier != "core":
            continue
        if selected_role_skills is not None and skill not in selected_role_skills:
            continue

        hit = entry_matches_installed(entry, installed) if entry else installed.get(skill)
        install = entry.get("install") if entry else None
        skill_rows.append(
            {
                "name": skill,
                "status": "installed" if hit else "missing",
                "tier": tier,
                "locations": sorted(hit.locations) if hit else [],
                "roles": usage[skill],
                "registry_status": "known" if entry else "unregistered",
                "install": install,
            }
        )

    role_rows: list[dict[str, Any]] = []
    selected_names = {row["name"] for row in skill_rows}
    missing_names = {row["name"] for row in skill_rows if row["status"] == "missing"}
    for role in sorted(roles, key=lambda item: item.name):
        scoped_skills = sorted(skill for skill in role.skills if skill in selected_names)
        role_rows.append(
            {
                "name": role.name,
                "path": str(role.path),
                "skills": scoped_skills,
                "missing_skills": sorted(skill for skill in scoped_skills if skill in missing_names),
            }
        )

    installed_count = sum(1 for row in skill_rows if row["status"] == "installed")
    missing_count = sum(1 for row in skill_rows if row["status"] == "missing")
    return {
        "summary": {
            "total": len(skill_rows),
            "installed": installed_count,
            "missing": missing_count,
            "scope": scope,
            "target": selected_target,
        },
        "skills": skill_rows,
        "roles": role_rows,
    }


def missing_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in report["skills"] if row["status"] == "missing"]


def install_text(row: dict[str, Any], target: str) -> list[str]:
    install = row.get("install") or {}
    target_info = install.get(target) or {}
    commands = install_commands(install, target)
    lines = [f"- {row['name']} [{row.get('tier', 'optional')}]"]
    roles = ", ".join(row.get("roles", []))
    if roles:
        lines.append(f"  roles: {roles}")
    if target_info.get("path"):
        lines.append(f"  target path: {target_info['path']}")
    if target_info.get("instructions"):
        lines.append(f"  instructions: {target_info['instructions']}")
    if install.get("prompt"):
        lines.append(f"  prompt: {install['prompt']}")
    if commands:
        lines.append("  commands:")
        for command in commands:
            lines.append(f"    {command}")
    if install.get("notes"):
        lines.append(f"  notes: {install['notes']}")
    return lines


def install_commands(install: dict[str, Any], target: str) -> list[str]:
    raw_commands = install.get("commands", [])
    if isinstance(raw_commands, dict):
        target_commands = raw_commands.get(target, [])
    else:
        target_commands = raw_commands
    if not isinstance(target_commands, list):
        return []
    return [command.strip() for command in target_commands if isinstance(command, str) and command.strip()]


def has_any_install_commands(install: dict[str, Any]) -> bool:
    raw_commands = install.get("commands", [])
    if isinstance(raw_commands, dict):
        return any(install_commands(install, target) for target in ("global", "project"))
    return bool(install_commands(install, "global"))


def install_plan_counts(rows: list[dict[str, Any]], target: str) -> tuple[int, int]:
    executable = 0
    non_executable = 0
    for row in rows:
        install = row.get("install") or {}
        if install.get("source_type") in EXECUTABLE_SOURCE_TYPES and install_commands(install, target):
            executable += 1
        else:
            non_executable += 1
    return executable, non_executable


def print_install_plan(report: dict[str, Any]) -> None:
    rows = missing_rows(report)
    target = report["summary"]["target"]
    if not rows:
        print(f"No missing skills for scope={report['summary']['scope']} target={target}.")
        return
    print(f"Install plan for scope={report['summary']['scope']} target={target}:")
    executable, non_executable = install_plan_counts(rows, target)
    print(f"Executable: {executable}")
    print(f"Manual/plugin/prompt: {non_executable}")
    for row in rows:
        for line in install_text(row, target):
            print(line)


def executable_commands(rows: list[dict[str, Any]], target: str) -> tuple[list[str], list[dict[str, Any]]]:
    commands: list[str] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        install = row.get("install") or {}
        row_commands = install_commands(install, target)
        if install.get("source_type") not in EXECUTABLE_SOURCE_TYPES or not row_commands:
            skipped.append(row)
            continue
        commands.extend(row_commands)
    return commands, skipped


def run_guided_install(report: dict[str, Any]) -> int:
    rows = missing_rows(report)
    target = report["summary"]["target"]
    if not rows:
        print("No missing skills. Nothing to install.")
        return 0

    executable, non_executable = install_plan_counts(rows, target)
    print(f"Install plan for scope={report['summary']['scope']} target={target}:")
    print(f"Executable: {executable}")
    print(f"Manual/plugin/prompt: {non_executable}")

    commands, skipped = executable_commands(rows, target)
    if skipped:
        print("Manual or prompt-only entries:")
        for row in skipped:
            for line in install_text(row, target):
                print(line)
    if not commands:
        print("No allowlisted executable commands available for guided install.")
        return 0

    print("Allowlisted commands to run:")
    for command in commands:
        print(f"- {command}")
    answer = input("Proceed with guided install? Type yes: ").strip()
    if answer != "yes":
        print("Aborted.")
        return 1
    for command in commands:
        try:
            subprocess.run(command, shell=True, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"command failed ({exc.returncode}): {command}")
            return exc.returncode or 1
    return 0


def validate_registry(roles: list[RoleSkills], registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    usage = role_skill_map(roles)
    entries = registry_entries(registry)

    if registry.get("version") != REGISTRY_VERSION:
        errors.append(f"registry version must be {REGISTRY_VERSION}")

    missing = sorted(set(usage) - set(entries))
    if missing:
        errors.append(f"skills missing from registry: {', '.join(missing)}")

    stale = sorted(name for name in set(entries) - set(usage) if entries[name].get("status") != "retired")
    if stale:
        errors.append(f"stale registry entries must be retired or removed: {', '.join(stale)}")

    for name, entry in entries.items():
        tier = entry.get("tier")
        if tier not in TIERS:
            errors.append(f"{name}: invalid tier: {tier}")
        roles_value = entry.get("required_by_roles")
        if not isinstance(roles_value, list):
            errors.append(f"{name}: required_by_roles must be an array")
        elif entry.get("status") != "retired" and sorted(roles_value) != usage.get(name, []):
            errors.append(f"{name}: required_by_roles do not match agents/*.md")

        install = entry.get("install")
        if not isinstance(install, dict):
            errors.append(f"{name}: install must be an object")
            continue
        if install.get("source_type") not in SOURCE_TYPES:
            errors.append(f"{name}: invalid source_type: {install.get('source_type')}")
        for target in ("global", "project"):
            target_info = install.get(target)
            if not isinstance(target_info, dict):
                errors.append(f"{name}: install.{target} must be an object")
                continue
            if not target_info.get("instructions"):
                errors.append(f"{name}: install.{target}.instructions is required")
            if tier == "core" and not target_info.get("path"):
                errors.append(f"{name}: core install.{target}.path is required")
        if not isinstance(install.get("commands", []), list):
            commands_value = install.get("commands", [])
            if not isinstance(commands_value, dict):
                errors.append(f"{name}: install.commands must be an array or target object")
            else:
                for target in ("global", "project"):
                    if not isinstance(commands_value.get(target, []), list):
                        errors.append(f"{name}: install.commands.{target} must be an array")
        source_type = install.get("source_type")
        if tier == "core" and source_type in EXECUTABLE_SOURCE_TYPES and not has_any_install_commands(install):
            errors.append(f"{name}: core {source_type} entry must include allowlisted commands")
        if tier == "core" and source_type in NON_EXECUTABLE_SOURCE_TYPES and not install.get("notes"):
            errors.append(f"{name}: core {source_type} entry must explain why it is non-executable")

    return errors
