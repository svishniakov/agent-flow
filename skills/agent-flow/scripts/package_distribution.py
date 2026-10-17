"""Shared archive inventory and installed-package checks (Python standard library)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

PACKAGE_FILES = ("SKILL.md", "LICENSE", "check-tools.json")
PACKAGE_DIRS = ("agents", "references", "registries", "scripts", "docs/en", "docs/ru", "docs/assets")
PACKAGE_ROOT_ENTRIES = frozenset(PACKAGE_FILES) | {d.split("/", 1)[0] for d in PACKAGE_DIRS} | {"testdata", "agent-flow-package.json"}
OMIT = {"__pycache__", ".DS_Store", ".git", ".agent-work", ".codex", ".env", "node_modules"}
RECORD = "agent-flow-package.json"
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


class PackageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PackageError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path):
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents), f"symlink forbidden: {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise PackageError(f"{path}: {exc}") from exc
    require(isinstance(value, dict), f"{path}: expected JSON object")
    return value


def release_tag_name(release_tag):
    require(isinstance(release_tag, str) and re.fullmatch(
        r"v(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)", release_tag),
        "release-tag must be vX.Y.Z without leading zeros or suffixes")
    return release_tag


def inventory(root):
    """Collect the runtime package by walking the allowlisted files and directories."""
    require(not any(path.is_symlink() for path in (root, *root.parents)), f"symlink package root forbidden: {root}")
    for entry in root.iterdir():
        if entry.name in OMIT:
            continue
        require(entry.name in PACKAGE_ROOT_ENTRIES, f"unexpected package entry: {entry.name}")
    files = {}
    for name in PACKAGE_FILES:
        path = root / name
        require(path.is_file() and not path.is_symlink(), f"missing package resource: {path}")
        files[name] = path.read_bytes()
    for dirname in PACKAGE_DIRS:
        base = root / dirname
        require(base.is_dir() and not base.is_symlink(), f"missing package directory: {base}")
        for path in sorted(base.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            relative = path.relative_to(root)
            name = relative.as_posix()
            if OMIT.intersection(relative.parts) or "testdata" in relative.parts or name.endswith((".bak", ".pyc", ".pyo")):
                continue
            if dirname == "scripts" and path.name.startswith("test-") and path.suffix == ".py":
                continue
            require(not any(p.is_symlink() for p in (path, *path.parents) if p != root and p.is_relative_to(root)),
                    f"symlink forbidden in distribution: {path}")
            require(path.is_file(), f"missing or unsupported package resource: {path}")
            files[name] = path.read_bytes()
    require("agents/agent-identities.json" in files, "missing role identities")
    validate_links(files)
    return dict(sorted(files.items()))


def validate_links(files):
    for name, data in files.items():
        if not (name == "SKILL.md" or name.startswith(("references/", "agents/", "docs/en/", "docs/ru/"))) or not name.endswith(".md"):
            continue
        text = re.sub(r"```.*?```", "", data.decode(), flags=re.S)
        for link in re.findall(r"\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", text):
            url = urlsplit(link)
            if url.scheme or not url.path:
                continue
            parts = list(Path(name).parent.parts)
            for part in Path(unquote(url.path)).parts:
                if part == "..":
                    require(bool(parts), f"package link escapes root: {name}: {link}")
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            target = "/".join(parts)
            require(target in files or any(p.startswith(target + "/") for p in files), f"missing package link: {name}: {link}")


def marketplace():
    return {"name": "agent-flow", "interface": {"displayName": "Agent Flow"}, "plugins": [{
        "name": "agent-flow", "source": {"source": "local", "path": "./"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}


def validate_manifest(manifest):
    allowed = {"name", "version", "description", "author", "repository", "license", "skills", "interface"}
    require(set(manifest) <= allowed, "unsupported plugin manifest fields")
    require(manifest.get("name") == "agent-flow", "plugin name must be agent-flow")
    require(isinstance(manifest.get("version"), str) and SEMVER.fullmatch(manifest["version"]), "plugin version must be strict semver")
    require(manifest.get("skills") == "./skills/", "plugin skills must be ./skills/")
    require(isinstance(manifest.get("description"), str) and manifest["description"].strip(), "missing plugin description")
    require(isinstance(manifest.get("author"), dict) and manifest["author"].get("name"), "missing author.name")
    ui = manifest.get("interface")
    require(isinstance(ui, dict), "missing plugin interface")
    for field in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        require(isinstance(ui.get(field), str) and ui[field].strip(), f"missing interface.{field}")
    for field in ("capabilities", "defaultPrompt"):
        require(isinstance(ui.get(field), list) and ui[field] and all(isinstance(v, str) and v.strip() for v in ui[field]), f"invalid interface.{field}")
    require(set(ui) <= {"displayName", "shortDescription", "longDescription", "developerName", "category", "capabilities", "defaultPrompt"}, "unsupported interface field")


def validate_marketplace(data):
    require(data == marketplace(), "marketplace must contain agent-flow at ./ with required policy and category")


def plugin_root(package):
    parent = package.parent.parent
    markers = (".codex-plugin/plugin.json", ".agents/plugins/marketplace.json")
    return parent if package.parent.name == "skills" and any((parent / name).exists() or (parent / name).is_symlink() for name in markers) else None


def check_package(root):
    files = inventory(root)
    record_path = root / RECORD
    source = root.parent.parent
    record = None
    if record_path.exists():
        require(not record_path.is_symlink(), f"symlink forbidden: {record_path}")
        record = read_json(record_path)
        require(set(record) == {"version"} and isinstance(record["version"], str)
                and SEMVER.fullmatch(record["version"]), f"invalid package record: {record_path}")
    else:
        require(root.parent.name == "skills" and (source / ".git").exists()
                and (source / "scripts/build-plugin.py").is_file(), f"missing package record: {record_path}")
    parent = plugin_root(root)
    if parent is not None:
        manifest = read_json(parent / ".codex-plugin/plugin.json")
        validate_manifest(manifest)
        if record is not None:
            require(record["version"] == manifest["version"], "package record does not match plugin manifest")
        catalog = parent / ".agents/plugins/marketplace.json"
        if record is not None:
            require(catalog.is_file(), f"missing marketplace: {catalog}")
        if catalog.exists():
            validate_marketplace(read_json(catalog))
    return {"package_root": str(root), "format": "plugin" if parent else "skill", "files": len(files)}
