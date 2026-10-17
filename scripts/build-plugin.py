#!/usr/bin/env python3
"""Build the Agent Flow Codex plugin archive and verify the written bytes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
import sys
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "skills/agent-flow/scripts"))
from package_distribution import (RECORD, PackageError, check_package, digest, inventory,
                                  json_bytes, marketplace, read_json, release_tag_name,
                                  require, validate_manifest)


def archive_bytes(files):
    import io
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return output.getvalue()


def check_checkout(source, commit_sha):
    def git(*args):
        result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True)
        require(result.returncode == 0, "cannot inspect Git checkout: " + result.stderr.strip())
        return result.stdout.strip()
    require(Path(git("rev-parse", "--show-toplevel")).resolve() == source, "source must be the Git checkout root")
    require(git("rev-parse", "HEAD") == commit_sha, "commit-sha does not match Git HEAD")
    require(not git("status", "--porcelain", "--untracked-files=no"), "tracked source files have changes")


def plugin_files(source, commit_sha=None, release_tag=None):
    if release_tag is not None:
        require(isinstance(commit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", commit_sha),
                "release commit-sha must be a full lowercase Git SHA")
        release_tag_name(release_tag)
    elif commit_sha is not None:
        require(isinstance(commit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", commit_sha),
                "commit-sha must be a full lowercase Git SHA")
    if commit_sha is not None:
        check_checkout(source, commit_sha)
    manifest = read_json(source / ".codex-plugin/plugin.json")
    validate_manifest(manifest)
    package = inventory(source / "skills/agent-flow")
    outer = {}
    for name in ("README.md", "README.ru.md", "LICENSE"):
        path = source / name
        require(path.is_file() and not path.is_symlink(), f"missing plugin resource: {path}")
        outer[name] = path.read_bytes()
    snapshot_hash = digest(json_bytes({"package": {name: digest(data) for name, data in package.items()},
                                       "manifest": manifest,
                                       "outer": {name: digest(data) for name, data in outer.items()}}))
    if release_tag is not None:
        require(manifest["version"] == release_tag[1:], "release tag does not match manifest version")
    else:
        manifest["version"] = manifest["version"].split("+", 1)[0] + "+codex." + snapshot_hash[:12]
    package[RECORD] = json_bytes({"version": manifest["version"]})
    files = {"agent-flow/skills/agent-flow/" + name: data for name, data in package.items()}
    files["agent-flow/.codex-plugin/plugin.json"] = json_bytes(manifest)
    files["agent-flow/.agents/plugins/marketplace.json"] = json_bytes(marketplace())
    for name, data in outer.items():
        files["agent-flow/" + name] = data
    if commit_sha is not None:
        check_checkout(source, commit_sha)
    return files, manifest["version"]


def read_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), f"duplicate archive members: {path.name}")
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            require(not name.is_absolute() and ".." not in name.parts
                    and name.as_posix() == member.filename and "\\" not in member.filename,
                    f"unsafe archive member: {member.filename}")
            require(member.external_attr >> 16 == 0o100644 and not member.is_dir(),
                    f"unsupported archive member: {member.filename}")
        return {name: archive.read(name) for name in names}


def verify_archive(source, path, commit_sha=None, release_tag=None):
    """Re-read the written archive, compare with a fresh snapshot, and smoke the package."""
    files, version = plugin_files(source, commit_sha, release_tag)
    require(path.name == f"agent-flow-{version}-codex-plugin.zip", f"unexpected archive name: {path.name}")
    require(read_archive(path) == files, f"archive bytes or metadata mismatch: {path.name}")
    with tempfile.TemporaryDirectory(prefix="agent-flow-verify-") as temporary:
        base = Path(temporary).resolve()
        project = base / "project"
        project.mkdir()
        profile = base / "profile"
        profile.mkdir()
        environment = {"PATH": os.environ.get("PATH", ""), "CODEX_HOME": str(profile),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        installed = base / "install"
        for relative, data in files.items():
            target = installed / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        package = installed / "agent-flow/skills/agent-flow"
        check_package(package)
        roles = profile / "roles"
        commands = [
            [str(package / "scripts/check-installed-package.py"), "--package-only"],
            [str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(roles)],
            [str(package / "scripts/check-installed-package.py"), "--roles-dir", str(roles),
             "--project", str(project)],
        ]
        for command in commands:
            result = subprocess.run([sys.executable, "-B", *command], cwd=project,
                                    env=environment, capture_output=True, text=True)
            require(result.returncode == 0, "installed package command failed: " +
                    " ".join(command) + "\n" + result.stdout + result.stderr)
    return version


def build(source, output, commit_sha=None, release_tag=None):
    source, output = source.resolve(), output.resolve()
    require(not output.is_relative_to(source), "output must be outside source tree and skill discovery")
    files, version = plugin_files(source, commit_sha, release_tag)
    data = archive_bytes(files)
    target = output / f"agent-flow-{version}-codex-plugin.zip"
    require(not target.is_symlink(), f"refusing output symlink: {target}")
    require(not target.exists() or target.read_bytes() == data, f"output exists with different bytes: {target}")
    output.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as tmp:
            tmp.write(data)
            temporary = Path(tmp.name)
        try:
            os.link(temporary, target)
        finally:
            temporary.unlink()
        try:
            verify_archive(source, target, commit_sha, release_tag)
        except BaseException:
            target.unlink()
            raise
    else:
        verify_archive(source, target, commit_sha, release_tag)
    return {"version": version, "archive": str(target), "sha256": digest(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit-sha")
    parser.add_argument("--release-tag")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output, args.commit_sha, args.release_tag), indent=2))
    except (PackageError, OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"build: {exc}\n")


if __name__ == "__main__":
    main()
