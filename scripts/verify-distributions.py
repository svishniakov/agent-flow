#!/usr/bin/env python3
"""Verify CI archives against their checkout and check unpacked packages in a clean profile."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "skills/agent-flow/scripts"))
from package_distribution import PackageError, check_package, digest, json_bytes, require

spec = importlib.util.spec_from_file_location("build_distributions", SOURCE / "scripts/build-distributions.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def read_archive(path, expected):
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
        require(set(names) == set(expected), f"archive member inventory mismatch: {path.name}")
        files = {name: archive.read(name) for name in names}
    require(files == expected, f"archive bytes or metadata mismatch: {path.name}")
    return files


def verify(source, directory, commit_sha, run_id=None, run_attempt=None, release_tag=None):
    source, directory = source.resolve(), directory.resolve()
    common, plugin, info = builder.distribution_files(source, commit_sha, run_id, run_attempt, release_tag)
    require(commit_sha is not None, "CI identity is required")
    stem = f"agent-flow-{info['version']}"
    archives = {f"{stem}-skill.zip": common}
    if release_tag is None:
        archives[f"{stem}-codex-plugin.zip"] = plugin
    metadata_name, sums_name = f"{stem}-build.json", f"{stem}-SHA256SUMS.txt"
    expected_names = set(archives) | {metadata_name, sums_name}
    require(directory.is_dir() and {p.name for p in directory.iterdir()} == expected_names,
            "distribution directory must contain exactly the expected archives, metadata and SHA256SUMS")
    for name in expected_names:
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"unsupported distribution file: {name}")
    archive_hashes = {name: digest((directory / name).read_bytes()) for name in archives}
    require((directory / metadata_name).read_bytes() == json_bytes(
        {**info, "format": "skill" if release_tag is not None else "dual", "archives": archive_hashes}), "download metadata mismatch")
    hashes = {**archive_hashes, metadata_name: digest((directory / metadata_name).read_bytes())}
    expected_sums = "".join(f"{sha}  {name}\n" for name, sha in sorted(hashes.items())).encode()
    require((directory / sums_name).read_bytes() == expected_sums, "SHA256SUMS mismatch")
    contents = {name: read_archive(directory / name, files) for name, files in archives.items()}
    if release_tag is not None:
        for name, files in archives.items():
            require((directory / name).read_bytes() == builder.archive_bytes(files),
                    f"release archive bytes are not reproducible: {name}")
    # Only previously validated regular files are materialized; ZIP extraction is never delegated.
    with tempfile.TemporaryDirectory(prefix="agent-flow-verify-") as temporary:
        base = Path(temporary).resolve()
        project = base / "project"
        project.mkdir()
        profile = base / "profile"
        profile.mkdir()
        environment = {"PATH": os.environ.get("PATH", ""), "CODEX_HOME": str(profile),
                       "PYTHONDONTWRITEBYTECODE": "1"}
        for index, (name, files) in enumerate(contents.items()):
            installed = base / f"install-{index}"
            for relative, data in files.items():
                path = installed / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            package = installed / ("agent-flow/skills/agent-flow" if name.endswith("-codex-plugin.zip") else "agent-flow")
            check_package(package)
            roles = profile / f"roles-{index}"
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
    identity = ({"release_tag": release_tag} if release_tag is not None else
                {"run_id": run_id, "run_attempt": run_attempt})
    return {"version": info["version"], "commit_sha": commit_sha, **identity,
            "package_sha256": info["package_sha256"],
            "archives": archive_hashes, "unpacked_package_checks": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--release-tag")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.source, args.directory, args.commit_sha,
                                args.run_id, args.run_attempt, args.release_tag), indent=2))
    except (PackageError, OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"verify: {exc}\n")


if __name__ == "__main__":
    main()
