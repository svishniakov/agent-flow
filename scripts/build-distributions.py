#!/usr/bin/env python3
"""Build both Agent Flow distributions from one in-memory source snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "skills/agent-flow/scripts"))
from package_distribution import (BUILD_RECORD, RECORD, PackageError, digest, inventory,
                                  ci_metadata, json_bytes, marketplace, read_json, require, validate_manifest)


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


def distribution_files(source, commit_sha=None, run_id=None, run_attempt=None):
    metadata = ci_metadata(commit_sha, run_id, run_attempt)
    if metadata:
        check_checkout(source, commit_sha)
    manifest = read_json(source / ".codex-plugin/plugin.json")
    validate_manifest(manifest)
    package = inventory(source / "skills/agent-flow")
    hashes = {name: digest(data) for name, data in package.items()}
    package_hash = digest(json_bytes(hashes))
    outer = {}
    for name in ("README.md", "README.ru.md", "LICENSE"):
        path = source / name
        require(path.is_file() and not path.is_symlink(), f"missing plugin resource: {path}")
        outer[name] = path.read_bytes()
    snapshot_hash = digest(json_bytes({"package": hashes, "manifest": manifest, "outer": {name: digest(data) for name, data in outer.items()}}))
    if metadata:
        require(re.fullmatch(r"(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)", manifest["version"]), "CI base version must be X.Y.Z")
        manifest["version"] += f"-dev.{run_id}.{run_attempt}"
    else:
        manifest["version"] = manifest["version"].split("+", 1)[0] + "+codex." + snapshot_hash[:12]
    package[RECORD] = json_bytes({"version": manifest["version"], "files": hashes, "sha256": package_hash, **metadata})
    common = {"agent-flow/" + name: data for name, data in package.items()}
    plugin = {"agent-flow/skills/agent-flow/" + name: data for name, data in package.items()}
    plugin["agent-flow/.codex-plugin/plugin.json"] = json_bytes(manifest)
    plugin["agent-flow/.agents/plugins/marketplace.json"] = json_bytes(marketplace())
    build_info = {"format": "plugin", "version": manifest["version"], "package_sha256": package_hash, "snapshot_sha256": snapshot_hash, **metadata}
    plugin["agent-flow/" + BUILD_RECORD] = json_bytes(build_info)
    for name, data in outer.items():
        plugin["agent-flow/" + name] = data
    if metadata:
        check_checkout(source, commit_sha)
    return common, plugin, build_info


def build(source, output, commit_sha=None, run_id=None, run_attempt=None):
    source, output = source.resolve(), output.resolve()
    require(not output.is_relative_to(source), "output must be outside source tree and skill discovery")
    common, plugin, build_info = distribution_files(source, commit_sha, run_id, run_attempt)
    # Both archives already share the exact captured bytes before any output is written.
    stem = f"agent-flow-{build_info['version']}"
    results = {f"{stem}-skill.zip": archive_bytes(common), f"{stem}-codex-plugin.zip": archive_bytes(plugin)}
    sums = {name: digest(data) for name, data in results.items()}
    metadata_name = f"{stem}-build.json"
    if commit_sha is not None:
        results[metadata_name] = json_bytes({**build_info, "format": "dual", "archives": sums.copy()})
        sums[metadata_name] = digest(results[metadata_name])
    results[f"{stem}-SHA256SUMS.txt"] = "".join(f"{sha}  {name}\n" for name, sha in sorted(sums.items())).encode()
    for name, data in results.items():
        target = output / name
        require(not target.is_symlink(), f"refusing output symlink: {target}")
        require(not target.exists() or target.read_bytes() == data, f"output exists with different bytes: {target}")
    output.mkdir(parents=True, exist_ok=True)
    for name, data in results.items():
        if (output / name).exists():
            continue
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as tmp:
            tmp.write(data)
            temporary = Path(tmp.name)
        try:
            os.link(temporary, output / name)
        finally:
            temporary.unlink()
    result = {"version": build_info["version"], "package_sha256": build_info["package_sha256"],
              "archives": {str(output / name): sha for name, sha in sums.items() if name.endswith(".zip")},
              "checksums": str(output / f"{stem}-SHA256SUMS.txt")}
    if commit_sha is not None:
        result["metadata"] = str(output / metadata_name)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output, args.commit_sha, args.run_id, args.run_attempt), indent=2))
    except (PackageError, OSError, ValueError) as exc:
        parser.exit(1, f"build: {exc}\n")


if __name__ == "__main__":
    main()
