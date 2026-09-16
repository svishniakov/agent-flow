#!/usr/bin/env python3
"""Build both Agent Flow distributions from one in-memory source snapshot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "skills/agent-flow/scripts"))
from package_distribution import (BUILD_RECORD, RECORD, PackageError, digest, inventory,
                                  json_bytes, marketplace, read_json, require, validate_manifest)


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


def build(source, output):
    source, output = source.resolve(), output.resolve()
    require(not output.is_relative_to(source), "output must be outside source tree and skill discovery")
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
    manifest["version"] = manifest["version"].split("+", 1)[0] + "+codex." + snapshot_hash[:12]
    package[RECORD] = json_bytes({"version": manifest["version"], "files": hashes, "sha256": package_hash})
    common = {"agent-flow/" + name: data for name, data in package.items()}
    plugin = {"agent-flow/skills/agent-flow/" + name: data for name, data in package.items()}
    plugin["agent-flow/.codex-plugin/plugin.json"] = json_bytes(manifest)
    plugin["agent-flow/.agents/plugins/marketplace.json"] = json_bytes(marketplace())
    build_info = {"format": "plugin", "version": manifest["version"], "package_sha256": package_hash, "snapshot_sha256": snapshot_hash}
    plugin["agent-flow/" + BUILD_RECORD] = json_bytes(build_info)
    for name, data in outer.items():
        plugin["agent-flow/" + name] = data
    # Both archives already share the exact captured bytes before any output is written.
    stem = f"agent-flow-{manifest['version']}"
    results = {f"{stem}-skill.zip": archive_bytes(common), f"{stem}-codex-plugin.zip": archive_bytes(plugin)}
    sums = {name: digest(data) for name, data in results.items()}
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
    return {"version": manifest["version"], "package_sha256": package_hash,
            "archives": {str(output / name): sha for name, sha in sums.items()},
            "checksums": str(output / f"{stem}-SHA256SUMS.txt")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.source, args.output), indent=2))
    except (PackageError, OSError, ValueError) as exc:
        parser.exit(1, f"build: {exc}\n")


if __name__ == "__main__":
    main()
