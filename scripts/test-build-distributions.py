#!/usr/bin/env python3
"""Archive, relocation, and installed-source regression checks; no model calls."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/agent-flow/scripts"))
from package_distribution import (PackageError, check_package, digest, inventory, json_bytes,
                                  marketplace, validate_manifest, validate_marketplace)

spec = importlib.util.spec_from_file_location("build_distributions", ROOT / "scripts/build-distributions.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
spec = importlib.util.spec_from_file_location("verify_distributions", ROOT / "scripts/verify-distributions.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class Distributions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-distribution-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.output = cls.base / "archives with spaces"
        cls.result = builder.build(ROOT, cls.output)
        cls.install = cls.base / "relocated packages"
        for archive in cls.result["archives"]:
            form = "plugin" if archive.endswith("-codex-plugin.zip") else "skill"
            with zipfile.ZipFile(archive) as z:
                z.extractall(cls.install / form)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_reproducible_and_identical_common_package(self):
        second = builder.build(ROOT, self.base / "second build")
        self.assertEqual(list(self.result["archives"].values()), list(second["archives"].values()))
        skill = self.install / "skill/agent-flow"
        plugin = self.install / "plugin/agent-flow/skills/agent-flow"
        self.assertEqual(inventory(skill), inventory(plugin))
        self.assertEqual((skill / "agent-flow-package.json").read_bytes(), (plugin / "agent-flow-package.json").read_bytes())

    def test_installed_check_runs_without_source_checkout(self):
        for root in (self.install / "skill/agent-flow", self.install / "plugin/agent-flow/skills/agent-flow"):
            result = subprocess.run([sys.executable, str(root / "scripts/check-installed-package.py"), "--package-only"], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["package_root"], str(root))

    def test_both_archives_create_v2_journals_and_reject_manual_final(self):
        for form, package in (("skill", self.install / "skill/agent-flow"),
                              ("plugin", self.install / "plugin/agent-flow/skills/agent-flow")):
            with self.subTest(form=form):
                project = self.base / f"{form}-lifecycle"
                project.mkdir()
                command = [sys.executable, "-B", str(package / "scripts/init-run.py"),
                           "--repo", str(project), "--slug", "archive-check", "--mode", "compact"]
                initialized = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                run = Path(initialized.stdout.strip())
                with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
                    before = connection.execute("SELECT version, revision FROM run_state").fetchone()
                self.assertEqual(before[0], 2)
                manual = subprocess.run([sys.executable, "-B", str(package / "scripts/append-timeline.py"),
                                         "--run-dir", str(run), "--role", "orchestrator", "--stage", "final",
                                         "--status", "pass", "--summary", "Manual final must fail"],
                                        capture_output=True, text=True)
                self.assertNotEqual(manual.returncode, 0, manual.stdout + manual.stderr)
                self.assertIn("finalize", manual.stdout + manual.stderr)
                with sqlite3.connect(run / ".journal/state.sqlite3") as connection:
                    self.assertEqual(before, connection.execute("SELECT version, revision FROM run_state").fetchone())
                help_result = subprocess.run([sys.executable, "-B", str(package / "scripts/journal.py"),
                                              "--run-dir", str(run), "finalize", "--help"],
                                             capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn("--expected-generation", help_result.stdout)

    def copy_source(self, name):
        source = self.base / name
        shutil.copytree(ROOT / "skills/agent-flow", source / "skills/agent-flow", ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, source / name)
        return source

    def test_missing_resource_and_symlink_fail_before_output(self):
        source = self.copy_source("broken resource")
        path = source / "skills/agent-flow/references/delegation.md"
        path.unlink()
        output = self.base / "missing output"
        with self.assertRaisesRegex(PackageError, "missing"):
            builder.build(source, output)
        self.assertFalse(output.exists())
        path.symlink_to(ROOT / "skills/agent-flow/references/delegation.md")
        with self.assertRaisesRegex(PackageError, "symlink"):
            builder.build(source, output)
        self.assertFalse(output.exists())

    def test_local_files_are_not_packaged(self):
        source = self.copy_source("local additions")
        (source / "skills/agent-flow/scripts/private.txt").write_text("DO_NOT_SHIP")
        (source / "skills/agent-flow/.env").write_text("DO_NOT_SHIP")
        result = builder.build(source, self.base / "filtered output")
        for path in result["archives"]:
            with zipfile.ZipFile(path) as z:
                self.assertFalse(any("private.txt" in p or p.endswith("/.env") for p in z.namelist()))
                self.assertFalse(any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in z.infolist()))

    def test_rejects_unsafe_file_list(self):
        source = self.copy_source("unsafe list")
        index = source / "skills/agent-flow/package-files.txt"
        names = index.read_text().splitlines()
        index.write_text("\n".join(sorted([*names, "../account.json"])) + "\n")
        with self.assertRaisesRegex(PackageError, "unsafe package path"):
            builder.build(source, self.base / "unsafe output")

    def test_schema_negative_cases(self):
        for field in ("policy", "category"):
            data = marketplace()
            del data["plugins"][0][field]
            with self.assertRaisesRegex(PackageError, "policy and category"):
                validate_marketplace(data)
        data = marketplace()
        data["plugins"][0]["source"]["path"] = "../outside"
        with self.assertRaises(PackageError):
            validate_marketplace(data)
        manifest = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        for field in ("name", "version", "skills", "interface"):
            broken = dict(manifest)
            del broken[field]
            with self.assertRaises(PackageError):
                validate_manifest(broken)

    def test_no_build_tree_in_source(self):
        with self.assertRaisesRegex(PackageError, "outside source"):
            builder.build(ROOT, ROOT / "dist")

    def test_changed_outer_document_selects_a_new_plugin_cache_version(self):
        source = self.copy_source("changed readme")
        readme = source / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nRelease fixture.\n")
        result = builder.build(source, self.base / "new version")
        self.assertNotEqual(result["version"], self.result["version"])

    def test_missing_checksum_record_is_not_a_source_install(self):
        root = self.base / "missing record"
        shutil.copytree(self.install / "skill/agent-flow", root)
        (root / "agent-flow-package.json").unlink()
        with self.assertRaisesRegex(PackageError, "missing package checksum record"):
            check_package(root)

    def test_missing_plugin_metadata_is_not_a_standalone_install(self):
        for index, name in enumerate((".codex-plugin/plugin.json", "agent-flow-build.json", ".agents/plugins/marketplace.json")):
            with self.subTest(name=name):
                root = self.base / f"missing plugin metadata {index}"
                shutil.copytree(self.install / "plugin/agent-flow", root)
                (root / name).unlink()
                with self.assertRaises(PackageError):
                    check_package(root / "skills/agent-flow")

    def test_external_package_root_symlink_fails_before_output(self):
        source = self.copy_source("external package root")
        package = source / "skills/agent-flow"
        external = self.base / "external package"
        package.rename(external)
        package.symlink_to(external, target_is_directory=True)
        output = self.base / "external output"
        with self.assertRaisesRegex(PackageError, "symlink package root"):
            builder.build(source, output)
        self.assertFalse(output.exists())

    def test_project_role_directory_symlink_is_detected(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "linked project roles"
        (project / ".codex").mkdir(parents=True)
        roles = self.base / "global roles"
        result = subprocess.run([sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(roles)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (project / ".codex/agents").symlink_to(roles, target_is_directory=True)
        result = subprocess.run([sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(roles), "--project", str(project)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory symlink", result.stderr)

    def test_explicit_codex_role_overrides_are_detected_without_writes(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "explicit overrides"
        project.mkdir()
        home = project / "profile"
        roles = home / "agents"
        result = subprocess.run([sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(roles)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = home / "config.toml"
        command = [sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(roles), "--project", str(project)]
        environment = dict(os.environ, CODEX_HOME=str(home))
        for filename, table in (("config.toml", "agents.qa-verifier"), ("config.toml", "profiles.legacy.agents.qa-verifier"), ("legacy.config.toml", "agents.qa-verifier")):
            config.unlink(missing_ok=True)
            config = home / filename
            config.write_text(f'[{table}]\nconfig_file = "custom.toml"\n')
            before = {p.name: p.read_bytes() for p in home.rglob("*") if p.is_file()}
            result = subprocess.run(command, env=environment, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("explicit role overrides", result.stderr)
            self.assertEqual(before, {p.name: p.read_bytes() for p in home.rglob("*") if p.is_file()})

    def test_role_drift_is_detected_in_installed_copy(self):
        package = self.install / "plugin/agent-flow/skills/agent-flow"
        project = self.base / "consumer project"
        project.mkdir()
        output = project / ".codex/agents"
        command = [sys.executable, str(package / "scripts/sync-codex-agent-config.py"), "--output-dir", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        check = [sys.executable, str(package / "scripts/check-installed-package.py"), "--roles-dir", str(output), "--project", str(project)]
        result = subprocess.run(check, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        role = output / "qa-verifier.toml"
        role.write_text(role.read_text().replace('model = "gpt-6-astra"', 'model = "different-model"'))
        result = subprocess.run(check, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(role), result.stderr)


class CIDistributions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="agent-flow-ci-distribution-tests-")
        cls.base = Path(cls.temp.name).resolve()
        cls.source = cls.base / "source"
        shutil.copytree(ROOT / "skills/agent-flow", cls.source / "skills/agent-flow",
                        ignore=shutil.ignore_patterns("__pycache__", "*.bak"))
        shutil.copytree(ROOT / ".codex-plugin", cls.source / ".codex-plugin")
        for name in ("README.md", "README.ru.md", "LICENSE"):
            shutil.copyfile(ROOT / name, cls.source / name)
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"]):
            subprocess.run(["git", "-C", str(cls.source), *args], check=True, capture_output=True)
        cls.sha = subprocess.check_output(["git", "-C", str(cls.source), "rev-parse", "HEAD"], text=True).strip()
        cls.output = cls.base / "distribution"
        cls.result = builder.build(cls.source, cls.output, cls.sha, "123", "1")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def verify(self, directory=None, sha=None, run="123", attempt="1"):
        return verifier.verify(self.source, directory or self.output, sha or self.sha, run, attempt)

    def copy_distribution(self):
        directory = self.base / self.id().split(".")[-1]
        shutil.copytree(self.output, directory)
        return directory

    def refresh_sums(self, directory):
        metadata_path = directory / Path(self.result["metadata"]).name
        metadata = json.loads(metadata_path.read_bytes())
        metadata["archives"] = {p.name: digest(p.read_bytes()) for p in directory.glob("*.zip")}
        metadata_path.write_bytes(json_bytes(metadata))
        sums = {**metadata["archives"], metadata_path.name: digest(metadata_path.read_bytes())}
        (directory / Path(self.result["checksums"]).name).write_text(
            "".join(f"{sha}  {name}\n" for name, sha in sorted(sums.items())))

    def mutate_archive(self, directory, change):
        path = next(directory.glob("*-skill.zip"))
        with zipfile.ZipFile(path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        change(files)
        path.write_bytes(builder.archive_bytes(files))
        self.refresh_sums(directory)

    def test_cli_build_and_verify_clean_install(self):
        output = self.base / "cli"
        identity = ["--commit-sha", self.sha, "--run-id", "123", "--run-attempt", "1"]
        for script, args in (("build-distributions.py", ["--output", str(output)]),
                             ("verify-distributions.py", ["--directory", str(output)])):
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / script),
                                     "--source", str(self.source), *args, *identity],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["version"], self.result["version"])
        self.assertEqual(len(list(output.iterdir())), 4)

    def test_reproducible_next_build_and_no_overwrite(self):
        output = self.base / "reproducible"
        builder.build(self.source, output, self.sha, "123", "1")
        self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()},
                         {p.name: p.read_bytes() for p in self.output.iterdir()})
        for run, attempt in (("124", "1"), ("123", "2")):
            result = builder.build(self.source, output, self.sha, run, attempt)
            self.assertNotEqual(result["version"], self.result["version"])
            self.assertTrue({Path(p).name for p in result["archives"]}.isdisjoint(
                Path(p).name for p in self.result["archives"]))
        target = next(output.glob("*-dev.123.1-skill.zip"))
        target.write_bytes(b"do not overwrite")
        with self.assertRaisesRegex(PackageError, "different bytes"):
            builder.build(self.source, output, self.sha, "123", "1")
        self.assertEqual(target.read_bytes(), b"do not overwrite")

    def test_invalid_identity_fails_before_output(self):
        values = [(self.sha, None, "1"), (None, "1", "1"), ("bad", "1", "1"),
                  (self.sha, "0", "1"), (self.sha, "01", "1"), (self.sha, "1", "-1"),
                  (self.sha, "1", "1.0"), (self.sha, "1", " 1"), ("0" * 40, "1", "1")]
        for index, identity in enumerate(values):
            with self.subTest(identity=identity):
                output = self.base / f"invalid-{index}"
                with self.assertRaises(PackageError):
                    builder.build(self.source, output, *identity)
                self.assertFalse(output.exists())
        with self.assertRaisesRegex(PackageError, "Git HEAD"):
            self.verify(sha="0" * 40)
        with self.assertRaises(PackageError):
            self.verify(run="124")

    def test_dirty_tracked_source_fails(self):
        path = self.source / "README.md"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\nChanged tracked source\n")
            with self.assertRaisesRegex(PackageError, "tracked source"):
                builder.build(self.source, self.base / "dirty", self.sha, "123", "1")
        finally:
            path.write_bytes(original)

    def test_extra_output_and_bad_checksum_fail(self):
        directory = self.copy_distribution()
        extra = directory / "old.zip"
        extra.write_bytes(b"old build")
        with self.assertRaisesRegex(PackageError, "exactly"):
            self.verify(directory)
        extra.unlink()
        (directory / Path(self.result["checksums"]).name).write_text("wrong\n")
        with self.assertRaisesRegex(PackageError, "SHA256SUMS"):
            self.verify(directory)

    def test_unsafe_archive_fails_even_with_valid_checksums(self):
        directory = self.copy_distribution()
        self.mutate_archive(directory, lambda files: files.update({"../escape": b"escape"}))
        with self.assertRaisesRegex(PackageError, "unsafe"):
            self.verify(directory)
        self.assertFalse((self.base / "escape").exists())

    def test_extra_archive_member_fails(self):
        directory = self.copy_distribution()
        self.mutate_archive(directory, lambda files: files.update({"agent-flow/extra": b"extra"}))
        with self.assertRaisesRegex(PackageError, "inventory mismatch"):
            self.verify(directory)

    def test_wrong_record_version_and_common_bytes_fail(self):
        for index, mutation in enumerate(("version", "commit_sha", "common")):
            directory = self.base / f"record-mutation-{index}"
            shutil.copytree(self.output, directory)
            def change(files):
                if mutation == "common":
                    files["agent-flow/SKILL.md"] += b"\nchanged"
                else:
                    name = "agent-flow/agent-flow-package.json"
                    record = json.loads(files[name])
                    record[mutation] = "0.0.0" if mutation == "version" else "f" * 40
                    files[name] = json_bytes(record)
            self.mutate_archive(directory, change)
            with self.assertRaisesRegex(PackageError, "bytes or metadata mismatch"):
                self.verify(directory)

    def test_partial_installed_ci_record_fails(self):
        installed = self.base / "partial-record"
        with zipfile.ZipFile(next(self.output.glob("*-skill.zip"))) as archive:
            archive.extractall(installed)
        path = installed / "agent-flow/agent-flow-package.json"
        record = json.loads(path.read_bytes())
        del record["run_id"]
        path.write_bytes(json_bytes(record))
        with self.assertRaisesRegex(PackageError, "together"):
            check_package(path.parent)

    def test_unsupported_base_version_fails(self):
        source = self.base / "unsupported-version"
        shutil.copytree(self.source, source)
        path = source / ".codex-plugin/plugin.json"
        manifest = json.loads(path.read_bytes())
        manifest["version"] += "-rc.1"
        path.write_bytes(json_bytes(manifest))
        subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "Prerelease fixture"], check=True, capture_output=True)
        sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        with self.assertRaisesRegex(PackageError, "base version"):
            builder.build(source, self.base / "unsupported-output", sha, "123", "1")

    def test_partial_cli_identity_fails_without_output(self):
        output = self.base / "partial-cli"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-distributions.py"),
                                 "--source", str(self.source), "--output", str(output),
                                 "--commit-sha", self.sha], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("together", result.stderr)
        self.assertFalse(output.exists())


class ReleaseDistributions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CIDistributions.setUpClass.__func__(cls)
        cls.tag = "v" + json.loads((cls.source / ".codex-plugin/plugin.json").read_bytes())["version"]
        cls.output = cls.base / "release"
        cls.result = builder.build(cls.source, cls.output, cls.sha, release_tag=cls.tag)

    tearDownClass = classmethod(CIDistributions.tearDownClass.__func__)
    copy_distribution = CIDistributions.copy_distribution
    refresh_sums = CIDistributions.refresh_sums
    mutate_archive = CIDistributions.mutate_archive

    def verify(self, directory=None):
        return verifier.verify(self.source, directory or self.output, self.sha, release_tag=self.tag)

    def test_release_cli_and_installed_package(self):
        output = self.base / "release-cli"
        for script, flag in (("build-distributions.py", "--output"),
                             ("verify-distributions.py", "--directory")):
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / script),
                                     "--source", str(self.source), flag, str(output),
                                     "--commit-sha", self.sha, "--release-tag", self.tag],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["version"], self.tag[1:])
        self.assertEqual(data["unpacked_package_checks"], "passed")
        print("release-tag-match-ok release-skill-bundle-verified release-unpacked-skill-check-passed")

    def test_release_rebuild_has_exact_three_identical_files(self):
        output = self.base / "release-rebuilt"
        builder.build(self.source, output, self.sha, release_tag=self.tag)
        self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()},
                         {p.name: p.read_bytes() for p in self.output.iterdir()})
        self.assertEqual(len(list(output.iterdir())), 3)
        metadata = json.loads(Path(self.result["metadata"]).read_bytes())
        self.assertEqual(metadata["format"], "skill")
        self.assertEqual(metadata["release_tag"], self.tag)
        self.assertEqual(metadata["commit_sha"], self.sha)
        self.assertEqual(set(metadata["archives"]), {f"agent-flow-{self.tag[1:]}-skill.zip"})
        self.assertNotIn("run_id", metadata)
        self.assertNotIn("run_attempt", metadata)
        print("release-rebuild-identical")

    def test_release_invalid_cli_identity_fails_before_output(self):
        cases = [(self.tag, None, "release commit-sha"),
                 ("v01.0.0", self.sha, "release-tag"),
                 ("v1.0.0-rc.1", self.sha, "release-tag"),
                 ("v1.0.0+build", self.sha, "release-tag"),
                 ("vbad", self.sha, "release-tag"),
                 ("v999.0.0", self.sha, "manifest version"),
                 (self.tag, "0" * 40, "Git HEAD")]
        for index, (tag, sha, error) in enumerate(cases):
            output = self.base / f"invalid-release-{index}"
            args = ["--commit-sha", sha] if sha is not None else []
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build-distributions.py"),
                                     "--source", str(self.source), "--output", str(output),
                                     "--release-tag", tag, *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertFalse(output.exists())
        print("release-tag-invalid-rejected release-tag-mismatch-rejected release-source-sha-rejected")

    def test_release_mixed_identity_rejected(self):
        for run, attempt in (("1", None), (None, "1"), ("1", "1")):
            with self.assertRaisesRegex(PackageError, "forbids"):
                builder.build(self.source, self.base / "mixed-release", self.sha,
                              run, attempt, self.tag)
        installed = self.base / "release-installed"
        with zipfile.ZipFile(next(self.output.glob("*.zip"))) as archive:
            archive.extractall(installed)
        path = installed / "agent-flow/agent-flow-package.json"
        original = json.loads(path.read_bytes())
        for change in ({"run_id": None}, {"run_attempt": "1"}, {"version": "999.0.0"},
                       {"commit_sha": None}, {"release_tag": None}):
            path.write_bytes(json_bytes({**original, **change}))
            with self.assertRaises(PackageError):
                check_package(path.parent)
        print("release-mixed-identity-rejected")

    def test_release_corruption_rejected_with_updated_checksums(self):
        for index, mutation in enumerate(("zip", "metadata", "extra", "zip-container", "checksum")):
            directory = self.base / f"corrupt-release-{index}"
            shutil.copytree(self.output, directory)
            if mutation == "zip":
                self.mutate_archive(directory, lambda files: files.update({"agent-flow/SKILL.md": b"corrupt"}))
            elif mutation == "metadata":
                path = directory / Path(self.result["metadata"]).name
                data = json.loads(path.read_bytes())
                data["release_tag"] = "v999.0.0"
                path.write_bytes(json_bytes(data))
                self.refresh_sums(directory)
            elif mutation == "zip-container":
                path = next(directory.glob("*.zip"))
                path.write_bytes(path.read_bytes() + b"trailing bytes")
                self.refresh_sums(directory)
            elif mutation == "checksum":
                (directory / Path(self.result["checksums"]).name).write_text("wrong checksum")
            else:
                (directory / "extra.zip").write_bytes(b"unexpected")
            with self.assertRaises(PackageError):
                self.verify(directory)
        print("release-bundle-corruption-rejected")

    def test_release_dirty_source_and_overwrite_rejected(self):
        path = self.source / "README.md"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"changed")
            with self.assertRaisesRegex(PackageError, "tracked source"):
                builder.build(self.source, self.base / "release-dirty", self.sha, release_tag=self.tag)
        finally:
            path.write_bytes(original)
        directory = self.copy_distribution()
        archive = next(directory.glob("*.zip"))
        archive.write_bytes(b"keep existing")
        with self.assertRaisesRegex(PackageError, "different bytes"):
            builder.build(self.source, directory, self.sha, release_tag=self.tag)
        self.assertEqual(archive.read_bytes(), b"keep existing")


if __name__ == "__main__":
    unittest.main()
