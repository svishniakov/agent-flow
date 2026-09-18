#!/usr/bin/env python3
"""Publish a verified numbered skill bundle, making the release public last."""

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
        names = [asset["name"] for asset in assets]
        require(len(names) == len(set(names)), "duplicate release assets")
        require(set(names) <= files.keys(), "unexpected release asset")
        if complete:
            require(set(names) == files.keys(), "incomplete release assets")
        for asset in assets:
            data = command(["gh", "api", f"{self.base}/releases/assets/{asset['id']}",
                            "--header", "Accept: application/octet-stream"])
            require(data == files[asset["name"]].read_bytes(), "downloaded asset bytes mismatch: " + asset["name"])
        return set(names)

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


def publish(source, directory, tag, sha, repository):
    check_source(source, tag, sha)
    verifier.verify(source, directory, sha, release_tag=tag)
    files = {path.name: path for path in directory.iterdir()}
    github = GitHub(repository)
    marker = f"<!-- agent-flow-skill-release {tag} {sha} -->"
    release = github.release(tag)
    if release is None:
        notes = (f"{marker}\n\nDownload `agent-flow-{tag[1:]}-skill.zip` and verify it using the attached "
                 f"SHA256SUMS file. Installation: https://github.com/{repository}/blob/{sha}/"
                 "skills/agent-flow/docs/en/installation.md\n\n"
                 f"Source commit: `{sha}`.\n")
        github.create(tag, notes)
        release = github.release(tag)
    require(release is not None and release.get("tag_name") == tag
            and marker in release.get("body", "") and not release.get("prerelease"),
            "existing release identity does not match this skill release")
    present = github.verify_assets(release, files, complete=not release["draft"])
    if not release["draft"]:
        check_source(source, tag, sha)
        return {"status": "already-published", "url": release["html_url"]}
    for name in sorted(files.keys() - present):
        github.upload(tag, files[name])
    github.verify_assets(release, files, complete=True)
    check_source(source, tag, sha)
    github.publish(tag)
    published = github.release(tag)
    require(published is not None and not published["draft"], "release publication was not confirmed")
    require(published.get("tag_name") == tag and marker in published.get("body", ""),
            "published release identity changed")
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
    args = parser.parse_args()
    try:
        source = args.source.resolve()
        if args.check_source_only:
            check_source(source, args.release_tag, args.commit_sha)
            result = {"release_tag": args.release_tag, "commit_sha": args.commit_sha, "source": "verified"}
        else:
            require(args.directory is not None and args.repository, "directory and repository are required for publication")
            result = publish(source, args.directory.resolve(), args.release_tag, args.commit_sha, args.repository)
        print(json.dumps(result, indent=2))
    except (PackageError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"release: {exc}\n")


if __name__ == "__main__":
    main()
