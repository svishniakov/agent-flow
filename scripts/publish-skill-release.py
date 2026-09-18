#!/usr/bin/env python3
"""Publish one verified Codex plugin ZIP, making new releases public last."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_distributions", SOURCE / "scripts/verify-distributions.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
PackageError, require = verifier.PackageError, verifier.require


def command(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True)
    require(result.returncode == 0, f"{args[0]} command failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def check_source(source, tag, sha):
    require(re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag),
            "release tag must be vX.Y.Z without suffixes or leading zeroes")
    require(re.fullmatch(r"[0-9a-f]{40}", sha), "release SHA must be a full lowercase commit SHA")
    verifier.builder.check_checkout(source, sha)
    manifest = json.loads((source / ".codex-plugin/plugin.json").read_text())
    require(manifest["version"] == tag[1:], "release tag does not match manifest version")
    remote = command(["git", "ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], source)
    refs = dict(line.split()[::-1] for line in remote.decode().splitlines())
    require(refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}")) == sha,
            "remote release tag does not resolve to the requested commit SHA")
    command(["git", "fetch", "--no-tags", "origin", "refs/heads/main"], source)
    result = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "FETCH_HEAD"], cwd=source,
                            capture_output=True)
    require(result.returncode == 0, "release commit is not in remote main history")


def check_release_identity(release, tag, marker):
    require(isinstance(release, dict) and type(release.get("id")) is int and release["id"] > 0
            and release.get("tag_name") == tag and isinstance(release.get("body"), str)
            and marker in release["body"].splitlines()
            and type(release.get("draft")) is bool and release.get("prerelease") is False
            and type(release.get("immutable")) is bool, "invalid release identity or state")


class GitHub:
    def __init__(self, repository):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "invalid repository")
        self.repository = repository
        self.base = f"repos/{repository}"

    def release(self, tag):
        result = subprocess.run(["gh", "api", f"{self.base}/releases/tags/{tag}"], capture_output=True)
        if result.returncode:
            # Only the explicit HTTP status permits creation; auth/network failures stop here.
            require(b"(HTTP 404)" in result.stderr, "cannot read release: " + result.stderr.decode(errors="replace"))
            # The tag endpoint omits drafts; authenticated release listings include them.
            pages = json.loads(command(["gh", "api", f"{self.base}/releases", "--paginate", "--slurp"]))
            matches = [release for page in pages for release in page if release.get("tag_name") == tag]
            require(len(matches) <= 1, "multiple releases match the requested tag")
            return matches[0] if matches else None
        return json.loads(result.stdout)

    def assets(self, release):
        pages = json.loads(command(["gh", "api", f"{self.base}/releases/{release['id']}/assets",
                                    "--paginate", "--slurp"]))
        return [asset for page in pages for asset in page]

    def verify_assets(self, release, files, complete):
        assets = self.assets(release)
        require(all(isinstance(asset, dict) and type(asset.get("id")) is int
                    and asset["id"] > 0 and isinstance(asset.get("name"), str) for asset in assets),
                "invalid release asset identity")
        require(len({asset["id"] for asset in assets}) == len(assets), "duplicate release asset IDs")
        names = [asset["name"] for asset in assets]
        require(len(names) == len(set(names)), "duplicate release assets")
        require(set(names) <= files.keys(), "unexpected release asset")
        if complete:
            require(set(names) == files.keys(), "incomplete release assets")
        for asset in assets:
            data = command(["gh", "api", f"{self.base}/releases/assets/{asset['id']}",
                            "--header", "Accept: application/octet-stream"])
            require(data == files[asset["name"]], "downloaded asset bytes mismatch: " + asset["name"])
        return {asset["name"]: asset["id"] for asset in assets}

    def create(self, tag, notes):
        with tempfile.TemporaryDirectory(prefix="agent-flow-release-") as temporary:
            path = Path(temporary) / "notes.md"
            path.write_text(notes)
            command(["gh", "release", "create", tag, "--repo", self.repository, "--verify-tag", "--draft",
                     "--title", f"Agent Flow {tag}", "--notes-file", str(path)])

    def upload(self, tag, path):
        command(["gh", "release", "upload", tag, str(path), "--repo", self.repository])

    def publish(self, tag):
        command(["gh", "release", "edit", tag, "--repo", self.repository, "--draft=false", "--verify-tag"])

    def delete_asset(self, asset_id):
        command(["gh", "api", f"{self.base}/releases/assets/{asset_id}", "--method", "DELETE"])

    def update_notes(self, tag, notes):
        with tempfile.TemporaryDirectory(prefix="agent-flow-release-notes-") as temporary:
            path = Path(temporary) / "notes.md"
            path.write_text(notes)
            command(["gh", "release", "edit", tag, "--repo", self.repository, "--notes-file", str(path)])


def release_notes(tag, sha, repository, plugin_name, plugin_bytes):
    return (f"<!-- agent-flow-plugin-release {tag} {sha} -->\n\n"
            f"## Codex plugin\n\n[Download {plugin_name}]"
            f"(https://github.com/{repository}/releases/download/{tag}/{plugin_name})\n\n"
            f"SHA-256: `{verifier.digest(plugin_bytes)}`\n\n"
            f"## Standalone skill\n\n```sh\nnpx skills add https://github.com/{repository} -a codex -g\n```\n\n"
            f"[Installation and updates](https://github.com/{repository}/blob/main/"
            "skills/agent-flow/docs/en/installation.md).\n\n"
            f"Source commit: `{sha}`. GitHub's Source code links are repository snapshots.\n")


def legacy_skill_files(source, tag, sha):
    common, _, info = verifier.builder.distribution_files(source, sha, release_tag=tag)
    stem = f"agent-flow-{tag[1:]}"
    files = {f"{stem}-skill.zip": verifier.builder.archive_bytes(common)}
    sums = {name: verifier.digest(data) for name, data in files.items()}
    metadata = f"{stem}-build.json"
    files[metadata] = verifier.json_bytes({**info, "format": "skill", "archives": sums.copy()})
    sums[metadata] = verifier.digest(files[metadata])
    files[f"{stem}-SHA256SUMS.txt"] = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(sums.items())).encode()
    return files


def replace_skill_release(github, release, source, directory, tag, sha, files, notes, backup):
    old_marker = f"<!-- agent-flow-skill-release {tag} {sha} -->"
    check_release_identity(release, tag, old_marker)
    require(release["draft"] is False and release["immutable"] is False,
            "existing release is not a mutable public skill release")

    def current_release():
        current = github.release(tag)
        check_release_identity(current, tag, old_marker)
        require(current["id"] == release["id"] and current["draft"] is False
                and current["immutable"] is False, "release identity changed")
        return current

    legacy = legacy_skill_files(source, tag, sha)
    allowed = {**legacy, **files}
    present = github.verify_assets(release, allowed, complete=False)
    plugin_name = next(iter(files))
    require(plugin_name in present or set(present) == set(legacy),
            "incomplete legacy release without verified plugin")
    require(not backup.is_symlink(), "backup directory must not be a symlink")
    backup = backup.resolve()
    require(not backup.is_relative_to(source) and not backup.is_relative_to(directory),
            "backup must be outside source and distribution directories")
    backup.mkdir(parents=True, exist_ok=True)
    require({p.name for p in backup.iterdir()} <= legacy.keys(), "unexpected backup file")
    # A resumed deletion needs the original backup, not a reconstruction of missing remote bytes.
    for name, data in legacy.items():
        path = backup / name
        require(not path.is_symlink(), "backup file must not be a symlink")
        if path.exists():
            require(path.is_file() and path.read_bytes() == data, "backup bytes mismatch: " + name)
        else:
            require(name in present, "missing backup for deleted legacy asset: " + name)
            with path.open("xb") as stream:
                stream.write(data)
    check_source(source, tag, sha)
    require(github.verify_assets(current_release(), allowed, complete=False) == present,
            "asset IDs changed before upload")
    if plugin_name not in present:
        github.upload(tag, directory / plugin_name)
    verified = github.verify_assets(release, allowed, complete=False)
    require(plugin_name in verified, "uploaded plugin is missing")
    require({name: asset_id for name, asset_id in verified.items() if name in legacy}
            == {name: asset_id for name, asset_id in present.items() if name in legacy},
            "legacy asset IDs changed before deletion")
    for name in sorted(legacy.keys() & verified.keys()):
        check_source(source, tag, sha)
        require(github.verify_assets(current_release(), allowed, complete=False) == verified,
                "asset IDs changed during replacement")
        github.delete_asset(verified[name])
        del verified[name]
    check_source(source, tag, sha)
    require(github.verify_assets(current_release(), files, complete=True) == verified,
            "asset IDs changed before updating notes")
    github.update_notes(tag, notes)
    published = github.release(tag)
    check_release_identity(published, tag, notes.splitlines()[0])
    require(published["id"] == release["id"] and published["draft"] is False
            and notes == published["body"], "replacement release was not confirmed")
    github.verify_assets(published, files, complete=True)
    return {"status": "replaced-skill-release", "url": published["html_url"],
            "release_id": published["id"], "commit_sha": sha,
            "plugin_sha256": verifier.digest(files[plugin_name]), "backup": str(backup)}


def publish(source, directory, tag, sha, repository, replace_skill=False, backup=None):
    require(replace_skill == (backup is not None),
            "--replace-skill-release and --backup-directory must be used together")
    check_source(source, tag, sha)
    verifier.verify(source, directory, sha, release_tag=tag)
    plugin_name = f"agent-flow-{tag[1:]}-codex-plugin.zip"
    files = {plugin_name: (directory / plugin_name).read_bytes()}
    github = GitHub(repository)
    marker = f"<!-- agent-flow-plugin-release {tag} {sha} -->"
    notes = release_notes(tag, sha, repository, plugin_name, files[plugin_name])
    release = github.release(tag)
    if replace_skill and (not isinstance(release, dict) or not isinstance(release.get("body"), str)
                          or marker not in release["body"].splitlines()):
        require(release is not None, "replacement requires an existing release")
        return replace_skill_release(github, release, source, directory, tag, sha, files, notes, backup)
    if release is None:
        github.create(tag, notes)
        release = github.release(tag)
    check_release_identity(release, tag, marker)
    present = github.verify_assets(release, files, complete=not release["draft"])
    if not release["draft"]:
        check_source(source, tag, sha)
        return {"status": "already-published", "url": release["html_url"]}
    for name in sorted(files.keys() - present.keys()):
        github.upload(tag, directory / name)
    github.verify_assets(release, files, complete=True)
    check_source(source, tag, sha)
    github.publish(tag)
    published = github.release(tag)
    check_release_identity(published, tag, marker)
    require(not published["draft"] and published["id"] == release["id"],
            "release publication was not confirmed")
    github.verify_assets(published, files, complete=True)
    return {"status": "published", "url": published["html_url"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--check-source-only", action="store_true")
    parser.add_argument("--replace-skill-release", action="store_true")
    parser.add_argument("--backup-directory", type=Path)
    args = parser.parse_args()
    try:
        source = args.source.resolve()
        if args.check_source_only:
            require(not args.replace_skill_release and args.backup_directory is None,
                    "source-only check does not accept replacement options")
            check_source(source, args.release_tag, args.commit_sha)
            result = {"release_tag": args.release_tag, "commit_sha": args.commit_sha, "source": "verified"}
        else:
            require(args.directory is not None and args.repository, "directory and repository are required for publication")
            result = publish(source, args.directory.resolve(), args.release_tag, args.commit_sha, args.repository,
                             args.replace_skill_release, args.backup_directory)
        print(json.dumps(result, indent=2))
    except (PackageError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"release: {exc}\n")


if __name__ == "__main__":
    main()
